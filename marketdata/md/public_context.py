"""Ücretsiz kaynaklar: tarihle etiketlenen bağlam; eksik değerler asla sıfıra çevrilmez."""
import asyncio
import math
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
from . import http, cross_assets

UTC = timezone.utc


FEEDS = {
    "stablecoin_liquidity": ("DefiLlama", "Stablecoin arzı"),
    "etf_flows": ("Farside Investors", "Spot ETF"),
    "macro_calendar": ("Forex Factory / Fair Economy", "ABD takvimi"),
    "news": ("CoinDesk RSS", "Haber"),
    "network_activity": ("mempool.space", "Bitcoin ağı"),
    "macro_cross_assets": ("FRED", "Makro göstergeler"),
}


async def cached_feed(key, ttl, fetch):
    async def stamped():
        data = await fetch()
        return {**data, "fetched_utc": datetime.now(UTC).isoformat()}
    return await http.cached(key, ttl, stamped)


async def text_url(url):
    client = await http.session()
    async with client.get(url) as response:
        response.raise_for_status()
        text = await response.text()
        if len(text) > 3_000_000:
            raise ValueError("unexpected feed size")
        return text


def stablecoin_metrics(data):
    rows = []
    for r in data["peggedAssets"]:
        if r["symbol"] not in ("USDT", "USDC", "DAI", "USDS"):
            continue
        current = r.get("circulating", {}).get("peggedUSD")
        changes = {}
        for name, key in (("1d", "circulatingPrevDay"), ("7d", "circulatingPrevWeek"), ("30d", "circulatingPrevMonth")):
            previous = r.get(key, {}).get("peggedUSD")
            changes[name] = (current/previous-1)*100 if previous and current is not None else None
        rows.append({"symbol": r["symbol"], "circulating_usd": current, "price_usd": r.get("price"), "change_pct": changes})
    if not rows:
        raise ValueError("no recognized stablecoin data")
    return {"source": "DefiLlama", "url": "https://stablecoins.llama.fi/stablecoins?includePrices=true",
            "assets": rows, "source_timestamp": None,
            "method": "Provider-reported circulating USD value and previous-period values; not exchange inflows or investable cash."}


async def stablecoins():
    async def fetch():
        return stablecoin_metrics(await http.get("https://stablecoins.llama.fi/stablecoins", {"includePrices": "true"}))
    return await cached_feed("stablecoin-context", 1800, fetch)


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr": self.row = []
        if tag in ("td", "th") and self.row is not None: self.cell = []

    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def amount(cell):
    value = cell.strip().replace(",", "")
    if value in ("", "-", "—", "–"):
        return None
    if value.startswith("(") and value.endswith(")"):
        value = "-"+value[1:-1]
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("invalid amount")
    return number


def etf_rows(html):
    parser = TableParser(); parser.feed(html)
    found = {}
    for cells in parser.rows:
        if len(cells) < 4: continue
        try:
            date = datetime.strptime(cells[0], "%d %b %Y").date().isoformat()
            values = [amount(cell) for cell in cells[1:]]
        except (ValueError, TypeError):
            continue
        complete = all(v is not None for v in values)
        reconciled = complete and abs(sum(values[:-1])-values[-1]) <= max(1., .06*len(values))
        row = {"date": date, "total_usd": values[-1]*1_000_000 if reconciled else None,
               "complete": complete, "reconciled": reconciled,
               "reported_funds": sum(v is not None for v in values[:-1]), "funds": len(values)-1}
        if date not in found or (row["reconciled"] and not found[date]["reconciled"]):
            found[date] = row
    if not found:
        raise ValueError("ETF table format not recognized")
    return [found[d] for d in sorted(found)][-15:]


async def etf(coin):
    if coin not in ("BTC", "ETH"):
        return {"status": "unsupported", "reason": "This feed covers BTC and ETH only."}
    async def fetch():
        url = f"https://farside.co.uk/{coin.lower()}/"
        rows = etf_rows(await text_url(url))
        complete = [r for r in rows if r["reconciled"]]
        last = complete[-1] if complete else None
        days = (datetime.now(UTC).date()-datetime.fromisoformat(last["date"]).date()).days if last else None
        return {"source": "Farside Investors", "url": url, "rows": rows, "latest_complete": last,
                "status": "available" if days is not None and 0 <= days <= 5 else "stale",
                "latest_complete_age_days": days,
                "method": "Daily USD net flows, not intraday. Missing fund cells remain null even if published total is 0. Sum is reconciled within rounding tolerance."}
    return await cached_feed(f"etf-context:{coin}", 1800, fetch)


