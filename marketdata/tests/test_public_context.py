from datetime import datetime, timezone
import pytest
from md import http, public_context


def test_etf_unreported_is_not_zero_and_wrong_total_is_rejected():
    html = """<table>
        <tr><td>25 Nov 2026</td><td>(2.5)</td><td>1.0</td><td>(1.5)</td></tr>
        <tr><td>26 Nov 2026</td><td>1.0</td><td>1.0</td><td>10.0</td></tr>
        <tr><td>27 Nov 2026</td><td>-</td><td>-</td><td>0.0</td></tr>
        </table>"""
    rows = public_context.etf_rows(html)
    assert rows[0]["total_usd"] == -1_500_000
    assert rows[1]["total_usd"] is None and not rows[1]["reconciled"]
    assert rows[2]["total_usd"] is None and rows[2]["reported_funds"] == 0
    lines = public_context.summary({"etf_flows": {"status": "available", "rows": rows, "latest_complete": rows[0]}})
    assert any("25" in line and "-1.5" in line for line in lines)
    assert any("27" in line and "doğrulanmadı" in line for line in lines)


def test_calendar_filters_and_utc():
    now = datetime(2026, 11, 27, 13, tzinfo=timezone.utc)
    data = [{"date": "2026-11-27T09:00:00-05:00", "country": "USD", "impact": "High", "title": "Core CPI"},
            {"date": "2026-11-27T09:00:00", "country": "USD", "impact": "High", "title": "No time zone"},
            {"date": "2026-11-27T13:00:00Z", "country": "GBP", "impact": "High", "title": "Other"}]
    calendar = public_context.calendar_rows(data, now)
    assert [e["name"] for e in calendar["events"]] == ["Core CPI"]
    assert calendar["events"][0]["minutes_until"] == 60
    assert calendar["coverage_end_utc"].startswith("2026-11-27T14:00")


@pytest.mark.asyncio
async def test_provider_outage_is_isolated_and_cached(monkeypatch):
    calls = []
    async def fails():
        calls.append(1)
        raise RuntimeError("offline")
    async def ok(*args):
        return {"source": "fixture", "fetched_utc": "2026-11-26T11:00:00+00:00"}
    monkeypatch.setattr(public_context, "stablecoins", fails)
    for k in ("etf", "calendar", "news", "network"):
        monkeypatch.setattr(public_context, k, ok)
    monkeypatch.setattr(public_context.cross_assets, "fetch", ok)
    monkeypatch.setattr(http, "_cache", {})
    r = await public_context.load("BTC")
    again = await public_context.load("BTC")
    assert r["stablecoin_liquidity"]["status"] == "unavailable" and len(calls) == 1
    assert "fetched_utc" not in r["stablecoin_liquidity"]
    assert again["macro_calendar"] == r["macro_calendar"]
    assert r["macro_calendar"]["fetched_utc"].startswith("2026-11-26")


def test_stablecoin_missing_prior_supply_stays_missing():
    r = public_context.stablecoin_metrics({"peggedAssets": [{"symbol":"USDT","circulating":{"peggedUSD":105},
                                        "circulatingPrevWeek":{"peggedUSD":100}}]})
    assert r["assets"][0]["change_pct"]["1d"] is None
    assert r["assets"][0]["change_pct"]["7d"] == pytest.approx(5)
