# Crypto News Strategy One-File MVP

## Files
- `app.py`
- `requirements.txt`

## Render settings

Build Command:
```bash
pip install -r requirements.txt
```

Start Command:
```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

## Render Environment Variables

```text
TELEGRAM_BOT_TOKEN=your_new_token
TELEGRAM_CHAT_ID=-5306251294
NEWS_MIN_SCORE=70
```

## Test URLs

```text
/
 /health
/test-telegram
/run-check
/run-check?symbol=BTCUSDT&headline=Bitcoin ETF approval sparks institutional adoption
```
