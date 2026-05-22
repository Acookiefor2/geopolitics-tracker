# config.py
"""
Central configuration for the Geopolitical War Room Dashboard.
Modify these values to tune behavior without touching core logic.
"""

import os

# ─── Ollama ───────────────────────────────────────────────────────────────────
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")
OLLAMA_TIMEOUT = 120          # seconds before giving up on LLM response
OLLAMA_MAX_RETRIES = 2        # retry attempts for malformed JSON

# ─── RSS Feeds ────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    "http://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.reuters.com/reuters/worldNews",
    "https://rss.dw.com/rdf/rss-en-world",
    "https://www.aljazeera.com/xml/rss/all.xml",
]
WORKER_INTERVAL_SECONDS = 300   # poll every 5 minutes
MAX_ARTICLES_PER_CYCLE = 10     # articles processed per feed cycle

# ─── Database ─────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "events.db")
MAX_EVENTS_DISPLAY = 200        # cap rows shown on dashboard

# ─── Geocoding ────────────────────────────────────────────────────────────────
GEOCODE_TIMEOUT = 10            # seconds
GEOCODE_USER_AGENT = "geopolitical-dashboard/1.0"

# ─── Map ──────────────────────────────────────────────────────────────────────
MAP_INITIAL_VIEW = {
    "latitude": 20.0,
    "longitude": 0.0,
    "zoom": 1.8,
    "pitch": 40,
    "bearing": 0,
}

# Neon color palette per event type [R, G, B, A]
EVENT_COLORS = {
    "Military":    [255, 50,  50,  220],   # neon red
    "Diplomatic":  [50,  200, 255, 220],   # neon cyan
    "Cyber":       [180, 50,  255, 220],   # neon purple
    "Economic":    [255, 200, 50,  220],   # neon amber
    "Humanitarian":[50,  255, 150, 220],   # neon green
    "Political":   [255, 100, 200, 220],   # neon pink
    "Unknown":     [150, 150, 150, 180],   # grey fallback
}

# Urgency scale → arc width multiplier
URGENCY_WIDTH_MAP = {
    range(1, 4):  2,
    range(4, 7):  5,
    range(7, 11): 9,
}

# ─── UI ───────────────────────────────────────────────────────────────────────
REFRESH_INTERVAL_SECONDS = 30   # Streamlit auto-refresh cadence
STREAMLIT_PAGE_TITLE = "⚡ GeoAlert War Room"