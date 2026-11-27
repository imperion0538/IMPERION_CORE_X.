"""Compact five-minute research observations and forward outcomes on a fixed history budget."""
import json
import statistics
from datetime import datetime, timedelta, timezone

from . import db

HISTORY_DAYS = 7


def observation(report):
    reference = report["price_consensus"]
    spot = reference["markets"]["spot"]
    flow = report["flows"].get("15m", {}).get("markets", {})
    return {
        "schema_version": 1,
        "generated_utc": report["generated_utc"],
        "spot_median": spot["median"] if len(spot["sources"]) >= 2 else None,
        "live_trade_sources": report["quality"]["live_sources"],
        "state": report["assessment"]["state"],
        "regimes": {k: v["regime"] for k, v in report["timeframes"].items()},
        "delta_fraction": {m: v["delta_fraction"] if v["comparable_sources"] >= 2 else None
                           for m, v in flow.items()},
        "funding_8h": report["derivatives"].get("funding_8h_weighted"),
        "oi_usd": report["derivatives"].get("oi_total_usd"),
        "l2_sources": report["live_orderbooks"]["live_sources"],
    }


async def save(report):
    timestamp = datetime.fromisoformat(report["generated_utc"])
    data = json.dumps(observation(report), allow_nan=False)
    async with db.pool.acquire() as conn, conn.transaction():
        await conn.execute("""INSERT INTO research_snapshots(coin,ts,data)
            VALUES ($1,date_bin('5 minutes',$2::timestamptz,'2000-01-01'),$3::jsonb)
            ON CONFLICT DO NOTHING""", report["coin"], timestamp, data)
        await conn.execute("DELETE FROM research_snapshots WHERE coin=$1 AND ts<$2", report["coin"],
                           timestamp - timedelta(days=HISTORY_DAYS))


def _state_key(row):
    flow = row.get("delta_fraction", {})
    if any(flow.get(m) is None for m in ("spot", "perp")):
        return None
    regime = row.get("regimes", {}).get("4h")
    if regime is None or row.get("state") == "VERİ YETERSİZ":
        return None
    return regime, flow["spot"] > 0, flow["perp"] > 0


def outcomes(rows, current, hours=4):
    """Use aligned non-overlapping UTC horizons and require an observed endpoint."""
    target = _state_key(current)
    by_bucket = {int(t.timestamp())//300: data for t, data in rows}
    returns = []
    matched = []
    span = hours * 12
    for bucket, a in by_bucket.items():
        if bucket % span:
            continue
        b = by_bucket.get(bucket + span, {})
        if not a.get("spot_median") or not b.get("spot_median") or not _state_key(a):
            continue
        elapsed = (datetime.fromisoformat(b["generated_utc"])-datetime.fromisoformat(a["generated_utc"])).total_seconds()
        if abs(elapsed-hours*3600) > 120:
            continue
        change = (b["spot_median"]/a["spot_median"]-1)*100
        returns.append(change)
        if target == _state_key(a):
            matched.append(change)
    return {"hours": hours, "same_state_samples": len(matched), "baseline_samples": len(returns),
            "status": "descriptive" if len(matched) >= 20 else "insufficient_samples",
            "same_state_median_return_pct": statistics.median(matched) if len(matched) >= 20 else None,
            "baseline_median_return_pct": statistics.median(returns) if len(returns) >= 20 else None}


async def evaluation(coin):
    async with db.pool.acquire() as conn:
        rows = await conn.fetch("SELECT ts,data FROM research_snapshots WHERE coin=$1 AND ts>=now()-interval '7 days' ORDER BY ts", coin)
    series = [(r["ts"], json.loads(r["data"])) for r in rows]
    reference = series[-1][1] if series else {}
    return {"reference_utc": reference.get("generated_utc"), "observations": len(series),
            "results": [outcomes(series, reference, hours) for hours in (1, 4)],
            "method": "Observed spot median return after matching archived 4h regime and 15m spot/perp delta signs. Non-overlapping UTC intervals, 2-minute timing tolerance. Descriptive, before execution costs."}
