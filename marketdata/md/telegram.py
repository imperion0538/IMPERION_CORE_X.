"""Telegram botu: komutlar (long polling), alarmlar ve isteğe bağlı zamanlı raporlar."""
import asyncio, json, logging, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import aiohttp
from . import analysis, card4k, charts, config, db, fmt, heatmap, http, orderbook, state, whales

log = logging.getLogger("telegram")
API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
WMAP = {"1s": "1h", "4s": "4h", "24s": "24h", "7g": "7d", "30g": "30d", "1h": "1h", "4h": "4h", "24h": "24h", "7d": "7d", "30d": "30d"}

async def send(chat, text):
    """Mesajı satır sınırlarından böler (HTML etiketi ortadan kesilmez); biçim reddedilirse düz metin dener."""
    s = await http.session()
    for part in fmt.split_message(text):
        async with s.post(f"{API}/sendMessage", json={"chat_id": chat, "text": part, "parse_mode": "HTML", "disable_web_page_preview": True}) as r:
            if r.status == 200: continue
            body = await r.text()
            log.warning("sendMessage %s: %s", r.status, body[:300])
        if r.status == 400:
            async with s.post(f"{API}/sendMessage", json={"chat_id": chat, "text": fmt.strip_html(part), "disable_web_page_preview": True}) as r2:
                if r2.status != 200: log.warning("sendMessage düz metin %s", r2.status)

async def photo(chat, png, caption, document=False):
    s = await http.session()
    cap, rest = fmt.split_caption(caption)
    form = aiohttp.FormData(); form.add_field("chat_id", str(chat)); form.add_field("caption", cap); form.add_field("parse_mode", "HTML")
    form.add_field("document" if document else "photo", png, filename="imperion.png", content_type="image/png")
    async with s.post(f"{API}/{'sendDocument' if document else 'sendPhoto'}", data=form) as r:
        if r.status != 200: log.warning("sendPhoto %s: %s", r.status, (await r.text())[:300])
    if rest:
        await send(chat, rest)

CARD = Path(__file__).resolve().parents[1] / "assets" / "imperion-btc-tr-4k.png"


async def send_card(chat):
    client = await http.session()
    with CARD.open("rb") as image:
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat))
        form.add_field("document", image, filename="IMPERION-BTC-TR-4K.png", content_type="image/png")
        form.add_field("caption", "IMPERION CORE · BTCUSDT · 4K PNG · 26 Kasım 2026, 15:54 TSİ")
        async with client.post(f"{API}/sendDocument", data=form) as response:
            if response.status != 200:
                log.warning("4K kart teslimi başarısız: HTTP %s", response.status)
                return False
            return bool((await response.json()).get("ok"))


async def deliver_card():
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_IDS):
        return
    for chat in sorted(config.TELEGRAM_CHAT_IDS):
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                claimed = await conn.fetchval(
                    "INSERT INTO telegram_deliveries (kind, chat) VALUES ($1,$2) ON CONFLICT DO NOTHING RETURNING true",
                    "btc-tr-4k-2026-11-26", chat,
                )
                if claimed:
                    try:
                        sent = await send_card(chat)
                    except (aiohttp.ClientError, asyncio.TimeoutError):
                        sent = False
                    if not sent:
                        raise RuntimeError("Telegram 4K kartı teslim edilemedi")
                    log.info("Telegram 4K kart teslimi başarılı")


KEYWORDS = {"arsiv", "arşiv", "dosya", "ozet", "özet"}

def _args(parts, default_window):
    coin, window = "BTC", default_window
    for p in parts:
        p2 = p.lower()
        if p2 in KEYWORDS: continue
        if p2 in WMAP: window = WMAP[p2]
        elif p.upper() in config.COINS or p.upper().isalpha(): coin = p.upper()
    return coin, window

LIVE_ONLY = ("/likidasyon", "/balina", "/akis", "/oi", "/ls", "/analiz", "/derinlik", "/defter", "/cvd", "/kart")
HOURS = {"1h": 1, "4h": 4, "24h": 24, "7d": 168, "30d": 720}

async def live_card(coin):
    a, h, d = await asyncio.gather(analysis.analysis(coin), heatmap.model(coin, "24h"), orderbook.latest(coin))
    return charts.analysis_card_png(a, h, d)

