import os
import time
import html
import requests
import xml.etree.ElementTree as ET
from fastapi import FastAPI, Query

app = FastAPI(title="Crypto News Strategy V2")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
NEWS_MIN_SCORE = int(os.getenv("NEWS_MIN_SCORE", "70"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "400"))
CONTRACT_SIZE_BTC = float(os.getenv("CONTRACT_SIZE_BTC", "1"))

COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/{product_id}/candles"

NEWS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cryptoslate.com/feed/",
]

NEWS_CATEGORIES = [
    {
        "name": "ETF / Institutional",
        "sentiment": "BULLISH",
        "weight": 95,
        "terms": ["etf approval", "spot bitcoin etf", "blackrock", "fidelity", "institutional adoption", "etf inflow", "buys bitcoin"]
    },
    {
        "name": "Major exploit / hack",
        "sentiment": "BEARISH",
        "weight": 90,
        "terms": ["hack", "exploit", "breach", "stolen", "bridge attack", "wallet drained"]
    },
    {
        "name": "Regulatory crackdown",
        "sentiment": "BEARISH",
        "weight": 85,
        "terms": ["sec charges", "lawsuit", "ban", "regulatory crackdown", "criminal probe", "sanctions"]
    },
    {
        "name": "Exchange listing",
        "sentiment": "BULLISH",
        "weight": 70,
        "terms": ["listing", "listed on", "coinbase lists", "binance lists"]
    },
    {
        "name": "Macro bullish",
        "sentiment": "BULLISH",
        "weight": 75,
        "terms": ["rate cut", "dovish", "inflation cools", "fed pause"]
    },
    {
        "name": "Macro bearish",
        "sentiment": "BEARISH",
        "weight": 75,
        "terms": ["rate hike", "hawkish", "inflation rises", "fed tightening"]
    },
    {
        "name": "General bullish",
        "sentiment": "BULLISH",
        "weight": 55,
        "terms": ["surge", "rally", "record high", "all-time high", "accumulation", "adoption"]
    },
    {
        "name": "General bearish",
        "sentiment": "BEARISH",
        "weight": 55,
        "terms": ["selloff", "plunge", "liquidation", "collapse", "outflow", "delisting"]
    }
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
    text = f" {headline.lower()} "
    if " ethereum " in text or " ether " in text or " eth " in text:
        return "ETHUSDT"
    if " solana " in text or " sol " in text:
        return "SOLUSDT"
    return "BTCUSDT"


def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()
    return True


def score_news(headline: str) -> dict:
    text = headline.lower()
    matches = []

    for category in NEWS_CATEGORIES:
        hit_terms = [term for term in category["terms"] if term in text]
        if hit_terms:
            matches.append({
                "category": category["name"],
                "sentiment": category["sentiment"],
                "weight": category["weight"],
                "terms": hit_terms
            })

    if not matches:
        return {"headline": headline, "sentiment": "NEUTRAL", "score": 20, "category": "Unimportant", "matches": []}

    best = max(matches, key=lambda x: x["weight"])
    return {
        "headline": headline,
        "sentiment": best["sentiment"],
        "score": best["weight"],
        "category": best["category"],
        "matches": matches
    }


def fetch_rss_headlines(limit: int = 15) -> list[dict]:
    items, seen = [], set()

    for feed_url in NEWS_FEEDS:
        try:
            response = requests.get(feed_url, headers={"User-Agent": "crypto-news-strategy/2.0"}, timeout=15)
            response.raise_for_status()
            root = ET.fromstring(response.content)

            for item in root.findall(".//item"):
                title_el, link_el, pub_el = item.find("title"), item.find("link"), item.find("pubDate")
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
            items.append({"title": f"FEED_ERROR: {feed_url}", "link": "", "published": "", "source": str(e)})

    return items[:limit]


def get_klines(symbol: str, granularity: int = 900) -> list[dict]:
    product_id = normalize_symbol(symbol)
    url = COINBASE_CANDLES_URL.format(product_id=product_id)

    response = requests.get(url, params={"granularity": granularity}, headers={"User-Agent": "crypto-news-strategy/2.0"}, timeout=15)
    response.raise_for_status()

    raw = response.json()
    if not isinstance(raw, list) or len(raw) < 60:
        raise RuntimeError(f"Coinbase returned invalid candles for {product_id}: {raw}")

    raw = sorted(raw, key=lambda x: x[0])[-200:]
    return [{"open_time": int(i[0]), "low": float(i[1]), "high": float(i[2]), "open": float(i[3]), "close": float(i[4]), "volume": float(i[5])} for i in raw]


def sma(values: list[float], period: int) -> float:
    return sum(values[-period:]) / period


def find_recent_swing_low(candles: list[dict], lookback: int = 20) -> float:
    return min(c["low"] for c in candles[-lookback:])


def find_recent_swing_high(candles: list[dict], lookback: int = 20) -> float:
    return max(c["high"] for c in candles[-lookback:])


def analyze_market(candles: list[dict]) -> dict:
    closes = [c["close"] for c in candles]
    last = candles[-1]
    close = last["close"]

    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)

    lookback = candles[-21:-1]
    previous_high = max(c["high"] for c in lookback)
    previous_low = min(c["low"] for c in lookback)
    average_volume = sum(c["volume"] for c in lookback) / len(lookback)
    volume_ratio = last["volume"] / average_volume if average_volume else 0

    if close > sma20 > sma50:
        trend = "BULLISH"
    elif close < sma20 < sma50:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    bullish_breakout = close > previous_high and volume_ratio >= 1.1
    bearish_breakdown = close < previous_low and volume_ratio >= 1.1

    if bullish_breakout:
        structure = "BULLISH_BREAKOUT"
        bias = "BULLISH"
    elif bearish_breakdown:
        structure = "BEARISH_BREAKDOWN"
        bias = "BEARISH"
    elif trend == "BULLISH" and close > sma20:
        structure = "BULLISH_TREND_CONTINUATION"
        bias = "BULLISH"
    elif trend == "BEARISH" and close < sma20:
        structure = "BEARISH_TREND_CONTINUATION"
        bias = "BEARISH"
    else:
        structure = "RANGE_OR_NO_CONFIRMATION"
        bias = "NEUTRAL"

    return {
        "bias": bias,
        "trend": trend,
        "structure": structure,
        "close": close,
        "sma20": round(sma20, 2),
        "sma50": round(sma50, 2),
        "previous_high": previous_high,
        "previous_low": previous_low,
        "swing_low": find_recent_swing_low(candles, 20),
        "swing_high": find_recent_swing_high(candles, 20),
        "volume": round(last["volume"], 4),
        "average_volume": round(average_volume, 4),
        "volume_ratio": round(volume_ratio, 2)
    }


