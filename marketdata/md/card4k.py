"""IMPERION 4K teknik analiz kartı (3840×2160), canlı veriden.

Düzen: logolu başlık · 1H ve 4H mum grafikleri (bölgeler, likidite, OTE, likidasyon kümeleri, defter duvarları,
alış-satış deltası) · koşullu senaryolar · piyasa özeti, seviyeler, yapı notları, önemli not.
"""
import io, textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker  # noqa: F401
from matplotlib.patches import FancyBboxPatch, Rectangle
matplotlib.rcParams["text.parse_math"] = False

from . import ta

LOGO = Path(__file__).resolve().parents[1] / "assets" / "imperion-logo.png"
DPI = 120
W, H = 32, 18  # inç -> 3840×2160
SYMBOL = "BTCUSDT"

BG = "#0b0f0d"; PANEL = "#ffffff"; INK = "#111827"; SUB = "#4b5563"; LINE = "#d1d5db"
GREEN = "#15803d"; GREEN_L = "#dcfce7"; GREEN_B = "#22c55e"; RED = "#dc2626"; RED_L = "#fee2e2"
ORANGE = "#f59e0b"; BLUE = "#2563eb"; PURPLE = "#7c3aed"; HEAD_FG = "#e5e7eb"; NEON = "#4ade80"
CANDLE_UP = "#16a34a"; CANDLE_DN = "#dc2626"


def tr_num(x, d=0):
    """Türkçe sayı biçimi: 61.658,00"""
    s = f"{x:,.{d}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def _box(fig, x, y, w, h, fc=PANEL, ec=LINE, lw=2, r=0.006):
    fig.patches.append(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", transform=fig.transFigure,
                                      facecolor=fc, edgecolor=ec, linewidth=lw, zorder=-2))


def _header_bar(fig, x, y, w, h, text, fc=GREEN, size=22):
    _box(fig, x, y, w, h, fc=fc, ec=fc, r=0.004)
    fig.patches[-1].set_zorder(-1)
    fig.text(x + 0.008, y + h / 2, text, color="white", fontsize=size, fontweight="bold", va="center")


def _label(ax, x, y, text, fc, ec, size=13, ha="center", color=INK):
    return ax.text(x, y, text, ha=ha, va="center", fontsize=size, color=color, fontweight="bold", zorder=6,
            bbox=dict(boxstyle="round,pad=0.35", fc=fc, ec=ec, lw=1.6))


def _candles(ax, cs, xs):
    for x, c in zip(xs, cs):
        o, h, l, cl = c[1], c[2], c[3], c[4]
        col = CANDLE_UP if cl >= o else CANDLE_DN
        ax.vlines(x, l, h, color=col, linewidth=1.3, zorder=3)
        ax.add_patch(Rectangle((x - 0.32, min(o, cl)), 0.64, max(abs(cl - o), (h - l) * 0.002 + 1e-9), facecolor=col, edgecolor=col, zorder=4))


