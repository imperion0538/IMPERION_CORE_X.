"""Real PostgreSQL: journal/aggregation atomicity, idempotence and durable delivery."""
import json
import os
import time
from uuid import uuid4
import asyncpg
import pytest
import pytest_asyncio
from md import config, db, ingest, outbox

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL required")


@pytest_asyncio.fixture
async def database(monkeypatch):
    # Tests must point to an isolated test database, never a production DSN.
    schema = "journal_test_" + uuid4().hex
    admin = await asyncpg.connect(URL)
    await admin.execute(f"CREATE SCHEMA {schema}")
    separator = "&" if "?" in URL else "?"
    monkeypatch.setattr(config, "DATABASE_URL", URL + separator + "search_path=" + schema)
    monkeypatch.setattr(ingest, "_partition_cache", set())
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "test-only-never-sent")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_IDS", {"test-chat"})
    await db.init()
    try:
        yield db.pool
    finally:
        await db.pool.close()
        await admin.execute(f"DROP SCHEMA {schema} CASCADE")
        await admin.close()


def row(identity=None, seconds=0, price=100):
    ts = int(time.time()//60)*60-120+seconds
    return ingest.normalize("trade", "TEST", "fixture", "spot", ts*1000, price, 100000, "buy", identity or uuid4().hex)


@pytest.mark.asyncio
async def test_replay_is_idempotent_and_out_of_order_close_is_correct(database):
    a, b, c = row(seconds=1), row(seconds=30, price=110), row(seconds=10, price=90)
    await ingest.persist([a, b])
    await ingest.persist([a, b])  # reconnect or uncertain commit acknowledgement
    await ingest.persist([c])
    async with database.acquire() as conn:
        bar = await conn.fetchrow("SELECT * FROM trades_1m WHERE coin='TEST' AND venue='fixture' ORDER BY ts DESC LIMIT 1")
        assert bar["n"] == 3
        assert (bar["open"], bar["close"], bar["high"], bar["low"]) == (100, 110, 110, 90)
        assert bar["buy_usd"] == 30_000_000
        assert await conn.fetchval("SELECT count(*) FROM market_events WHERE id=ANY($1)", [a["id"], b["id"], c["id"]]) == 3
        await conn.execute("DELETE FROM trades_1m WHERE coin='TEST'")


@pytest.mark.asyncio
async def test_transaction_failure_rolls_back_raw_and_projection(database, monkeypatch):
    e = row()
    async def fail(*args, **kwargs):
        raise RuntimeError("outbox unavailable")
    monkeypatch.setattr(outbox, "enqueue", fail)
    with pytest.raises(RuntimeError):
        await ingest.persist([e])
    async with database.acquire() as conn:
        assert not await conn.fetchval("SELECT 1 FROM market_events WHERE id=$1", e["id"])
        assert not await conn.fetchval("SELECT 1 FROM trades_1m WHERE coin='TEST'")


@pytest.mark.asyncio
async def test_durable_outbox_retries_and_deduplicates_per_recipient(database):
    key = uuid4().hex
    async with database.acquire() as conn:
        await conn.execute("DELETE FROM alert_outbox")
        await outbox.enqueue(conn, key, "fixture")
        await outbox.enqueue(conn, key, "fixture")
        assert await conn.fetchval("SELECT count(*) FROM alert_outbox") == 1
    sent = []
    async def fail(chat, body):
        return False
    async def success(chat, body):
        sent.append((chat, body))
        return True
    assert await outbox.deliver_once(fail)
    async with database.acquire() as conn:
        await conn.execute("UPDATE alert_outbox SET next_attempt=now()")
    assert await outbox.deliver_once(success)
    assert not await outbox.deliver_once(success)
    assert sent == [("test-chat", "fixture")]


@pytest.mark.asyncio
async def test_partition_rotation_fences_replayed_expired_ticks(database, monkeypatch):
    # Use an isolated event window prior to every other test's samples.
    now = time.time()
    stamp = ingest.partition_start(now - 540) + 2
    e = ingest.normalize("trade", "ROTATE", "fixture", "spot", stamp*1000, 10, 1,
                         "buy", "rotation-event", now=now)
    await ingest.persist([e])
    monkeypatch.setattr(config, "RAW_RETENTION_HOURS", .1)
    await ingest.trim_journal()
    async with database.acquire() as conn:
        assert not await conn.fetchval("SELECT true FROM market_events WHERE id=$1", e["id"])
        assert await conn.fetchval("SELECT retired_before FROM journal_floor WHERE singleton") > stamp
    assert await ingest.persist([e]) == 0
    async with database.acquire() as conn:
        assert await conn.fetchval("SELECT sum(n) FROM trades_1m WHERE coin='ROTATE'") == 1
        await conn.execute("DELETE FROM trades_1m WHERE coin='ROTATE'")


def test_byte_budget_retires_oldest_sealed_partitions_first():
    sizes = {0: 20, 15: 10, 30: 10, 45: 5}
    assert ingest.retire_partitions(sizes, now=53, retention=3600, byte_target=16) == [0, 15]
    assert ingest.retire_partitions(sizes, now=53, retention=3600, byte_target=2) == [0, 15, 30]
