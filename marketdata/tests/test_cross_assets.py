from datetime import date
import pytest
from md import cross_assets


def test_fred_holidays_future_and_stale():
    csv = "observation_date,DGS10\n2026-11-24,4.2\n2026-11-25,\n2026-11-26,.\n2026-11-27,nan\n2026-11-30,0.0"
    latest = cross_assets.parse_series(csv, "DGS10", date(2026, 11, 27))
    assert latest["value"] == 4.2
    assert latest["observation_date"] == "2026-11-24" and latest["status"] == "available"
    stale = cross_assets.parse_series(csv, "DGS10", date(2026, 12, 12))
    assert stale["value"] == 0 and stale["status"] == "stale"


@pytest.mark.asyncio
async def test_fred_failure_only_affects_one_series(monkeypatch):
    async def series(key):
        if key == "SP500":
            raise TimeoutError
        return {"status": "available", "value": 123, "observation_date": "2026-11-23",
                "fetched_utc": "2026-11-24T12:00:00+00:00"}

    monkeypatch.setattr(cross_assets, "_series", series)
    result = await cross_assets.fetch()
    assert result["status"] == "partial"
    assert result["series"]["SP500"]["value"] is None
    assert result["series"]["DGS10"]["status"] == "available"
