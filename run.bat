@echo off
setlocal
call .venv\Scripts\activate.bat

if not exist .env (
  copy .env.example .env >nul
)

REM Streamlit LAN
set STREAMLIT_SERVER_ADDRESS=0.0.0.0
set STREAMLIT_SERVER_PORT=8501
set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

streamlit run app.py

endlocal
