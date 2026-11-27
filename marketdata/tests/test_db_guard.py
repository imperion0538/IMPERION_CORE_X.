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
        await c.execute("DELETE FROM deriv_snap WHERE venue = 'test'")
    with pytest.raises(asyncpg.RaiseError):
        await stranger.execute("INSERT INTO deriv_snap (ts, venue, coin) VALUES (now(), 'test2', 'BTC')")
    importlib.reload(db)
    monkeypatch.setattr(config, "DATABASE_URL", URL)
    await db.init()
    with pytest.raises(asyncpg.RaiseError):
        async with first.acquire() as c:
            await c.execute("INSERT INTO deriv_snap (ts, venue, coin) VALUES (now(), 'test3', 'BTC')")
    assert await db.is_writer()
    await stranger.close(); await first.close(); await db.pool.close()
