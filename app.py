import os
import time
import html
import requests
import xml.etree.ElementTree as ET
from fastapi import FastAPI, Query

app = FastAPI(title="Crypto News Strategy MVP")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
NEWS_MIN_SCORE = int(os.getenv("NEWS_MIN_SCORE", "70"))

COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/{product_id}/candles"

NEWS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cryptoslate.com/feed/",
]

BULLISH_TERMS = [
    "approval", "approved", "etf", "inflow", "adoption", "institutional",
    "blackrock", "listing", "partnership", "rate cut", "bullish", "surge",
    "record high", "all-time high", "accumulation", "buys bitcoin"
]

BEARISH_TERMS = [
    "hack", "exploit", "lawsuit", "ban", "outflow", "delisting",
    "crackdown", "collapse", "rate hike", "bearish", "sec charges",
    "liquidation", "selloff", "plunge", "fraud", "breach"
]


def normalize_symbol(symbol: str) -> str:
    s = symbol.upper().replace("-", "")
    if s in ["BTCUSDT", "BTCUSD"]:
        return "BTC-USD"
    if s in ["ETHUSDT", "ETHUSD"]:
        return "ETH-USD"
    if s in ["SOLUSDT", "SOLUSD"]:
        return "SOL-USD"
    if s.endswith("USDT"):
        return s.replace("USDT", "-USD")
    if s.endswith("USD"):
        return s.replace("USD", "-USD")
    return symbol.upper()


def infer_symbol_from_headline(headline: str) -> str:
    text = headline.lower()

    if "ethereum" in text or "ether" in text or " eth " in f" {text} ":
        return "ETHUSDT"
    if "solana" in text or " sol " in f" {text} ":
        return "SOLUSDT"
    return "BTCUSDT"


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


def fetch_rss_headlines(limit: int = 15) -> list[dict]:
    items = []
    seen = set()

    for feed_url in NEWS_FEEDS:
        try:
            response = requests.get(
                feed_url,
                headers={"User-Agent": "crypto-news-strategy/1.0"},
                timeout=15
            )
            response.raise_for_status()

            root = ET.fromstring(response.content)
            channel_items = root.findall(".//item")

            for item in channel_items:
                title_el = item.find("title")
                link_el = item.find("link")
                pub_el = item.find("pubDate")

                if title_el is None or not title_el.text:
                    continue

                title = html.unescape(title_el.text.strip())
                if title.lower() in seen:
                    continue

                seen.add(title.lower())

                items.append({
                    "title": title,
                    "link": link_el.text.strip() if link_el is not None and link_el.text else "",
                    "published": pub_el.text.strip() if pub_el is not None and pub_el.text else "",
                    "source": feed_url
                })

                if len(items) >= limit:
                    return items

        except Exception as e:
            items.append({
                "title": f"FEED_ERROR: {feed_url}",
                "link": "",
                "published": "",
                "source": str(e)
            })

    return items[:limit]


def get_klines(symbol: str, granularity: int = 900) -> list[dict]:
    product_id = normalize_symbol(symbol)
    url = COINBASE_CANDLES_URL.format(product_id=product_id)

    response = requests.get(
        url,
        params={"granularity": granularity},
        headers={"User-Agent": "crypto-news-strategy/1.0"},
        timeout=15
    )
    response.raise_for_status()

    raw = response.json()
    if not isinstance(raw, list) or len(raw) < 30:
        raise RuntimeError(f"Coinbase returned invalid candles for {product_id}: {raw}")

    raw = sorted(raw, key=lambda x: x[0])[-100:]

    candles = []
    for item in raw:
        candles.append({
            "open_time": int(item[0]),
            "low": float(item[1]),
            "high": float(item[2]),
            "open": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5])
        })

    return candles


def analyze_price_action(candles: list[dict]) -> dict:
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
        "volume": round(volume, 4),
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
        "data_symbol": normalize_symbol(symbol),
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
{html.escape(result['news']['headline'])}

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


@app.get("/market-test")
def market_test(symbol: str = Query("BTCUSDT")):
    candles = get_klines(symbol)
    return {
        "symbol": symbol,
        "data_symbol": normalize_symbol(symbol),
        "candles": len(candles),
        "last_close": candles[-1]["close"]
    }


@app.get("/news-test")
def news_test(limit: int = Query(10)):
    headlines = fetch_rss_headlines(limit=limit)
    return {
        "count": len(headlines),
        "headlines": headlines
    }


@app.get("/run-check")
def run_check(
    symbol: str = Query("BTCUSDT"),
    headline: str = Query("Bitcoin ETF approval sparks institutional adoption")
):
    try:
        result = evaluate_signal(symbol=symbol, headline=headline)
        message = format_signal(result)
        send_telegram(message)
        return result
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "symbol": symbol,
            "headline": headline
        }


@app.get("/live-news-check")
def live_news_check(limit: int = Query(10), send_waits: bool = Query(False)):
    headlines = fetch_rss_headlines(limit=limit)
    results = []
    sent_count = 0

    for item in headlines:
        title = item["title"]

        if title.startswith("FEED_ERROR"):
            results.append({
                "status": "feed_error",
                "item": item
            })
            continue

        symbol = infer_symbol_from_headline(title)

        try:
            result = evaluate_signal(symbol=symbol, headline=title)
            result["source_link"] = item.get("link", "")
            result["published"] = item.get("published", "")

            should_send = result["action"] in ["BUY", "SELL"] or (
                send_waits and result["news"]["score"] >= NEWS_MIN_SCORE
            )

            if should_send:
                send_telegram(format_signal(result))
                sent_count += 1

            results.append(result)

        except Exception as e:
            results.append({
                "status": "error",
                "headline": title,
                "error": str(e)
            })

    return {
        "checked": len(results),
        "sent": sent_count,
        "results": results
    }
