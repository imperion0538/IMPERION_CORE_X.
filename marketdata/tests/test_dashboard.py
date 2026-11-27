import json
from types import SimpleNamespace
import pytest
from md import api, config, http, state

@pytest.mark.asyncio
async def test_candles_validates_frame(monkeypatch):
    monkeypatch.setattr(config, "COINS", ["BTC"])
    response = await api.dashboard_candles(SimpleNamespace(query={"coin":"BTC","frame":"2m"}))
    assert response.status == 400

@pytest.mark.asyncio
async def test_candles_real_source_and_closed_flag(monkeypatch):
    monkeypatch.setattr(config, "COINS", ["BTC"])
    monkeypatch.setattr(api.time, "time", lambda: 1000)
    async def fake_get(url, params):
        assert params["symbol"] == "BTCUSDT"
        return [[0,"10","12","9","11","2",900000],[900001,"11","13","10","12","3",1800000]]
    async def no_cache(key, ttl, fn):
        return await fn()
    monkeypatch.setattr(http, "get", fake_get)
    monkeypatch.setattr(http, "cached", no_cache)
    response = await api.dashboard_candles(SimpleNamespace(query={"coin":"BTC","frame":"15m"}))
    data = json.loads(response.text)
    assert data["candles"][0]["closed"] is True
    assert data["candles"][1]["closed"] is False
    assert data["candles"][0]["c"] == 11

@pytest.mark.asyncio
async def test_tickers_missing_and_stale(monkeypatch):
    monkeypatch.setattr(config, "COINS", ["BTC","ETH","SOL"])
    monkeypatch.setattr(api.time, "time", lambda: 1000)
    monkeypatch.setattr(state, "last_price", {("binance","perp","BTC"):(42,990),("binance","perp","ETH"):(2,500)})
    response = await api.dashboard_tickers(SimpleNamespace())
    data = json.loads(response.text)
    assert data["BTC"]["price"] == 42
    assert data["ETH"]["price"] is None and data["SOL"]["price"] is None


def test_public_rate_limit_budget(monkeypatch):
    monkeypatch.setattr(api, "_hits", {})
    monkeypatch.setattr(api.time, "time", lambda: 1000)
    request = SimpleNamespace(headers={}, remote="192.0.2.1")
    assert all(api._rate_ok(request) for _ in range(120))
    assert not api._rate_ok(request)
    monkeypatch.setattr(api.time, "time", lambda: 1061)
    assert api._rate_ok(request)
