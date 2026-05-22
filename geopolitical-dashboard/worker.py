# worker.py
"""
Background data pipeline.

Lifecycle per cycle:
  1. Fetch RSS articles from configured feeds.
  2. Deduplicate against existing DB hashes.
  3. Send article to local Ollama for structured extraction.
  4. Validate and geocode the LLM response.
  5. Insert the clean event into SQLite.

Run continuously with: python worker.py
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import feedparser
import ollama

from config import (
    MAX_ARTICLES_PER_CYCLE,
    OLLAMA_MAX_RETRIES,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT,
    RSS_FEEDS,
    WORKER_INTERVAL_SECONDS,
)
from db import compute_feed_hash, fetch_latest_events, init_db, insert_event
from geocoder import resolve_coordinates

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("worker")


# ─── Ollama System Prompt ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a geopolitical intelligence parser. Your sole function is to convert raw news headlines and summaries into structured JSON. You output ONLY a single valid JSON object. No markdown, no code fences, no explanation, no preamble, no trailing text.

OUTPUT SCHEMA (all fields required):
{
  "source_country": "<string: country where the action originates>",
  "source_city": "<string: specific city/region, or empty string if unknown>",
  "source_lat": <float: latitude of source location, e.g. 55.7558>,
  "source_lon": <float: longitude of source location, e.g. 37.6173>,
  "target_country": "<string: country primarily affected or targeted>",
  "target_city": "<string: specific city/region, or empty string if unknown>",
  "target_lat": <float: latitude of target location>,
  "target_lon": <float: longitude of target location>,
  "event_type": "<one of exactly: Military, Diplomatic, Cyber, Economic, Humanitarian, Political>",
  "urgency": <integer: 1-10 where 1=routine and 10=immediate armed conflict>,
  "summary": "<string: one neutral sentence (max 200 chars) describing the event>"
}

RULES:
- source_country is where the initiating actor is based; target_country is the recipient/affected party.
- If the event is purely domestic (e.g. internal political crisis), set source_country and target_country to the same country.
- Coordinates must be the capital city or the most relevant city for that country. Do NOT use (0, 0).
- urgency: 1-3 = political statement/routine; 4-6 = sanctions/expulsion/protest; 7-8 = skirmish/cyberattack/crisis; 9-10 = active military strikes/war declaration.
- If the headline does not describe a geopolitical event (e.g. sports, entertainment), output exactly: {"error": "not_geopolitical"}
- Never wrap the JSON in markdown code blocks.

EXAMPLE INPUT:
"Russia launches missile strikes on Kyiv infrastructure amid escalating war"

EXAMPLE OUTPUT:
{"source_country":"Russia","source_city":"Moscow","source_lat":55.7558,"source_lon":37.6173,"target_country":"Ukraine","target_city":"Kyiv","target_lat":50.4501,"target_lon":30.5234,"event_type":"Military","urgency":9,"summary":"Russia conducts missile strikes targeting critical infrastructure in Kyiv."}"""


USER_PROMPT_TEMPLATE = """Parse this news article into the required JSON format.

HEADLINE: {headline}
SUMMARY: {summary}

JSON:"""


