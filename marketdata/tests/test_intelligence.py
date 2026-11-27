import asyncio
import math
import time
from datetime import datetime, timezone
import pytest
from md import candles, context, evidence, http, ingest, intelligence, livebook, quality, ws


def event(identity, ts, price, side="buy", qty=2):
    return ingest.normalize("trade", "BTC", "test", "spot", ts*1000, price, qty, side, identity, now=ts+1)


def test_native_identity_survives_receive_time_and_separates_venues():
    a = event("42", 1700000000, 100)
    b = ingest.normalize("trade", "BTC", "test", "spot", 1700000000000, 100, 2, "buy", "42", now=1700000010)
    c = ingest.normalize("trade", "BTC", "other", "spot", 1700000000000, 100, 2, "buy", "42", now=1700000010)
    assert a["id"] == b["id"] != c["id"]


@pytest.mark.parametrize("price,qty,ts", [(float("nan"), 1, 1000), (10, float("inf"), 1000), (0, 1, 1000), (10, -1, 1000), (10, 1, 2000), (10, 1, 1)])
def test_invalid_or_clock_skewed_events_are_rejected(price, qty, ts):
    with pytest.raises(ValueError):
        ingest.normalize("trade", "BTC", "v", "spot", ts*1000, price, qty, "buy", now=1000)


def test_event_time_ohlc_is_independent_of_arrival_order():
    minute = 1700000040
    rows = [event("3", minute+30, 99, "sell"), event("1", minute+1, 100), event("2", minute+20, 103)]
    a = ingest.aggregate(rows)[0]
    assert a[4:11] == (406, 198, 3, 100, 103, 99, 99)
    assert a == ingest.aggregate(list(reversed(rows)))[0]


def test_binance_book_sequence_gap_invalidates_instead_of_guessing():
    b = livebook.Book("binance", "spot", "BTC")
    b.apply([[99, 5]], [[101, 5]], 10, time.time(), snapshot=True)
    b.apply([[99, 0], [98, 3]], [[102, 1]], 12, time.time(), first_id=11)
    assert 99 not in b.bids and b.valid
    assert not b.apply([], [], 12, time.time(), first_id=11)  # duplicate
    with pytest.raises(ws.ResyncRequired):
        b.apply([], [], 20, time.time(), first_id=19)
    assert b.view()["status"] == "resync"
    assert "spread_bps" not in b.view()


def test_crossed_book_and_expired_quote_cannot_supply_a_cost():
    b = livebook.Book("bybit", "perp", "BTC")
    b.apply([[99, 5]], [[101, 5]], 10, time.time()-20, snapshot=True)
    assert b.view()["status"] == "stale" and "slippage" not in b.view()
    with pytest.raises(ws.ResyncRequired):
        b.apply([[102, 1]], [], 20, time.time())


def test_slippage_never_extrapolates_missing_depth():
    b = livebook.Book("bybit", "spot", "BTC")
    b.apply([[99, 10]], [[101, 2], [102, 3]], 5, time.time(), snapshot=True)
    assert livebook.sweep(b, "buy", 101)["slippage_bps"] == 0
    assert livebook.sweep(b, "buy", 300)["slippage_bps"] > 0
    missing = livebook.sweep(b, "buy", 1000)
    assert not missing["complete"] and missing["vwap"] is None


def test_closed_candles_reject_open_invalid_and_deduplicate():
    rows = [[0, 100, 105, 90, 101, 2], [0, 100, 105, 90, 101, 2],
            [3600000, 101, 106, 95, 103, 2], [7200000, 101, 106, 95, 103, 2],
            [10800000, 110, 100, 90, 95, 1]]
    assert [b["ts"] for b in candles.validate(rows, "1h", now=7300)] == [0, 3600]


def test_missing_flow_is_null_and_bad_coverage_is_not_counted_as_zero_flow():
    assert intelligence.combine_flow([])["delta_usd"] is None
    missing = {"comparable": False, "buy_usd": None, "sell_usd": None}
    good = {"comparable": True, "buy_usd": 70, "sell_usd": 30, "delta_usd": 40, "price_change_pct": .01}
    out = intelligence.combine_flow([missing, good])
    assert out["delta_fraction"] == .4 and out["comparable_sources"] == 1
    assert out["absorption_candidate"]


def test_missing_sources_block_readiness_and_do_not_generate_trade_signal():
    a = intelligence.assess({}, {}, {}, {}, {"live_sources": 0})
    assert a["state"] == "VERİ YETERSİZ" and a["signal"] is None
    assert len(a["blockers"]) >= 5 and not a["automatic_execution"]


def test_hourly_history_is_descriptive_and_gaps_disable_evidence():
    rows = [{"ts": i*3600, "close": 100*1.001**i} for i in range(1000)]
    r = evidence.evaluate(rows)
    assert r["samples"] > 30 and r["historical_positive_fraction"] == 1
    assert r["live_success_probability"] is None
    assert r["walk_forward"]["brier"] == r["walk_forward"]["baseline_brier"] == 0
    assert r["positive_fraction_ci95"][0] < 1
    assert evidence.evaluate(rows[:60])["status"] == "insufficient_history"
    assert evidence.evaluate(rows[:100]+rows[101:])["status"] == "insufficient_history"


def test_flat_market_indicators_are_neutral_and_finite():
    rows = [{"ts": i*3600, "close_ts": (i+1)*3600, "open": 100, "high": 100,
             "low": 100, "close": 100, "volume": 0} for i in range(300)]
    f = intelligence.technical({"rows": rows, "source": "fixture", "fallback": False, "gaps": 0, "interval": "1h"})
    assert f["rsi14"] == 50 and f["atr14"] == 0 and f["range_position"] is None
    assert f["vwap_24h_approx"] is None and f["volume_vs_20"] is None


def test_risk_budget_includes_costs_and_refuses_invalid_inputs():
    r = context.risk_budget(10000, .5, 100, 98, 5, 5)
    assert r["risk_budget_usd"] == 50
    assert r["quantity_base"] < 25
    assert r["quantity_base"]*2+r["modeled_cost_usd"] == pytest.approx(50)
    for args in [(1000, 1, 100, 100), (float("nan"), 1, 100, 90), (1000, -1, 100, 90)]:
        with pytest.raises(ValueError): context.risk_budget(*args)


@pytest.mark.asyncio
async def test_cache_singleflight_avoids_duplicate_exchange_requests():
    key = "test-singleflight"
    http._cache.pop(key, None)
    calls = []
    async def build():
        calls.append(1)
        await asyncio.sleep(.02)
        return 42
    values = await asyncio.gather(*(http.cached(key, 10, build) for _ in range(10)))
    assert values == [42]*10 and len(calls) == 1


@pytest.mark.asyncio
async def test_partial_report_survives_all_upstreams_failing(monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("fixture failure")
    http._cache.pop("intelligence:TEST", None)
    for module, name in ((candles, "load"), (intelligence, "flow_matrix"), (intelligence.analysis, "deriv"),
                         (intelligence.analysis, "options"), (intelligence.orderbook, "latest"),
                         (context, "breadth"), (intelligence.public_context, "load"), (intelligence.whales, "report"), (intelligence, "liquidations")):
        monkeypatch.setattr(module, name, fail)
    r = await intelligence.generate("TEST")
    assert r["assessment"]["state"] == "VERİ YETERSİZ"
    assert r["price_consensus"]["reference"] is None
    assert all(s["status"] == "error" for s in r["source_status"].values())
