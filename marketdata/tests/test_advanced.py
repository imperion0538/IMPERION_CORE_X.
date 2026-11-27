from md.advanced import aggregate_options, anomaly, cascade

def trade(id, typ="C", direction="buy", index=100):
    return {"trade_id": id, "instrument_name": f"BTC-30DEC26-100-{typ}",
            "timestamp": 1000, "amount": 2, "price": .1, "index_price": index, "direction": direction}

def test_options_dedup_and_zero_ratio():
    t = trade("a")
    r = aggregate_options([t,t],0,2000)
    assert r["trade_count"] == 1
    assert r["buy_premium_usd"] == 20
    assert r["put_call_premium_ratio"] == 0

def test_options_missing_index_and_truncated():
    r = aggregate_options([trade("a",index=None)],0,2000,True)
    assert r["status"] == "partial" and r["trade_count"] == 0
    assert r["rejected"] == 1

def test_options_no_calls():
    assert aggregate_options([trade("a","P","sell")],0,2000)["put_call_premium_ratio"] is None

def test_cascade_requires_observed_data():
    assert cascade(100, [], 10, 1, None, 1, {"binance":3})["score"] is None
    assert cascade(100, [], 10, 1, 1, .5, {"binance":3})["score"] is None
    assert cascade(100, [], 10, 1, 1, 1, {})["score"] is None
    r = cascade(100,[{"price":99,"usd":100_000_000}],50,10_000_000,10_000_000,1,{"binance":10})
    assert r["status"] == "estimated" and 0 <= r["score"] <= 100

def test_anomaly_does_not_use_current_bucket():
    assert anomaly([1,2],100)["z"] is None
    assert anomaly([1]*12,100)["z"] is None
    assert anomaly(list(range(12)),100)["anomalous"]