def _as_text(value: object) -> str:
    """Normalize feedparser values to plain text for downstream string APIs."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


# ─── RSS Fetching ─────────────────────────────────────────────────────────────

def fetch_articles() -> list[dict]:
    """
    Fetch articles from all configured RSS feeds.
    Returns a deduplicated list of article dicts, newest first.
    """
    seen_hashes: set[str] = set()
    articles: list[dict] = []

    for feed_url in RSS_FEEDS:
        try:
            logger.info("Fetching RSS: %s", feed_url)
            parsed = feedparser.parse(feed_url)

            if parsed.bozo and not parsed.entries:
                logger.warning("Feed parse error for %s: %s", feed_url, parsed.bozo_exception)
                continue

            for entry in parsed.entries[:MAX_ARTICLES_PER_CYCLE]:
                headline = _as_text(entry.get("title", "")).strip()
                if not headline:
                    continue

                # Extract summary text, strip HTML tags
                raw_summary = _as_text(entry.get("summary", entry.get("description", "")))
                summary = re.sub(r"<[^>]+>", "", raw_summary).strip()

                link = _as_text(entry.get("link", ""))
                article_hash = compute_feed_hash(headline, link)

                if article_hash in seen_hashes:
                    continue
                seen_hashes.add(article_hash)

                articles.append({
                    "headline": headline,
                    "summary": summary[:500],   # cap to avoid huge prompts
                    "link": link,
                    "feed_url": feed_url,
                })

        except Exception as exc:
            logger.error("Failed to fetch feed %s: %s", feed_url, exc)

    logger.info("Fetched %d unique articles across all feeds", len(articles))
    return articles


# ─── LLM Extraction ───────────────────────────────────────────────────────────

def _extract_json_from_response(text: str) -> Optional[dict]:
    """
    Robustly extract a JSON object from LLM response text.
    Handles cases where the model accidentally adds fences or extra text.
    """
    # Remove markdown code fences if present
    text = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    text = text.strip("`").strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find the first { ... } block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


def _validate_event_schema(data: dict) -> bool:
    """Check that the parsed dict contains all required fields with correct types."""
    required_fields = {
        "source_country": str,
        "target_country": str,
        "source_lat": (int, float),
        "source_lon": (int, float),
        "target_lat": (int, float),
        "target_lon": (int, float),
        "event_type": str,
        "urgency": int,
        "summary": str,
    }
    valid_event_types = {"Military", "Diplomatic", "Cyber", "Economic", "Humanitarian", "Political"}

    for field, expected_type in required_fields.items():
        if field not in data:
            logger.debug("Missing field: %s", field)
            return False
        if not isinstance(data[field], expected_type):
            # Allow float/int interop for numeric fields
            if expected_type == int and isinstance(data[field], float):
                data[field] = int(data[field])
            else:
                logger.debug("Wrong type for %s: got %s", field, type(data[field]))
                return False

    if data["event_type"] not in valid_event_types:
        logger.debug("Invalid event_type: %s — normalizing to 'Political'", data["event_type"])
        data["event_type"] = "Political"

    data["urgency"] = max(1, min(10, int(data["urgency"])))
    return True


def call_ollama(headline: str, summary: str) -> Optional[dict]:
    """
    Call local Ollama and extract a validated event dict.
    Returns None if extraction fails after retries.
    """
    user_message = USER_PROMPT_TEMPLATE.format(
        headline=headline,
        summary=summary or headline,
    )

    for attempt in range(1, OLLAMA_MAX_RETRIES + 2):
        try:
            logger.debug("Ollama attempt %d for: %s", attempt, headline[:60])
            response = ollama.chat(
                model=OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_message},
                ],
                options={
                    "temperature": 0.1,    # near-deterministic for JSON extraction
                    "num_predict": 400,    # JSON object won't exceed this
                },
            )

            raw_text = response["message"]["content"].strip()
            logger.debug("Raw LLM response: %s", raw_text[:200])

            parsed = _extract_json_from_response(raw_text)
            if parsed is None:
                logger.warning("Could not extract JSON on attempt %d", attempt)
                continue

            # Model flagged as non-geopolitical
            if parsed.get("error") == "not_geopolitical":
                logger.info("Skipped non-geopolitical article: %s", headline[:60])
                return None

            if _validate_event_schema(parsed):
                return parsed

            logger.warning("Schema validation failed on attempt %d: %s", attempt, parsed)

        except ollama.ResponseError as exc:
            logger.error("Ollama API error: %s", exc)
            time.sleep(5)
        except Exception as exc:
            logger.error("Unexpected error calling Ollama: %s", exc)
            time.sleep(5)

    return None


# ─── Event Assembly ───────────────────────────────────────────────────────────

def build_event_record(llm_data: dict, article: dict) -> dict:
    """
    Merge LLM output with geocoder-verified coordinates and article metadata.
    """
    # Resolve source coordinates
    src_lat, src_lon = resolve_coordinates(
        country=llm_data["source_country"],
        city=llm_data.get("source_city") or None,
        llm_lat=float(llm_data["source_lat"]),
        llm_lon=float(llm_data["source_lon"]),
    )

    # Resolve target coordinates
    tgt_lat, tgt_lon = resolve_coordinates(
        country=llm_data["target_country"],
        city=llm_data.get("target_city") or None,
        llm_lat=float(llm_data["target_lat"]),
        llm_lon=float(llm_data["target_lon"]),
    )

    return {
        "source_country": llm_data["source_country"].strip(),
        "source_city":    llm_data.get("source_city", "").strip(),
        "source_lat":     src_lat,
        "source_lon":     src_lon,
        "target_country": llm_data["target_country"].strip(),
        "target_city":    llm_data.get("target_city", "").strip(),
        "target_lat":     tgt_lat,
        "target_lon":     tgt_lon,
        "event_type":     llm_data["event_type"],
        "urgency":        int(llm_data["urgency"]),
        "summary":        llm_data["summary"][:500],
        "raw_headline":   article["headline"],
        "source_url":     article["link"],
    }


# ─── Already-seen hash cache ──────────────────────────────────────────────────

def load_existing_hashes() -> set[str]:
    """Pre-load hashes from DB to avoid redundant LLM calls."""
    import sqlite3
    import os
    from config import DB_PATH
    if not os.path.exists(DB_PATH):
        return set()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT feed_hash FROM events").fetchall()
    conn.close()
    return {r[0] for r in rows}


# ─── Main loop ────────────────────────────────────────────────────────────────

def run_pipeline_cycle(known_hashes: set[str]) -> set[str]:
    """
    Execute one full pipeline cycle.
    Returns the updated known_hashes set.
    """
    articles = fetch_articles()
    new_count = 0

    for article in articles:
        article_hash = compute_feed_hash(article["headline"], article["link"])

        if article_hash in known_hashes:
            logger.debug("Already processed: %s", article["headline"][:60])
            continue

        known_hashes.add(article_hash)   # optimistic add to avoid reprocessing in same cycle

        llm_data = call_ollama(article["headline"], article["summary"])
        if llm_data is None:
            continue

        event = build_event_record(llm_data, article)

        # Filter out (0,0) coordinates that indicate total resolution failure
        if event["source_lat"] == 0.0 and event["source_lon"] == 0.0:
            logger.warning("Source geocoding failed, skipping: %s", article["headline"][:60])
            continue
        if event["target_lat"] == 0.0 and event["target_lon"] == 0.0:
            logger.warning("Target geocoding failed, skipping: %s", article["headline"][:60])
            continue

        inserted = insert_event(event)
        if inserted:
            new_count += 1

        # Small delay between LLM calls to avoid overwhelming local Ollama
        time.sleep(2)

    logger.info("Cycle complete. Inserted %d new events.", new_count)
    return known_hashes


def main():
    logger.info("=" * 60)
    logger.info("Geopolitical War Room — Worker Starting")
    logger.info("Model: %s | Interval: %ds", OLLAMA_MODEL, WORKER_INTERVAL_SECONDS)
    logger.info("=" * 60)

    init_db()
    known_hashes = load_existing_hashes()
    logger.info("Loaded %d existing hashes from DB", len(known_hashes))

    while True:
        cycle_start = time.time()
        try:
            known_hashes = run_pipeline_cycle(known_hashes)
        except Exception as exc:
            logger.error("Pipeline cycle failed unexpectedly: %s", exc, exc_info=True)

        elapsed = time.time() - cycle_start
        sleep_for = max(0, WORKER_INTERVAL_SECONDS - elapsed)
        logger.info("Next cycle in %.0f seconds...", sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
