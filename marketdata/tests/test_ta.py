import math
from md import card4k, ta


def series(n=200, base=100.0):
    out = []
    for i in range(n):
        c = base + 8 * math.sin(i / 9) + i * 0.02
        out.append((i * 3600_000, c - 0.3, c + 0.8, c - 0.8, c, 1000.0, 520.0 if i % 2 else 480.0))
    return out


def test_pivots_and_structure():
    cs = series()
    piv = ta.pivots(cs)
    assert any(p[2] == "H" for p in piv) and any(p[2] == "L" for p in piv)
    st = ta.structure(cs, piv)
    assert st["trend"] in ("yükseliş", "düşüş", "yatay")


def test_ote_levels_ordered():
    cs = series()
    o = ta.ote(cs, "düşüş")
    lv = o["levels"]
    assert o["side"] == "short" and lv[0.618] < lv[0.705] < lv[0.79]
    o2 = ta.ote(cs, "yükseliş")
    assert o2["side"] == "long" and o2["levels"][0.618] > o2["levels"][0.79]


def test_scenarios_consistent():
    cs = series()
    h1, h4 = ta.analyze(cs), ta.analyze(cs)
    scs = ta.scenarios(h1["price"], h1, h4)
    assert [s.no for s in scs] == [1, 2, 3, 4]
    for s in scs:
        mid = sum(s.entry) / 2
        if s.side == "ALIŞ":
            assert s.stop < s.entry[0] and all(t > mid for t in s.tps)
            assert s.tps == sorted(s.tps)
        else:
            assert s.stop > s.entry[1] and all(t < mid for t in s.tps)
            assert s.tps == sorted(s.tps, reverse=True)


def test_levels_keep_min_distance():
    cs = series()
    h = ta.analyze(cs)
    p = h["price"]
    assert h["resistance"][0] > p * (1 + ta.MIN_DIST) * 0.998
    assert h["support"][1] < p * (1 - ta.MIN_DIST) * 1.002


def test_card_renders_4k():
    import io
    from PIL import Image
    cs = series()
    png = card4k.render(cs, cs, {"derivatives": {"funding_ann_pct": 3.0, "oi_change_pct": {"4h": 1.2}, "venues": {"binance": {"ls_global": 2.1}}}},
                        {"clusters": [{"side": "long", "price": 95, "usd": 1e6, "dist_pct": -5}, {"side": "short", "price": 110, "usd": 1e6, "dist_pct": 5}]},
                        {"walls": {"bid": [{"price": 98, "usd": 1e6}], "ask": [{"price": 104, "usd": 1e6}]}}, coin="ETH")
    assert Image.open(io.BytesIO(png)).size == (3840, 2160)


def test_tr_num():
    assert card4k.tr_num(61658) == "61.658" and card4k.tr_num(61658.5, 2) == "61.658,50"
