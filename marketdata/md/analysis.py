"""BTC odaklı çoklu borsa analizi: fiyat, OI, funding, long/short, likidasyon, balina akışı, CVD, Coinbase primi, opsiyon, altcoin uyumu."""
import asyncio, json, math, time
from datetime import datetime, timedelta, timezone
from . import config, db, heatmap, http, state

UTC = timezone.utc
BN = "https://fapi.binance.com"
WIN = {"1h": 3600, "4h": 14400, "24h": 86400}

async def price_changes(coin):
    k = await http.get(f"{BN}/fapi/v1/klines", {"symbol": coin + "USDT", "interval": "1h", "limit": 25})
    last = float(k[-1][4])
    return last, {w: (last / float(k[-1 - h][4]) - 1) * 100 for w, h in (("1h", 1), ("4h", 4), ("24h", 24))}

async def deriv(coin):
    async with db.pool.acquire() as c:
        latest = await c.fetch("SELECT DISTINCT ON (venue) venue, ts, price, oi_usd, funding_8h, extra FROM deriv_snap WHERE coin=$1 AND ts > now() - interval '30 minutes' ORDER BY venue, ts DESC", coin)
        past = {}
        for w, s in WIN.items():
            past[w] = await c.fetch("SELECT DISTINCT ON (venue) venue, oi_usd FROM deriv_snap WHERE coin=$1 AND ts <= now() - $2::interval AND ts > now() - $2::interval - interval '30 minutes' ORDER BY venue, ts DESC", coin, timedelta(seconds=s))
    venues = {r["venue"]: {"price": r["price"], "oi_usd": r["oi_usd"], "funding_8h": r["funding_8h"], **json.loads(r["extra"] or "{}")} for r in latest}
    tot = sum(v["oi_usd"] or 0 for v in venues.values())
    oi_chg = {}
    for w, rows in past.items():
        prev = {r["venue"]: r["oi_usd"] for r in rows}
        common = [v for v in venues if v in prev and prev[v]]
        if common:
            a = sum(venues[v]["oi_usd"] for v in common); b = sum(prev[v] for v in common)
            oi_chg[w] = (a / b - 1) * 100
    fw = sum((v["funding_8h"] or 0) * (v["oi_usd"] or 0) for v in venues.values()) / tot if tot else None
    return {"venues": venues, "oi_total_usd": tot, "oi_change_pct": oi_chg,
            "funding_8h_weighted": fw, "funding_ann_pct": fw * 3 * 365 * 100 if fw is not None else None}

async def flows(coin):
    out = {}
    async with db.pool.acquire() as c:
        for w, s in WIN.items():
            iv = timedelta(seconds=s)
            liq = await c.fetch("SELECT venue, side, sum(usd) usd, count(*) n FROM liquidations WHERE coin=$1 AND ts > now() - $2::interval GROUP BY venue, side", coin, iv)
            cvd = await c.fetch("SELECT market, sum(buy_usd) b, sum(sell_usd) s FROM trades_1m WHERE coin=$1 AND ts > now() - $2::interval GROUP BY market", coin, iv)
            wh = await c.fetch("SELECT side, sum(usd) usd, count(*) n FROM whale_trades WHERE coin=$1 AND ts > now() - $2::interval GROUP BY side", coin, iv)
            L = {"long": 0.0, "short": 0.0, "by_venue": {}}
            for r in liq:
                L[r["side"]] += r["usd"]; L["by_venue"].setdefault(r["venue"], {})[r["side"]] = r["usd"]
            C = {r["market"]: {"buy": r["b"], "sell": r["s"], "delta": r["b"] - r["s"]} for r in cvd}
            W = {r["side"]: {"usd": r["usd"], "n": r["n"]} for r in wh}
            out[w] = {"liquidations": L, "cvd": C, "whales": {"buy_usd": W.get("buy", {}).get("usd", 0), "sell_usd": W.get("sell", {}).get("usd", 0),
                                                            "buy_n": W.get("buy", {}).get("n", 0), "sell_n": W.get("sell", {}).get("n", 0),
                                                            "net_usd": W.get("buy", {}).get("usd", 0) - W.get("sell", {}).get("usd", 0)}}
    return out

def coinbase_premium(coin):
    cb = state.last_price.get(("coinbase", "spot", coin)); bn = state.last_price.get(("binance", "spot", coin))
    if not cb or not bn or time.time() - cb[1] > 300 or time.time() - bn[1] > 300: return None
    return (cb[0] / bn[0] - 1) * 100

async def options(coin):
    async with db.pool.acquire() as c:
        r = await c.fetchrow("SELECT ts, data FROM options_snap WHERE coin=$1 ORDER BY ts DESC LIMIT 1", coin)
    return json.loads(r["data"]) if r else None

