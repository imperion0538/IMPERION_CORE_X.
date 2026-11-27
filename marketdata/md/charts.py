"""Telegram için PNG grafikler (matplotlib, Agg)."""
import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches  # noqa: F401
import matplotlib.ticker  # noqa: F401
matplotlib.rcParams["text.parse_math"] = False  # "$" işaretleri formül sanılmasın

BG, FG, MUTED, GRID = "#0f1419", "#e6e9ef", "#8b95a7", "#252c36"
LONG, SHORT = "#ef5350", "#26a69a"

def _usd(v):
    a, s = abs(v), "-" if v < 0 else ""
    return f"{s}${a/1e9:.1f}B" if a >= 1e9 else f"{s}${a/1e6:.1f}M" if a >= 1e6 else f"{s}${a/1e3:.0f}K" if a >= 1e3 else f"{s}${a:.0f}"

def _style(ax, fig):
    fig.patch.set_facecolor(BG); ax.set_facecolor(BG)
    for s in ax.spines.values(): s.set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9); ax.grid(axis="x", color=GRID, linewidth=0.6)

def heatmap_png(h, title):
    bins = h["bins"]
    fig, ax = plt.subplots(figsize=(8, 9), dpi=130); _style(ax, fig)
    ys = [b["mid"] for b in bins]; hgt = (bins[0]["hi"] - bins[0]["lo"]) * 0.9
    ax.barh(ys, [b["long_usd"] for b in bins], height=hgt, color=LONG, label="Long likidasyonu (fiyat düşerse)")
    ax.barh(ys, [b["short_usd"] for b in bins], height=hgt, color=SHORT, label="Short likidasyonu (fiyat yükselirse)")
    ax.axhline(h["price"], color=FG, linestyle="--", linewidth=1)
    ax.text(ax.get_xlim()[1], h["price"], f"  Fiyat ${h['price']:,.0f}", color=FG, va="bottom", ha="right", fontsize=9)
    for c in h.get("clusters", []):
        ax.text(c["usd"], c["price"], f" {_usd(c['usd'])} @ ${c['price']:,.0f} ({c['dist_pct']:+.1f}%)", color=FG, va="center", fontsize=8)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: _usd(v)))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title(title, color=FG, fontsize=12, loc="left")
    ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=FG, fontsize=8, loc="lower right")
    fig.text(0.01, 0.005, "Tahmini model: Binance+Bybit+OKX OI değişimi ve kaldıraç dağılımı. Yatırım tavsiyesi değildir.", color=MUTED, fontsize=7)
    buf = io.BytesIO(); fig.tight_layout(rect=(0, 0.02, 1, 1)); fig.savefig(buf, format="png", facecolor=BG); plt.close(fig)
    return buf.getvalue()

def realized_png(r, title):
    bins = r["bins"]
    fig, ax = plt.subplots(figsize=(8, 7), dpi=130); _style(ax, fig)
    if bins:
        ys = [(b["lo"] + b["hi"]) / 2 for b in bins]; hgt = (bins[0]["hi"] - bins[0]["lo"]) * 0.9
        ax.barh(ys, [b["long_usd"] for b in bins], height=hgt, color=LONG, label=f"Long likide oldu {_usd(r['total_long'])}")
        ax.barh(ys, [-b["short_usd"] for b in bins], height=hgt, color=SHORT, label=f"Short likide oldu {_usd(r['total_short'])}")
        ax.axvline(0, color=GRID)
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: _usd(abs(v))))
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
        ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=FG, fontsize=8)
    else:
        ax.text(0.5, 0.5, "Bu aralıkta kayıtlı likidasyon yok", color=MUTED, ha="center", transform=ax.transAxes)
    ax.set_title(title, color=FG, fontsize=12, loc="left")
    buf = io.BytesIO(); fig.tight_layout(); fig.savefig(buf, format="png", facecolor=BG); plt.close(fig)
    return buf.getvalue()


