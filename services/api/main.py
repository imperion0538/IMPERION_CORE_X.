"""FastAPI service reading from Redis + DB."""
import os, json
from fastapi import FastAPI, Header, HTTPException
import redis.asyncio as redis

app = FastAPI(title="IMPERION Advanced API")
r = None

@app.on_event("startup")
async def startup():
    global r
    r = await redis.from_url(os.environ["REDIS_URL"])

@app.get("/health")
async def health():
    return {"status": "ok", "version": "advanced-v2"}

@app.get("/api/heatmap/{coin}")
async def heatmap(coin: str, window: str = "24h"):
    data = await r.get(f"heatmap:{coin.upper()}:{window}")
    if not data:
        raise HTTPException(404, "No heatmap yet")
    return json.loads(data)

@app.get("/api/cascade/{coin}")
async def cascade(coin: str):
    data = await r.get(f"cascade:{coin.upper()}")
    return json.loads(data) if data else {"cascade_risk": 0, "level": "UNKNOWN"}

@app.get("/api/analysis/{coin}")
async def analysis(coin: str):
    # Combine heatmap, cascade, flows from Redis
    hm = await r.get(f"heatmap:{coin.upper()}:24h")
    risk = await r.get(f"cascade:{coin.upper()}")
    return {
        "coin": coin.upper(),
        "heatmap": json.loads(hm) if hm else None,
        "cascade": json.loads(risk) if risk else None,
        "note": "Enhanced model – estimates only"
    }
