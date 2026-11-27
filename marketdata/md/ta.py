"""Kart için kural tabanlı teknik analiz (saf fonksiyonlar, ağ yok).

Mum: (open_time_ms, open, high, low, close, volume, taker_buy_volume).
Çıktılar betimleyicidir; senaryolar koşulludur ve teyit gerektirir. Yatırım tavsiyesi değildir.
"""
from dataclasses import dataclass, field

FIBS = (0.618, 0.705, 0.79)


def pivots(candles, left=3, right=3):
    """Swing tepe/dipleri: [(index, price, 'H'|'L')], zamana göre sıralı."""
    out = []
    for i in range(left, len(candles) - right):
        h, l = candles[i][2], candles[i][3]
        win = candles[i - left:i + right + 1]
        if h == max(c[2] for c in win) and h > max(c[2] for c in win if c is not candles[i]):
            out.append((i, h, "H"))
        if l == min(c[3] for c in win) and l < min(c[3] for c in win if c is not candles[i]):
            out.append((i, l, "L"))
    return out


def structure(candles, piv=None):
    """Son iki tepe ve dibe göre yapı + CHoCH adayı.
    trend: 'yükseliş' (HH+HL), 'düşüş' (LH+LL), 'yatay'.
    choch: düşüş yapısında son tepe üstünde kapanış -> 'bullish'; yükselişte son dip altında kapanış -> 'bearish'."""
    piv = piv if piv is not None else pivots(candles)
    highs = [p for p in piv if p[2] == "H"][-2:]
    lows = [p for p in piv if p[2] == "L"][-2:]
    trend = "yatay"
    if len(highs) == 2 and len(lows) == 2:
        if highs[1][1] > highs[0][1] and lows[1][1] > lows[0][1]:
            trend = "yükseliş"
        elif highs[1][1] < highs[0][1] and lows[1][1] < lows[0][1]:
            trend = "düşüş"
    close = candles[-1][4]
    choch = None
    if trend == "düşüş" and highs and close > highs[-1][1]:
        choch = "bullish"
    elif trend == "yükseliş" and lows and close < lows[-1][1]:
        choch = "bearish"
    # kısa vadeli tepki: son 12 mumda dipten belirgin yükseliş / tepeden düşüş
    recent = candles[-12:]
    lo, hi = min(c[3] for c in recent), max(c[2] for c in recent)
    reaction = None
    if hi > lo:
        pos = (close - lo) / (hi - lo)
        reaction = "tepki yükselişi" if pos > 0.65 else "tepki düşüşü" if pos < 0.35 else None
    return {"trend": trend, "choch": choch, "reaction": reaction,
            "last_high": highs[-1][1] if highs else None, "last_low": lows[-1][1] if lows else None}


def ote(candles, trend, lookback=120):
    """Son bacağın OTE bölgesi. Düşüşte: dip->tepe geri çekilmesinde short premium; yükselişte long indirim."""
    seg = candles[-lookback:]
    hi_i = max(range(len(seg)), key=lambda i: seg[i][2]); lo_i = min(range(len(seg)), key=lambda i: seg[i][3])
    hi, lo = seg[hi_i][2], seg[lo_i][3]
    rng = hi - lo
    if rng <= 0:
        return None
    if trend == "düşüş" or (trend == "yatay" and hi_i < lo_i):
        lv = {f: lo + rng * f for f in FIBS}
        return {"side": "short", "levels": lv, "zone": (lv[0.618], lv[0.79]), "leg": (hi, lo)}
    lv = {f: hi - rng * f for f in FIBS}
    return {"side": "long", "levels": lv, "zone": (lv[0.79], lv[0.618]), "leg": (lo, hi)}


MIN_DIST = 0.0025  # seviyeler fiyattan en az %0,25 uzakta


def liquidity(candles, piv, price, tol=0.0015):
    """Fiyatın üstündeki en yakın tepe kümesi (BSL) ve altındaki en yakın dip kümesi (SSL); fiyata yapışık olanlar hariç."""
    above = sorted([p[1] for p in piv if p[2] == "H" and p[1] > price * (1 + MIN_DIST)])
    below = sorted([p[1] for p in piv if p[2] == "L" and p[1] < price * (1 - MIN_DIST)], reverse=True)

    def cluster(levels):
        if not levels:
            return None
        base = levels[0]; grp = [x for x in levels if abs(x / base - 1) <= tol * 3]
        return (min(grp), max(grp), len(grp))
    return {"bsl": cluster(above), "ssl": cluster(below)}


def zone(center, width_pct=0.15):
    w = center * width_pct / 100
    return (center - w, center + w)


