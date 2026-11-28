"""Kapanmış dakikanın gözlem satırı. Skor veya emir üretmez.

Her satır o dakikada görülen spot ve perp akışı, borsa kapanış medyanı,
Coinbase ve Hyperliquid USD fiyatı ve kaynak sayısıdır.
"""
import logging
import statistics
from datetime import datetime, timedelta, timezone

from . import config, db

log = logging.getLogger("observe")
UTC = timezone.utc


def fold(rows):
    """trades_1m satırlarından tek dakikalık gözlem. Satır yoksa None."""
    if not rows:
        return None
    spot = [r for r in rows if r["market"] == "spot"]
    perp = [r for r in rows if r["market"] == "perp"]
    closes = sorted(r["close"] for r in rows if r["close"] is not None)
    cb = next((r["close"] for r in rows if r["venue"] == "coinbase" and r["market"] == "spot" and r["close"]), None)
    hl = next((r["close"] for r in rows if r["venue"] == "hyperliquid" and r["market"] == "perp" and r["close"]), None)
    basis = (hl / cb - 1) * 1e4 if cb and hl else None
    return {
        "spot_buy_usd": sum(r["buy_usd"] or 0 for r in spot),
        "spot_sell_usd": sum(r["sell_usd"] or 0 for r in spot),
        "perp_buy_usd": sum(r["buy_usd"] or 0 for r in perp),
        "perp_sell_usd": sum(r["sell_usd"] or 0 for r in perp),
        "venue_median": statistics.median(closes) if closes else None,
        "coinbase_close": cb,
        "hyperliquid_close": hl,
        "usd_basis_bps": basis,
        "sources": len(rows),
    }


def windows(rows, now):
    """obs_1m satırlarını 15 dk, 1 saat ve 4 saat toplamına çevirir."""
    if not rows:
        return {"status": "unavailable", "windows": {}}
    out = {}
    for name, seconds in (("15m", 900), ("1h", 3600), ("4h", 14400)):
        start = now - timedelta(seconds=seconds)
        picked = [r for r in rows if r["ts"] > start]
        if not picked:
            out[name] = None
            continue
        spot_buy = sum(r["spot_buy_usd"] or 0 for r in picked)
        spot_sell = sum(r["spot_sell_usd"] or 0 for r in picked)
        perp_buy = sum(r["perp_buy_usd"] or 0 for r in picked)
        perp_sell = sum(r["perp_sell_usd"] or 0 for r in picked)
        bases = [r["usd_basis_bps"] for r in picked if r["usd_basis_bps"] is not None]
        out[name] = {
            "minutes": len(picked),
            "spot_delta_usd": spot_buy - spot_sell,
            "perp_delta_usd": perp_buy - perp_sell,
            "same_direction": (spot_buy - spot_sell) * (perp_buy - perp_sell) > 0,
            "usd_basis_bps": bases[-1] if bases else None,
            "sources_min": min(r["sources"] for r in picked),
        }
    return {"status": "available", "windows": out, "method": "Closed one-minute venue rows. USD basis is Hyperliquid perp versus Coinbase spot."}


async def _write_minute(conn, coin, ts):
    rows = await conn.fetch(
        "SELECT venue, market, buy_usd, sell_usd, close FROM trades_1m WHERE coin=$1 AND ts=$2",
        coin, ts)
    folded = fold(rows)
    if not folded:
        return
    await conn.execute(
        """INSERT INTO obs_1m (ts, coin, spot_buy_usd, spot_sell_usd, perp_buy_usd, perp_sell_usd,
               venue_median, coinbase_close, hyperliquid_close, usd_basis_bps, sources)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
           ON CONFLICT (coin, ts) DO UPDATE SET
             spot_buy_usd=EXCLUDED.spot_buy_usd, spot_sell_usd=EXCLUDED.spot_sell_usd,
             perp_buy_usd=EXCLUDED.perp_buy_usd, perp_sell_usd=EXCLUDED.perp_sell_usd,
             venue_median=EXCLUDED.venue_median, coinbase_close=EXCLUDED.coinbase_close,
             hyperliquid_close=EXCLUDED.hyperliquid_close, usd_basis_bps=EXCLUDED.usd_basis_bps,
             sources=EXCLUDED.sources""",
        ts, coin, folded["spot_buy_usd"], folded["spot_sell_usd"], folded["perp_buy_usd"], folded["perp_sell_usd"],
        folded["venue_median"], folded["coinbase_close"], folded["hyperliquid_close"], folded["usd_basis_bps"], folded["sources"])


async def write_recent(now=None):
    now = datetime.now(UTC) if now is None else now
    closed = now.replace(second=0, microsecond=0)
    minutes = [closed - timedelta(minutes=1), closed - timedelta(minutes=2)]
    async with db.pool.acquire() as conn:
        for coin in config.COINS:
            for ts in minutes:
                await _write_minute(conn, coin, ts)


async def load(coin, now=None):
    now = datetime.now(UTC) if now is None else now
    async with db.pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT ts, spot_buy_usd, spot_sell_usd, perp_buy_usd, perp_sell_usd,
                      venue_median, coinbase_close, hyperliquid_close, usd_basis_bps, sources
               FROM obs_1m WHERE coin=$1 AND ts > now() - interval '4 hours' ORDER BY ts""", coin)
    return windows(rows, now)


async def run():
    import asyncio
    import time
    while True:
        now = time.time()
        delay = (int(now) // 60 + 1) * 60 + 5 - now
        await asyncio.sleep(max(1, delay))
        try:
            await write_recent()
        except Exception:
            log.exception("dakikalık gözlem yazılamadı")
