import os
import time
import requests
from fastapi import FastAPI, Query

app = FastAPI(title="Crypto News Strategy One-File MVP")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
NEWS_MIN_SCORE = int(os.getenv("NEWS_MIN_SCORE", "70"))

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

BULLISH_TERMS = [
    "approval", "approved", "etf", "inflow", "adoption", "institutional",
    "blackrock", "listing", "partnership", "rate cut", "bullish", "surge"
]

BEARISH_TERMS = [
    "hack", "exploit", "lawsuit", "ban", "outflow", "delisting",
    "crackdown", "collapse", "rate hike", "bearish", "sec charges"
]


def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()
    return True


def score_news(headline: str) -> dict:
    text = headline.lower()

    bullish_hits = [term for term in BULLISH_TERMS if term in text]
    bearish_hits = [term for term in BEARISH_TERMS if term in text]

    if len(bullish_hits) > len(bearish_hits):
        sentiment = "BULLISH"
        score = min(100, 40 + len(bullish_hits) * 15)
        hits = bullish_hits
    elif len(bearish_hits) > len(bullish_hits):
        sentiment = "BEARISH"
        score = min(100, 40 + len(bearish_hits) * 15)
        hits = bearish_hits
    else:
        sentiment = "NEUTRAL"
        score = 20
        hits = bullish_hits + bearish_hits

    return {
        "headline": headline,
        "sentiment": sentiment,
        "score": score,
        "matched_terms": hits
    }


def get_klines(symbol: str, interval: str = "15m", limit: int = 100) -> list[dict]:
    params = {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": limit
    }

    response = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
    response.raise_for_status()

    candles = []
    for item in response.json():
        candles.append({
            "open_time": item[0],
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
            "close_time": item[6]
        })

    return candles


def analyze_price_action(candles: list[dict]) -> dict:
    if len(candles) < 30:
        raise ValueError("Need at least 30 candles")

    last = candles[-1]
    lookback = candles[-21:-1]

    previous_high = max(c["high"] for c in lookback)
    previous_low = min(c["low"] for c in lookback)
    average_volume = sum(c["volume"] for c in lookback) / len(lookback)

    close = last["close"]
    volume = last["volume"]
    volume_ratio = volume / average_volume if average_volume else 0

    bullish_breakout = close > previous_high and volume_ratio >= 1.2
    bearish_breakdown = close < previous_low and volume_ratio >= 1.2

    if bullish_breakout:
        bias = "BULLISH"
    elif bearish_breakdown:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {
        "bias": bias,
        "close": close,
        "previous_high": previous_high,
        "previous_low": previous_low,
        "volume": volume,
        "average_volume": round(average_volume, 4),
        "volume_ratio": round(volume_ratio, 2)
    }


def evaluate_signal(symbol: str, headline: str) -> dict:
    news = score_news(headline)
    candles = get_klines(symbol)
    price = analyze_price_action(candles)

    action = "WAIT"
    confidence = 40
    reason = "No trade. News and price action are not aligned."

    if news["score"] >= NEWS_MIN_SCORE:
        confidence = 65
        reason = "News is important, but price action has not confirmed."

        if news["sentiment"] == "BULLISH" and price["bias"] == "BULLISH":
            action = "BUY"
            confidence = 85
            reason = "Bullish news confirmed by breakout and volume."

        elif news["sentiment"] == "BEARISH" and price["bias"] == "BEARISH":
            action = "SELL"
            confidence = 85
            reason = "Bearish news confirmed by breakdown and volume."

    return {
        "symbol": symbol.upper(),
        "action": action,
        "confidence": confidence,
        "reason": reason,
        "news": news,
        "price": price,
        "timestamp": int(time.time())
    }


def format_signal(result: dict) -> str:
    return f"""🚨 <b>{result['action']} / {result['symbol']}</b>

<b>Confidence:</b> {result['confidence']}%
<b>Reason:</b> {result['reason']}

<b>News:</b>
{result['news']['headline']}

<b>News sentiment:</b> {result['news']['sentiment']}
<b>News score:</b> {result['news']['score']}/100

<b>Price bias:</b> {result['price']['bias']}
<b>Close:</b> {result['price']['close']}
<b>Volume ratio:</b> {result['price']['volume_ratio']}x
"""


@app.get("/")
def root():
    return {"status": "live", "service": "crypto-news-strategy"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/test-telegram")
def test_telegram():
    message = "🚨 TEST SIGNAL\nCrypto news strategy is connected."
    sent = send_telegram(message)
    return {"sent": sent}


@app.get("/run-check")
def run_check(
    symbol: str = Query("BTCUSDT"),
    headline: str = Query("Bitcoin ETF approval sparks institutional adoption")
):
    result = evaluate_signal(symbol=symbol, headline=headline)
    message = format_signal(result)

    # MVP: send every result so we can verify Telegram + logic.
    send_telegram(message)

    return result