async def alts(base="BTC", hours=168):
    async def build():
        async def k(sym):
            try:
                r = await http.get(f"{BN}/fapi/v1/klines", {"symbol": sym, "interval": "1h", "limit": hours + 1})
                return [float(x[4]) for x in r]
            except Exception:
                return None
        syms = [base] + [a for a in config.ALTS if a != base]
        names = {a: ("1000PEPEUSDT" if a == "PEPE" else a + "USDT") for a in syms}
        closes = dict(zip(syms, await asyncio.gather(*[k(names[a]) for a in syms])))
        b = closes[base]; rb = [b[i] / b[i - 1] - 1 for i in range(1, len(b))]
        out = []
        for a in syms[1:]:
            c = closes.get(a)
            if not c or len(c) != len(b): continue
            ra = [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
            mb, ma = sum(rb) / len(rb), sum(ra) / len(ra)
            cov = sum((x - mb) * (y - ma) for x, y in zip(rb, ra)) / len(rb)
            vb = sum((x - mb) ** 2 for x in rb) / len(rb); va = sum((y - ma) ** 2 for y in ra) / len(ra)
            out.append({"coin": a, "corr": cov / math.sqrt(vb * va) if vb and va else None, "beta": cov / vb if vb else None,
                        "chg_24h_pct": (c[-1] / c[-25] - 1) * 100, "base_chg_24h_pct": (b[-1] / b[-25] - 1) * 100})
        return sorted(out, key=lambda x: -(x["corr"] or 0))
    return await http.cached(f"alts:{base}:{hours}", 600, build)

def summarize(a):
    """Verileri betimleyen kısa Türkçe maddeler (yorum değil, tavsiye değil)."""
    s = []
    p = a["price_change_pct"]
    s.append(f"Fiyat 1s {p['1h']:+.2f}%, 4s {p['4h']:+.2f}%, 24s {p['24h']:+.2f}%.")
    oc = a["derivatives"]["oi_change_pct"]
    if "4h" in oc:
        d = "artıyor" if oc["4h"] > 0.5 else "azalıyor" if oc["4h"] < -0.5 else "yatay"
        both = (p["4h"] > 0) == (oc["4h"] > 0)
        s.append(f"Toplam OI 4 saatte {oc['4h']:+.2f}% ({d}). " + ("Fiyat ve OI aynı yönde: yeni pozisyon girişi." if both and abs(oc['4h']) > 0.5 else
                 "Fiyat ve OI ters yönde: pozisyon kapanışı ağırlıklı." if abs(oc['4h']) > 0.5 else ""))
    fa = a["derivatives"]["funding_ann_pct"]
    if fa is not None:
        s.append(f"OI ağırlıklı funding yıllık {fa:+.1f}%: " + ("longlar ödüyor, long kalabalığı." if fa > 15 else "shortlar ödüyor, short kalabalığı." if fa < -5 else "nötre yakın."))
    f4 = a["flows"]["4h"]
    L = f4["liquidations"]
    if L["long"] + L["short"] > 0:
        s.append(f"Son 4 saatte likidasyon: long ${L['long']/1e6:.1f}M, short ${L['short']/1e6:.1f}M.")
    w = f4["whales"]
    s.append(f"Balina işlemleri (4s): alım ${w['buy_usd']/1e6:.1f}M, satım ${w['sell_usd']/1e6:.1f}M, net ${w['net_usd']/1e6:+.1f}M.")
    for m, lbl in (("spot", "Spot"), ("perp", "Perp")):
        if m in f4["cvd"]: s.append(f"{lbl} CVD (4s): ${f4['cvd'][m]['delta']/1e6:+.1f}M.")
    if a.get("coinbase_premium_pct") is not None:
        cp = a["coinbase_premium_pct"]; s.append(f"Coinbase primi {cp:+.3f}% " + ("(ABD tarafında alım iştahı)." if cp > 0.03 else "(ABD tarafında satış baskısı)." if cp < -0.03 else "(nötr)."))
    h = a.get("heatmap_24h")
    if h and h.get("clusters"):
        lo = min([c for c in h["clusters"] if c["side"] == "long"], key=lambda c: abs(c["dist_pct"]), default=None)
        hi = min([c for c in h["clusters"] if c["side"] == "short"], key=lambda c: abs(c["dist_pct"]), default=None)
        if lo and hi: s.append(f"En yakın büyük likidasyon kümeleri: aşağıda ${lo['price']:,.0f} ({lo['dist_pct']:+.1f}%), yukarıda ${hi['price']:,.0f} ({hi['dist_pct']:+.1f}%).")
    o = a.get("options")
    if o and o.get("expiries"):
        e = o["expiries"][0]; s.append(f"En yakın büyük opsiyon vadesi {e['expiry']}: max pain ${e['max_pain']:,.0f}, put/call {e['put_call']:.2f}. DVOL {o.get('dvol') or 0:.1f}.")
    return s

async def analysis(coin="BTC"):
    async def build():
        (price, chg), der, fl, opt, hm = await asyncio.gather(price_changes(coin), deriv(coin), flows(coin), options(coin), heatmap.model(coin, "24h"))
        a = {"coin": coin, "generated_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"), "price": price, "price_change_pct": chg,
             "derivatives": der, "flows": fl, "coinbase_premium_pct": coinbase_premium(coin), "options": opt,
             "heatmap_24h": {"clusters": hm["clusters"], "price": hm["price"]}}
        a["summary"] = summarize(a)
        return a
    return await http.cached(f"analysis:{coin}", 30, build)
