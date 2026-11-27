"""Canlı L2: Binance spot sıra zinciri, Bybit spot/perp sıralı snapshot+delta.

Delta'daki boyut azalması emir iptali veya gerçekleşme olabilir; burada ayrıştırılmaz.
Bybit u/seq monotoniktir, ardışık +1 olması şart değildir. Kesin boşluk doğrulaması
yalnız Binance U/u zincirinde yapılır. Defter derinliği sınırlıdır.
"""
import asyncio
import math
import time
from dataclasses import dataclass, field
from . import config, http, quality, ws


@dataclass
class Book:
    venue: str
    market: str
    coin: str
    bids: dict = field(default_factory=dict)
    asks: dict = field(default_factory=dict)
    update_id: int = 0
    received: float = 0
    event_ts: float = 0
    valid: bool = False
    invalid_reason: str = "snapshot bekleniyor"
    resets: int = 0

    def invalidate(self, reason):
        self.valid = False
        self.invalid_reason = reason
        self.resets += 1

    def apply(self, bids, asks, update_id, event_ts, *, snapshot=False, first_id=None):
        if not snapshot:
            if not self.valid:
                raise ws.ResyncRequired("snapshot required")
            if update_id <= self.update_id:
                return False
            if first_id is not None and not first_id <= self.update_id + 1 <= update_id:
                self.invalidate("sıra boşluğu")
                quality.counters["book_sequence_gaps"] += 1
                raise ws.ResyncRequired("sequence gap")
        if snapshot:
            self.bids, self.asks = {}, {}
        for target, levels in ((self.bids, bids), (self.asks, asks)):
            for row in levels:
                p, q = float(row[0]), float(row[1])
                if not math.isfinite(p) or not math.isfinite(q) or p <= 0 or q < 0:
                    self.invalidate("geçersiz kademe")
                    raise ws.ResyncRequired("invalid level")
                if q:
                    target[p] = q
                else:
                    target.pop(p, None)
        if not self.bids or not self.asks or max(self.bids) >= min(self.asks):
            self.invalidate("çapraz veya boş defter")
            raise ws.ResyncRequired("crossed book")
        if len(self.bids) > 2000:
            self.bids = dict(sorted(self.bids.items(), reverse=True)[:2000])
        if len(self.asks) > 2000:
            self.asks = dict(sorted(self.asks.items())[:2000])
        self.update_id, self.event_ts, self.received = update_id, event_ts, time.time()
        self.valid, self.invalid_reason = True, None
        return True

    def view(self, now=None):
        now = time.time() if now is None else now
        age = now - self.event_ts if self.event_ts else None
        usable = self.valid and age is not None and -1 <= age < 10 and now - self.received < 10
        base = {"venue": self.venue, "market": self.market, "status": "live" if usable else "stale" if self.valid else "resync",
                "age_seconds": round(age, 2) if age is not None else None,
                "sequence_check": "U/u chain" if self.venue == "binance" else "monotonic u; transport reconnect resets",
                "reason": self.invalid_reason, "resets": self.resets, "update_id": self.update_id}
        if not usable:
            return base
        bid, ask = max(self.bids), min(self.asks)
        mid = (bid + ask) / 2
        bands = {}
        for bps in (5, 10, 25, 50):
            buy = sum(p*q for p, q in self.bids.items() if p >= mid*(1-bps/1e4))
            sell = sum(p*q for p, q in self.asks.items() if p <= mid*(1+bps/1e4))
            bands[str(bps)] = {"bid_usd": buy, "ask_usd": sell,
                               "imbalance": (buy-sell)/(buy+sell) if buy+sell else None,
                               "fully_covered": min(self.bids) <= mid*(1-bps/1e4) and max(self.asks) >= mid*(1+bps/1e4)}
        base.update({"mid": mid, "spread_bps": (ask-bid)/mid*1e4, "bands_bps": bands,
                     "microprice": (ask*self.bids[bid]+bid*self.asks[ask])/(self.bids[bid]+self.asks[ask]),
                     "slippage": {str(size): {side: sweep(self, side, size) for side in ("buy", "sell")}
                                  for size in (10000, 100000, 1000000)}})
        return base


