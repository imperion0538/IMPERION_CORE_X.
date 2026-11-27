"""PostgreSQL bağlantısı, şema, tek yazıcı kilidi ve saklama süresi temizliği."""
import logging, os, socket, time  # noqa: E401
import asyncpg
from . import config

log = logging.getLogger("db")
# Bu sürecin veritabanındaki kimliği. Yalnızca writer_lock tablosundaki kimlik yazabilir;
# böylece kapanmamış eski dağıtımlar (ör. Railway'de "REMOVED" görünüp çalışmaya devam eden süreç) veri yazamaz.
APP_NAME = f"md:{os.environ.get('RAILWAY_DEPLOYMENT_ID', socket.gethostname())[:40]}:{int(time.time())}:{os.urandom(4).hex()}"
WRITE_TABLES = ("alert_outbox", "research_snapshots", "trade_write_batches", "liquidations", "trades_1m", "whale_trades", "deriv_snap", "options_snap", "telegram_deliveries", "depth_snap", "macro_snap")

GUARD = """
CREATE TABLE IF NOT EXISTS writer_lock (id INT PRIMARY KEY DEFAULT 1, app TEXT NOT NULL, since TIMESTAMPTZ NOT NULL DEFAULT now(), CHECK (id = 1));
CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY, applied TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE OR REPLACE FUNCTION md_single_writer() RETURNS trigger AS $$
BEGIN
  -- Aktif yazıcı olmayan süreçlerin satırları sessizce atlanır (hata fırlatılmaz: eski kod çökme döngüsüne girip
  -- borsalara tekrar tekrar istek atmasın). Kilit sahibini kaybeden yeni kod, writer_watchdog ile kendini kapatır.
  IF current_setting('application_name') IS DISTINCT FROM (SELECT app FROM writer_lock WHERE id = 1) THEN
    RETURN NULL;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION md_writer_statement() RETURNS trigger AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM writer_lock WHERE id=1 AND app=current_setting('application_name') FOR SHARE) THEN
    RAISE EXCEPTION 'inactive market-data writer' USING ERRCODE='55000';
  END IF;
  RETURN NULL;
END $$ LANGUAGE plpgsql;
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_write_batches (
  id UUID PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now());
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
CREATE TABLE IF NOT EXISTS depth_snap (
  ts TIMESTAMPTZ NOT NULL, coin TEXT NOT NULL, mid DOUBLE PRECISION NOT NULL, step DOUBLE PRECISION NOT NULL, data JSONB NOT NULL,
  PRIMARY KEY (coin, ts));
CREATE TABLE IF NOT EXISTS options_snap (
  ts TIMESTAMPTZ NOT NULL, coin TEXT NOT NULL, data JSONB NOT NULL, PRIMARY KEY (coin, ts));
ALTER TABLE trades_1m ADD COLUMN IF NOT EXISTS first_trade_ts TIMESTAMPTZ;
ALTER TABLE trades_1m ADD COLUMN IF NOT EXISTS last_trade_ts TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS market_events (
  id TEXT NOT NULL, ts TIMESTAMPTZ NOT NULL, received TIMESTAMPTZ NOT NULL,
  kind TEXT NOT NULL, venue TEXT NOT NULL, market TEXT NOT NULL, coin TEXT NOT NULL,
  side TEXT NOT NULL, price DOUBLE PRECISION NOT NULL, qty DOUBLE PRECISION NOT NULL,
  usd DOUBLE PRECISION NOT NULL, PRIMARY KEY(ts,id)) PARTITION BY RANGE(ts);
CREATE TABLE IF NOT EXISTS journal_floor (singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton),
  retired_before BIGINT NOT NULL DEFAULT 0);
INSERT INTO journal_floor VALUES (TRUE, 0) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS alert_outbox (
  id TEXT PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(), chat TEXT NOT NULL,
  body TEXT NOT NULL, expires TIMESTAMPTZ NOT NULL, delivered TIMESTAMPTZ,
  attempts INT NOT NULL DEFAULT 0, next_attempt TIMESTAMPTZ NOT NULL DEFAULT now());
ALTER TABLE alert_outbox ADD COLUMN IF NOT EXISTS card_coin TEXT;
CREATE INDEX IF NOT EXISTS alert_outbox_pending ON alert_outbox(next_attempt) WHERE delivered IS NULL;
CREATE TABLE IF NOT EXISTS research_snapshots (
  coin TEXT NOT NULL, ts TIMESTAMPTZ NOT NULL, data JSONB NOT NULL, PRIMARY KEY(coin, ts));
CREATE TABLE IF NOT EXISTS macro_snap (ts TIMESTAMPTZ PRIMARY KEY, data JSONB NOT NULL);
"""
RETENTION = [("alert_outbox", 7), ("research_snapshots", 35), ("trade_write_batches", 2), ("depth_snap", 10), ("trades_1m", 35), ("whale_trades", 120), ("liquidations", 120), ("deriv_snap", 120), ("options_snap", 60), ("macro_snap", 90)]
pool: asyncpg.Pool | None = None

