"""Borsa erişim testi: Railway bölgesinden hangi borsa API'lerine ulaşılabildiğini gösterir."""
import json, os, socket, ssl, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

REST = {
    "binance_futures": "https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT",
    "binance_futures_data": "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=5m&limit=1",
    "binance_spot": "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
    "bybit": "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=5min&limit=1",
    "okx": "https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP",
    "coinbase": "https://api.exchange.coinbase.com/products/BTC-USD/ticker",
    "deribit": "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd",
    "bitget": "https://api.bitget.com/api/v2/mix/market/open-interest?symbol=BTCUSDT&productType=usdt-futures",
    "gate": "https://api.gateio.ws/api/v4/futures/usdt/contracts/BTC_USDT",
    "kraken_futures": "https://futures.kraken.com/derivatives/api/v3/tickers/PF_XBTUSD",
    "bitmex": "https://www.bitmex.com/api/v1/instrument?symbol=XBTUSD&count=1",
    "htx": "https://api.hbdm.com/linear-swap-api/v1/swap_open_interest?contract_code=BTC-USDT",
    "mexc": "https://contract.mexc.com/api/v1/contract/ticker?symbol=BTC_USDT",
}
WS = {"binance_ws": "fstream.binance.com", "bybit_ws": "stream.bybit.com", "okx_ws": "ws.okx.com",
      "coinbase_ws": "ws-feed.exchange.coinbase.com", "deribit_ws": "www.deribit.com", "hyperliquid_ws": "api.hyperliquid.xyz"}

def rest(name, url):
    t = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "imperion-probe/1.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            body = r.read(200).decode("utf-8", "replace")
            return name, {"ok": True, "status": r.status, "ms": int((time.time() - t) * 1000), "sample": body[:120]}
    except urllib.error.HTTPError as e:
        return name, {"ok": False, "status": e.code, "ms": int((time.time() - t) * 1000), "sample": e.read(160).decode("utf-8", "replace")}
    except Exception as e:
        return name, {"ok": False, "error": str(e)[:160]}

def tls(name, host):
    t = time.time()
    try:
        with socket.create_connection((host, 443), timeout=8) as s:
            with ssl.create_default_context().wrap_socket(s, server_hostname=host):
                return name, {"ok": True, "ms": int((time.time() - t) * 1000)}
    except Exception as e:
        return name, {"ok": False, "error": str(e)[:160]}

def hyperliquid():
    t = time.time()
    try:
        req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=b'{"type":"meta"}', headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=12) as r:
            return "hyperliquid", {"ok": True, "status": r.status, "ms": int((time.time() - t) * 1000)}
    except Exception as e:
        return "hyperliquid", {"ok": False, "error": str(e)[:160]}

def run():
    with ThreadPoolExecutor(20) as ex:
        jobs = [ex.submit(rest, n, u) for n, u in REST.items()] + [ex.submit(tls, n, h) for n, h in WS.items()] + [ex.submit(hyperliquid)]
        res = dict(j.result() for j in jobs)
    return {"region": os.environ.get("RAILWAY_REPLICA_REGION", "?"), "checked_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "ok": sorted(k for k, v in res.items() if v.get("ok")), "blocked": sorted(k for k, v in res.items() if not v.get("ok")), "detail": res}

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok"} if self.path == "/health" else run(), indent=1).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

if __name__ == "__main__":
    print(json.dumps(run()), flush=True)
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), H).serve_forever()
