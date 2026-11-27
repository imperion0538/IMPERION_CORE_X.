"""Tek yazıcı kilidinin entegrasyon testi. TEST_DATABASE_URL yoksa atlanır (CI'da PostgreSQL servisiyle çalışır)."""
import importlib, os
import asyncpg, pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL tanımlı değil")


@pytest.mark.asyncio
async def test_single_writer_and_takeover(monkeypatch):
    from md import config, db
    monkeypatch.setattr(config, "DATABASE_URL", URL)
    stranger = await asyncpg.connect(URL)
    await stranger.execute(db.SCHEMA)
    await db.init()
    first = db.pool
    async with first.acquire() as c:
        await c.execute("INSERT INTO deriv_snap (ts, venue, coin) VALUES (now(), 'test', 'BTC') ON CONFLICT DO NOTHING")
        assert await c.fetchval("SELECT count(*) FROM deriv_snap WHERE venue = 'test'") == 1
        await c.execute("DELETE FROM deriv_snap WHERE venue = 'test'")
        assert await c.fetchval("SELECT count(*) FROM deriv_snap WHERE venue = 'test'") == 0
    await stranger.execute("INSERT INTO deriv_snap (ts, venue, coin) VALUES (now(), 'test2', 'BTC')")
    assert await stranger.fetchval("SELECT count(*) FROM deriv_snap WHERE venue = 'test2'") == 0  # sessizce atlandı
    importlib.reload(db)
    monkeypatch.setattr(config, "DATABASE_URL", URL)
    await db.init()
    async with first.acquire() as c:
        await c.execute("INSERT INTO deriv_snap (ts, venue, coin) VALUES (now(), 'test3', 'BTC')")
        assert await c.fetchval("SELECT count(*) FROM deriv_snap WHERE venue = 'test3'") == 0
    assert await db.is_writer()
    await stranger.close(); await first.close(); await db.pool.close()
