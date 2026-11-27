"""Paylaşılan HTTP istemcisi ve basit önbellek."""
import asyncio, json, time
import aiohttp
from urllib.parse import urlsplit

_session: aiohttp.ClientSession | None = None
_cache: dict = {}
_locks: dict = {}
_host_limits: dict = {}

async def session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=20), headers={"User-Agent": "imperion-md/1.0"})
    return _session

async def get(url, params=None):
    s = await session()
    gate = _host_limits.setdefault(urlsplit(url).netloc, asyncio.Semaphore(4))
    for attempt in range(3):
        async with gate:
            async with s.get(url, params=params) as r:
                if r.status in (429, 502, 503, 504) and attempt < 2:
                    await r.read()
                    try:
                        delay = min(5, max(.25, float(r.headers.get("Retry-After", 2**attempt))))
                    except ValueError:
                        delay = 2**attempt
                else:
                    r.raise_for_status()
                    return await r.json(content_type=None)
        await asyncio.sleep(delay)

async def post(url, body):
    s = await session()
    async with s.post(url, json=body) as r:
        r.raise_for_status()
        return await r.json(content_type=None)

async def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    # Aynı coin raporunun panel, kart ve Telegram isteğinde eşzamanlı çoğalmasını önler.
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = await fn()
        _cache[key] = (time.time(), val)
        if len(_cache) > 512:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)
        return val
