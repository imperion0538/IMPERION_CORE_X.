"""PostgreSQL'de kalıcı, alıcı ve mesaj parçası bazında Telegram teslim kuyruğu."""
import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from . import config, db, fmt

log = logging.getLogger("outbox")


async def enqueue(conn, key, text, expires_seconds=1800, *, coin=None):
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_IDS:
        return
    expires = datetime.now(timezone.utc) + timedelta(seconds=expires_seconds)
    card_coin = coin if config.TELEGRAM_CARD_ONLY else None
    parts = [text] if card_coin else fmt.split_message(text)
    for chat in sorted(config.TELEGRAM_CHAT_IDS):
        for part, body in enumerate(parts):
            event_id = hashlib.sha256(f"{key}:{chat}:{part}".encode()).hexdigest()
            await conn.execute("""INSERT INTO alert_outbox (id, chat, body, expires, card_coin)
                VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING""", event_id, chat, body, expires, card_coin)


async def broadcast(key, text, expires_seconds=1800, *, coin=None):
    async with db.pool.acquire() as conn, conn.transaction():
        await enqueue(conn, key, text, expires_seconds, coin=coin)


async def deliver_once(send, send_card=None):
    # Tek aktif yazıcı; lease, teslim esnasında süreç kapanırsa tekrar denemeyi sağlar.
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow("""UPDATE alert_outbox SET attempts=attempts+1,
            next_attempt=now()+interval '120 seconds'
            WHERE id=(SELECT id FROM alert_outbox WHERE delivered IS NULL
                AND expires>now() AND next_attempt<=now()
                ORDER BY ts FOR UPDATE SKIP LOCKED LIMIT 1)
            RETURNING id, chat, body, attempts, card_coin""")
    if not row:
        return False
    try:
        if row["card_coin"] and send_card:
            ok = await send_card(row["chat"], row["card_coin"], row["body"])
        else:
            ok = await send(row["chat"], row["body"])
    except Exception as exc:
        log.warning("Teslim yeniden denenecek: %s", type(exc).__name__)
        ok = False
    async with db.pool.acquire() as conn:
        if ok:
            await conn.execute("UPDATE alert_outbox SET delivered=now() WHERE id=$1", row["id"])
        else:
            delay = min(900, 2 ** min(row["attempts"], 9))
            await conn.execute("UPDATE alert_outbox SET next_attempt=now()+$2::interval WHERE id=$1",
                               row["id"], timedelta(seconds=delay))
    return True


async def run():
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_IDS):
        return
    from .telegram import send, send_live_4k
    while True:
        if await db.is_writer():
            worked = await deliver_once(send, send_live_4k)
            await asyncio.sleep(.1 if worked else 2)
        else:
            await asyncio.sleep(5)


async def status():
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow("""SELECT count(*) FILTER (WHERE delivered IS NULL AND expires>now()) pending,
            count(*) FILTER (WHERE delivered IS NULL AND expires<=now()) expired,
            count(*) FILTER (WHERE delivered IS NOT NULL) delivered FROM alert_outbox""")
    return dict(row)