async def handle(chat, text):
    parts = text.strip().split(); cmd = parts[0].split("@")[0].lower(); rest = parts[1:]
    if cmd in LIVE_ONLY:
        coin = _args(rest, "24h")[0]
        if coin not in config.COINS:
            return await send(chat, f"{coin} canlı izlenmiyor; canlı izlenenler: {', '.join(config.COINS)}.\n"
                                    f"Isı haritası her Binance perp coini için çalışır: /isi {coin} 24s\n"
                                    f"Canlı izlemeye eklemek için Railway'de COINS değişkenine {coin} ekle.")
    if cmd in ("/start", "/yardim", "/help"):
        return await send(chat, fmt.HELP)
    if cmd in ("/btc", "/eth", "/sol") or cmd == "/analiz":
        coin = cmd[1:].upper() if cmd != "/analiz" else _args(rest, "24h")[0]
        return await send(chat, fmt.analysis_text(await analysis.analysis(coin)))
    if cmd == "/kart":
        if rest and rest[0].lower() in ("arsiv", "arşiv"):
            return await send_card(chat)
        coin = _args(rest, "24h")[0]
        if rest and rest[-1].lower() == "ozet":
            return await photo(chat, await live_card(coin), f"<b>{coin} canlı özet kartı</b> · 1920×1080")
        png = await card4k.build(coin)
        cap = f"<b>{coin}USDT teknik analiz kartı</b> · 4K · {datetime.now(timezone.utc).strftime('%H:%M')} UTC\nKoşullu senaryolar teyit bekler; yatırım tavsiyesi değildir."
        await photo(chat, png, cap)
        return await photo(chat, png, f"{coin}USDT · 3840×2160 PNG (tam çözünürlük)", document=True)
    if cmd == "/derinlik":
        coin = _args(rest, "24h")[0]
        d = await orderbook.latest(coin)
        if not d: return await send(chat, "Henüz emir defteri görüntüsü yok; dakikada bir toplanıyor.")
        return await send(chat, fmt.depth_text(coin, d))
    if cmd == "/defter":
        coin, w = _args(rest, "4h")
        hours = min(HOURS.get(w, 4), 24)
        hist = await orderbook.history(coin, hours)
        return await photo(chat, charts.orderbook_heatmap_png(hist, f"{coin} emir defteri ısı haritası · son {hours} saat"),
                           f"<b>{coin} emir defteri ısı haritası</b> · son {hours} saat · {len(hist)} görüntü\nParlak bantlar büyük bekleyen emirlerdir (duvar).")
    if cmd == "/cvd":
        coin, w = _args(rest, "24h")
        hours = HOURS.get(w, 24)
        rows = await analysis.cvd_series(coin, hours)
        tot = {m: sum(r["delta"] for r in rows if r["market"] == m) for m in ("spot", "perp")}
        return await photo(chat, charts.cvd_png(rows, f"{coin} CVD · {w}{fmt.coverage_note(hours * 3600)}"),
                           f"<b>{coin} CVD · {w}</b>\nSpot {fmt.usd(tot['spot'])} · Perp {fmt.usd(tot['perp'])}")
    if cmd == "/isi":
        coin, w = _args(rest, "24h")
        h = await heatmap.model(coin, w)
        cap = f"<b>{coin} tahmini likidasyon ısı haritası · {w}</b>\n" + "\n".join(
            f"{'🚫 Long' if c['side']=='long' else '💸 Short'} ${c['price']:,.0f} ({c['dist_pct']:+.1f}%) {fmt.usd(c['usd'])}" for c in sorted(h["clusters"], key=lambda c: -c["price"]))
        return await photo(chat, charts.heatmap_png(h, f"{coin} tahmini likidasyon haritası · {w} · {h['generated_utc']} UTC"), cap)
    if cmd == "/likidasyon":
        coin, w = _args(rest, "24h")
        r = await heatmap.realized(coin, w)
        byv = " · ".join(f"{v}: L {fmt.usd(x.get('long',0))} S {fmt.usd(x.get('short',0))}" for v, x in r["by_venue"].items())
        cap = f"<b>{coin} gerçekleşen likidasyonlar · {w}{fmt.coverage_note({'1h': 3600, '4h': 14400, '24h': 86400, '7d': 604800, '30d': 2592000}[w])}</b>\nLong {fmt.usd(r['total_long'])} · Short {fmt.usd(r['total_short'])}\n{byv}"
        return await photo(chat, charts.realized_png(r, f"{coin} gerçekleşen likidasyonlar · {w}"), cap)
    if cmd == "/akis":
        coin = _args(rest[:1], "15m")[0]
        args = rest[1:] if rest and rest[0].upper() == coin else rest
        window = WMAP.get(args[0], args[0]) if args else "15m"
        if window not in whales.WINDOWS or len(args) not in (0, 1, 3):
            return await send(chat, "Kullanım: /akis BTC 15m [alt_fiyat üst_fiyat]")
        low = high = None
        if len(args) == 3:
            try:
                low, high = map(float, args[1:])
            except ValueError:
                return await send(chat, "Fiyat sınırlarını sayı olarak gir.")
            if not 0 < low <= high < float("inf"):
                return await send(chat, "Alt ve üst fiyat sınırlarını kontrol et.")
        return await send(chat, fmt.whale_flow_text(await whales.report(coin, window, low, high, 8)))
    if cmd == "/balina":
        coin, w = _args(rest, "4h")
        secs = {"1h": 3600, "4h": 14400, "24h": 86400, "7d": 604800, "30d": 2592000}[w]
        async with db.pool.acquire() as c:
            rows = await c.fetch("SELECT ts, venue, market, side, price, usd FROM whale_trades WHERE coin=$1 AND ts > now() - $2::interval ORDER BY usd DESC LIMIT 15", coin, timedelta(seconds=secs))
            agg = await c.fetch("SELECT side, sum(usd) u, count(*) n FROM whale_trades WHERE coin=$1 AND ts > now() - $2::interval GROUP BY side", coin, timedelta(seconds=secs))
        a = {r["side"]: r for r in agg}
        b, s_ = a.get("buy"), a.get("sell")
        L = [f"<b>{coin} balina işlemleri · {w}{fmt.coverage_note(secs)}</b> (eşik {fmt.usd(config.whale_threshold(coin))})",
             f"Alım {fmt.usd(b['u'] if b else 0)} ({b['n'] if b else 0}) · Satım {fmt.usd(s_['u'] if s_ else 0)} ({s_['n'] if s_ else 0}) · Net {fmt.usd((b['u'] if b else 0) - (s_['u'] if s_ else 0))}", ""]
        L += [f"{'💸' if r['side']=='buy' else '🚫'} {r['ts'].strftime('%H:%M')} {r['venue']} {r['market']} {fmt.usd(r['usd'])} @ ${r['price']:,.0f}" for r in rows]
        return await send(chat, "\n".join(L))
    if cmd in ("/oi", "/ls"):
        coin = _args(rest, "24h")[0]
        d = await analysis.deriv(coin)
        if cmd == "/oi":
            L = [f"<b>{coin} OI ve funding</b> · toplam {fmt.usd(d['oi_total_usd'])} · " + " ".join(f"{w} {fmt.pct(x)}" for w, x in d["oi_change_pct"].items())]
            L += [f"{k}: OI {fmt.usd(v['oi_usd'])} · funding 8s {(v['funding_8h'] or 0)*100:+.4f}%" for k, v in sorted(d["venues"].items(), key=lambda kv: -(kv[1]['oi_usd'] or 0))]
            L.append(f"OI ağırlıklı funding yıllık {fmt.pct(d['funding_ann_pct'], 1)}")
        else:
            L = [f"<b>{coin} long/short oranları</b>"]
            for k, v in d["venues"].items():
                xs = [f"{n}={v[n]:.2f}" for n in ("ls_global", "ls_top_account", "ls_top_position", "ls_account", "taker_buy_sell") if n in v]
                if xs: L.append(f"{k}: " + " · ".join(xs))
            L.append("1'in üstü: long hesap/pozisyon ağırlığı. taker_buy_sell 1'in üstü: agresif alıcı ağırlığı.")
        return await send(chat, "\n".join(L))
    if cmd == "/opsiyon":
        coin = _args(rest, "24h")[0]
        o = await analysis.options(coin)
        if not o: return await send(chat, f"{coin} için opsiyon verisi yok (yalnız BTC, ETH).")
        L = [f"<b>{coin} opsiyonlar (Deribit)</b> · OI {fmt.usd(o['oi_usd'])} · put/call {o['put_call']:.2f} · DVOL {o['dvol'] or 0:.1f} (24s önce {o['dvol_24h_ago'] or 0:.1f})"]
        for e in o["expiries"]:
            L.append(f"{e['expiry']}: OI {fmt.usd(e['oi_usd'])} · max pain ${e['max_pain']:,.0f} · P/C {e['put_call']:.2f}")
            if "max_pain_payout_usd" in e:
                L.append(f"  Endekse uzaklık {fmt.pct(e['max_pain_distance_pct'])} · minimum ödeme {fmt.usd(e['max_pain_payout_usd'])}")
                L.append(f"  GEX / %1: brüt {fmt.usd(e['gex_gross_usd_1pct'])} · imzalı tahmin {fmt.usd(e['gex_signed_proxy_usd_1pct'])}"
                         f" · model OI kapsamı %{e['greeks_oi_coverage_pct']:.0f}")
                if e["top_strike_gex"]:
                    L.append("  GEX seviyeleri: " + ", ".join(f"${x['strike']:,.0f} {fmt.usd(x['signed_gex_usd_1pct'])}"
                                                              for x in e["top_strike_gex"][:3]))
        for item in o.get("exchange_greeks", [])[:3]:
            g = item["greeks"]
            vals = " ".join(f"{key} {g[key]:.4g}" for key in ("delta", "gamma", "vega", "theta", "rho") if g.get(key) is not None)
            L.append(f"{fmt.escape(item['instrument'])}: {vals}")
        if o.get("gex_method"):
            L.append("İmzalı GEX, call pozitif / put negatif modelidir. Piyasa yapıcı pozisyonları bilinmiyor.")
        return await send(chat, "\n".join(L))
    if cmd == "/altlar":
        rows = await analysis.alts("BTC")
        L = ["<b>Altcoinler · BTC'ye göre (7 gün, saatlik)</b>", "coin · korelasyon · beta · 24s"]
        L += [f"{r['coin']}: {r['corr']:.2f} · {r['beta']:.2f} · {fmt.pct(r['chg_24h_pct'])}" for r in rows if r["corr"] is not None]
        L.append(f"BTC 24s {fmt.pct(rows[0]['base_chg_24h_pct']) if rows else '—'}. Beta 1'in üstü: BTC hareketini büyüterek izliyor.")
        return await send(chat, "\n".join(L))
    if cmd == "/durum":
        now = time.time()
        L = ["<b>Veri akışları</b>"] + [f"{'✅' if now - h['last_msg'] < 180 else '⚠️'} {n}: son mesaj {int(now - h['last_msg'])} sn önce · yeniden bağlanma {h['reconnects']}" for n, h in sorted(state.stream_health.items())]
        return await send(chat, "\n".join(L))
    await send(chat, "Bilinmeyen komut. /yardim yazabilirsin.")

