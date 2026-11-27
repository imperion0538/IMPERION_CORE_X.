"""FRED günlük gözlemleri: ABD tahvil getirisi, geniş dolar endeksi ve S&P 500."""
import asyncio
import csv
import io
import math
from datetime import date, datetime, timedelta, timezone

from . import http


SOURCE = "Federal Reserve Economic Data (FRED)"
URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
# The broad dollar series can be published in weekly batches.
SERIES = {
    "DGS10": ("ABD 10 yıllık Hazine tahvili", "percent", 5),
    "DTWEXBGS": ("Nominal geniş ABD doları endeksi", "index_jan_2006_100", 10),
    "SP500": ("S&P 500 kapanış endeksi", "index_points", 5),
}


def parse_series(text, key, today):
    """Return latest dated, finite measurement; leave holidays and absent observations missing."""
    title, units, max_age = SERIES[key]
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames or not {"observation_date", key}.issubset(reader.fieldnames):
        raise ValueError("FRED CSV columns missing")
    observations = {}
    for row in reader:
        try:
            day = date.fromisoformat(row["observation_date"])
            value = float(row[key])
        except (ValueError, TypeError):
            continue
        if day <= today and math.isfinite(value):
            observations[day] = value
    base = {"title": title, "units": units, "url": f"https://fred.stlouisfed.org/series/{key}",
            "observation_date": None, "value": None, "status": "unavailable"}
    if observations:
        latest = max(observations)
        age = (today-latest).days
        base.update(value=observations[latest], observation_date=latest.isoformat(), age_days=age,
                    status="available" if age <= max_age else "stale")
    return base


async def _series(key):
    async def download():
        client = await http.session()
        today = datetime.now(timezone.utc).date()
        params = {"id": key, "cosd": (today - timedelta(days=45)).isoformat()}
        async with client.get(URL, params=params, timeout=9) as response:
            response.raise_for_status()
            # One series per request: the FRED multi-series endpoint responds with a ZIP.
            row = parse_series(await response.text(), key, today)
        row["fetched_utc"] = datetime.now(timezone.utc).isoformat()
        return row
    return await http.cached("macro-fred-" + key, 3600, download)


async def fetch():
    values = await asyncio.gather(*(_series(key) for key in SERIES), return_exceptions=True)
    series = {}
    for key, value in zip(SERIES, values):
        if isinstance(value, Exception):
            title, units, _ = SERIES[key]
            value = {"title": title, "units": units, "status": "unavailable", "value": None,
                     "observation_date": None, "error": type(value).__name__,
                     "url": f"https://fred.stlouisfed.org/series/{key}"}
        series[key] = value
    statuses = {v["status"] for v in series.values()}
    status = statuses.pop() if len(statuses) == 1 else "partial"
    fetched = [v["fetched_utc"] for v in series.values() if v.get("fetched_utc")]
    return {"source": SOURCE, "url": "https://fred.stlouisfed.org/", "series": series, "status": status,
            "fetched_utc": max(fetched, default=None), "frequency": "daily",
            "method": "Son yayımlanan günlük gözlem ve tarihi. Getiri yüzde, endeksler kendi birimleriyle gösterilir."}
