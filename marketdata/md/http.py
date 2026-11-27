"""Paylaşılan HTTP istemcisi ve basit önbellek."""
import asyncio, json, time
import aiohttp

_session: aiohttp.ClientSession | None = None
_cache: dict = {}

async def session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), headers={"User-Agent": "imperion-md/1.0"})
    return _session

async def get(url, params=None):
    s = await session()
    async with s.get(url, params=params) as r:
        r.raise_for_status()
        return await r.json(content_type=None)

async def post(url, body):
    s = await session()
    async with s.post(url, json=body) as r:
        r.raise_for_status()
        return await r.json(content_type=None)

async def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = await fn()
    _cache[key] = (time.time(), val)
    return val