def calculate_trade_plan(action: str, market: dict) -> dict:
    entry = float(market["close"])

    if action == "BUY":
        stop_loss = float(market["swing_low"])
        risk_distance = entry - stop_loss
        tp1 = entry + risk_distance
        tp2 = entry + (risk_distance * 2)
    elif action == "SELL":
        stop_loss = float(market["swing_high"])
        risk_distance = stop_loss - entry
        tp1 = entry - risk_distance
        tp2 = entry - (risk_distance * 2)
    else:
        return {"entry": round(entry, 2), "stop_loss": None, "tp1": None, "tp2": None, "risk_usd": RISK_PER_TRADE, "lot_size": None}

    lot_size = None if risk_distance <= 0 else RISK_PER_TRADE / risk_distance / CONTRACT_SIZE_BTC

    return {
        "entry": round(entry, 2),
        "stop_loss": round(stop_loss, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "risk_distance": round(risk_distance, 2),
        "risk_usd": RISK_PER_TRADE,
        "lot_size": round(lot_size, 4) if lot_size else None,
        "risk_reward_tp1": "1:1",
        "risk_reward_tp2": "1:2"
    }


def evaluate_signal(symbol: str, headline: str) -> dict:
    news = score_news(headline)
    candles = get_klines(symbol)
    market = analyze_market(candles)

    action = "WAIT"
    confidence = 40
    reason = "No trade. News and chart do not align."

    if news["score"] >= NEWS_MIN_SCORE:
        confidence = 60
        reason = "News is important, but chart confirmation is incomplete."

        if news["sentiment"] == "BULLISH" and market["bias"] == "BULLISH":
            action = "BUY"
            confidence = 80 if market["structure"].endswith("CONTINUATION") else 88
            reason = f"Bullish {news['category']} news aligned with {market['structure']}."
        elif news["sentiment"] == "BEARISH" and market["bias"] == "BEARISH":
            action = "SELL"
            confidence = 80 if market["structure"].endswith("CONTINUATION") else 88
            reason = f"Bearish {news['category']} news aligned with {market['structure']}."

    trade = calculate_trade_plan(action, market)

    return {
        "symbol": symbol.upper(),
        "data_symbol": normalize_symbol(symbol),
        "action": action,
        "confidence": confidence,
        "reason": reason,
        "news": news,
        "market": market,
        "trade": trade,
        "timestamp": int(time.time())
    }


def format_signal(result: dict) -> str:
    trade = result["trade"]
    trade_block = ""

    if result["action"] in ["BUY", "SELL"]:
        trade_block = f"""
<b>Entry:</b> {trade['entry']}
<b>Stop Loss:</b> {trade['stop_loss']}
<b>TP1:</b> {trade['tp1']}
<b>TP2:</b> {trade['tp2']}

<b>Risk:</b> ${trade['risk_usd']}
<b>MT5 Lot Size:</b> {trade['lot_size']}
<b>RR:</b> TP1 {trade['risk_reward_tp1']} / TP2 {trade['risk_reward_tp2']}
"""

    return f"""🚨 <b>{result['action']} / {result['symbol']}</b>

<b>Confidence:</b> {result['confidence']}%
<b>Reason:</b> {result['reason']}
{trade_block}
<b>News:</b>
{html.escape(result['news']['headline'])}

<b>News category:</b> {result['news']['category']}
<b>News sentiment:</b> {result['news']['sentiment']}
<b>News score:</b> {result['news']['score']}/100

<b>Market bias:</b> {result['market']['bias']}
<b>Trend:</b> {result['market']['trend']}
<b>Structure:</b> {result['market']['structure']}
<b>Close:</b> {result['market']['close']}
<b>Volume ratio:</b> {result['market']['volume_ratio']}x
"""


@app.get("/")
def root():
    return {"status": "live", "service": "crypto-news-strategy-v2"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/test-telegram")
def test_telegram():
    return {"sent": send_telegram("🚨 TEST SIGNAL\nCrypto news strategy V2 is connected.")}


@app.get("/market-test")
def market_test(symbol: str = Query("BTCUSDT")):
    candles = get_klines(symbol)
    return {"symbol": symbol, "data_symbol": normalize_symbol(symbol), "candles": len(candles), "market": analyze_market(candles)}


@app.get("/news-test")
def news_test(limit: int = Query(10)):
    headlines = fetch_rss_headlines(limit=limit)
    scored = [{**item, "score": score_news(item["title"])} for item in headlines if not item["title"].startswith("FEED_ERROR")]
    return {"count": len(scored), "headlines": scored}


@app.get("/run-check")
def run_check(symbol: str = Query("BTCUSDT"), headline: str = Query("Bitcoin ETF approval sparks institutional adoption")):
    try:
        result = evaluate_signal(symbol=symbol, headline=headline)
        send_telegram(format_signal(result))
        return result
    except Exception as e:
        return {"status": "error", "error": str(e), "symbol": symbol, "headline": headline}


@app.get("/live-news-check")
def live_news_check(limit: int = Query(10), send_waits: bool = Query(False)):
    headlines = fetch_rss_headlines(limit=limit)
    results, sent_count = [], 0

    for item in headlines:
        title = item["title"]
        if title.startswith("FEED_ERROR"):
            results.append({"status": "feed_error", "item": item})
            continue

        symbol = infer_symbol_from_headline(title)

        try:
            result = evaluate_signal(symbol=symbol, headline=title)
            result["source_link"] = item.get("link", "")
            result["published"] = item.get("published", "")

            should_send = result["action"] in ["BUY", "SELL"] or (send_waits and result["news"]["score"] >= NEWS_MIN_SCORE)
            if should_send:
                send_telegram(format_signal(result))
                sent_count += 1

            results.append(result)
        except Exception as e:
            results.append({"status": "error", "headline": title, "error": str(e)})

    return {"checked": len(results), "sent": sent_count, "results": results}
