"""Bellekteki canlı durum: dakikalık işlem kovaları, son fiyatlar, akış sağlığı, alarm kuyruğu."""
import time
from collections import defaultdict, deque

# (coin, venue, market, minute_ts) -> [buy_usd, sell_usd, n, open, high, low, close]
buckets: dict = {}
last_price: dict = {}            # (venue, market, coin) -> (price, ts)
stream_health: dict = {}         # stream name -> {"last_msg": ts, "connected": bool, "reconnects": int, "msgs": int}
alerts: deque = deque(maxlen=500)  # alarm olayları (telegram döngüsü tüketir)
recent_liq: deque = deque(maxlen=5000)  # (ts, coin, usd) kaskad tespiti için

def touch(stream: str):
    h = stream_health.setdefault(stream, {"last_msg": 0, "connected": False, "reconnects": 0, "msgs": 0})
    h["last_msg"] = time.time(); h["msgs"] += 1; h["connected"] = True

def add_trade(coin, venue, market, ts_ms, price, usd, is_buy):
    minute = int(ts_ms // 60000) * 60
    k = (coin, venue, market, minute)
    b = buckets.get(k)
    if b is None:
        b = buckets[k] = [0.0, 0.0, 0, price, price, price, price]
    if is_buy: b[0] += usd
    else: b[1] += usd
    b[2] += 1
    if price > b[4]: b[4] = price
    if price < b[5]: b[5] = price
    b[6] = price
    last_price[(venue, market, coin)] = (price, ts_ms / 1000)

def pop_closed_buckets(now=None):
    """Bitmiş dakikaları (şimdiki dakikadan eski) döndürür ve bellekten siler."""
    now = now or time.time()
    cur = int(now // 60) * 60
    out = []
    for k in [k for k in buckets if k[3] < cur]:
        out.append((k, buckets.pop(k)))
    return out