async def poll():
    if not config.TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN yok, bot kapalı"); return
    offset = None
    s = await http.session()
    async with s.post(f"{API}/deleteWebhook") as r: await r.read()
    while True:
        try:
            async with s.get(f"{API}/getUpdates", params={"timeout": 50, **({"offset": offset} if offset else {})}, timeout=aiohttp.ClientTimeout(total=70)) as r:
                data = await r.json()
            for u in data.get("result", []):
                offset = u["update_id"] + 1
                m = u.get("message") or u.get("channel_post")
                if not m or not m.get("text", "").startswith("/"): continue
                chat = str(m["chat"]["id"])
                if config.TELEGRAM_CHAT_IDS and chat not in config.TELEGRAM_CHAT_IDS:
                    continue
                if not config.TELEGRAM_CHAT_IDS:
                    await send(chat, f"Bu sohbetin kimliği: <code>{chat}</code>\nRailway'de TELEGRAM_CHAT_ID değişkenine bunu yaz; bot o zaman komutlara ve alarmlara açılır.")
                    continue
                asyncio.create_task(_safe_handle(chat, m["text"]))
        except Exception as e:
            log.warning("getUpdates hatası: %s", e); await asyncio.sleep(5)

async def _safe_handle(chat, text):
    try:
        await handle(chat, text)
    except Exception as e:
        log.exception("komut hatası")
        await send(chat, f"Komut çalıştırılamadı: {fmt.escape(str(e))[:300]}")

