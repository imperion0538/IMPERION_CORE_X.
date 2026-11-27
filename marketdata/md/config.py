"""Ortam değişkenlerinden ayarlar ve borsa sembol eşlemeleri."""
import os

def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)

COINS = [c.strip().upper() for c in os.environ.get("COINS", "BTC,ETH,SOL").split(",") if c.strip()]
DATABASE_URL = os.environ.get("DATABASE_URL", "")
API_KEY = os.environ.get("API_KEY", "")
# 1 ise /api/* okuma uçları anahtarsız açılır (IP başına dakikada 60 istek). Veriler yalnız herkese açık piyasa verisidir.
PUBLIC_READ = os.environ.get("PUBLIC_READ", "0") == "1"
PORT = int(os.environ.get("PORT", "8080"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_IDS = {s.strip() for s in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if s.strip()}
# Balina eşiği: BTC için tam değer, diğer coinler için WHALE_ALT_FACTOR ile çarpılır.
WHALE_MIN_USD = _f("WHALE_MIN_USD", 250_000)
WHALE_ALT_FACTOR = _f("WHALE_ALT_FACTOR", 0.4)
ALERT_LIQ_USD = _f("ALERT_LIQ_USD", 1_000_000)
ALERT_CASCADE_USD = _f("ALERT_CASCADE_USD", 5_000_000)
ALERT_WHALE_USD = _f("ALERT_WHALE_USD", 5_000_000)
ALERTS_ENABLED = os.environ.get("ALERTS_ENABLED", "1") == "1"
REPORT_HOURS_UTC = [int(h) for h in os.environ.get("REPORT_HOURS_UTC", "").split(",") if h.strip().isdigit()]

# OKX kontrat büyüklükleri (coin cinsinden)
OKX_CTVAL = {"BTC": 0.01, "ETH": 0.1, "SOL": 1.0}  # açılışta OKX'ten güncellenir (ws.load_okx_ctval)
STARTED_AT = __import__("time").time()
ALTS = [a.strip().upper() for a in os.environ.get(
    "ALTS", "ETH,SOL,BNB,XRP,DOGE,ADA,AVAX,LINK,DOT,TON,SUI,APT,NEAR,ARB,OP,INJ,LTC,BCH,TRX,PEPE").split(",") if a.strip()]

def whale_threshold(coin: str) -> float:
    return WHALE_MIN_USD if coin == "BTC" else WHALE_MIN_USD * WHALE_ALT_FACTOR

def alert_scale(coin: str) -> float:
    return 1.0 if coin == "BTC" else 0.5

OPTIONS_DVOL_CHANGE = _f("OPTIONS_DVOL_CHANGE", 3)
OPTIONS_MAX_PAIN_CHANGE_PCT = _f("OPTIONS_MAX_PAIN_CHANGE_PCT", 2)
OPTIONS_GEX_FLIP_MIN_USD = _f("OPTIONS_GEX_FLIP_MIN_USD", 1_000_000)
RAW_RETENTION_HOURS = max(.25, min(48, _f("RAW_RETENTION_HOURS", 1)))
RAW_JOURNAL_TARGET_MIB = max(8, min(8192, _f("RAW_JOURNAL_TARGET_MIB", 24)))

# Production Telegram defaults to original full-resolution cards only.
TELEGRAM_CARD_ONLY = os.environ.get("TELEGRAM_CARD_ONLY", "1") == "1"