def _chart(fig, rect, cs, res, tf, tag, tag_fc, extra):
    """rect: (x, y, w, h) figür kesirleri. res: ta.analyze() çıktısı."""
    x0, y0, w, h = rect
    _box(fig, x0, y0, w, h, ec=LINE, lw=2.5)
    fig.text(x0 + 0.018, y0 + h - 0.028, "B", fontsize=26, color="white", fontweight="bold", va="center", ha="center",
             bbox=dict(boxstyle="circle,pad=0.3", fc="#f7931a", ec="#f7931a"))  # ₿ her yazı tipinde yok
    fig.text(x0 + 0.036, y0 + h - 0.028, f"{SYMBOL} · {tf}", fontsize=30, fontweight="bold", color=INK, va="center")
    fig.text(x0 + 0.155, y0 + h - 0.028, f" {tag} ", fontsize=18, fontweight="bold", color="white", va="center",
             bbox=dict(boxstyle="round,pad=0.35", fc=tag_fc, ec=tag_fc))
    ax = fig.add_axes([x0 + 0.008, y0 + 0.11, w - 0.06, h - 0.17])
    axd = fig.add_axes([x0 + 0.008, y0 + 0.035, w - 0.06, 0.07], sharex=ax)
    xs = list(range(len(cs)))
    _candles(ax, cs, xs)
    lo = min(c[3] for c in cs); hi = max(c[2] for c in cs); pad = (hi - lo) * 0.06
    ax.set_ylim(lo - pad, hi + pad); ax.set_xlim(-1, len(cs) + 6)
    price = cs[-1][4]
    inside = lambda v: lo - pad < v < hi + pad
    right = len(cs) + 5.5

    pending = []  # (x tercihi, y, metin, dolgu, kenar, boyut) — çakışma kontrolüyle en sona yerleştirilir

    def band(z, fc, label, lfc, lec, xpos=None):
        if not (inside(z[0]) or inside(z[1])):
            return
        ax.axhspan(z[0], z[1], color=fc, alpha=0.55, zorder=1)
        if label:
            pending.append((xpos if xpos is not None else len(cs) * 0.45, (z[0] + z[1]) / 2, label, lfc, lec, 13))

    band(res["major_res"], RED_L, f"Majör Direnç Bölgesi\n{tr_num(res['major_res'][0])} – {tr_num(res['major_res'][1])}", RED_L, RED, len(cs) * 0.45)
    o = res["ote"]
    if o:
        lbl = "Bullish OTE / olası long indirim" if o["side"] == "long" else "Bearish OTE / olası short premium"
        band(o["zone"], GREEN_L if o["side"] == "long" else RED_L,
             f"{lbl}\n{tr_num(o['zone'][0])} – {tr_num(o['zone'][1])} (0,705 = {tr_num(o['levels'][0.705])})",
             GREEN_L if o["side"] == "long" else RED_L, GREEN if o["side"] == "long" else RED, len(cs) * 0.25)
    bsl = res["liq"]["bsl"]
    if bsl:
        z = (bsl[0], bsl[1]) if bsl[1] - bsl[0] > price * 0.0008 else ta.zone(bsl[0], 0.08)
        band(z, RED_L, f"Likidite (BSL)\n{tr_num(z[0])} – {tr_num(z[1])}", RED_L, RED, len(cs) * 0.55)
    band(res["support"], GREEN_L, f"Yakın Destek / Talep\n{tr_num(res['support'][0])} – {tr_num(res['support'][1])}", GREEN_L, GREEN, len(cs) * 0.55)
    ssl = res["liq"]["ssl"]
    if ssl and inside(ssl[0]):
        ax.axhline(ssl[0], color=SUB, linestyle=":", linewidth=2, zorder=2)
        pending.append((len(cs) * 0.3, ssl[0], f"Likidite (SSL) {tr_num(ssl[0])}", "#f3f4f6", SUB, 12))
    if inside(res["local_low"]):
        ax.axhline(res["local_low"], color=INK, linestyle=":", linewidth=1.6, zorder=2)
        pending.append((len(cs) * 0.15, res["local_low"], f"Lokal Dip {tr_num(res['local_low'])}", "white", INK, 12))
    st = res["structure"]
    if st["choch"]:
        pending.append((len(cs) * 0.12, lo + (hi - lo) * 0.85, f"{tf} {'bullish' if st['choch'] == 'bullish' else 'bearish'} CHoCH adayı\n(teyit bekleniyor)", "white", INK, 12))
    gap = (hi - lo + 2 * pad) * 0.04  # etiketler arası en az dikey boşluk (veri birimi)
    labels = []
    for kind in ("liq", "wall"):
        col = PURPLE if kind == "liq" else BLUE
        ps = sorted(p for p, k in extra if k == kind and inside(p))
        for p in ps:
            ax.axhline(p, color=col, linestyle="--", linewidth=1.4, alpha=0.8, zorder=2)
        groups = []  # birbirine çok yakın seviyeler tek etikette birleşir
        for p in ps:
            if groups and p - groups[-1][-1] < gap: groups[-1].append(p)
            else: groups.append([p])
        for g in groups:
            name = ("Likidasyon kümesi" if len(g) == 1 else "Likidasyon kümeleri") if kind == "liq" else ("Defter duvarı" if len(g) == 1 else "Defter duvarları")
            labels.append([sum(g) / len(g), f"{name} " + " / ".join(tr_num(x) for x in g), col])
    labels.sort(key=lambda l: l[0])
    for i in range(1, len(labels)):  # üst üste binenleri yukarı it
        if labels[i][0] - labels[i - 1][0] < gap: labels[i][0] = labels[i - 1][0] + gap
    renderer = fig.canvas.get_renderer()
    placed = []

    def extent(t):
        bb = t.get_window_extent(renderer)
        return bb.expanded(1.0, 1.0).padded(14)

    for y, text, col in labels:
        t = ax.text(len(cs) - 0.5, y, text, color=col, fontsize=12, fontweight="bold", va="center", ha="right", zorder=8,
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=col, lw=1, alpha=0.92))
        placed.append(extent(t))
    # bölge etiketleri: tercih edilen x'ten başlayarak çakışmayan ilk konuma yerleşir
    fracs = (0.12, 0.24, 0.36, 0.48, 0.60, 0.72)
    for xpref, y, text, lfc, lec, size in pending:
        best = None
        for xc in [xpref] + [len(cs) * f for f in fracs]:
            t = _label(ax, xc, y, text, lfc, lec, size)
            bb = extent(t)
            if not any(bb.overlaps(p) for p in placed):
                best = (t, bb); break
            t.remove()
        if best is None:  # hiçbir konum boş değilse tercih edilen yere koy
            t = _label(ax, xpref, y, text, lfc, lec, size); best = (t, extent(t))
        placed.append(best[1])
    ax.axhline(price, color=ORANGE, linewidth=1.4, zorder=2)
    ax.text(right, price, f" {tr_num(price, 2)} ", color="white", fontsize=15, fontweight="bold", va="center", ha="left", zorder=7,
            bbox=dict(boxstyle="square,pad=0.25", fc=ORANGE, ec=ORANGE), clip_on=False)
    ax.yaxis.tick_right(); ax.tick_params(axis="y", labelsize=14, colors=INK); ax.tick_params(axis="x", labelbottom=False, length=0)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: tr_num(v, 2)))
    for s in ax.spines.values(): s.set_visible(False)
    ax.grid(axis="y", color="#eef0f3", linewidth=1)
    delta = res["delta"][-len(cs):]
    axd.bar(xs, delta, color=[CANDLE_UP if d >= 0 else CANDLE_DN for d in delta], width=0.75, alpha=0.75)
    axd.axhline(0, color=LINE, linewidth=1)
    axd.yaxis.tick_right(); axd.tick_params(axis="y", labelsize=11, colors=SUB); axd.tick_params(axis="x", labelsize=14, colors=INK)
    axd.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1e3:,.0f}K" if abs(v) >= 1e3 else f"{v:,.0f}"))
    axd.yaxis.get_offset_text().set_visible(False)
    for s in axd.spines.values(): s.set_visible(False)
    step = max(1, len(cs) // 6)
    ticks = xs[step // 2::step]
    axd.set_xticks(ticks)
    axd.set_xticklabels([(datetime.fromtimestamp(cs[i][0] / 1000, timezone.utc) + timedelta(hours=3)).strftime("%d.%m %H:%M" if tf == "1H" else "%d.%m") for i in ticks])
    axd.text(0.0, 1.04, f"Alış − satış deltası (taker, {SYMBOL[:-4]})", transform=axd.transAxes, fontsize=12, color=SUB,
             bbox=dict(boxstyle="square,pad=0.15", fc="white", ec="none"))


def _scenarios(fig, rect, scs):
    x0, y0, w, h = rect
    _header_bar(fig, x0, y0 + h - 0.04, w, 0.04, "Koşullu İşlem Senaryoları", size=26)
    bh = (h - 0.05) / 4
    for i, s in enumerate(scs):
        by = y0 + h - 0.05 - (i + 1) * bh + 0.004
        col, light = (GREEN, GREEN_L) if s["side"] == "ALIŞ" else (RED, RED_L)
        _box(fig, x0, by, w, bh - 0.008, fc="white", ec=col, lw=2.5)
        _box(fig, x0, by + bh - 0.042, w, 0.034, fc=light, ec=light, r=0.003); fig.patches[-1].set_zorder(-1)
        fig.text(x0 + 0.006, by + bh - 0.025, f" {s['no']} ", fontsize=22, fontweight="bold", color="white", va="center",
                 bbox=dict(boxstyle="round,pad=0.2", fc=col, ec=col))
        fig.text(x0 + 0.03, by + bh - 0.025, f"{s['side']} – {s['title']}", fontsize=21, fontweight="bold", color=col, va="center")
        cond = "\n".join(textwrap.wrap("Koşul: " + s["condition"], 96)[:2])
        fig.text(x0 + 0.008, by + bh - 0.046, cond, fontsize=12.5, color=INK, va="top", linespacing=1.25)
        cols = ["Giriş", "Stop", "TP1", "TP2", "TP3", "TP4"]
        vals = [f"{tr_num(s['entry'][0])} – {tr_num(s['entry'][1])}", tr_num(s["stop"])] + [tr_num(t) for t in s["tps"]] + ["—"] * (4 - len(s["tps"]))
        widths = [0.30, 0.14, 0.14, 0.14, 0.14, 0.14]
        cx = x0 + 0.006; ty = by + 0.022
        for c, v, wd in zip(cols, vals, widths):
            ww = (w - 0.012) * wd
            fig.patches.append(Rectangle((cx, ty), ww, 0.032, transform=fig.transFigure, facecolor="#f9fafb", edgecolor=LINE, linewidth=1.2, zorder=-1))
            fig.text(cx + ww / 2, ty + 0.023, c, ha="center", va="center", fontsize=13, fontweight="bold", color=RED if c == "Stop" else INK)
            fig.text(cx + ww / 2, ty + 0.009, v, ha="center", va="center", fontsize=13, color=RED if c == "Stop" else INK)
            cx += ww
        fig.text(x0 + 0.008, by + 0.010, "✓ " + s["status"], fontsize=14, color=GREEN if "Teyit" in s["status"] else ORANGE,
                 fontstyle="italic", va="center", fontweight="bold")


def _rows_panel(fig, rect, title, rows, size=16):
    x0, y0, w, h = rect
    _box(fig, x0, y0, w, h, ec=GREEN, lw=2.5)
    _header_bar(fig, x0, y0 + h - 0.035, w, 0.035, title, size=22)
    lh = (h - 0.05) / max(len(rows), 1)
    for i, (k, v, c) in enumerate(rows):
        yy = y0 + h - 0.05 - i * lh - lh / 2
        fig.text(x0 + 0.008, yy, k, fontsize=size, fontweight="bold", color=INK, va="center")
        fig.text(x0 + w * 0.46, yy, v, fontsize=size, fontweight="bold", color=c or INK, va="center")
        if i < len(rows) - 1:
            fig.lines.append(plt.Line2D([x0 + 0.006, x0 + w - 0.006], [yy - lh / 2, yy - lh / 2], transform=fig.transFigure, color="#eef0f3", linewidth=1, zorder=-1))


def _text_panel(fig, rect, title, lines, size=14, width=70, bullet="■ "):
    x0, y0, w, h = rect
    _box(fig, x0, y0, w, h, ec=GREEN, lw=2.5)
    _header_bar(fig, x0, y0 + h - 0.035, w, 0.035, title, size=22)
    txt = []
    for ln in lines:
        wrapped = textwrap.wrap(ln, width)
        txt.append(bullet + wrapped[0]); txt += ["   " + x for x in wrapped[1:]]
    fig.text(x0 + 0.008, y0 + h - 0.047, "\n".join(txt), fontsize=size, color=INK, va="top", linespacing=1.35)


def render(c1, c4, analysis=None, heat=None, depth=None, now=None, coin="BTC"):
    """c1, c4: mumlar (ta modülü biçimi). analysis/heat/depth: isteğe bağlı canlı veriler. PNG bayt döndürür."""
    global SYMBOL
    SYMBOL = f"{coin}USDT"
    now = now or datetime.now(timezone.utc)
    h1, h4 = ta.analyze(c1), ta.analyze(c4)
    price = h1["price"]
    extra_lv, extra_marks = [], []
    if heat:
        for c in heat.get("clusters", [])[:6]:
            extra_lv.append(c["price"]); extra_marks.append((c["price"], "liq"))
    if depth:
        for wl in depth["walls"]["bid"][:2] + depth["walls"]["ask"][:2]:
            extra_lv.append(wl["price"]); extra_marks.append((wl["price"], "wall"))
    scs = [s.as_dict() for s in ta.scenarios(price, h1, h4, extra_lv)]
    active = [s for s in scs if "giriş bölgesinde" in s["status"]]

    fig = plt.figure(figsize=(W, H), dpi=DPI); fig.patch.set_facecolor("#f3f4f6")
    # --- başlık
    fig.patches.append(Rectangle((0, 0.855), 1, 0.145, transform=fig.transFigure, facecolor=BG, edgecolor="none", zorder=-2))
    if LOGO.exists():
        lax = fig.add_axes([0.0, 0.855, 0.246, 0.145]); lax.imshow(plt.imread(LOGO)); lax.axis("off")
    fig.lines.append(plt.Line2D([0.3, 0.3], [0.875, 0.98], transform=fig.transFigure, color=GREEN_B, linewidth=3))
    tsi = now + timedelta(hours=3)
    fig.text(0.315, 0.955, f"{SYMBOL} TEKNİK ANALİZ", fontsize=46, fontweight="bold", color="white", va="center")
    fig.text(0.315, 0.913, f"1 saat ve 4 saat  •  {tsi.strftime('%d.%m.%Y, %H:%M')} TSİ", fontsize=24, color=HEAD_FG, va="center")
    fig.text(0.315, 0.878, f"Referans fiyat {tr_num(price, 2)} USDT", fontsize=26, fontweight="bold", color=NEON, va="center")
    fig.lines.append(plt.Line2D([0.725, 0.725], [0.875, 0.98], transform=fig.transFigure, color=GREEN_B, linewidth=3))
    status = f"SENARYO {active[0]['no']} BÖLGESİNDE" if active else "BEKLE / İŞLEM YOK"
    fig.text(0.74, 0.945, status, fontsize=38, fontweight="bold", color=ORANGE if active else NEON, va="center")
    fig.text(0.74, 0.900, "Koşullu senaryolar teyit bekliyor.", fontsize=20, color=HEAD_FG, va="center")

    def tag(res):
        st = res["structure"]
        t = {"yükseliş": "Yükseliş Yapısı", "düşüş": "Düşüş Eğilimli Yapı", "yatay": "Yatay / Belirsiz Yapı"}[st["trend"]]
        if st["reaction"]: t += f" · {st['reaction'].capitalize()}"
        return t, GREEN if st["trend"] == "yükseliş" or st["reaction"] == "tepki yükselişi" else RED if st["trend"] == "düşüş" else SUB

    t1, f1 = tag(h1); t4, f4 = tag(h4)
    _chart(fig, (0.006, 0.335, 0.338, 0.51), c1[-120:], h1, "1H", t1, f1, extra_marks)
    _chart(fig, (0.35, 0.335, 0.372, 0.51), c4[-120:], h4, "4H", t4, f4, extra_marks)
    _scenarios(fig, (0.728, 0.335, 0.266, 0.51), scs)

    # --- alt paneller
    d = (analysis or {}).get("derivatives", {})
    fa = d.get("funding_ann_pct"); oic = (d.get("oi_change_pct") or {}).get("4h") or (d.get("oi_change_pct") or {}).get("1h")
    ls = ((d.get("venues") or {}).get("binance") or {}).get("ls_global")
    trend_txt = lambda r: {"yükseliş": ("Yükseliş yapısı", GREEN), "düşüş": ("Düşüş eğilimli", RED), "yatay": ("Yatay / belirsiz", SUB)}[r["structure"]["trend"]]
    r4, r1 = trend_txt(h4), trend_txt(h1)
    if h1["structure"]["reaction"]:
        r1 = (h1["structure"]["reaction"].capitalize(), GREEN if "yükseliş" in h1["structure"]["reaction"] else RED)
    rows = [("4H Ana Trend:", *r4), ("1H Kısa Vadeli Yapı:", *r1),
            ("Funding:", "—" if fa is None else ("Pozitif" if fa > 0 else "Negatif") + f" (yıllık {fa:+.1f}%)", GREEN if (fa or 0) <= 15 else RED),
            ("Open Interest:", "—" if oic is None else ("Artış" if oic > 0.5 else "Düşüş" if oic < -0.5 else "Yatay / sınırlı") + f" ({oic:+.2f}%)", INK),
            ("Long/Short Hesap Oranı:", "—" if ls is None else f"{ls:.2f}" + (" · long kalabalığı riski" if ls > 1.8 else ""), RED if (ls or 0) > 1.8 else INK),
            ("Genel Karar:", "Teyit gelmeden agresif işlem yok." if not active else f"Senaryo {active[0]['no']} bölgesinde, teyit bekleniyor.", INK)]
    flow = (analysis or {}).get("flows", {}).get("4h", {})
    cov = flow.get("coverage", {})
    observed = max((v["observed_minutes"] for v in cov.get("sources", [])), default=0)
    rows[-1] = ("4H işlem verisi:", f"En geniş kaynak {observed}/{cov.get('expected_minutes', 240)} dk", INK)
    _rows_panel(fig, (0.006, 0.01, 0.26, 0.315), "Piyasa Özeti", rows, size=16)

    lv = [("Majör Direnç Bölgesi:", f"{tr_num(h1['major_res'][0])} – {tr_num(h1['major_res'][1])}"),
          ("İkincil Direnç:", f"{tr_num(h1['resistance'][0])} – {tr_num(h1['resistance'][1])}"),
          ("Güncel / Referans:", tr_num(price, 2)),
          ("Yakın Destek / Talep:", f"{tr_num(h1['support'][0])} – {tr_num(h1['support'][1])}"),
          ("HTF Destek (4H):", f"{tr_num(h4['support'][0])} – {tr_num(h4['support'][1])}")]
    if h4["ote"]:
        lv.append((f"{'Bullish' if h4['ote']['side'] == 'long' else 'Bearish'} OTE (4H):", f"{tr_num(h4['ote']['zone'][0])} – {tr_num(h4['ote']['zone'][1])}"))
    if h1["liq"]["bsl"]: lv.append(("Likidite (BSL, 1H):", tr_num(h1["liq"]["bsl"][1])))
    if h4["liq"]["ssl"]: lv.append(("Likidite (SSL, 4H):", tr_num(h4["liq"]["ssl"][0])))
    lv += [("Lokal Dip (4H):", tr_num(h4["local_low"])), ("Makro Dip (aralık):", tr_num(h4["macro_low"]))]
    _rows_panel(fig, (0.27, 0.01, 0.235, 0.315), "Ana Seviyeler ve Bölgeler", [(k, v, INK) for k, v in lv], size=13)

    notes = []
    if h1["liq"]["bsl"]: notes.append(f"Likidite Bölgesi (BSL): {tr_num(h1['liq']['bsl'][1])} üzerinde olası buy-side likidite.")
    if h4["liq"]["ssl"]: notes.append(f"Likidite Bölgesi (SSL): {tr_num(h4['liq']['ssl'][0])} altı sell-side likidite.")
    notes.append(f"1H: {h1['structure']['trend']} yapı" + (f", {h1['structure']['reaction']}" if h1['structure']['reaction'] else "") +
                 ("; bullish CHoCH adayı (teyit bekleniyor)." if h1["structure"]["choch"] == "bullish" else "; bearish CHoCH adayı (teyit bekleniyor)." if h1["structure"]["choch"] == "bearish" else "."))
    notes.append(f"4H: {h4['structure']['trend']} yapı; trend dönüşümü için 4H kapanış teyidi gerekir.")
    if h4["ote"]:
        lvls = h4["ote"]["levels"]
        notes.append(f"{'Bullish' if h4['ote']['side'] == 'long' else 'Bearish'} OTE (4H): 0,618 = {tr_num(lvls[0.618])}, 0,705 = {tr_num(lvls[0.705])}, 0,790 = {tr_num(lvls[0.79])}.")
    if heat and heat.get("clusters"):
        lo_c = min([c for c in heat["clusters"] if c["side"] == "long"], key=lambda c: abs(c["dist_pct"]), default=None)
        hi_c = min([c for c in heat["clusters"] if c["side"] == "short"], key=lambda c: abs(c["dist_pct"]), default=None)
        if lo_c and hi_c:
            notes.append(f"Tahmini likidasyon kümeleri: aşağıda {tr_num(lo_c['price'])} ({lo_c['dist_pct']:+.1f}%), yukarıda {tr_num(hi_c['price'])} ({hi_c['dist_pct']:+.1f}%).")
    if depth:
        b = depth["walls"]["bid"][:1]; a = depth["walls"]["ask"][:1]
        if b and a:
            notes.append(f"Emir defteri duvarları: alış {tr_num(b[0]['price'])}, satış {tr_num(a[0]['price'])} (birleşik defter).")
    notes.append("CHoCH ve likidite adayları yalnızca olasılıktır; teyit alınmadan geçerli kabul edilmez.")
    _text_panel(fig, (0.51, 0.01, 0.26, 0.315), "Yapı ve Likidite Notları", notes, size=15, width=56)

    x0, y0, w, h = 0.775, 0.01, 0.219, 0.315
    _box(fig, x0, y0 + 0.11, w, h - 0.11, ec=GREEN, lw=2.5)
    fig.text(x0 + 0.01, y0 + h - 0.03, "i", fontsize=20, fontweight="bold", color="white", va="center", ha="center",
             bbox=dict(boxstyle="circle,pad=0.3", fc=GREEN, ec=GREEN))
    fig.text(x0 + 0.022, y0 + h - 0.03, "Önemli Not", fontsize=26, fontweight="bold", color=GREEN, va="center")
    fig.text(x0 + 0.01, y0 + h - 0.055, "\n".join(textwrap.wrap(
        "Tüm seviyeler ve senaryolar koşulludur. Herhangi bir kurulumun gerçekleştiği varsayılmamaktadır. "
        "İşlem için mutlaka belirtilen teyit koşullarının sağlanması beklenmelidir.", 58)), fontsize=15, color=INK, va="top", linespacing=1.4)
    fig.text(x0 + 0.005, y0 + 0.095, "\n".join(textwrap.wrap(
        "Veri kaynakları: Binance (fiyat, taker deltası), Binance+Bybit+OKX (OI modeli), birleşik emir defteri. "
        "Seviyeler kural tabanlı algoritmayla otomatik üretilir. Bu görsel eğitim ve analiz amaçlıdır; yatırım tavsiyesi değildir.", 70)),
        fontsize=12, color=SUB, va="top", linespacing=1.35)
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=DPI, facecolor=fig.get_facecolor()); plt.close(fig)
    return buf.getvalue()


def binance_candles(rows):
    """Binance kline satırları -> ta mum biçimi (taker alış hacmi 9. alan)."""
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[9])) for r in rows]


async def build(coin="BTC"):
    import asyncio
    from . import analysis, heatmap, http, orderbook
    k1, k4 = await asyncio.gather(
        http.get("https://fapi.binance.com/fapi/v1/klines", {"symbol": coin + "USDT", "interval": "1h", "limit": 200}),
        http.get("https://fapi.binance.com/fapi/v1/klines", {"symbol": coin + "USDT", "interval": "4h", "limit": 200}))
    async def safe(coro):
        try: return await coro
        except Exception: return None
    a, h, d = await asyncio.gather(safe(analysis.analysis(coin)), safe(heatmap.model(coin, "24h")), safe(orderbook.latest(coin)))
    from time import time
    now_ms = int(time() * 1000)
    c1, c4 = (binance_candles([row for row in rows if row[6] < now_ms]) for rows in (k1, k4))
    return render(c1, c4, a, h, d, coin=coin)