async def broadcast(text):
    for chat in config.TELEGRAM_CHAT_IDS: await send(chat, text)

async def alerts_loop():
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_IDS and config.ALERTS_ENABLED): return
    last_cascade = {}
    while True:
        await asyncio.sleep(60)
        items = []
        while state.alerts: items.append(state.alerts.popleft())
        now = time.time()
        for c in config.COINS:
            tot = sum(u for t, cc, u, _ in state.recent_liq if cc == c and now - t < 300)
            if tot >= config.ALERT_CASCADE_USD * config.alert_scale(c) and now - last_cascade.get(c, 0) > 900:
                last_cascade[c] = now
                lg = sum(u for t, cc, u, sd in state.recent_liq if cc == c and now - t < 300 and sd == "long")
                items.append({"type": "cascade", "coin": c, "usd": tot, "long": lg, "short": tot - lg})
        if not items: continue
        L = ["<b>🗼 Alarm</b>"]
        for a in items[:20]:
            if a["type"] == "liq":
                L.append(f"{'🚫' if a['side']=='long' else '💸'} {a['coin']} {a['venue']} {a['side']} likidasyonu {fmt.usd(a['usd'])} @ ${a['price']:,.0f}")
            elif a["type"] == "whale":
                L.append(f"🐋 {a['coin']} {a['venue']} {a['market']} {'alım' if a['side']=='buy' else 'satım'} {fmt.usd(a['usd'])} @ ${a['price']:,.0f}")
            else:
                L.append(f"🌊 {a['coin']} likidasyon dalgası (5 dk): {fmt.usd(a['usd'])} · long {fmt.usd(a['long'])} / short {fmt.usd(a['short'])}")
        await broadcast("\n".join(L))

