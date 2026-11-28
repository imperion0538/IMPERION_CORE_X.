"""İşlem öncesi katman: gözlem, canlı türev örtmesi, dosya ve Hyperliquid defteri."""
import time
from datetime import datetime, timedelta, timezone

from md import derivfeed, intelligence, livebook, observe


def test_minute_fold_keeps_usd_basis_and_does_not_invent_a_score():
    rows = [
        {"venue": "coinbase", "market": "spot", "buy_usd": 70, "sell_usd": 30, "close": 100},
        {"venue": "binance", "market": "spot", "buy_usd": 10, "sell_usd": 40, "close": 101},
        {"venue": "hyperliquid", "market": "perp", "buy_usd": 20, "sell_usd": 80, "close": 102},
        {"venue": "bybit", "market": "perp", "buy_usd": 5, "sell_usd": 5, "close": 99},
    ]
    folded = observe.fold(rows)
    assert folded["spot_buy_usd"] == 80 and folded["perp_sell_usd"] == 85
    assert folded["venue_median"] == 100.5
    assert folded["usd_basis_bps"] == (102 / 100 - 1) * 1e4
    assert folded["sources"] == 4
    assert "score" not in folded
    assert observe.fold([]) is None


def test_observation_windows_mark_same_direction_only_when_both_sides_exist():
    now = datetime(2026, 11, 28, tzinfo=timezone.utc)
    rows = [{
        "ts": now - timedelta(minutes=i), "spot_buy_usd": 3, "spot_sell_usd": 1,
        "perp_buy_usd": 1, "perp_sell_usd": 4, "usd_basis_bps": 12.5, "sources": 6,
    } for i in range(1, 21)]
    out = observe.windows(rows, now)
    assert out["windows"]["15m"]["same_direction"] is False
    assert out["windows"]["15m"]["minutes"] == 14
    assert out["windows"]["15m"]["spot_delta_usd"] == 28
    assert out["windows"]["15m"]["usd_basis_bps"] == 12.5
    assert observe.windows([], now)["status"] == "unavailable"


def test_live_overlay_replaces_only_fresh_fields():
    venues = {"binance": {"price": 1, "oi_usd": 10, "funding_8h": 0.01, "age_seconds": 200, "snapshot_utc": "old"}}
    live = {("binance", "BTC"): {"price": 2, "price_ts": 1000, "price_transport": "websocket",
                                 "funding_8h": 0.02, "funding_ts": 990, "funding_transport": "websocket",
                                 "extra": {"premium": 0.001}}}
    out = derivfeed.overlay_live(venues, live, "BTC", now=1005)
    assert out["binance"]["price"] == 2
    assert out["binance"]["oi_usd"] == 10
    assert out["binance"]["funding_8h"] == 0.02
    assert out["binance"]["age_seconds"] == 15
    assert out["binance"]["premium"] == 0.001
    stale = {("bybit", "BTC"): {"price": 9, "price_ts": 100, "price_transport": "websocket"}}
    assert "bybit" not in derivfeed.overlay_live({}, stale, "BTC", now=1005)


def test_dossier_is_not_an_order_and_keeps_the_model_out():
    frames = {"4h": {"close": 90, "prior_20_high": 100, "prior_20_low": 80, "regime": "karışık / yatay"},
              "1h": {"close": 90, "prior_20_high": 100, "prior_20_low": 80, "regime": "karışık / yatay"},
              "15m": {"close": 90, "prior_20_high": 100, "prior_20_low": 80, "regime": "karışık / yatay"}}
    flows = {"15m": {"markets": {"spot": {"delta_usd": -5}, "perp": {"delta_usd": -2}}}}
    books = {"sources": [{"venue": "hyperliquid", "market": "perp", "status": "live", "spread_bps": 1.2,
                          "slippage": {"10000": {"buy": {"slippage_bps": 1.5}, "sell": {"slippage_bps": 1.1}},
                                       "100000": {"buy": {"slippage_bps": None}, "sell": {"slippage_bps": 4}}}}]}
    assessment = {"state": "İNCELEME HAZIR", "counter_evidence": [], "blockers": []}
    dossier = intelligence.build_dossier("BTC", frames, flows, {"venues": {}, "funding_8h_weighted": None, "oi_total_usd": None, "oi_change_pct": {}},
                                        books, assessment, {"observed": [{"side": "long", "usd": 10}, {"side": "short", "usd": 4}]}, None)
    assert dossier["watch"] == "BAKILIR" and dossier["signal"] is None and dossier["automatic_execution"] is False
    assert dossier["structure"]["4h"]["structure"] == "aralık"
    assert dossier["flow"]["15m"]["same_direction"] is True
    assert dossier["cost"][0]["buy_10k_bps"] == 1.5 and dossier["cost"][0]["buy_100k_bps"] is None
    assert dossier["liquidation_model"]["included"] is False
    assert dossier["realized_liquidations"]["kind"] == "observed_liquidation_prints"
    assert dossier["realized_liquidations"]["long_usd"] == 10
    blocked = intelligence.build_dossier("SOL", {}, {}, {}, {"sources": []},
                                         {"state": "VERİ YETERSİZ", "counter_evidence": [], "blockers": ["yok"]}, None, {"stale": False})
    assert blocked["watch"] == "BAKMA" and blocked["options"] is None


def test_hyperliquid_book_is_a_full_snapshot_and_reports_slippage():
    book = livebook.Book("hyperliquid", "perp", "BTC")
    book.apply([[100, 2], [99, 3]], [[101, 2], [102, 3]], 5, time.time(), snapshot=True)
    view = book.view()
    assert view["status"] == "live" and view["sequence_check"] == "full snapshot"
    small = livebook.sweep(book, "buy", 100)
    assert small["complete"] and small["slippage_bps"] == 0
    large = view["slippage"]["10000"]["buy"]
    assert large["complete"] is False and large["slippage_bps"] is None
    assert livebook.snapshot("BTC")["expected_sources"] == 5

