"""Saklanan olayları kapalı dakikalara tekrar yansıtıp toplamlarla karşılaştırır; salt okunur."""
from datetime import datetime, timedelta, timezone
from . import db


async def check(coin, minutes=5):
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end-timedelta(minutes=minutes)
    async with db.pool.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
        first = await conn.fetchval("SELECT min(ts) FROM market_events WHERE coin=$1 AND kind='trade' AND ts>=$2", coin, start)
        if first is None:
            return {"status": "no_journal_data", "coin": coin, "checked_buckets": 0}
        # First observed minute may straddle the v1 -> v2 cutover / retention boundary.
        start = max(start, first.replace(second=0, microsecond=0)+timedelta(minutes=1))
        rows = await conn.fetch("""WITH replay AS (
            SELECT date_trunc('minute',ts) ts,venue,market,count(*) n,
              coalesce(sum(usd) FILTER (WHERE side='buy'),0) buy,
              coalesce(sum(usd) FILTER (WHERE side='sell'),0) sell
            FROM market_events WHERE coin=$1 AND kind='trade' AND ts>=$2 AND ts<$3
            GROUP BY 1,2,3)
            SELECT r.*, t.n stored_n,t.buy_usd,t.sell_usd
            FROM replay r LEFT JOIN trades_1m t ON t.coin=$1 AND t.ts=r.ts AND t.venue=r.venue AND t.market=r.market""", coin, start, end)
    mismatches = [{"minute": r["ts"].isoformat(), "venue": r["venue"], "market": r["market"]}
                  for r in rows if r["stored_n"] != r["n"] or r["buy_usd"] is None
                  or abs(r["buy"]-r["buy_usd"]) > max(.01, abs(r["buy"])*1e-9)
                  or abs(r["sell"]-r["sell_usd"]) > max(.01, abs(r["sell"])*1e-9)]
    return {"coin": coin, "status": "mismatch" if mismatches else "consistent" if rows else "warming_up",
            "start_utc": start.isoformat(), "end_utc": end.isoformat(), "checked_buckets": len(rows),
            "mismatches": mismatches,
            "scope": "Committed trade journal vs stored minute counts and notionals. Does not prove exchange completeness; first partial minute excluded."}
