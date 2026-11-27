"""Telegram için PNG grafikler (matplotlib, Agg)."""
import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BG, FG, MUTED, GRID = "#0f1419", "#e6e9ef", "#8b95a7", "#252c36"
LONG, SHORT = "#ef5350", "#26a69a"

def _usd(v):
    a = abs(v)
    return f"${v/1e9:.1f}B" if a >= 1e9 else f"${v/1e6:.1f}M" if a >= 1e6 else f"${v/1e3:.0f}K" if a >= 1e3 else f"${v:.0f}"

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
