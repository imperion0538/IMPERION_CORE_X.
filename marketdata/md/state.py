"""Süreç içindeki son işlem fiyatı, türev katmanı ve WebSocket bağlantı sağlığı."""
import time

last_price = {}
stream_health = {}
# (venue, coin) -> price / oi_usd / funding_8h and the epoch each field was observed.
live_deriv = {}


def touch(stream: str):
    h = stream_health.setdefault(stream, {"last_msg": 0, "connected": False, "reconnects": 0, "msgs": 0})
    h["last_msg"] = time.time(); h["msgs"] += 1; h["connected"] = True


def note_deriv(venue, coin, *, price=None, oi_usd=None, funding_8h=None, transport="websocket", extra=None, now=None):
    """Alan bazında yazar. Eksik alan, daha önce gelen değeri silmez."""
    now = time.time() if now is None else now
    row = live_deriv.setdefault((venue, coin), {})
    if price is not None:
        row["price"] = float(price)
        row["price_ts"] = now
        row["price_transport"] = transport
    if oi_usd is not None:
        row["oi_usd"] = float(oi_usd)
        row["oi_ts"] = now
        row["oi_transport"] = transport
    if funding_8h is not None:
        row["funding_8h"] = float(funding_8h)
        row["funding_ts"] = now
        row["funding_transport"] = transport
    if extra:
        row.setdefault("extra", {}).update(extra)
    return row
