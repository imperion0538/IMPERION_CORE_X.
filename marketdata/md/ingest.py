"""Normalize -> bounded queue -> transactional journal, minute bars and alert outbox.

Exchange IDs deduplicate reconnect replays, including after restart. A DB commit is the
durability boundary; events still in the queue on abrupt process death can be lost.
"""
import asyncio
import hashlib
import json
import logging
import math
import re
import time
from datetime import datetime, timezone, timedelta
from . import config, db, fmt, outbox, quality, state

log = logging.getLogger("ingest")
QUEUE = asyncio.Queue(maxsize=50_000)
UTC = timezone.utc
PARTITION_SECONDS = 15
_partition_cache = set()
_journal_lock = asyncio.Lock()



def normalize(kind, coin, venue, market, ts_ms, price, qty, side, native_id=None, now=None):
    now = time.time() if now is None else now
    ts, price, qty = float(ts_ms) / 1000, float(price), float(qty)
    if (not all(math.isfinite(x) for x in (ts, price, qty, price*qty))
            or price <= 0 or qty <= 0 or ts > now + 30 or ts < now - 600
            or kind not in ("trade", "liquidation")
            or side not in (("buy", "sell") if kind == "trade" else ("long", "short"))):
        raise ValueError("invalid or stale exchange event")
    identity = str(native_id) if native_id is not None else f"{ts:.6f}:{side}:{price:.12g}:{qty:.12g}"
    key = hashlib.blake2b(f"{kind}:{venue}:{market}:{coin}:{identity}".encode(), digest_size=20).hexdigest()
    return {"id": key, "ts": ts, "received": now, "kind": kind, "venue": venue,
            "market": market, "coin": coin, "side": side, "price": price, "qty": qty,
            "usd": price * qty}


async def submit(kind, coin, venue, market, ts_ms, price, qty, side, native_id=None):
    try:
        event = normalize(kind, coin, venue, market, ts_ms, price, qty, side, native_id)
    except (ValueError, TypeError, OverflowError):
        quality.counters["invalid_events"] += 1
        return
    # Backpressure instead of silently dropping a full queue.
    await QUEUE.put(event)
    quality.counters["queued_events"] += 1
    if kind == "trade":
        quality.observe(coin, venue, market, event["ts"], event["received"])
        key = (venue, market, coin)
        if event["ts"] >= state.last_price.get(key, (0, 0))[1]:
            state.last_price[key] = (price, event["ts"])


INSERT_EVENTS = """INSERT INTO market_events
    (id, ts, received, kind, venue, market, coin, side, price, qty, usd)
    SELECT id, to_timestamp(ts), to_timestamp(received), kind, venue, market, coin, side, price, qty, usd
    FROM jsonb_to_recordset($1::jsonb) AS e(id text, ts float8, received float8, kind text,
        venue text, market text, coin text, side text, price float8, qty float8, usd float8)
    ON CONFLICT DO NOTHING RETURNING *"""

UPSERT_MINUTES = """INSERT INTO trades_1m
    (ts, venue, market, coin, buy_usd, sell_usd, n, open, high, low, close, first_trade_ts, last_trade_ts)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
    ON CONFLICT (coin, venue, market, ts) DO UPDATE SET
    buy_usd=trades_1m.buy_usd+EXCLUDED.buy_usd, sell_usd=trades_1m.sell_usd+EXCLUDED.sell_usd,
    n=trades_1m.n+EXCLUDED.n, high=GREATEST(trades_1m.high,EXCLUDED.high),
    low=LEAST(trades_1m.low,EXCLUDED.low),
    open=CASE WHEN EXCLUDED.first_trade_ts < trades_1m.first_trade_ts THEN EXCLUDED.open ELSE trades_1m.open END,
    close=CASE WHEN trades_1m.last_trade_ts IS NULL OR EXCLUDED.last_trade_ts >= trades_1m.last_trade_ts
        THEN EXCLUDED.close ELSE trades_1m.close END,
    first_trade_ts=LEAST(trades_1m.first_trade_ts,EXCLUDED.first_trade_ts),
    last_trade_ts=GREATEST(trades_1m.last_trade_ts,EXCLUDED.last_trade_ts)"""


def aggregate(events):
    """Deterministic event-time OHLC; arrival order never selects the close."""
    buckets = {}
    for e in sorted(events, key=lambda e: (e["ts"], e["id"])):
        if e["kind"] != "trade":
            continue
        ts = e["ts"]
        if not isinstance(ts, datetime):
            ts = datetime.fromtimestamp(ts, UTC)
        key = (ts.replace(second=0, microsecond=0), e["venue"], e["market"], e["coin"])
        b = buckets.setdefault(key, [0., 0., 0, e["price"], e["price"], e["price"], e["price"], ts, ts])
        b[0 if e["side"] == "buy" else 1] += e["usd"]
        b[2] += 1
        b[4], b[5], b[6], b[8] = max(b[4], e["price"]), min(b[5], e["price"]), e["price"], ts
    return [(*key, *b) for key, b in buckets.items()]


def partition_start(ts):
    return int(ts) // PARTITION_SECONDS * PARTITION_SECONDS


