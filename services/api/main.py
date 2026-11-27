"""FastAPI service reading from Redis + DB (includes options flow)."""
import os, json
from fastapi import FastAPI, HTTPException
import redis.asyncio as redis

app = FastAPI(title="IMPERION Advanced API")
r = None

@app.on_event("startup")
async def startup():
    global r
    r = await redis.from_url(os.environ["REDIS_URL"])

@app.get("/health")
async def health():
    return {"status": "ok", "version": "advanced-v2+options"}

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

@app.get("/api/options/{coin}")
async def options_summary(coin: str):
    data = await r.get(f"options:summary:{coin.upper()}")
    if not data:
        raise HTTPException(404, "No options summary yet")
    return json.loads(data)

@app.get("/api/options/flow/{coin}")
async def options_flow(coin: str):
    data = await r.get(f"options:flow:{coin.upper()}")
    if not data:
        raise HTTPException(404, "No options flow yet")
    return json.loads(data)

@app.get("/api/analysis/{coin}")
async def analysis(coin: str):
    hm = await r.get(f"heatmap:{coin.upper()}:24h")
    risk = await r.get(f"cascade:{coin.upper()}")
    opt_sum = await r.get(f"options:summary:{coin.upper()}")
    opt_flow = await r.get(f"options:flow:{coin.upper()}")
    return {
        "coin": coin.upper(),
        "heatmap": json.loads(hm) if hm else None,
        "cascade": json.loads(risk) if risk else None,
        "options_summary": json.loads(opt_sum) if opt_sum else None,
        "options_flow": json.loads(opt_flow) if opt_flow else None,
        "note": "Enhanced model with Deribit options flow – estimates only"
    }
