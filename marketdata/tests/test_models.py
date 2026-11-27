"""Saf hesaplama birimlerinin testleri (ağ ve veritabanı gerektirmez)."""
import math
import pytest
from md import fmt, heatmap, option_metrics, orderbook, rest


def test_heatmap_new_oi_creates_levels_both_sides():
    prices = {0: (100, 100, 100), 1: (100, 100, 100)}
    series = {"binance": {0: 1000.0, 1: 2000.0}}
    ref, bins, used = heatmap.simulate(prices, series, rng=0.25, leverage=[(10, 1.0)])
    assert ref == 100 and used["binance"] == 1
    long_usd = sum(b["long_usd"] for b in bins); short_usd = sum(b["short_usd"] for b in bins)
    assert long_usd == pytest.approx(500) and short_usd == pytest.approx(500)
    lb = next(b for b in bins if b["long_usd"]); sb = next(b for b in bins if b["short_usd"])
    assert lb["lo"] - 1e-9 <= 90 < lb["hi"] and sb["lo"] - 1e-9 <= 110 < sb["hi"] + 1e-9


def test_heatmap_price_crossing_liquidates_and_oi_drop_scales():
    prices = {0: (100, 100, 100), 1: (100, 100, 100), 2: (100, 89, 95), 3: (96, 94, 95)}
    series = {"x": {0: 0.0, 1: 1000.0, 2: 1000.0, 3: 750.0}}
    ref, bins, _ = heatmap.simulate(prices, series, rng=0.5, leverage=[(10, 1.0)])
    assert sum(b["long_usd"] for b in bins) == 0  # 90'daki long seviyesi düşük 89 ile silindi
    assert sum(b["short_usd"] for b in bins) == pytest.approx(250)  # 500'lük short, OI 1000 -> 750 düşüşüyle %50 azalır (toplam 500 açık)


def test_heatmap_missing_venue_points_are_skipped():
    prices = {0: (1, 1, 1), 1: (1, 1, 1), 2: (1, 1, 1)}
    _, _, used = heatmap.simulate(prices, {"a": {0: 1, 2: 3}, "b": {}}, rng=0.1)
    assert used == {"a": 1, "b": 0}


def test_clusters_sorted_by_size():
    bins = [{"mid": 90, "long_usd": 5, "short_usd": 0}, {"mid": 95, "long_usd": 9, "short_usd": 0}, {"mid": 110, "long_usd": 0, "short_usd": 7}]
    c = heatmap.clusters(bins, 100, k=1)
    assert [(x["side"], x["price"]) for x in c] == [("long", 95), ("short", 110)]
    assert c[0]["dist_pct"] == pytest.approx(-5)


def test_funding_normalization():
    assert rest.to_8h(0.0001, 8) == pytest.approx(0.0001)
    assert rest.to_8h(0.0001, 4) == pytest.approx(0.0002)
    assert rest.to_8h(0.0001, 1) == pytest.approx(0.0008)
    assert rest.to_8h(0.0001, 0) == pytest.approx(0.0001)


def test_nice_step():
    assert orderbook.nice_step(61700) == 50
    assert orderbook.nice_step(1730) == 2
    assert orderbook.nice_step(73) == pytest.approx(0.1)


def test_orderbook_summary_bands_walls_imbalance():
    mid = 100.0
    bids = [(99.9, 10), (99.0, 100), (97.0, 5)]
    asks = [(100.1, 10), (101.0, 20)]
    s = orderbook.summarize({"v": (bids, asks)}, mid, step=0.1)
    b1 = s["bands"]["1"]
    assert b1["bid_usd"] == pytest.approx(99.9 * 10 + 99 * 100)
    assert b1["ask_usd"] == pytest.approx(100.1 * 10 + 101 * 20)
    assert -1 <= b1["imbalance"] <= 1 and b1["imbalance"] > 0
    assert s["walls"]["bid"][0]["price"] == pytest.approx(99.0)  # 99.9 en iyi alışa yapışık, duvar sayılmaz
    assert all(w["price"] > mid * 1.002 for w in s["walls"]["ask"])


def test_option_metrics_max_pain_and_greeks():
    rows = [{"strike": k, "side": sd, "oi": oi, "forward": 100, "iv": 0.5, "rate": 0} for k, sd, oi in
            [(90, "P", 10), (100, "C", 5), (100, "P", 5), (110, "C", 10)]]
    m = option_metrics.expiry_metrics(rows, 100, 7 * 86400)
    assert m["max_pain"] == 100
    assert m["put_call"] == pytest.approx(1.0)
    g = option_metrics.greeks(100, 100, 0.5, 30 * 86400, 0, "C")
    assert 0.45 < g["delta"] < 0.6 and g["gamma"] > 0 and g["theta"] < 0


def test_split_message_keeps_tags_and_limits():
    text = "\n".join(f"<b>satır {i}</b> " + "x" * 90 for i in range(200))
    parts = fmt.split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert all(p.count("<b>") == p.count("</b>") for p in parts)
    assert "".join(p.replace("\n", "") for p in parts) == text.replace("\n", "")


def test_split_message_very_long_line():
    parts = fmt.split_message("y" * 9000, limit=3900)
    assert all(len(p) <= 3900 for p in parts) and sum(len(p) for p in parts) == 9000


def test_split_caption():
    cap, rest_ = fmt.split_caption("a\n" * 800, limit=1000)
    assert len(cap) <= 1000 and rest_
    assert fmt.split_caption("kısa", 1000) == ("kısa", "")


def test_usd_and_pct_format():
    assert fmt.usd(1_500_000) == "$1.5M" and fmt.usd(-2_000) == "-$2K" and fmt.usd(None) == "—"
    assert fmt.pct(1.234) == "+1.23%" and fmt.pct(None) == "—"
