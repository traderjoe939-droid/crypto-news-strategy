# Crypto News Strategy Worker

Web Service start command:
uvicorn app:app --host 0.0.0.0 --port $PORT

Background Worker start command:
python app.py

Environment variables:
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
NEWS_MIN_SCORE=70
RISK_PER_TRADE=400
CONTRACT_SIZE_BTC=1
POLL_SECONDS=30
SEND_WAITS=false
