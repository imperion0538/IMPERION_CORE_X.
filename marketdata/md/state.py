"""Süreç içindeki son işlem fiyatı ve WebSocket bağlantı sağlığı."""
import time

last_price = {}
stream_health = {}

def touch(stream: str):
    h = stream_health.setdefault(stream, {"last_msg": 0, "connected": False, "reconnects": 0, "msgs": 0})
    h["last_msg"] = time.time(); h["msgs"] += 1; h["connected"] = True