def calendar_rows(data, now=None):
    now = now or datetime.now(UTC)
    events, dates = [], []
    for row in data:
        try:
            stamp = datetime.fromisoformat(row["date"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                continue
            stamp = stamp.astimezone(UTC)
        except (KeyError, ValueError): continue
        dates.append(stamp)
        if row.get("country") == "USD" and row.get("impact") in ("High", "Medium") and now-timedelta(hours=1) <= stamp <= now+timedelta(days=7):
            events.append({"name": str(row["title"])[:180], "at_utc": stamp.isoformat(), "impact": row["impact"],
                           "minutes_until": round((stamp-now).total_seconds()/60),
                           "forecast": row.get("forecast") or None, "previous": row.get("previous") or None})
    if not dates: raise ValueError("calendar has no dated events")
    return {"source": "Forex Factory / Fair Economy public weekly calendar", "url": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            "status": "available" if max(dates).date() >= now.date() else "stale",
            "coverage_start_utc": min(dates).isoformat(), "coverage_end_utc": max(dates).isoformat(),
            "events": sorted(events, key=lambda r:r["at_utc"]), "method": "Current-week USD high/medium events; not a complete future calendar. No events does not mean no macro risk."}


async def calendar():
    async def fetch():
        return calendar_rows(await http.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json"))
    return await cached_feed("calendar-context", 900, fetch)


def news_rows(xml):
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        raise ValueError("XML entities not allowed")
    root = ET.fromstring(xml)
    rows = []
    for item in root.findall("./channel/item"):
        try:
            ts = parsedate_to_datetime(item.findtext("pubDate", "")).astimezone(UTC)
        except (TypeError, ValueError): continue
        url = item.findtext("link", "")
        if urlsplit(url).scheme != "https" or urlsplit(url).hostname not in ("www.coindesk.com", "coindesk.com"):
            continue
        rows.append({"headline": item.findtext("title", "")[:240], "url": url, "published_utc": ts.isoformat()})
    rows.sort(key=lambda r:r["published_utc"], reverse=True)
    if not rows: raise ValueError("no timestamped headlines")
    age = (datetime.now(UTC)-datetime.fromisoformat(rows[0]["published_utc"])).total_seconds()
    return {"source": "CoinDesk RSS", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "status": "available" if 0 <= age < 86400 else "stale", "headlines": rows[:8],
            "method": "Publisher headlines with original links; not independently fact-checked or scored for trade direction."}


async def news():
    async def fetch():
        return news_rows(await text_url("https://www.coindesk.com/arc/outboundfeeds/rss/"))
    return await cached_feed("news-context", 300, fetch)


async def network(coin):
    if coin != "BTC":
        return {"status": "unsupported", "reason": "Public network feed currently covers Bitcoin only."}
    async def fetch():
        fees, mempool = await asyncio.gather(http.get("https://mempool.space/api/v1/fees/recommended"),
                                           http.get("https://mempool.space/api/mempool"))
        return {"source": "mempool.space", "url": "https://mempool.space/", "fees_sat_vb": fees,
                "unconfirmed_count": mempool["count"], "unconfirmed_vsize": mempool["vsize"],
                "method": "Network congestion and fee estimates; not whale identification or exchange netflows."}
    return await cached_feed("btc-network-context", 120, fetch)


async def macro_with_fallback():
    from . import macro

    primary = await cross_assets.fetch()
    if primary["status"] == "available":
        return primary
    try:
        fallback = await asyncio.wait_for(macro.latest_macro(), timeout=2)
    except Exception:
        return primary
    if fallback.get("status") != "observed" or fallback.get("stale", True):
        return primary
    quotes = {k: fallback[k] for k in ("dxy", "us10y", "spx") if fallback.get(k) is not None}
    if not quotes:
        return primary
    return {**primary, "status": "partial", "fallback_quotes": quotes,
            "fallback_source": fallback["source"], "fallback_fetched_utc": fallback["fetched_utc"]}


async def load(coin):
    # A failed provider is retried once per minute; one source failure cannot block other evidence.
    async def get_status(name, fetch, scope):
        async def read():
            try:
                value = await asyncio.wait_for(fetch(), 12)
            except Exception as exc:
                value = {"status": "unavailable", "error": type(exc).__name__}
            return {"source": FEEDS[name][0], "status": "available", **value,
                    "checked_utc": datetime.now(UTC).isoformat()}
        return await http.cached(f"public-status:{name}:{scope}", 60, read)
    jobs = {"stablecoin_liquidity": (stablecoins, ""), "etf_flows": (lambda: etf(coin), coin),
            "macro_calendar": (calendar, ""), "news": (news, ""),
            "network_activity": (lambda: network(coin), coin), "macro_cross_assets": (macro_with_fallback, "")}
    values = await asyncio.gather(*(get_status(name, fn, scope) for name, (fn, scope) in jobs.items()))
    return dict(zip(jobs, values))


def summary(feeds, now=None):
    now = now or datetime.now(UTC)
    lines = []
    available = {k: v for k, v in feeds.items() if v.get("status") in ("available", "partial")}
    etf_data = available.get("etf_flows", {})
    last = etf_data.get("latest_complete")
    if last:
        lines.append(f"Spot ETF · {last['date']} net akış {last['total_usd']/1e6:+,.1f} milyon USD (Farside).")
        if etf_data["rows"][-1]["date"] > last["date"]:
            lines.append(f"{etf_data['rows'][-1]['date']} ETF toplamı henüz doğrulanmadı.")
    coins = available.get("stablecoin_liquidity", {}).get("assets", [])
    for asset in coins:
        if asset.get("symbol") == "USDT" and asset["change_pct"].get("7d") is not None:
            lines.append(f"USDT dolaşımdaki arzı 7 günde %{asset['change_pct']['7d']:+.2f} (DefiLlama).")
            break
    cal = available.get("macro_calendar", {})
    events = [e for e in cal.get("events", []) if datetime.fromisoformat(e["at_utc"]) >= now]
    if events:
        event = events[0]
        stamp = datetime.fromisoformat(event["at_utc"]).astimezone(UTC)
        lines.append(f"ABD takvimi · {stamp:%d.%m %H:%M} UTC · {event['name']} ({'yüksek' if event['impact'] == 'High' else 'orta'} etki).")
    elif cal.get("coverage_end_utc"):
        lines.append(f"Haftalık takvimde {cal['coverage_end_utc'][:10]} sonuna kadar listelenmiş başka orta/yüksek etkili USD olayı yok.")
    for code, row in available.get("macro_cross_assets", {}).get("series", {}).items():
        if row.get("status") == "available":
            label = {"SP500": "S&P 500", "DGS10": "ABD 10 yıllık faiz (%)", "DTWEXBGS": "Fed geniş dolar endeksi"}.get(code, code)
            lines.append(f"{label} · {row['value']:,.2f} ({row['observation_date']}, FRED).")
    macro_data = available.get("macro_cross_assets", {})
    fallback = macro_data.get("fallback_quotes", {})
    if fallback:
        labels = {"dxy": "DXY", "us10y": "ABD 10 yıllık faiz (%)", "spx": "S&P 500"}
        values = ", ".join(f"{labels[key]} {value:,.2f}" for key, value in fallback.items())
        lines.append(f"{values} ({macro_data['fallback_source']}, {macro_data['fallback_fetched_utc'][:16]} UTC).")
    network_data = available.get("network_activity", {})
    if network_data.get("unconfirmed_count") is not None:
        lines.append(f"Bitcoin ağında {network_data['unconfirmed_count']:,} onaysız işlem (mempool.space).")
    headlines = available.get("news", {}).get("headlines", [])
    if headlines:
        h = headlines[0]
        lines.append(f"CoinDesk · {h['published_utc'][:16]} UTC · {h['headline']}")
    for key, label in FEEDS.items():
        status = feeds.get(key, {}).get("status", "unavailable")
        if status in ("stale", "unavailable", "error", "partial"):
            state = {"stale":"eski veri", "partial":"kısmi kapsam"}.get(status, "veri alınamadı")
            lines.append(f"{label[1]} ({label[0]}) · {state}.")
    lines.append("Cüzdan sahipliği doğrulanmış borsa net akışının kapsamı yok.")
    return lines
