"""Telegram metin biçimleri (HTML)."""
from html import escape  # noqa: F401  (telegram modülü de kullanır)

def usd(v):
    if v is None: return "—"
    a = abs(v); s = "-" if v < 0 else ""
    return f"{s}${a/1e9:.2f}B" if a >= 1e9 else f"{s}${a/1e6:.1f}M" if a >= 1e6 else f"{s}${a/1e3:.0f}K" if a >= 1e3 else f"{s}${a:.0f}"

def pct(v, d=2): return "—" if v is None else f"{v:+.{d}f}%"

def coverage_note(window_s):
    import time
    from . import config
    up = time.time() - config.STARTED_AT
    return f" (veri {int(up // 60)} dk)" if up < window_s else ""

def analysis_text(a):
    d = a["derivatives"]; v = d["venues"]
    L = ["<b>%s analizi</b> · %s UTC" % (a["coin"], a["generated_utc"][11:16]),
         f"Fiyat <b>${a['price']:,.2f}</b> · 1s {pct(a['price_change_pct']['1h'])} · 4s {pct(a['price_change_pct']['4h'])} · 24s {pct(a['price_change_pct']['24h'])}", ""]
    L.append("<b>Özet</b>")
    L += ["• " + escape(x) for x in a["summary"]]
    L += ["", f"<b>Open interest</b> toplam {usd(d['oi_total_usd'])} · " + " · ".join(f"{w} {pct(x)}" for w, x in d["oi_change_pct"].items())]
    L.append(" ".join(f"{k}: {usd(x['oi_usd'])}" for k, x in sorted(v.items(), key=lambda kv: -(kv[1]['oi_usd'] or 0))))
    ls = []
    if "binance" in v:
        b = v["binance"]
        if "ls_global" in b: ls.append(f"Binance hesap {b['ls_global']:.2f}")
        if "ls_top_position" in b: ls.append(f"Binance top pozisyon {b['ls_top_position']:.2f}")
    for k in ("bybit", "okx"):
        if k in v and "ls_account" in v[k]: ls.append(f"{k.capitalize()} {v[k]['ls_account']:.2f}")
    if ls: L += ["", "<b>Long/short</b> " + " · ".join(ls)]
    L += ["", "<b>Likidasyon / balina / CVD</b>" + (" — sistem yeni başladı, uzun pencereler dolana kadar aynı veriyi gösterir" if coverage_note(86400) else "")]
    for w, ws_ in (("1h", 3600), ("4h", 14400), ("24h", 86400)):
        f = a["flows"][w]; lq = f["liquidations"]; wh = f["whales"]; cv = f["cvd"]
        L.append(f"{w}{coverage_note(ws_)}: liq L {usd(lq['long'])} / S {usd(lq['short'])} · balina net {usd(wh['net_usd'])} · CVD spot {usd(cv.get('spot', {}).get('delta'))} perp {usd(cv.get('perp', {}).get('delta'))}")
    return "\n".join(L)

HELP = """<b>IMPERION Market Data</b>
/btc · /eth · /sol — tam analiz
/analiz COIN — tam analiz
/isi [COIN] [1s|4s|24s|7g|30g] — tahmini likidasyon ısı haritası
/likidasyon [COIN] [1s|4s|24s|7g] — gerçekleşen likidasyonlar
/balina [COIN] [1s|4s|24s] — büyük işlemler
/oi [COIN] — borsa bazında OI ve funding
/ls [COIN] — long/short oranları
/opsiyon [COIN] — Deribit vadeleri, max pain
/altlar — altcoinlerin BTC ile korelasyonu ve betası
/durum — veri akışlarının sağlığı
Veriler bilgilendirme amaçlıdır, yatırım tavsiyesi değildir."""
