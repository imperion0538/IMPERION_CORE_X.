"""PostgreSQL bağlantısı, şema ve saklama süresi temizliği."""
import asyncpg
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_deliveries (
  kind TEXT NOT NULL, chat TEXT NOT NULL, PRIMARY KEY (kind, chat));
CREATE TABLE IF NOT EXISTS liquidations (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL, venue TEXT NOT NULL, coin TEXT NOT NULL,
  side TEXT NOT NULL, price DOUBLE PRECISION NOT NULL, qty DOUBLE PRECISION NOT NULL, usd DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS liquidations_coin_ts ON liquidations (coin, ts DESC);
CREATE TABLE IF NOT EXISTS trades_1m (
  ts TIMESTAMPTZ NOT NULL, venue TEXT NOT NULL, market TEXT NOT NULL, coin TEXT NOT NULL,
  buy_usd DOUBLE PRECISION NOT NULL, sell_usd DOUBLE PRECISION NOT NULL, n INTEGER NOT NULL,
  open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION,
  PRIMARY KEY (coin, venue, market, ts));
CREATE INDEX IF NOT EXISTS trades_1m_coin_ts ON trades_1m (coin, ts DESC);
CREATE TABLE IF NOT EXISTS whale_trades (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL, venue TEXT NOT NULL, market TEXT NOT NULL, coin TEXT NOT NULL,
  side TEXT NOT NULL, price DOUBLE PRECISION NOT NULL, usd DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS whale_trades_coin_ts ON whale_trades (coin, ts DESC);
CREATE TABLE IF NOT EXISTS deriv_snap (
  ts TIMESTAMPTZ NOT NULL, venue TEXT NOT NULL, coin TEXT NOT NULL,
  price DOUBLE PRECISION, oi_usd DOUBLE PRECISION, funding_8h DOUBLE PRECISION, extra JSONB,
  PRIMARY KEY (coin, venue, ts));
CREATE INDEX IF NOT EXISTS deriv_snap_coin_ts ON deriv_snap (coin, ts DESC);
CREATE TABLE IF NOT EXISTS options_snap (
  ts TIMESTAMPTZ NOT NULL, coin TEXT NOT NULL, data JSONB NOT NULL, PRIMARY KEY (coin, ts));
"""
RETENTION = [("trades_1m", 35), ("whale_trades", 120), ("liquidations", 120), ("deriv_snap", 120), ("options_snap", 60)]
pool: asyncpg.Pool | None = None

async def init():
    global pool
    pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=8)
    async with pool.acquire() as c:
        await c.execute(SCHEMA)

async def cleanup():
    async with pool.acquire() as c:
        for table, days in RETENTION:
            await c.execute(f"DELETE FROM {table} WHERE ts < now() - interval '{int(days)} days'")