def orderbook_heatmap_png(hist, title):
    """Zaman × fiyat emir defteri ısı haritası (Kingfisher/CoinGlass benzeri). hist: orderbook.history() çıktısı."""
    import numpy as np
    from matplotlib.colors import LogNorm
    fig, ax = plt.subplots(figsize=(11, 7), dpi=130); _style(ax, fig)
    if len(hist) < 2:
        ax.text(0.5, 0.5, "Henüz yeterli emir defteri görüntüsü yok (dakikada bir toplanıyor)", color=MUTED, ha="center", transform=ax.transAxes)
        ax.set_title(title, color=FG, fontsize=12, loc="left")
        buf = io.BytesIO(); fig.savefig(buf, format="png", facecolor=BG); plt.close(fig); return buf.getvalue()
    step = hist[-1]["step"]
    lo = min(h["mid"] for h in hist) * 0.975; hi = max(h["mid"] for h in hist) * 1.025
    nb = int((hi - lo) / step) + 1
    grid = np.zeros((nb, len(hist)))
    for j, h in enumerate(hist):
        for side in ("bids", "asks"):
            for k, v in h[side].items():
                i = int((float(k) - lo) / step)
                if 0 <= i < nb: grid[i, j] += v
    grid[grid <= 0] = np.nan
    vmax = np.nanpercentile(grid, 99.5) if np.isfinite(grid).any() else 1
    import matplotlib.dates as mdates
    t = [h["ts"] for h in hist]
    ext = [mdates.date2num(t[0]), mdates.date2num(t[-1]), lo, lo + nb * step]
    im = ax.imshow(grid, aspect="auto", origin="lower", extent=ext, cmap="inferno", norm=LogNorm(vmin=max(vmax / 300, 1), vmax=vmax), interpolation="nearest")
    ax.plot(t, [h["mid"] for h in hist], color="#4fc3f7", linewidth=1.2, label="Orta fiyat")
    ax.xaxis_date(); ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:,.0f}" if v >= 100 else f"${v:,.3f}"))
    cb = fig.colorbar(im, ax=ax, pad=0.01); cb.ax.tick_params(colors=MUTED, labelsize=8)
    cb.ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: _usd(v)))
    ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=FG, fontsize=8, loc="upper left")
    ax.set_title(title, color=FG, fontsize=12, loc="left")
    fig.text(0.01, 0.005, "Binance perp+spot, Bybit, OKX, Coinbase birleşik emir defteri (USD). Saat UTC.", color=MUTED, fontsize=7)
    buf = io.BytesIO(); fig.tight_layout(rect=(0, 0.02, 1, 1)); fig.savefig(buf, format="png", facecolor=BG); plt.close(fig)
    return buf.getvalue()


def cvd_png(rows, title):
    """rows: [{"t": datetime, "market": "spot"|"perp", "delta": usd, "price": float}] dakikalık/saatlik."""
    import matplotlib.dates as mdates
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(10, 7), dpi=130, sharex=True, gridspec_kw={"height_ratios": [2, 3]})
    for a in (ax, ax2): _style(a, fig)
    px = {}
    for r in rows:
        if r["price"]: px[r["t"]] = r["price"]
    if px:
        ks = sorted(px); ax.plot(ks, [px[k] for k in ks], color=FG, linewidth=1.2)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title(title, color=FG, fontsize=12, loc="left")
    for m, col in (("spot", "#ffb74d"), ("perp", "#4fc3f7")):
        pts = sorted((r["t"], r["delta"]) for r in rows if r["market"] == m)
        if not pts: continue
        cum, ys = 0.0, []
        for _, d in pts: cum += d; ys.append(cum)
        ax2.plot([p[0] for p in pts], ys, color=col, linewidth=1.4, label=f"{m} CVD ({_usd(cum)})")
    ax2.axhline(0, color=GRID)
    ax2.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: _usd(v)))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
    ax2.legend(facecolor=BG, edgecolor=GRID, labelcolor=FG, fontsize=8, loc="upper left")
    fig.text(0.01, 0.005, "CVD: agresif alım − agresif satım, kümülatif. Binance, Bybit, OKX, Hyperliquid (perp), Binance+Coinbase (spot).", color=MUTED, fontsize=7)
    buf = io.BytesIO(); fig.tight_layout(rect=(0, 0.02, 1, 1)); fig.savefig(buf, format="png", facecolor=BG); plt.close(fig)
    return buf.getvalue()