async def prepare_partitions(conn, starts):
    for start in sorted(starts - _partition_cache):
        lo = datetime.fromtimestamp(start, UTC).isoformat()
        hi = datetime.fromtimestamp(start + PARTITION_SECONDS, UTC).isoformat()
        await conn.execute(f"CREATE TABLE IF NOT EXISTS md_events_{start} PARTITION OF market_events "
                           f"FOR VALUES FROM ('{lo}') TO ('{hi}')")


async def persist(events):
    async with _journal_lock, db.pool.acquire() as conn, conn.transaction():
        # Retired event times may no longer be deduplicated. Never reopen them.
        floor = await conn.fetchval("SELECT retired_before FROM journal_floor WHERE singleton")
        admitted = [e for e in events if e["ts"] >= floor]
        starts = {partition_start(e["ts"]) for e in admitted}
        await prepare_partitions(conn, starts)
        inserted = await conn.fetch(INSERT_EVENTS, json.dumps(admitted, allow_nan=False))
        bars = aggregate(inserted)
        if bars:
            await conn.executemany(UPSERT_MINUTES, bars)
        for e in inserted:
            if e["kind"] == "liquidation":
                await conn.execute("""INSERT INTO liquidations (ts,venue,coin,side,price,qty,usd)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)""", e["ts"], e["venue"], e["coin"], e["side"], e["price"], e["qty"], e["usd"])
                alert = e["usd"] >= config.ALERT_LIQ_USD * config.alert_scale(e["coin"])
                label = f"{'🚫' if e['side']=='long' else '💸'} {e['side']} likidasyonu"
            else:
                if e["usd"] < config.whale_threshold(e["coin"]):
                    continue
                await conn.execute("""INSERT INTO whale_trades (ts,venue,market,coin,side,price,usd)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)""", e["ts"], e["venue"], e["market"], e["coin"], e["side"], e["price"], e["usd"])
                alert = e["usd"] >= config.ALERT_WHALE_USD * config.alert_scale(e["coin"])
                label = f"🐋 Büyük işlem · {e['market']} · {'alım' if e['side']=='buy' else 'satım'}"
            if alert and config.ALERTS_ENABLED:
                text = (f"<b>🗼 {e['coin']} · {e['venue']}</b>\n{label}: {fmt.usd(e['usd'])} @ ${e['price']:,.2f}"
                        f"\n{e['ts'].strftime('%H:%M:%S')} UTC · Gözlenen işlem; hesap sahibinin kimliği bilinmez.")
                await outbox.enqueue(conn, e["id"], text)
    _partition_cache.update(starts)  # Only cache partition creation after commit.
    quality.counters["expired_before_commit"] += len(events) - len(admitted)
    quality.counters["persisted_events"] += len(inserted)
    quality.counters["duplicate_events"] += len(admitted) - len(inserted)
    quality.counters["last_commit_epoch"] = time.time()
    return len(inserted)


async def run():
    pending = []
    while True:
        if not pending:
            pending.append(await QUEUE.get())
            await asyncio.sleep(.25)
            while len(pending) < 2000 and not QUEUE.empty():
                pending.append(QUEUE.get_nowait())
        try:
            await persist(pending)
            for _ in pending:
                QUEUE.task_done()
            pending = []
        except Exception as exc:
            quality.counters["write_errors"] += 1
            log.warning("Olay grubu yeniden denenecek: %s", type(exc).__name__)
            await asyncio.sleep(2)


def retire_partitions(sizes, now, retention, byte_target):
    """Prefer recent observations. Always preserve the currently writable interval."""
    used = sum(sizes.values())
    expired = []
    for start, size in sorted(sizes.items()):
        if start + PARTITION_SECONDS > now:
            break
        if start + PARTITION_SECONDS > now - retention and used <= byte_target:
            break
        expired.append(start)
        used -= size
    return expired


async def trim_journal():
    async with _journal_lock, db.pool.acquire() as conn, conn.transaction():
        if not await conn.fetchval("SELECT true FROM writer_lock WHERE id=1 AND app=$1 FOR SHARE", db.APP_NAME):
            return
        rows = await conn.fetch("""SELECT relname,pg_total_relation_size(c.oid) bytes
            FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid
            WHERE i.inhparent='market_events'::regclass""")
        sizes = {int(r["relname"][10:]): r["bytes"] for r in rows
                 if re.fullmatch(r"md_events_[0-9]+", r["relname"])}
        expired = retire_partitions(sizes, time.time(), config.RAW_RETENTION_HOURS * 3600,
                                    config.RAW_JOURNAL_TARGET_MIB * 1024**2)
        if expired:
            floor = expired[-1] + PARTITION_SECONDS
            await conn.execute("UPDATE journal_floor SET retired_before=greatest(retired_before,$1) "
                               "WHERE singleton", floor)
            for start in expired:
                await conn.execute(f"DROP TABLE md_events_{start}")
    _partition_cache.difference_update(expired)
    quality.counters["journal_bytes"] = sum(sizes.values()) - sum(sizes[k] for k in expired)
    quality.counters["journal_target_bytes"] = int(config.RAW_JOURNAL_TARGET_MIB * 1024**2)
    quality.counters["journal_retired_before"] = await db.pool.fetchval(
        "SELECT retired_before FROM journal_floor WHERE singleton")


async def cleanup():
    while True:
        await trim_journal()
        await asyncio.sleep(5)