def sweep(book, side, notional):
    """Displayed liquidity only; no fill guarantee and no fees."""
    levels = sorted(book.asks.items()) if side == "buy" else sorted(book.bids.items(), reverse=True)
    remaining, base_qty, spent = notional, 0., 0.
    for p, q in levels:
        take = min(remaining, p*q)
        spent += take
        base_qty += take/p
        remaining -= take
        if remaining <= .000001:
            break
    complete = remaining <= .000001
    average = spent/base_qty if base_qty else None
    touch = levels[0][0] if levels else None
    return {"complete": complete, "filled_notional_usd": spent,
            "vwap": average if complete else None,
            "slippage_bps": (average/touch-1)*1e4*(1 if side == "buy" else -1) if complete else None}


BOOKS = {}


def get_book(venue, market, coin):
    return BOOKS.setdefault((venue, market, coin), Book(venue, market, coin))


async def binance_spot():
    symbols = {c+"USDT": c for c in config.COINS}
    url = "wss://stream.binance.com:9443/stream?streams=" + "/".join(s.lower()+"@depth@100ms" for s in symbols)
    async def on_open(socket):
        for c in config.COINS:
            get_book("binance", "spot", c).invalidate("yeniden bağlanıyor")
        for symbol, coin in symbols.items():
            d = await http.get("https://api.binance.com/api/v3/depth", {"symbol": symbol, "limit": 1000})
            get_book("binance", "spot", coin).apply(d["bids"], d["asks"], d["lastUpdateId"], 0, snapshot=True)
    async def on_msg(socket, message):
        d = message.get("data", message)
        if d.get("e") == "depthUpdate" and d["s"] in symbols:
            get_book("binance", "spot", symbols[d["s"]]).apply(d["b"], d["a"], d["u"], d["E"]/1000, first_id=d["U"])
    def disconnected():
        for coin in config.COINS:
            get_book("binance", "spot", coin).invalidate("bağlantı kesildi")
    await ws.run_stream("l2_binance_spot", url, on_open, on_msg, on_disconnect=disconnected)


async def bybit_market(market):
    symbols = {c+"USDT": c for c in config.COINS}
    category = "linear" if market == "perp" else "spot"
    async def on_open(socket):
        for c in config.COINS:
            get_book("bybit", market, c).invalidate("yeniden bağlanıyor")
        await socket.send_json({"op": "subscribe", "args": ["orderbook.50."+s for s in symbols]})
    async def ping(socket):
        await socket.send_json({"op": "ping"})
    async def on_msg(socket, message):
        if message.get("topic", "").startswith("orderbook."):
            d = message["data"]
            if d["s"] in symbols:
                get_book("bybit", market, symbols[d["s"]]).apply(
                    d["b"], d["a"], d["u"], message["ts"]/1000,
                    snapshot=message["type"] == "snapshot" or d["u"] == 1)
    def disconnected():
        for coin in config.COINS:
            get_book("bybit", market, coin).invalidate("bağlantı kesildi")
    await ws.run_stream("l2_bybit_"+market, "wss://stream.bybit.com/v5/public/"+category,
                        on_open, on_msg, ping, on_disconnect=disconnected)


async def bybit_spot():
    await bybit_market("spot")


async def bybit_perp():
    await bybit_market("perp")


def snapshot(coin):
    rows = [get_book(v, m, coin).view() for v, m in (("binance", "spot"), ("bybit", "spot"), ("bybit", "perp"))]
    return {"sources": rows, "live_sources": sum(r["status"] == "live" for r in rows),
            "method": "Observed visible L2. Size reductions do not distinguish cancellations from fills; hidden orders are unknown."}


ALL = [binance_spot, bybit_spot, bybit_perp]
