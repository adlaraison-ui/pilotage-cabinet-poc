#!/usr/bin/env bash
set -e
source .venv/bin/activate

[ -f .env ] || cp .env.example .env

export STREAMLIT_SERVER_ADDRESS="0.0.0.0"
export STREAMLIT_SERVER_PORT="8501"
export STREAMLIT_BROWSER_GATHER_USAGE_STATS="false"

streamlit run app.py
