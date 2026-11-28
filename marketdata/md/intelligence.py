"""İşlem öncesi araştırma: ölçüm, yorum, karşı kanıt ve eksikler aynı raporda."""
import asyncio
import statistics
import time
from datetime import datetime, timedelta, timezone
from . import analysis, public_context, candles, config, context, db, evidence, http, ingest, livebook, orderbook, observe, quality, state, whales

UTC = timezone.utc
WINDOWS = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "24h": 1440}


def ema(xs, period):
    if len(xs) < period:
        return None
    value = statistics.fmean(xs[:period])
    for x in xs[period:]:
        value += 2/(period+1)*(x-value)
    return value


def technical(frame):
    bars = frame["rows"]
    closes = [b["close"] for b in bars]
    last = bars[-1]
    diffs = [b-a for a, b in zip(closes, closes[1:])]
    gain = statistics.fmean(max(d, 0) for d in diffs[:14])
    loss = statistics.fmean(max(-d, 0) for d in diffs[:14])
    for d in diffs[14:]:
        gain, loss = (13*gain+max(d, 0))/14, (13*loss+max(-d, 0))/14
    rsi = 100-100/(1+gain/loss) if loss else 100 if gain else 50
    tr = [max(b["high"]-b["low"], abs(b["high"]-a["close"]), abs(b["low"]-a["close"])) for a, b in zip(bars, bars[1:])]
    atr = statistics.fmean(tr[:14])
    for value in tr[14:]:
        atr = (13*atr+value)/14
    e20, e50, e200 = (ema(closes, p) for p in (20, 50, 200))
    if e20 is None or e50 is None:
        direction = "yetersiz geçmiş"
    else:
        direction = "yükseliş" if last["close"] > e20 > e50 else "düşüş" if last["close"] < e20 < e50 else "karışık / yatay"
    period = candles.INTERVALS[frame["interval"]][0]
    vwap_bars = bars[-max(1, 86400//period):]
    volume = sum(b["volume"] for b in vwap_bars)
    vwap = sum((b["high"]+b["low"]+b["close"])/3*b["volume"] for b in vwap_bars)/volume if volume else None
    prior = bars[-21:-1]
    high, low = max(b["high"] for b in prior), min(b["low"] for b in prior)
    return {"source": frame["source"], "fallback": frame["fallback"], "gaps": frame["gaps"],
            "closed_utc": datetime.fromtimestamp(last["close_ts"], UTC).isoformat(),
            "close": last["close"], "regime": direction, "rsi14": rsi, "atr14": atr,
            "atr_pct": atr/last["close"]*100, "ema20": e20, "ema50": e50, "ema200": e200,
            "vwap_24h_approx": vwap, "vwap_method": "closed-candle typical price weighted by base volume; not tick VWAP",
            "prior_20_high": high, "prior_20_low": low,
            "range_position": (last["close"]-low)/(high-low) if high > low else None,
            "volume_vs_20": last["volume"]/statistics.fmean(b["volume"] for b in prior) if sum(b["volume"] for b in prior) else None,
            "change_4bars_pct": (closes[-1]/closes[-5]-1)*100,
            "candles": bars[-100:]}


async def flow_matrix(coin):
    end = datetime.now(UTC).replace(second=0, microsecond=0)
    result = {}
    async with db.pool.acquire() as conn:
        for name, minutes in WINDOWS.items():
            rows = await conn.fetch("""SELECT venue,market,count(*) minutes,sum(buy_usd) buy,
                sum(sell_usd) sell,sum(n) trades,max(ts) last_minute,
                (array_agg(open ORDER BY ts))[1] open,(array_agg(close ORDER BY ts DESC))[1] close
                FROM trades_1m WHERE coin=$1 AND ts >= $2 AND ts < $3 GROUP BY venue,market""",
                coin, end-timedelta(minutes=minutes), end)
            lookup = {(r["venue"], r["market"]): r for r in rows}
            sources = []
            for venue, market in quality.TRADE_SOURCES:
                r = lookup.get((venue, market))
                fresh = r is not None and (end-r["last_minute"]).total_seconds() <= 120
                covered = r["minutes"]/minutes if r else 0
                sources.append({"venue": venue, "market": market,
                                "coverage": covered, "observed_minutes": r["minutes"] if r else 0,
                                "comparable": bool(fresh and covered >= .8),
                                "last_minute_utc": r["last_minute"].isoformat() if r else None,
                                "buy_usd": r["buy"] if r else None, "sell_usd": r["sell"] if r else None,
                                "delta_usd": r["buy"]-r["sell"] if r else None,
                                "price_change_pct": (r["close"]/r["open"]-1)*100 if r and r["open"] else None})
            result[name] = {"start_utc": (end-timedelta(minutes=minutes)).isoformat(), "end_utc": end.isoformat(),
                            "sources": sources, "markets": {m: combine_flow([r for r in sources if r["market"] == m]) for m in ("spot", "perp")}}
    return result


def combine_flow(rows):
    good = [r for r in rows if r["comparable"]]
    total = sum(r["buy_usd"]+r["sell_usd"] for r in good)
    delta = sum(r["delta_usd"] for r in good)
    direction = 1 if delta > 0 else -1 if delta < 0 else 0
    changes = [r["price_change_pct"] for r in good if r["price_change_pct"] is not None]
    return {"comparable_sources": len(good), "expected_sources": len(rows),
            "delta_usd": delta if good else None, "total_usd": total if good else None,
            "delta_fraction": delta/total if total else None,
            "direction_agreement": sum(r["delta_usd"]*direction > 0 for r in good)/len(good) if good and direction else None,
            "largest_venue_share": max((r["buy_usd"]+r["sell_usd"] for r in good), default=0)/total if total else None,
            "median_price_change_pct": statistics.median(changes) if changes else None,
            "absorption_candidate": bool(total and changes and abs(delta/total) > .2 and abs(statistics.median(changes)) < .1),
            "method": "Only sources with >=80% observed closed minutes and recent data. A candidate is not proof of absorption."}


def consensus(coin):
    now = time.time()
    rows = [{"venue": v, "market": m, "price": p, "age_seconds": now-ts}
            for (v, m, c), (p, ts) in state.last_price.items() if c == coin and 0 <= now-ts <= 30]
    markets = {}
    for market in ("spot", "perp"):
        values = [r for r in rows if r["market"] == market]
        median = statistics.median(r["price"] for r in values) if values else None
        markets[market] = {"median": median, "sources": values,
                           "dispersion_bps": (max(r["price"] for r in values)-min(r["price"] for r in values))/median*1e4 if median else None}
    return {"markets": markets, "reference": markets["spot"]["median"] or markets["perp"]["median"],
            "method": "Unweighted last-trade median, max age 30s; USD/USDT not FX adjusted. Not an executable quote."}


async def liquidations(coin):
    async with db.pool.acquire() as conn:
        rows = await conn.fetch("""SELECT venue,side,sum(usd) usd,count(*) events,max(ts) last_event
            FROM liquidations WHERE coin=$1 AND ts>=now()-interval '4 hours' GROUP BY venue,side""", coin)
    return {"window": "4h", "observed": [dict(r, last_event=r["last_event"].isoformat()) for r in rows],
            "streams": {k: {"connected": v["connected"], "message_age_seconds": time.time()-v["last_msg"]}
                        for k, v in state.stream_health.items() if k in ("binance_liq", "bybit", "okx")},
            "method": "Exchange-published liquidation events; exchanges may sample or omit events. Zero observed is not proof of no liquidations."}


def assess(frames, flows, derivatives, books, data_quality):
    observations, contradictions, blockers = [], [], []
    if data_quality["live_sources"] < 4:
        blockers.append("Canlı işlem akışı dört kaynağın altında.")
    if len(frames) < 4 or any(f["gaps"] or f["regime"] == "yetersiz geçmiş" for f in frames.values()):
        blockers.append("Kapalı mumlarda eksik zaman dilimi veya boşluk var.")
    short, long = frames.get("1h"), frames.get("4h")
    if short and long:
        observations.append(f"1 saatlik yapı {short['regime']}; 4 saatlik yapı {long['regime']}.")
        if short["regime"] != long["regime"]:
            contradictions.append("1 saat ve 4 saat yön yapısı uyumlu değil.")
    m = flows.get("15m", {}).get("markets", {})
    spot, perp = m.get("spot", {}), m.get("perp", {})
    for market, title in ((spot, "Spot"), (perp, "Vadeli")):
        if market.get("comparable_sources", 0) < 2:
            blockers.append(f"{title}: 15 dk penceresinde en az iki yeterli kapsamlı kaynak yok.")
        if market.get("delta_fraction") is not None:
            observations.append(f"{title} agresif alım-satım dengesi %{market['delta_fraction']*100:+.1f}; {market['comparable_sources']} karşılaştırılabilir kaynak.")
        if market.get("absorption_candidate"):
            contradictions.append(f"{title} akış güçlü, fiyat tepkisi sınırlı: olası absorpsiyon; ek teyit gerekir.")
    if spot.get("delta_usd") is not None and perp.get("delta_usd") is not None and spot["delta_usd"]*perp["delta_usd"] < 0:
        contradictions.append("Spot ile vadeli işlem deltası ters yönlü.")
    venues = derivatives.get("venues", {})
    fresh = [v for v in venues.values() if v.get("age_seconds", 9999) <= 420 and v.get("oi_usd") is not None and v.get("funding_8h") is not None]
    if len(fresh) < 3:
        blockers.append("Üç borsadan güncel OI/funding karşılaştırması yok.")
    fw = derivatives.get("funding_8h_weighted")
    if fw is not None:
        observations.append(f"OI ağırlıklı 8 saatlik fonlama %{fw*100:+.4f}; pozitifse long tarafı ödeme yapar.")
    oi = derivatives.get("oi_base_proxy_change", {}).get("4h", {}).get("pct")
    if oi is not None:
        observations.append(f"Fiyat etkisi yaklaşık ayrıştırılmış OI 4 saatte %{oi:+.2f}; long/short sahibi bu toplamdan belirlenemez.")
    if books.get("live_sources", 0) < 2:
        blockers.append("İki güncel canlı L2 defteri yok; anlık işlem maliyeti eksik.")
    return {"state": "VERİ YETERSİZ" if blockers else "ÇELİŞKİLER VAR" if contradictions else "İNCELEME HAZIR",
            "observations": observations, "counter_evidence": contradictions, "blockers": blockers,
            "meaning": "Readiness of the research data, not permission or a signal to enter a trade.",
            "signal": None, "automatic_execution": False}


def _structure(frame):
    if not frame:
        return None
    close, high, low = frame["close"], frame["prior_20_high"], frame["prior_20_low"]
    if close > high:
        place = "üst kırılım"
    elif close < low:
        place = "alt kırılım"
    else:
        place = "aralık"
    return {"regime": frame["regime"], "structure": place, "close": close, "prior_20_high": high, "prior_20_low": low}


def _slip(source, size, side):
    cell = ((source.get("slippage") or {}).get(str(size)) or {}).get(side) or {}
    return cell.get("slippage_bps")


def build_dossier(coin, frames, flows, derivatives, books, assessment, liquidations_doc, options):
    """Tek okuma belgesi. Skor, hedef ve emir içermez."""
    watch = {"VERİ YETERSİZ": "BAKMA", "ÇELİŞKİLER VAR": "BEKLE"}.get(assessment["state"], "BAKILIR")
    place = {name: _structure(frames.get(name)) for name in ("15m", "1h", "4h")}
    flow = {}
    for name in ("15m", "1h", "4h"):
        markets = (flows.get(name) or {}).get("markets") or {}
        spot, perp = markets.get("spot") or {}, markets.get("perp") or {}
        spot_delta, perp_delta = spot.get("delta_usd"), perp.get("delta_usd")
        flow[name] = {"spot_delta_usd": spot_delta, "perp_delta_usd": perp_delta,
                      "same_direction": spot_delta is not None and perp_delta is not None and spot_delta * perp_delta > 0}
    cost = []
    for source in books.get("sources") or []:
        if source.get("status") != "live":
            continue
        cost.append({"venue": source["venue"], "market": source["market"], "spread_bps": source.get("spread_bps"),
                     "buy_10k_bps": _slip(source, 10000, "buy"), "sell_10k_bps": _slip(source, 10000, "sell"),
                     "buy_100k_bps": _slip(source, 100000, "buy"), "sell_100k_bps": _slip(source, 100000, "sell")})
    venues = derivatives.get("venues") or {}
    fresh = [v for v in venues.values() if v.get("funding_8h") is not None and v.get("oi_usd") is not None and v.get("age_seconds", 9999) <= 420]
    funding_age = max((v["age_seconds"] for v in fresh), default=None)
    rows = (liquidations_doc or {}).get("observed") or []
    long_usd = sum(r["usd"] for r in rows if r["side"] == "long")
    short_usd = sum(r["usd"] for r in rows if r["side"] == "short")
    four = place.get("4h") or {}
    spot15 = (flow.get("15m") or {}).get("spot_delta_usd")
    bits = [watch, coin]
    if four.get("structure"):
        bits.append("4s " + four["structure"])
    if spot15 is not None:
        bits.append(f"15dk spot delta {spot15:+.0f} USD")
    if funding_age is not None and funding_age > 180:
        bits.append(f"funding yaşı {funding_age}s")
    options_block = None
    if coin in ("BTC", "ETH") and options:
        options_block = {"stale": options.get("stale"), "age_seconds": options.get("age_seconds"),
                         "snapshot_utc": options.get("snapshot_utc")}
    return {"watch": watch, "state": assessment["state"], "headline_tr": " · ".join(bits),
            "structure": place, "flow": flow, "cost": cost,
            "crowd": {"funding_8h_weighted": derivatives.get("funding_8h_weighted"),
                      "oi_total_usd": derivatives.get("oi_total_usd"),
                      "oi_change_4h_pct": (derivatives.get("oi_change_pct") or {}).get("4h"),
                      "funding_age_seconds": funding_age,
                      "live": funding_age is not None and funding_age <= 180},
            "realized_liquidations": {"window": "4h", "kind": "observed_liquidation_prints",
                                      "long_usd": long_usd, "short_usd": short_usd,
                                      "text": "4 saatte bildirilen long ve short likidasyonlar. Tahmini ısı haritası bu toplama dahil değildir."},
            "liquidation_model": {"included": False, "note": "Tahmini ısı haritası bu belgede yoktur. /api/heatmap varsayılan kaldıraç modelidir."},
            "options": options_block,
            "counter_evidence": assessment["counter_evidence"], "blockers": assessment["blockers"],
            "signal": None, "automatic_execution": False}


async def generate(coin="BTC"):
    async def build():
        start = time.time()
        ctx_task = asyncio.create_task(public_context.load(coin))
        jobs = {**{f"candles_{w}": candles.load(coin, w) for w in candles.INTERVALS},
                "flows": flow_matrix(coin), "derivatives": analysis.deriv(coin),
                "options": analysis.options(coin), "depth": orderbook.latest(coin),
                "whales": whales.report(coin, "15m", limit=15), "liquidations": liquidations(coin),
                "breadth": context.breadth(), "history": candles.load(coin, "1h", 1000)}
        results = await asyncio.gather(*(asyncio.wait_for(task, 35) for task in jobs.values()), return_exceptions=True)
        try:
            pc = await asyncio.wait_for(ctx_task, 4)
        except Exception as exc:
            pc = exc
        if not ctx_task.done():
            ctx_task.cancel()
        parts = dict(zip(jobs, results))
        provenance = {name: {"status": "error" if isinstance(value, Exception) else "missing" if value is None else "available",
                             "error": type(value).__name__ if isinstance(value, Exception) else None} for name, value in parts.items()}
        if isinstance(pc, Exception):
            provenance["public_context"] = {"status": "error", "error": type(pc).__name__}
        elif pc is None:
            provenance["public_context"] = {"status": "missing", "error": None}
        else:
            provenance["public_context"] = {"status": "available", "error": None}
            parts["public_context"] = pc
        parts = {k: v for k, v in parts.items() if v is not None and not isinstance(v, Exception)}
        frames = {w: technical(parts[f"candles_{w}"]) for w in candles.INTERVALS if f"candles_{w}" in parts}
        dq = quality.snapshot(coin)
        dq["queue_size"] = ingest.QUEUE.qsize()
        commit = quality.counters.get("last_commit_epoch")
        dq["last_commit_age_seconds"] = time.time()-commit if commit else None
        books = livebook.snapshot(coin)
        der, flows = parts.get("derivatives", {}), parts.get("flows", {})
        feeds = {**context.external_feeds(), **parts.get("public_context", {})}
        if "public_context" not in parts:
            feeds.update({key: {"status": "unavailable", "source": labels[0]} for key, labels in public_context.FEEDS.items()})
        elif any(feeds[k].get("status") != "available" for k in public_context.FEEDS):
            provenance["public_context"]["status"] = "partial"
        result = {"schema_version": "2.1", "coin": coin, "generated_utc": datetime.now(UTC).isoformat(),
                  "assembly_seconds": round(time.time()-start, 2), "source_status": provenance,
                  "price_consensus": consensus(coin), "quality": dq, "timeframes": frames,
                  "flows": flows, "derivatives": der, "live_orderbooks": books,
                  "large_trades": parts.get("whales"), "liquidations": parts.get("liquidations"),
                  "sampled_depth": parts.get("depth"), "options": parts.get("options"),
                  "breadth": parts.get("breadth"), "external_context": feeds, "context_summary_tr": public_context.summary(feeds),
                  "historical_evidence": evidence.evaluate(parts["history"]["rows"]) if "history" in parts else {"status": "unavailable"},
                  "assessment": assess(frames, flows, der, books, dq)}
        for key in ("options", "depth"):
            value = parts.get(key)
            if value and value.get("stale"):
                result["source_status"][key]["status"] = "stale"
        h1 = frames.get("1h")
        result["watch_conditions"] = [] if not h1 else [
            {"name": "Üst aralık testi", "level": h1["prior_20_high"],
             "confirmation": "Kapalı 1 saatlik mum üst aralık üzerinde, yeterli kapsamlı spot akış aynı yönde ve yeniden test korunuyor.",
             "invalidation": "Kapanış tekrar eski aralık içine döner; yeniden değerlendirilir.", "status": "manual_confirmation_required"},
            {"name": "Alt aralık testi", "level": h1["prior_20_low"],
             "confirmation": "Kapalı 1 saatlik mum alt aralık altında, yeterli kapsamlı spot satış akışı ve başarısız yukarı yeniden test.",
             "invalidation": "Kapanış tekrar eski aralık içine döner; yeniden değerlendirilir.", "status": "manual_confirmation_required"},
        ]
        if dq["queue_size"] > 10000 or dq["last_commit_age_seconds"] is None or dq["last_commit_age_seconds"] > 10:
            result["assessment"]["blockers"].append("Kalıcı kayıt gecikiyor veya henüz başlamadı.")
            result["assessment"]["state"] = "VERİ YETERSİZ"
        try:
            result["minute_observations"] = await observe.load(coin)
        except Exception:
            result["minute_observations"] = {"status": "unavailable", "windows": {}}
        result["dossier"] = build_dossier(coin, frames, flows, der, books, result["assessment"], result.get("liquidations"), result.get("options"))
        result["limitations"] = [
            "Büyük borsa işlemi, kimliği bilinen balina veya zincir üstü transfer anlamına gelmez.",
            "Emir defteri görünür ve sınırlı derinliktedir; emir azalması iptal/gerçekleşme olarak ayrıştırılamaz.",
            "GEX bayi pozisyonu değildir; işaretli GEX bir varsayımdır. Max pain fiyat hedefi değildir.",
            "Geçmiş benzerlikler canlı başarı olasılığı veya gelecekteki kâr vaadi değildir.",
            "Tahmini likidasyon haritası gözlenen tasfiye değildir ve bu belgenin durumuna girmez.",
            "Her kaynak kendi zaman damgasını taşır; eşzamanlı borsa görüntüsü garantisi yoktur.",
        ]
        return result
    return await http.cached(f"intelligence:{coin}", 10, build)


def flow_risk_text(report):
    from . import fmt
    lines = []
    large = report.get("large_trades")
    if large:
        sources = large["coverage"]["sources"]
        if sources:
            threshold = fmt.usd(large["configured_trade_threshold_usd"])
            lines.append(f"\n<b>Büyük işlemler · 15 dk · eşik {threshold}</b>")
            for market, label in (("spot", "Spot"), ("perp", "Vadeli")):
                observed = [s for s in sources if s["market"] == market]
                if not observed:
                    lines.append(f"{label} · kaydedilmiş işlem dakikası yok.")
                    continue
                groups = [g for g in large["by_venue_market"] if g["market"] == market]
                net = sum(g["net_usd"] for g in groups)
                count = sum(g["buy_count"]+g["sell_count"] for g in groups)
                lines.append(f"{label} · {count} büyük işlem · gözlenen net {fmt.usd(net)} · {len(observed)} kaynak.")
            if large["trades"]:
                t = max(large["trades"], key=lambda row: row["usd"])
                side = "alım" if t["side"] == "buy" else "satım"
                lines.append(f"Son {len(large['trades'])} kaydın en büyüğü {fmt.escape(t['venue'])} {fmt.escape(t['market'])} {side} {fmt.usd(t['usd'])}.")
    liq = report.get("liquidations") or {}
    rows = liq.get("observed", [])
    if rows:
        totals = {side: sum(row["usd"] for row in rows if row["side"] == side) for side in ("long", "short")}
        lines.append(f"4 saatte bildirilen likidasyonlar · long {fmt.usd(totals['long'])}, short {fmt.usd(totals['short'])}.")
    opt = report.get("options") or {}
    if opt.get("expiries"):
        e = opt["expiries"][0]
        lines.append(f"Deribit · {e['expiry']} vadesi · max pain {fmt.usd(e.get('max_pain'))} · işaretli GEX tahmini {fmt.usd(e.get('gex_signed_proxy_usd_1pct'))}/%1 · {opt.get('snapshot_utc', '')[:16]} UTC" + (" · ESKİ" if opt.get("stale") else ""))
    return lines


def text(report):
    from . import fmt
    a = report["assessment"]
    dossier = report.get("dossier") or {}
    lines = [f"<b>IMPERION · {report['coin']} işlem öncesi araştırma</b>",
             f"{report['generated_utc'][:19]} UTC · {a['state']} · {dossier.get('watch', '')}"]
    price = report["price_consensus"]["reference"]
    if price:
        lines.append(f"Borsa medyanı: ${price:,.2f}")
    for name, values in (("Gözlenenler", a["observations"]), ("Karşı kanıt", a["counter_evidence"]), ("Veri engelleri", a["blockers"])):
        if values:
            lines.append(f"\n<b>{name}</b>")
            lines.extend("• "+fmt.escape(v) for v in values)
    q = report["quality"]
    books = report["live_orderbooks"]
    lines.append(f"\nCanlı işlem kaynakları: {q['live_sources']}/{q['expected_sources']}; canlı L2: {books['live_sources']}/{books.get('expected_sources', len(books.get('sources') or []))}.")
    for interval, f in report["timeframes"].items():
        lines.append(f"{interval}: {f['regime']} · RSI {f['rsi14']:.1f} · ATR %{f['atr_pct']:.2f} · aralık {f['prior_20_low']:,.0f}–{f['prior_20_high']:,.0f}")
    lines.extend(flow_risk_text(report))
    ev = report["historical_evidence"]
    lines.append(f"Geçmiş benzer örnek: {ev.get('samples', 0)}; canlı başarı yüzdesi üretilmez.")
    lines.append("\n<b>Piyasa bağlamı</b>")
    lines.extend("• "+fmt.escape(item) for item in report.get("context_summary_tr", []))
    lines.append("İşlem kararı için veri desteği; otomatik emir yok.")
    return "\n".join(lines)


async def archive():
    while True:
        for coin in config.COINS:
            try:
                r = await generate(coin)
                from . import archive_store
                await archive_store.save(r)
            except Exception:
                import logging
                logging.getLogger("intelligence").exception("Araştırma arşivi %s", coin)
        await asyncio.sleep(300)
