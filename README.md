# Geopolitical War Room Dashboard

A free, open-source, local-first geopolitical intelligence dashboard built with Python, Streamlit, Pydeck, SQLite, RSS feeds, and Ollama.

The dashboard scrapes international news RSS feeds, uses a local Ollama model to extract structured geopolitical events, stores them in SQLite, and visualizes source-to-target relationships on a dark tactical map.

## Features

- Real-time RSS news ingestion
- Local LLM processing with Ollama
- SQLite event storage
- Streamlit dashboard
- Dark tactical Pydeck map
- Neon arcs between countries
- Event urgency and type classification
- One-command launcher

## Tech Stack

- Python
- Streamlit
- Pydeck
- SQLite
- Ollama
- Feedparser
- Geopy
- Pandas

## Requirements

- Python 3.10+
- Ollama installed locally

Install Ollama:

https://ollama.com/download

Pull the model:

```bash
ollama pull mistral
