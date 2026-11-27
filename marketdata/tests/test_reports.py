import pytest

from md import reports


@pytest.mark.asyncio
async def test_report_sums_flow_and_identifies_missing_sources(monkeypatch):
    async def unavailable(*args):
        raise RuntimeError("upstream failed")

    async def heatmap(*args):
        return {"clusters": [{"side": "long", "dist_pct": -5}, {"side": "long", "dist_pct": -1}]}

    async def flow(*args, **kwargs):
        assert args == ("BTC", "15m", 60000, 62000)
        return {"by_venue_market": [{"market": "spot", "buy_usd": 10, "sell_usd": 4},
                                    {"market": "perp", "buy_usd": 2, "sell_usd": 8}],
                "trades": []}

    monkeypatch.setattr(reports.analysis, "analysis", unavailable)
    monkeypatch.setattr(reports.orderbook, "latest", unavailable)
    monkeypatch.setattr(reports.heatmap, "model", heatmap)
    monkeypatch.setattr(reports.whales, "report", flow)
    report = await reports.generate_report("BTC", min_price=60000, max_price=62000)
    assert report["sources"] == {"analysis": "error", "heatmap": "available",
                                  "orderbook": "error", "whale_flow": "available"}
    assert report["whale_liquidity"]["net_usd"] == 0
    assert report["whale_liquidity"]["by_market"]["spot"]["net_usd"] == 6
    assert report["whale_liquidity"]["by_market"]["perp"]["net_usd"] == -6
    assert report["risk_clusters"]["nearest_long"]["dist_pct"] == -1