def analysis_card_png(a, heat, depth):
    """Canlı analiz kartı (1920×1080). a: analysis(), heat: heatmap.model(), depth: orderbook.latest()."""
    fig = plt.figure(figsize=(16, 9), dpi=120); fig.patch.set_facecolor(BG)
    pc = a["price_change_pct"]
    col = lambda v: SHORT if (v or 0) >= 0 else LONG
    fig.text(0.03, 0.93, f"IMPERION · {a['coin']}", color=FG, fontsize=26, fontweight="bold")
    fig.text(0.03, 0.865, f"${a['price']:,.2f}", color=FG, fontsize=34)
    fig.text(0.30, 0.875, f"1s {pc['1h']:+.2f}%", color=col(pc["1h"]), fontsize=16)
    fig.text(0.40, 0.875, f"4s {pc['4h']:+.2f}%", color=col(pc["4h"]), fontsize=16)
    fig.text(0.50, 0.875, f"24s {pc['24h']:+.2f}%", color=col(pc["24h"]), fontsize=16)
    fig.text(0.97, 0.93, f"{a['generated_utc']} UTC", color=MUTED, fontsize=12, ha="right")
    d, f4 = a["derivatives"], a["flows"]["4h"]
    tiles = [("Toplam OI", _usd(d["oi_total_usd"]), " · ".join(f"{w} {v:+.1f}%" for w, v in d["oi_change_pct"].items())),
             ("Funding (yıllık, OI ağırlıklı)", f"{(d['funding_ann_pct'] or 0):+.1f}%", ""),
             ("Likidasyon 4s", f"L {_usd(f4['liquidations']['long'])} / S {_usd(f4['liquidations']['short'])}", ""),
             ("Balina net 4s", _usd(f4["whales"]["net_usd"]), f"alım {_usd(f4['whales']['buy_usd'])} · satım {_usd(f4['whales']['sell_usd'])}"),
             ("CVD 4s", f"spot {_usd(f4['cvd'].get('spot', {}).get('delta', 0))}", f"perp {_usd(f4['cvd'].get('perp', {}).get('delta', 0))}"),
             ("Coinbase primi", "—" if a.get("coinbase_premium_pct") is None else f"{a['coinbase_premium_pct']:+.3f}%", "")]
    if depth:
        b1 = depth["bands"]["1"]
        tiles.append(("Defter ±%1", f"alış {_usd(b1['bid_usd'])} / satış {_usd(b1['ask_usd'])}", f"dengesizlik {b1['imbalance']:+.2f}"))
    o = a.get("options")
    if o and o.get("expiries"):
        e = o["expiries"][0]; tiles.append(("Opsiyon", f"max pain ${e['max_pain']:,.0f}", f"{e['expiry']} · P/C {e['put_call']:.2f} · DVOL {o.get('dvol') or 0:.1f}"))
    for i, (k, v, sub) in enumerate(tiles):
        x, y = 0.03 + (i % 2) * 0.27, 0.74 - (i // 2) * 0.17
        fig.patches.append(matplotlib.patches.FancyBboxPatch((x, y - 0.035), 0.25, 0.135, boxstyle="round,pad=0.005", transform=fig.transFigure,
                                                            facecolor="#161c24", edgecolor=GRID))
        fig.text(x + 0.01, y + 0.07, k, color=MUTED, fontsize=11)
        fig.text(x + 0.01, y + 0.025, v, color=FG, fontsize=15)
        if sub: fig.text(x + 0.01, y - 0.015, sub, color=MUTED, fontsize=9)
    ax = fig.add_axes([0.60, 0.08, 0.37, 0.75]); _style(ax, fig)
    bins = heat["bins"]; ys = [b["mid"] for b in bins]; hgt = (bins[0]["hi"] - bins[0]["lo"]) * 0.9
    ax.barh(ys, [b["long_usd"] for b in bins], height=hgt, color=LONG)
    ax.barh(ys, [b["short_usd"] for b in bins], height=hgt, color=SHORT)
    ax.axhline(heat["price"], color=FG, linestyle="--", linewidth=1)
    if depth:
        for w in depth["walls"]["bid"][:3] + depth["walls"]["ask"][:3]:
            if bins[0]["lo"] <= w["price"] <= bins[-1]["hi"]:
                ax.axhline(w["price"], color="#4fc3f7", linewidth=0.8, alpha=0.7)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: _usd(v)))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Tahmini likidasyon haritası 24s · mavi: defter duvarları", color=FG, fontsize=11, loc="left")
    fig.text(0.03, 0.03, "Veriler bilgilendirme amaçlıdır, yatırım tavsiyesi değildir. Isı haritası tahmini modeldir.", color=MUTED, fontsize=9)
    buf = io.BytesIO(); fig.savefig(buf, format="png", facecolor=BG); plt.close(fig)
    return buf.getvalue()