DEDUPE_2026_11_27 = """
DELETE FROM whale_trades a USING whale_trades b
 WHERE a.id > b.id AND a.ts = b.ts AND a.venue = b.venue AND a.market = b.market AND a.coin = b.coin
   AND a.side = b.side AND a.price = b.price AND a.usd = b.usd;
DELETE FROM liquidations a USING liquidations b
 WHERE a.id > b.id AND a.ts = b.ts AND a.venue = b.venue AND a.coin = b.coin
   AND a.side = b.side AND a.price = b.price AND a.qty = b.qty;
TRUNCATE trades_1m;
"""

async def init():
    global pool
    pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=8, server_settings={"application_name": APP_NAME})
    async with pool.acquire() as c:
        await c.execute(SCHEMA)
        await c.execute(GUARD)
        await c.execute("DROP TRIGGER IF EXISTS md_single_writer ON market_events; "
                        "CREATE TRIGGER md_single_writer BEFORE INSERT OR UPDATE OR DELETE ON market_events "
                        "FOR EACH STATEMENT EXECUTE FUNCTION md_writer_statement()")
        for t in WRITE_TABLES:
            await c.execute(f"DROP TRIGGER IF EXISTS md_single_writer ON {t}; "
                            f"CREATE TRIGGER md_single_writer BEFORE INSERT OR UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION md_single_writer()")
        # En son başlayan süreç yazıcı olur; önceki süreçlerin yazmaları bundan sonra reddedilir.
        await c.execute("INSERT INTO writer_lock (id, app) VALUES (1, $1) ON CONFLICT (id) DO UPDATE SET app = EXCLUDED.app, since = now()", APP_NAME)
        if not await c.fetchval("SELECT 1 FROM migrations WHERE name = 'dedupe-2026-11-27'"):
            async with c.transaction():
                await c.execute(DEDUPE_2026_11_27)
                await c.execute("INSERT INTO migrations (name) VALUES ('dedupe-2026-11-27')")
            log.info("tekrar eden kayıtlar temizlendi, trades_1m sıfırlandı")
    log.info("aktif yazıcı: %s", APP_NAME)

async def connections():
    """Bu veritabanına aynı kullanıcıyla bağlı süreçler (tanılama). Kimliği 'md:' ile başlamayan bağlantılar
    eski/yabancı süreçlerdir (ör. kapanmamış eski dağıtım)."""
    async with pool.acquire() as c:
        rows = await c.fetch("""SELECT coalesce(application_name, '') app, host(client_addr) client, count(*) n,
                                       min(backend_start) since, max(state_change) last_activity,
                                       string_agg(DISTINCT coalesce(state, '?'), ',') states
                                FROM pg_stat_activity WHERE datname = current_database() AND usename = current_user
                                GROUP BY 1, 2 ORDER BY 3 DESC""")
        writer = await c.fetchval("SELECT app FROM writer_lock WHERE id = 1")
    return [{"app": r["app"] or "(kimliksiz)", "client": r["client"], "connections": r["n"], "since": r["since"].isoformat() if r["since"] else None,
             "last_activity": r["last_activity"].isoformat() if r["last_activity"] else None, "states": r["states"],
             "role": "aktif yazıcı" if r["app"] == writer else "bu süreç" if r["app"] == APP_NAME else "yabancı"} for r in rows]

async def is_writer():
    async with pool.acquire() as c:
        return await c.fetchval("SELECT app FROM writer_lock WHERE id = 1") == APP_NAME

async def cleanup():
    async with pool.acquire() as c:
        for table, days in RETENTION:
            await c.execute(f"DELETE FROM {table} WHERE ts < now() - interval '{int(days)} days'")
