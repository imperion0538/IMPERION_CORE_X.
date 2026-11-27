import os

def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)

COINS = [c.strip().upper() for c in os.environ.get("COINS", "BTC,ETH,SOL").split(",") if c.strip()]
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
WHALE_MIN_USD = _f("WHALE_MIN_USD", 250_000)
ONCHAIN_ENABLED = os.environ.get("ONCHAIN_ENABLED", "0") == "1"
