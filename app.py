# app.py
"""
Geopolitical War Room — Streamlit Dashboard

Renders a dark tactical map with:
  - ArcLayer: glowing neon arcs from source → target (color = event type)
  - ScatterplotLayer: pulsing rings on target locations
  - Sidebar: live event feed with urgency indicators

Run with: streamlit run app.py
"""

import time
from datetime import datetime
from typing import Optional

import pandas as pd
import pydeck as pdk
import streamlit as st

from config import (
    EVENT_COLORS,
    MAP_INITIAL_VIEW,
    MAX_EVENTS_DISPLAY,
    REFRESH_INTERVAL_SECONDS,
    STREAMLIT_PAGE_TITLE,
    URGENCY_WIDTH_MAP,
)
from db import fetch_event_count, fetch_latest_events, get_db_last_modified, init_db

# ─── Page configuration ───────────────────────────────────────────────────────

st.set_page_config(
    page_title=STREAMLIT_PAGE_TITLE,
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS — Dark War Room Aesthetic ─────────────────────────────────────

st.markdown(
    """
    <style>
    /* ── Global dark background ── */
    html, body, [data-testid="stApp"] {
        background-color: #050a12 !important;
        color: #c8d8e8 !important;
        font-family: 'Courier New', Courier, monospace;
    }

    /* ── Sidebar ── */
    [data-testid="stSidebar"] {
        background-color: #070e1a !important;
        border-right: 1px solid #0a3060;
    }
    [data-testid="stSidebar"] * { color: #8ab4d4 !important; }

    /* ── Headers ── */
    h1, h2, h3 { color: #00e5ff !important; letter-spacing: 0.08em; }
    h1 { text-shadow: 0 0 20px #00e5ff88; font-size: 1.4rem !important; }

    /* ── Metric boxes ── */
    [data-testid="metric-container"] {
        background: linear-gradient(135deg, #0a1628, #0d1f3c);
        border: 1px solid #0a4080;
        border-radius: 6px;
        padding: 12px;
    }
    [data-testid="metric-container"] label {
        color: #4a9eda !important;
        font-size: 0.7rem !important;
        letter-spacing: 0.1em;
        text-transform: uppercase;
    }
    [data-testid="metric-container"] [data-testid="stMetricValue"] {
        color: #00e5ff !important;
        font-size: 1.8rem !important;
        font-weight: bold;
    }

    /* ── Dataframe / table ── */
    [data-testid="stDataFrame"] {
        border: 1px solid #0a3060 !important;
        border-radius: 4px;
    }
    .stDataFrame thead th {
        background-color: #0a1628 !important;
        color: #4a9eda !important;
    }
    .stDataFrame tbody tr:nth-child(even) { background-color: #060c18; }
    .stDataFrame tbody tr:hover { background-color: #0a2040 !important; }

    /* ── Selectbox / multiselect ── */
    [data-testid="stSelectbox"] > div > div {
        background-color: #0a1628 !important;
        border-color: #0a4080 !important;
        color: #8ab4d4 !important;
    }

    /* ── Scrollbar ── */
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: #050a12; }
    ::-webkit-scrollbar-thumb { background: #1a4080; border-radius: 3px; }

    /* ── Alert/event card ── */
    .event-card {
        background: linear-gradient(135deg, #080f20, #0a1628);
        border-left: 3px solid #00e5ff;
        border-radius: 4px;
        padding: 8px 12px;
        margin-bottom: 8px;
        font-size: 0.78rem;
        line-height: 1.5;
    }
    .event-card.military  { border-left-color: #ff3232; }
    .event-card.diplomatic{ border-left-color: #32c8ff; }
    .event-card.cyber     { border-left-color: #b432ff; }
    .event-card.economic  { border-left-color: #ffc832; }
    .event-card.humanitarian { border-left-color: #32ff96; }
    .event-card.political { border-left-color: #ff64c8; }

    .urgency-badge {
        display: inline-block;
        padding: 1px 6px;
        border-radius: 3px;
        font-size: 0.68rem;
        font-weight: bold;
        margin-right: 6px;
    }

    /* ── Divider ── */
    hr { border-color: #0a3060 !important; }

    /* ── Status bar ── */
    .status-bar {
        font-size: 0.65rem;
        color: #3a7ab4;
        text-align: right;
        letter-spacing: 0.05em;
    }

    /* ── Hide Streamlit branding ── */
    #MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─── Helper utilities ─────────────────────────────────────────────────────────

def safe_str(value, default: str = "") -> str:
    """Convert any value to a safe string, handling None/NaN/NaT."""
    if value is None:
        return default
    if isinstance(value, float) and pd.isna(value):
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    return str(value)


def get_urgency_color_css(urgency: int) -> str:
    if urgency >= 8:
        return "#ff3232"
    elif urgency >= 5:
        return "#ffc832"
    else:
        return "#32ff96"


def get_arc_width(urgency: int) -> int:
    for urgency_range, width in URGENCY_WIDTH_MAP.items():
        if urgency in urgency_range:
            return width
    return 3


def urgency_bar(urgency: int) -> str:
    filled = "█" * urgency
    empty  = "░" * (10 - urgency)
    color  = get_urgency_color_css(urgency)
    return f'<span style="color:{color};font-size:0.65rem;">{filled}{empty}</span>'


def format_event_card(event: dict) -> str:
    etype          = safe_str(event.get("event_type"), "Unknown").lower()
    src_country    = safe_str(event.get("source_country"), "Unknown")
    src_city       = safe_str(event.get("source_city"))
    tgt_country    = safe_str(event.get("target_country"), "Unknown")
    tgt_city       = safe_str(event.get("target_city"))
    inserted_at    = safe_str(event.get("inserted_at"), "—")
    summary        = safe_str(event.get("summary"), "No summary available.")

    src = f"{src_city or src_country}, {src_country}"
    tgt = f"{tgt_city or tgt_country}, {tgt_country}"

    # Safely slice the timestamp
    ts = inserted_at[:16].replace("T", " ") if len(inserted_at) >= 16 else inserted_at

    try:
        urg = int(event.get("urgency", 1))
    except (TypeError, ValueError):
        urg = 1
    urg = max(1, min(10, urg))

    color = get_urgency_color_css(urg)
    badge = (
        f'<span class="urgency-badge" '
        f'style="background:{color}22;color:{color};border:1px solid {color};">U-{urg}</span>'
    )

    event_type_display = safe_str(event.get("event_type"), "Unknown").upper()

    return f"""
    <div class="event-card {etype}">
        {badge}
        <strong style="color:#c8e8ff;">{event_type_display}</strong>
        <span style="color:#4a7a9a;"> · {ts}</span><br>
        <span style="color:#6ab4e8;">▶</span> {src}
        <span style="color:#4a7a9a;"> → </span>
        <span style="color:#ff9060;">{tgt}</span><br>
        <span style="color:#8ab4c8;">{summary}</span><br>
        {urgency_bar(urg)}
    </div>
    """


def format_timestamp_short(timestamp: Optional[str]) -> str:
    """Extract HH:MM from an ISO-ish timestamp string. Returns '—' on failure."""
    if not isinstance(timestamp, str):
        return "—"
    if len(timestamp) >= 16:
        # Expected format: "YYYY-MM-DD HH:MM:SS" → take chars 11-16
        return timestamp[11:16]
    return timestamp if timestamp else "—"


# ─── Data loading ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=REFRESH_INTERVAL_SECONDS, show_spinner=False)
def load_events() -> pd.DataFrame:
    """Load events from SQLite into a DataFrame. Cached with TTL for auto-refresh."""
    events = fetch_latest_events(limit=MAX_EVENTS_DISPLAY)
    if not events:
        return pd.DataFrame()
    df = pd.DataFrame(events)

    # Fill any NaN text columns with empty strings so downstream code never sees NaN
    text_cols = [
        "source_country", "source_city", "target_country", "target_city",
        "event_type", "summary", "inserted_at", "raw_headline", "source_url",
    ]
    for col in text_cols:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    # Ensure numeric columns are valid
    for col in ["source_lat", "source_lon", "target_lat", "target_lon"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    if "urgency" in df.columns:
        df["urgency"] = pd.to_numeric(df["urgency"], errors="coerce").fillna(1).astype(int)

    return df


@st.cache_data(ttl=REFRESH_INTERVAL_SECONDS, show_spinner=False)
def load_stats() -> dict:
    """Return dashboard statistics. Never returns None values."""
    last_mod = get_db_last_modified()
    return {
        "total": fetch_event_count() or 0,
        "last_update": last_mod if isinstance(last_mod, str) and last_mod else "—",
    }


def prepare_arc_data(df: pd.DataFrame, selected_types: list) -> pd.DataFrame:
    """Build the arc layer dataframe with color and width columns."""
    filtered = df[df["event_type"].isin(selected_types)].copy()

    if filtered.empty:
        return filtered

    # Assign RGBA color arrays
    filtered["color"] = filtered["event_type"].map(
        lambda t: EVENT_COLORS.get(t, EVENT_COLORS["Unknown"])
    )

    # Assign arc width from urgency
    filtered["arc_width"] = filtered["urgency"].map(get_arc_width)

    # Rename for pydeck expected field names
    filtered = filtered.rename(columns={
        "source_lat": "src_lat",
        "source_lon": "src_lon",
        "target_lat": "tgt_lat",
        "target_lon": "tgt_lon",
    })

    return filtered


def prepare_scatter_data(df: pd.DataFrame, selected_types: list) -> pd.DataFrame:
    """Build the scatter layer dataframe for target pulse rings."""
    filtered = df[df["event_type"].isin(selected_types)].copy()

    if filtered.empty:
        return filtered

    filtered["color"] = filtered["event_type"].map(
        lambda t: EVENT_COLORS.get(t, EVENT_COLORS["Unknown"])
    )
    # Pulse radius: scale by urgency (in meters)
    filtered["radius"] = filtered["urgency"].map(lambda u: int(u) * 80_000)
    return filtered


# ─── Pydeck map builder ───────────────────────────────────────────────────────

def build_pydeck_chart(arc_df: pd.DataFrame, scatter_df: pd.DataFrame) -> pdk.Deck:
    """Construct the full pydeck Deck with all layers."""

    layers = []

    if not arc_df.empty:
        arc_layer = pdk.Layer(
            "ArcLayer",
            data=arc_df,
            get_source_position=["src_lon", "src_lat"],
            get_target_position=["tgt_lon", "tgt_lat"],
            get_source_color="color",
            get_target_color="color",
            get_width="arc_width",
            auto_highlight=True,
            pickable=True,
            great_circle=True,
        )
        layers.append(arc_layer)

        # Source origin dot (smaller)
        scatter_source = pdk.Layer(
            "ScatterplotLayer",
            data=arc_df,
            get_position=["src_lon", "src_lat"],
            get_fill_color="color",
            get_radius=80_000,
            radius_scale=1,
            pickable=True,
        )
        layers.append(scatter_source)

    if not scatter_df.empty:
        # Outer glow ring on target
        scatter_outer = pdk.Layer(
            "ScatterplotLayer",
            data=scatter_df,
            get_position=["target_lon", "target_lat"],
            get_fill_color=[[c[0], c[1], c[2], 30] for c in scatter_df["color"].tolist()],
            get_radius="radius",
            radius_scale=1,
            pickable=False,
            stroked=True,
            get_line_color="color",
            line_width_min_pixels=1,
        )
        layers.append(scatter_outer)

        # Inner solid target dot
        scatter_inner = pdk.Layer(
            "ScatterplotLayer",
            data=scatter_df,
            get_position=["target_lon", "target_lat"],
            get_fill_color="color",
            get_radius=150_000,
            radius_scale=1,
            pickable=True,
        )
        layers.append(scatter_inner)

    view_state = pdk.ViewState(
        latitude=MAP_INITIAL_VIEW["latitude"],
        longitude=MAP_INITIAL_VIEW["longitude"],
        zoom=MAP_INITIAL_VIEW["zoom"],
        pitch=MAP_INITIAL_VIEW["pitch"],
        bearing=MAP_INITIAL_VIEW["bearing"],
    )

    tooltip = {
        "html": """
        <div style="background:#050a12cc;border:1px solid #0a4080;padding:10px;
                    border-radius:6px;font-family:Courier New;font-size:12px;
                    color:#c8d8e8;max-width:300px;">
            <b style="color:#00e5ff;">{event_type}</b>
            <span style="color:#ff6040;"> ● URGENCY {urgency}/10</span><br>
            <hr style="border-color:#0a3060;margin:6px 0;">
            <b>SOURCE:</b> {source_city}, {source_country}<br>
            <b>TARGET:</b> {target_city}, {target_country}<br>
            <hr style="border-color:#0a3060;margin:6px 0;">
            <span style="color:#8ab4c8;">{summary}</span>
        </div>
        """,
        "style": {"background": "transparent", "border": "none"},
    }

    return pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        tooltip=tooltip,
    )


# ─── Sidebar ──────────────────────────────────────────────────────────────────

def render_sidebar(df: pd.DataFrame, stats: dict):
    """Render sidebar controls and event feed. Returns (selected_types, urgency_min)."""
    with st.sidebar:
        # Header
        st.markdown(
            """
            <div style="text-align:center;padding:10px 0 5px 0;">
                <span style="font-size:1.8rem;">⚡</span><br>
                <span style="color:#00e5ff;font-size:1.1rem;letter-spacing:0.15em;font-weight:bold;">
                    GEOALERT
                </span><br>
                <span style="color:#3a6a8a;font-size:0.65rem;letter-spacing:0.2em;">
                    WAR ROOM · INTELLIGENCE FEED
                </span>
            </div>
            <hr>
            """,
            unsafe_allow_html=True,
        )

        # Stats row — safely handle missing/None timestamps
        col1, col2 = st.columns(2)
        with col1:
            st.metric("TOTAL EVENTS", stats.get("total", 0))
        with col2:
            last_update_str = stats.get("last_update", "—")
            display_last = format_timestamp_short(last_update_str)
            st.metric("LAST UPDATE", display_last)

        st.markdown("<hr>", unsafe_allow_html=True)

        # Filters
        st.markdown(
            '<p style="color:#4a9eda;font-size:0.7rem;letter-spacing:0.15em;">⚙ FILTERS</p>',
            unsafe_allow_html=True,
        )

        all_types = list(EVENT_COLORS.keys())
        available_types = (
            sorted(df["event_type"].unique().tolist()) if not df.empty else all_types
        )

        selected_types = st.multiselect(
            "Event Types",
            options=all_types,
            default=available_types,
            label_visibility="collapsed",
        )

        urgency_min = st.slider(
            "Minimum Urgency",
            min_value=1,
            max_value=10,
            value=1,
            help="Filter events below this urgency threshold",
        )

        st.markdown("<hr>", unsafe_allow_html=True)

        # Color legend
        st.markdown(
            '<p style="color:#4a9eda;font-size:0.7rem;letter-spacing:0.15em;">◉ LEGEND</p>',
            unsafe_allow_html=True,
        )
        for etype, color in EVENT_COLORS.items():
            if etype == "Unknown":
                continue
            hex_color = "#{:02x}{:02x}{:02x}".format(*color[:3])
            st.markdown(
                f'<div style="margin:2px 0;">'
                f'<span style="color:{hex_color};font-size:1rem;">━</span> '
                f'<span style="font-size:0.75rem;color:#8ab4c8;">{etype}</span>'
                f"</div>",
                unsafe_allow_html=True,
            )

        st.markdown("<hr>", unsafe_allow_html=True)

        # Live event feed
        st.markdown(
            '<p style="color:#4a9eda;font-size:0.7rem;letter-spacing:0.15em;">📡 LIVE FEED</p>',
            unsafe_allow_html=True,
        )

        if df.empty:
            st.markdown(
                '<p style="color:#3a6a8a;font-size:0.75rem;">No events yet. '
                'Worker is initializing...</p>',
                unsafe_allow_html=True,
            )
        else:
            filtered_feed = df[
                (df["event_type"].isin(selected_types)) &
                (df["urgency"] >= urgency_min)
            ].head(20)

            for _, event in filtered_feed.iterrows():
                st.markdown(format_event_card(event.to_dict()), unsafe_allow_html=True)

    return selected_types, urgency_min


# ─── Main layout ──────────────────────────────────────────────────────────────

def render_main_panel(df: pd.DataFrame, selected_types: list, urgency_min: int):
    """Render the top metrics row and the pydeck map."""

    # ── Header bar
    col_title, col_time = st.columns([3, 1])
    with col_title:
        st.markdown(
            """
            <h1>⚡ GEOPOLITICAL INTELLIGENCE DASHBOARD</h1>
            <p style="color:#3a6a8a;font-size:0.7rem;margin-top:-10px;letter-spacing:0.1em;">
                REAL-TIME · AI-PROCESSED · LOCAL INFERENCE
            </p>
            """,
            unsafe_allow_html=True,
        )
    with col_time:
        st.markdown(
            f'<div class="status-bar" style="padding-top:20px;">'
            f'🟢 LIVE · {datetime.utcnow().strftime("%Y-%m-%d %H:%M")} UTC<br>'
            f'Auto-refresh: {REFRESH_INTERVAL_SECONDS}s'
            f"</div>",
            unsafe_allow_html=True,
        )

    # ── Empty state
    if df.empty:
        st.info(
            "⏳ No events loaded yet. Ensure `worker.py` is running and has processed at least one article.",
            icon="🛰️",
        )
        return

    # ── Filter dataframe
    filtered_df = df[
        (df["event_type"].isin(selected_types)) &
        (df["urgency"] >= urgency_min)
    ].copy()

    # ── Metrics row
    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        st.metric("ACTIVE ALERTS", len(filtered_df))
    with m2:
        high_tension = len(filtered_df[filtered_df["urgency"] >= 8])
        st.metric("HIGH TENSION (≥8)", high_tension)
    with m3:
        military = len(filtered_df[filtered_df["event_type"] == "Military"])
        st.metric("MILITARY", military)
    with m4:
        cyber = len(filtered_df[filtered_df["event_type"] == "Cyber"])
        st.metric("CYBER OPS", cyber)
    with m5:
        avg_urgency = filtered_df["urgency"].mean() if not filtered_df.empty else 0.0
        st.metric("AVG URGENCY", f"{avg_urgency:.1f}")

    # ── Map
    if not filtered_df.empty:
        arc_df     = prepare_arc_data(filtered_df, selected_types)
        scatter_df = prepare_scatter_data(filtered_df, selected_types)
        deck       = build_pydeck_chart(arc_df, scatter_df)

        st.pydeck_chart(deck, use_container_width=True)
    else:
        st.warning("No events match the current filter selection.")

    # ── Data table (collapsible)
    with st.expander("📋 RAW INTELLIGENCE TABLE", expanded=False):
        display_cols = [
            "inserted_at", "event_type", "urgency",
            "source_country", "source_city",
            "target_country", "target_city",
            "summary",
        ]
        # Only include columns that actually exist
        display_cols = [c for c in display_cols if c in filtered_df.columns]
        table_df = filtered_df[display_cols].rename(columns={
            "inserted_at":    "TIMESTAMP",
            "event_type":     "TYPE",
            "urgency":        "URG",
            "source_country": "SRC COUNTRY",
            "source_city":    "SRC CITY",
            "target_country": "TGT COUNTRY",
            "target_city":    "TGT CITY",
            "summary":        "AI SUMMARY",
        })
        st.dataframe(
            table_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "URG": st.column_config.ProgressColumn(
                    "URG",
                    min_value=0,
                    max_value=10,
                    format="%d",
                    width="small",
                ),
            },
        )


# ─── Auto-refresh mechanism ───────────────────────────────────────────────────

def check_and_trigger_refresh():
    """
    Uses st.session_state to track last known DB state.
    Triggers a rerun when new data arrives or TTL expires.
    """
    if "last_db_state" not in st.session_state:
        st.session_state.last_db_state = None
    if "last_refresh_time" not in st.session_state:
        st.session_state.last_refresh_time = time.time()

    current_db_state = get_db_last_modified()
    time_since_refresh = time.time() - st.session_state.last_refresh_time

    should_refresh = (
        current_db_state != st.session_state.last_db_state or
        time_since_refresh >= REFRESH_INTERVAL_SECONDS
    )

    if should_refresh:
        st.session_state.last_db_state = current_db_state
        st.session_state.last_refresh_time = time.time()
        # Clear the data cache to force reload
        load_events.clear()
        load_stats.clear()


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    init_db()    # ensure DB exists (no-op if already initialized)
    check_and_trigger_refresh()

    df    = load_events()
    stats = load_stats()

    selected_types, urgency_min = render_sidebar(df, stats)
    render_main_panel(df, selected_types, urgency_min)

    # Schedule next check via browser meta-refresh
    st.markdown(
        f'<meta http-equiv="refresh" content="{REFRESH_INTERVAL_SECONDS}">',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()