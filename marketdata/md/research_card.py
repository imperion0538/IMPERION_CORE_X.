"""3840×2160 Türkçe araştırma kartı; aynı raporun grafik ve kanıt görünümü."""
import asyncio
import io
import textwrap
from threading import Lock
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from . import intelligence

LOCK = Lock()


def render(report):
    with LOCK:
        return _render(report)


def _render(r):
    from .card4k import LOGO, tr_num
    fig = plt.figure(figsize=(32, 18), dpi=120, facecolor="#0a1210")
    green, red, ink, muted = "#16a66c", "#d1495b", "#12251e", "#63786e"
    fig.text(.025, .955, "IMPERION CORE", color="#6ef3a4", fontsize=36, weight="bold")
    fig.text(.025, .919, f"{r['coin']} · İŞLEM ÖNCESİ PİYASA ARAŞTIRMASI", color="white", fontsize=21, weight="bold")
    q = r["quality"]
    fig.text(.56, .956, r["assessment"]["state"], color="#b9ffca", fontsize=27, weight="bold")
    report_time = datetime.fromisoformat(r["generated_utc"]).astimezone(ZoneInfo("Europe/Istanbul"))
    fig.text(.56, .925, f"{report_time:%d.%m.%Y %H:%M:%S} TSİ · İşlem akışı {q['live_sources']}/{q['expected_sources']} · Canlı L2 {r['live_orderbooks']['live_sources']}/3", color="white", fontsize=16)
    if LOGO.exists():
        ax = fig.add_axes([.9, .92, .08, .065]); ax.imshow(plt.imread(LOGO)); ax.axis("off")

    def panel(x, y, w, h, title, lines, size=16):
        ax = fig.add_axes([x, y, w, h], facecolor="#f5faf6")
        ax.set_xticks([]); ax.set_yticks([])
        for spine in ax.spines.values(): spine.set_color("#2c5140")
        ax.text(.035, .94, title, color=green, fontsize=21, weight="bold", va="top", transform=ax.transAxes)
        wrapped = []
        width = max(25, int(w*3300/(.87*size)))
        for line in lines:
            wrapped.extend(textwrap.wrap(str(line), width=width) or [""])
        available = max(1, int(h*2160*.73/(size*120/72*1.42)))
        if len(wrapped) > available:
            wrapped = wrapped[:available-1]+["Devamı: /arastirma veya canlı panel"]
        ax.text(.035, .79, "\n".join(wrapped), color=ink, fontsize=size, va="top", linespacing=1.4, transform=ax.transAxes)
        return ax

    for interval, x in (("1h", .025), ("4h", .35)):
        f = r["timeframes"].get(interval)
        ax = fig.add_axes([x, .435, .305, .43], facecolor="#f7fcf8")
        for spine in ax.spines.values(): spine.set_color("#bfd5c6")
        if not f:
            ax.set_xticks([]); ax.set_yticks([])
            ax.text(.5, .5, f"{'1 saat' if interval == '1h' else '4 saat'} · Güncel kapalı mum verisi yok", ha="center", transform=ax.transAxes, fontsize=20)
            continue
        bars = f["candles"][-80:]
        for i, b in enumerate(bars):
            col = green if b["close"] >= b["open"] else red
            ax.vlines(i, b["low"], b["high"], colors=col, linewidth=1.1)
            ax.add_patch(Rectangle((i-.32, min(b["open"], b["close"])), .64,
                                   max(abs(b["close"]-b["open"]), b["close"]*.00002), color=col))
        for key, label, color in (("prior_20_high", "20 mum üst aralık", red), ("prior_20_low", "20 mum alt aralık", green), ("vwap_24h_approx", "Yaklaşık 24s VWAP", "#2679b7")):
            if f[key] is not None:
                ax.axhline(f[key], color=color, linestyle="--", alpha=.7)
                ax.text(.02, f[key], f"{label} {f[key]:,.0f}", color=color, fontsize=12,
                        transform=ax.get_yaxis_transform(), va="bottom", bbox={"facecolor": "white", "alpha": .8, "edgecolor": "none"})
        ax.set_title(f"{r['coin']} · {'1 SAAT' if interval=='1h' else '4 SAAT'} · {f['regime'].replace('i', 'İ').upper()}", loc="left", fontsize=21, pad=11, weight="bold", color="#d8eee1")
        ax.set_xlim(-2, len(bars)+2)
        ax.grid(alpha=.15)
        ax.tick_params(labelsize=12, labelcolor="#d0e5d9")
        ticks = list(range(0, len(bars), max(1, len(bars)//5)))
        ax.set_xticks(ticks, [datetime.fromtimestamp(bars[i]["ts"], timezone.utc).strftime("%d.%m %H:%M") for i in ticks])
        ax.text(.01, -.13, f"{f['source']} · saat ekseni UTC\nRSI(14) {tr_num(f['rsi14'],1)} · ATR %{tr_num(f['atr_pct'],2)} · kapanış {tr_num(f['close'],2)}", transform=ax.transAxes, fontsize=13, color="#c4ded0")

    a = r["assessment"]
    panel(.68, .63, .295, .245, "AKIŞ VE YAPI", a["observations"] or ["Yeterli gözlem yok."], 16)
    panel(.68, .385, .295, .225, "KARŞI KANIT / VERİ ENGELLERİ", a["counter_evidence"]+a["blockers"] or ["İzlenen ölçümlerde çelişki görülmedi."], 16)
    ob = [v for v in r["live_orderbooks"]["sources"] if v["status"] == "live"]
    book_lines = []
    for v in ob:
        cost = v["slippage"]["100000"]["buy"]
        slip = f"{cost['slippage_bps']:.2f} bp" if cost["complete"] else "derinlik yetersiz"
        book_lines.append(f"{v['venue']} {v['market']}: spread {v['spread_bps']:.2f} bp · 100 bin USD alım kayması {slip}")
    if not ob:
        book_lines.append("Güncel L2 defteri yok; kayma hesaplanamadı.")
    large = (r.get("large_trades") or {}).get("trades", [])
    if large:
        t = large[0]
        book_lines.append(f"Son büyük işlem · {t['venue']} {t['market']} · {'alım' if t['side']=='buy' else 'satım'} · {tr_num(t['usd'],0)} USD")
    book_lines.append("Kayma, görünür kademeler üzerinden hesaplanır. Ücret ayrıdır.")
    panel(.025, .11, .305, .235, "EMİR DEFTERİ VE MALİYET", book_lines, 15)
    opt = r.get("options") or {}
    option_lines = [f"Opsiyon verisi: {'ESKİ' if opt.get('stale') else 'mevcut' if opt else 'yok'}"]
    if opt:
        num = lambda value: tr_num(value, 2) if isinstance(value, (int, float)) else "—"
        option_lines.append(f"DVOL {num(opt.get('dvol'))} · Put/Call {num(opt.get('put_call'))}")
        for e in opt.get("expiries", [])[:2]:
            option_lines.append(f"{e['expiry']} max pain {e['max_pain']:,.0f}")
    option_lines += ["GEX işareti varsayımdır; bayi envanteri bilinmez.", f"Geçmiş benzer örnek: {r['historical_evidence'].get('samples', 0)}. Canlı başarı oranı üretilmez."]
    panel(.35, .11, .305, .235, "OPSİYONLAR VE TARİHSEL KANIT", option_lines, 15)
    panel(.68, .11, .295, .235, "ETF, MAKRO VE LİKİDİTE", r.get("context_summary_tr") or
          ["Güncel dış piyasa verileri alınamadı."], 15)
    conditions = r.get("watch_conditions", [])
    conditions_line = "   •   ".join(f"{c['name']}  {tr_num(c['level'], 2)}" for c in conditions)
    fig.text(.025, .061, conditions_line or "Saatlik aralık seviyeleri için kapanmış mum bekleniyor.",
             color="#80efb0", fontsize=20, weight="bold")
    fig.text(.025, .031, "Aralık geçişlerinde kapanış, spot akışı ve yeniden test birlikte izlenir. Kaynak zamanları ve ayrıntılı ölçümler /arastirma komutunda ve panelde bulunur.",
             color="#c4ded0", fontsize=14)
    output = io.BytesIO()
    try:
        fig.savefig(output, format="png", dpi=120, facecolor=fig.get_facecolor())
        return output.getvalue()
    finally:
        plt.close(fig)


async def build(coin):
    from . import http
    async def make():
        return await asyncio.to_thread(render, await intelligence.generate(coin))
    return await http.cached(f"research-card:{coin}", 30, make)