@dataclass
class Scenario:
    no: int
    side: str  # "ALIŞ" | "SATIŞ"
    title: str
    condition: str
    entry: tuple
    stop: float
    tps: list = field(default_factory=list)
    status: str = "Teyit bekleniyor."

    def as_dict(self):
        return {"no": self.no, "side": self.side, "title": self.title, "condition": self.condition,
                "entry": self.entry, "stop": self.stop, "tps": self.tps, "status": self.status}


def _tps(entry_mid, levels, direction, n=4, min_gap=0.0025):
    """Giriş yönünde, girişten ve birbirinden en az min_gap uzak ilk n seviye."""
    lim = entry_mid * (1 + min_gap * direction)
    cands = sorted({round(x, 2) for x in levels if x and (x > lim if direction > 0 else x < lim)}, reverse=direction < 0)
    out = []
    for x in cands:
        if not out or abs(x / out[-1] - 1) >= min_gap:
            out.append(x)
        if len(out) == n:
            break
    return out


def scenarios(price, h1, h4, extra_levels=()):
    """Dört koşullu senaryo. h1/h4: analyze() çıktıları. extra_levels: likidasyon kümeleri, defter duvarları vb."""
    sup = h1["support"]; res = h1["resistance"]
    levels = [h1["major_res"][0], h1["major_res"][1], res[0], res[1], sup[0], sup[1], h1["local_low"], h4["local_low"],
              h4["macro_low"], h4["support"][0], h4["support"][1], h4["resistance"][0], h4["resistance"][1],
              h4["major_res"][0], h4["range_high"], *extra_levels]
    for key in ("bsl", "ssl"):
        if h4["liq"][key]: levels += [h4["liq"][key][0], h4["liq"][key][1]]
    if h1["liq"]["bsl"]: levels += [h1["liq"]["bsl"][0], h1["liq"]["bsl"][1]]
    if h1["liq"]["ssl"]: levels += [h1["liq"]["ssl"][0], h1["liq"]["ssl"][1]]
    if h4["ote"]: levels += list(h4["ote"]["levels"].values())
    pad = price * 0.0015
    out = []
    e = sup; m = sum(e) / 2
    out.append(Scenario(1, "ALIŞ", "Destekten Dönüş", f"{e[0]:,.0f}–{e[1]:,.0f} bölgesinde likidite süpürmesi ve 15 dk yükseliş CHoCH teyidi.",
                        e, round(e[0] - pad, 2), _tps(m, levels, +1)))
    brk = res[1]; e = (brk - pad * 0.3, brk + pad * 0.4); m = sum(e) / 2
    out.append(Scenario(2, "ALIŞ", "Kırılım ve Geri Test", f"{brk:,.0f} üzerinde 1 saat kapanış ve başarılı geri test.",
                        e, round(brk - pad * 2, 2), _tps(m, levels, +1)))
    bsl = h1["liq"]["bsl"] or (res[0], res[1], 1); e = (bsl[0] - pad * 0.3, bsl[1] + pad * 0.3); m = sum(e) / 2
    out.append(Scenario(3, "SATIŞ", "Likidite Süpürmesi Sonrası Ret", f"{bsl[0]:,.0f}–{bsl[1]:,.0f} üzeri likidite süpürmesi, ret ve 15 dk düşüş CHoCH.",
                        e, round(e[1] + pad, 2), _tps(m, levels, -1)))
    e = (sup[0] - pad * 0.8, sup[0] - pad * 0.1); m = sum(e) / 2
    out.append(Scenario(4, "SATIŞ", "Destek Kırılımı ve Başarısız Geri Test", f"{sup[0]:,.0f} altı 1 saat kapanış ve başarısız geri test.",
                        e, round(sup[0] + pad * 1.2, 2), _tps(m, levels, -1)))
    for s in out:
        if s.entry[0] <= price <= s.entry[1]:
            s.status = "Fiyat giriş bölgesinde — koşul teyidi bekleniyor."
    return out


def analyze(candles, lookback=120):
    """Tek zaman dilimi için seviyeler ve yapı."""
    seg = candles[-lookback:]
    piv = pivots(seg)
    price = seg[-1][4]
    st = structure(seg, piv)
    highs = sorted([p[1] for p in piv if p[2] == "H" and p[1] > price * (1 + MIN_DIST)])
    lows = sorted([p[1] for p in piv if p[2] == "L" and p[1] < price * (1 - MIN_DIST)], reverse=True)
    hi = max(c[2] for c in seg); lo = min(c[3] for c in seg)
    res_c = highs[0] if highs else price * 1.01
    sup_c = lows[0] if lows else price * 0.99
    return {
        "price": price, "structure": st, "pivots": piv,
        "resistance": zone(res_c), "support": zone(sup_c),
        "major_res": zone(hi, 0.12), "local_low": min(c[3] for c in seg[-48:]), "macro_low": lo, "range_high": hi,
        "ote": ote(seg, st["trend"]), "liq": liquidity(seg, piv, price),
        "delta": [2 * c[6] - c[5] for c in seg],
    }
