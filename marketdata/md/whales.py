"""Büyük işlemleri, veri kapsamını ve aynı borsadaki fiyat tepkisini ölçer."""
from datetime import datetime, timedelta, timezone

from . import config, db

WINDOWS = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}
HORIZONS = (1, 5, 15)


async def coverage(conn, coin, start, end):
    rows = await conn.fetch("""
        SELECT venue, market, count(*) AS observed_minutes, max(ts) AS last_minute
        FROM trades_1m WHERE coin=$1 AND ts >= $2 AND ts < $3
        GROUP BY venue, market ORDER BY market, venue
    """, coin, start, end)
    return {
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "expected_minutes": int((end - start).total_seconds() // 60),
        "sources": [dict(row, last_minute=row["last_minute"].isoformat()) for row in rows],
    }


async def report(coin, window, min_price=None, max_price=None, limit=20):
    now = datetime.now(timezone.utc)
    end = now.replace(second=0, microsecond=0)
    start = end - timedelta(minutes=WINDOWS[window])
    async with db.pool.acquire() as conn:
        observed = await coverage(conn, coin, start, end)
        groups = await conn.fetch("""
            SELECT venue, market,
                coalesce(sum(usd) FILTER (WHERE side='buy'), 0) AS buy_usd,
                coalesce(sum(usd) FILTER (WHERE side='sell'), 0) AS sell_usd,
                count(*) FILTER (WHERE side='buy') AS buy_count,
                count(*) FILTER (WHERE side='sell') AS sell_count
            FROM whale_trades
            WHERE coin=$1 AND ts >= $2 AND ts <= $3
                AND ($4::float8 IS NULL OR price >= $4)
                AND ($5::float8 IS NULL OR price <= $5)
            GROUP BY venue, market ORDER BY market, venue
        """, coin, start, now, min_price, max_price)
        events = await conn.fetch("""
            WITH recent AS (
                SELECT id, ts, venue, market, side, price, usd
                FROM whale_trades WHERE coin=$1 AND ts >= $2 AND ts <= $3
                    AND ($4::float8 IS NULL OR price >= $4)
                    AND ($5::float8 IS NULL OR price <= $5)
                ORDER BY ts DESC, id DESC LIMIT $6
            )
            SELECT r.*, h.minutes, b.close AS reaction_close
            FROM recent r CROSS JOIN unnest($7::int[]) AS h(minutes)
            LEFT JOIN trades_1m b ON b.coin=$1 AND b.venue=r.venue AND b.market=r.market
                AND b.ts=date_trunc('minute', r.ts, 'UTC') + h.minutes * interval '1 minute'
                AND b.ts < $8
            ORDER BY r.ts DESC, r.id DESC, h.minutes
        """, coin, start, now, min_price, max_price, limit, list(HORIZONS), end)
    trades = {}
    for row in events:
        event = trades.setdefault(row["id"], {
            "ts": row["ts"].isoformat(), "venue": row["venue"], "market": row["market"],
            "side": row["side"], "price": row["price"], "usd": row["usd"], "reaction_pct": {},
        })
        close = row["reaction_close"]
        event["reaction_pct"][f"{row['minutes']}m"] = (close / row["price"] - 1) * 100 if close is not None else None
    sources = [dict(row, net_usd=row["buy_usd"] - row["sell_usd"]) for row in groups]
    return {
        "coin": coin, "window": window, "generated_utc": now.isoformat(),
        "min_price": min_price, "max_price": max_price,
        "configured_trade_threshold_usd": config.whale_threshold(coin),
        "coverage": observed, "by_venue_market": sources, "trades": list(trades.values()),
        "reaction_method": "Trade price to same-venue and same-market close of the minute bucket opened N minutes after the trade minute. Null means the closed bucket is unavailable.",
    }