async def reports_loop():
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_IDS and config.REPORT_HOURS_UTC): return
    sent = None
    while True:
        await asyncio.sleep(30)
        now = datetime.now(timezone.utc)
        key = (now.date(), now.hour)
        if now.hour in config.REPORT_HOURS_UTC and now.minute < 5 and sent != key:
            sent = key
            try: await broadcast(fmt.analysis_text(await analysis.analysis("BTC")))
            except Exception as e: log.warning("rapor hatası: %s", e)

async def options_alerts():
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_IDS and config.ALERTS_ENABLED):
        return
    seen = {}
    while True:
        async with db.pool.acquire() as conn:
            for coin in (c for c in config.COINS if c in ("BTC", "ETH")):
                rows = await conn.fetch(
                    "SELECT ts, data FROM options_snap WHERE coin=$1 AND ts > now() - interval '45 minutes' ORDER BY ts DESC LIMIT 2",
                    coin,
                )
                if not rows:
                    continue
                previous = seen.get(coin)
                seen[coin] = rows[0]["ts"]
                if previous is None or previous >= rows[0]["ts"] or len(rows) < 2:
                    continue
                current, old = (json.loads(row["data"]) for row in rows)
                notices = []
                if current.get("dvol") is not None and old.get("dvol") is not None:
                    change = current["dvol"] - old["dvol"]
                    if abs(change) >= config.OPTIONS_DVOL_CHANGE:
                        notices.append(f"DVOL {change:+.1f} puan → {current['dvol']:.1f}")
                prior = {e["expiry"]: e for e in old["expiries"]}
                for e in current["expiries"]:
                    p = prior.get(e["expiry"])
                    if not p:
                        continue
                    change = (e["max_pain"] / p["max_pain"] - 1) * 100
                    if abs(change) >= config.OPTIONS_MAX_PAIN_CHANGE_PCT:
                        notices.append(f"{e['expiry']} max pain: ${p['max_pain']:,.0f} → ${e['max_pain']:,.0f} ({change:+.1f}%)")
                    a, b = e.get("gex_signed_proxy_usd_1pct"), p.get("gex_signed_proxy_usd_1pct")
                    if (a is not None and b is not None and a * b < 0
                            and min(abs(a), abs(b)) >= config.OPTIONS_GEX_FLIP_MIN_USD):
                        notices.append(f"{e['expiry']} imzalı GEX tahmini: {fmt.usd(b)} → {fmt.usd(a)} / %1")
                if notices:
                    await broadcast(f"<b>🗼 {coin} opsiyon alarmı · Deribit</b>\n" + "\n".join(notices))
        await asyncio.sleep(60)


ALL = [poll, alerts_loop, reports_loop, deliver_card, options_alerts]
