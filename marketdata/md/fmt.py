"""Telegram metin biçimleri (HTML)."""
from html import escape  # noqa: F401  (telegram modülü de kullanır)

import re

def strip_html(s):
    from html import unescape
    return unescape(re.sub(r"<[^>]+>", "", s))

def split_message(text, limit=3900):
    """Telegram'ın 4096 karakter sınırı için metni satır sınırlarından parçalara böler."""
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # tek satır çok uzunsa etiketsiz böl
            line = strip_html(line)
            parts.append((cur + "\n" if cur else "") + line[:limit - len(cur) - 1] if cur else line[:limit]); cur = ""
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur: parts.append(cur)
    return parts or [""]

def split_caption(text, limit=1000):
    """Fotoğraf açıklaması (1024 sınırı) için ilk satırları alır, kalanını ayrı mesaj olarak döndürür."""
    if len(text) <= limit: return text, ""
    lines, cap = text.split("\n"), ""
    for i, line in enumerate(lines):
        if len(cap) + len(line) + 1 > limit:
            return cap, "\n".join(lines[i:])
        cap = f"{cap}\n{line}" if cap else line
    return cap, ""

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

def whale_flow_text(report):
    c = report["coverage"]
    lines = [f"<b>{escape(report['coin'])} büyük işlem akışı</b> · {report['window']} · {report['generated_utc'][11:16]} UTC"]
    if report["min_price"] is not None or report["max_price"] is not None:
        lines.append(f"Fiyat bölgesi: {usd(report['min_price'])} – {usd(report['max_price'])}")
    for row in c["sources"]:
        lines.append(f"{escape(row['venue'])} {escape(row['market'])}: {row['observed_minutes']}/{c['expected_minutes']} kapalı dakika")
    if not c["sources"]:
        lines.append("Kapalı dakikalık işlem kaydı yok.")
    for row in report["by_venue_market"]:
        lines.append(f"{escape(row['venue'])} {escape(row['market'])}: alış {usd(row['buy_usd'])} ({row['buy_count']}) · "
                     f"satış {usd(row['sell_usd'])} ({row['sell_count']}) · net {usd(row['net_usd'])}")
    if not report["trades"]:
        lines.append("Seçili aralıkta kaydedilmiş büyük işlem yok.")
    for trade in report["trades"]:
        reaction = " · ".join(f"{w} {pct(v)}" for w, v in trade["reaction_pct"].items())
        side = "alış" if trade["side"] == "buy" else "satış"
        lines.append(f"{trade['ts'][11:19]} {escape(trade['venue'])}/{escape(trade['market'])} "
                     f"{side} {usd(trade['usd'])} @ ${trade['price']:,.2f} · {reaction}")
    lines.append("Tepki: aynı borsanın ilgili dakika kapanışı. —: kapanış kaydı yok.")
    return "\n".join(lines)


HELP = """<b>IMPERION Market Data</b>
/btc · /eth · /sol — tam analiz
/analiz COIN — tam analiz
/isi [COIN] [1s|4s|24s|7g|30g] — tahmini likidasyon ısı haritası
/likidasyon [COIN] [1s|4s|24s|7g] — gerçekleşen likidasyonlar
/akis [COIN] [5m|15m|1h|4h] [alt_fiyat üst_fiyat] — akış ve fiyat tepkisi
/balina [COIN] [1s|4s|24s] — büyük işlemler
/oi [COIN] — borsa bazında OI ve funding
/ls [COIN] — long/short oranları
/opsiyon [COIN] — Deribit vadeleri, max pain, GEX ve Greeks
/kart [COIN] — 4K teknik analiz kartı (canlı); /kart ozet — 1920×1080 özet kartı; /kart arsiv — 26.11 tarihli kart
/derinlik [COIN] — birleşik emir defteri: ±%0,5/1/2/5 derinlik, dengesizlik, duvarlar
/defter [COIN] [1s|4s|24s] — emir defteri ısı haritası (zaman × fiyat)
/cvd [COIN] [4s|24s|7g] — spot ve perp CVD grafiği
/altlar — altcoinlerin BTC ile korelasyonu ve betası
/durum — veri akışlarının sağlığı
Veriler bilgilendirme amaçlıdır, yatırım tavsiyesi değildir."""


def depth_text(coin, d):
    L = [f"<b>{escape(coin)} emir defteri</b> · {d['ts'][11:16]} UTC · orta ${d['mid']:,.2f}",
         "Kaynaklar: " + ", ".join(escape(s) for s in d["sources"]), "", "<b>Derinlik (alış / satış · dengesizlik)</b>"]
    for b, v in d["bands"].items():
        L.append(f"±%{b}: {usd(v['bid_usd'])} / {usd(v['ask_usd'])} · {v['imbalance']:+.2f}")
    L += ["", "<b>Alış duvarları</b>"] + [f"${w['price']:,.2f} ({w['dist_pct']:+.2f}%) {usd(w['usd'])}" for w in d["walls"]["bid"]]
    L += ["", "<b>Satış duvarları</b>"] + [f"${w['price']:,.2f} ({w['dist_pct']:+.2f}%) {usd(w['usd'])}" for w in d["walls"]["ask"]]
    L += ["", "Dengesizlik +1: tamamen alış, −1: tamamen satış. Kapsam: " + ", ".join(f"{escape(k)} ±%{v['depth_pct']:.1f}" for k, v in d["venues"].items())]
    return "\n".join(L)

HELP += "\n/arastirma BTC — Çok katmanlı işlem öncesi araştırma, veri kapsamı ve karşı kanıt\n/kalite BTC — Canlı kaynak ve kayıt sağlığı"
