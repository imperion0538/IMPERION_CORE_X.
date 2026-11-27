"""Sinyal ve skorlama katmanı. Heuristic skorlar; observed/estimated/insufficient_data status zorunlu."""
import math
from datetime import datetime, timezone
from . import analysis, advanced, macro

UTC = timezone.utc

def safe_num(x):
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None

def score_direction(a):
    """Basit yön bias skoru -100..+100. Pozitif = long bias."""
    score = 0
    reasons = []
    p = a.get("price_change_pct", {})
    if p.get("4h", 0) > 1:
        score += 15; reasons.append("4h fiyat yükseliş")
    elif p.get("4h", 0) < -1:
        score -= 15; reasons.append("4h fiyat düşüş")

    oc = a.get("derivatives", {}).get("oi_change_pct", {}).get("4h")
    if oc is not None:
        if oc > 2 and p.get("4h", 0) > 0:
            score += 10; reasons.append("OI artışı + fiyat yükseliş")
        elif oc > 2 and p.get("4h", 0) < 0:
            score -= 10; reasons.append("OI artışı + fiyat düşüş (yeni short?)")

    fa = a.get("derivatives", {}).get("funding_ann_pct")
    if fa is not None:
        if fa > 30:
            score -= 12; reasons.append("aşırı long funding")
        elif fa < -10:
            score += 8; reasons.append("short funding")

    cp = a.get("coinbase_premium_pct")
    if cp is not None:
        if cp > 0.05:
            score += 10; reasons.append("pozitif Coinbase primi")
        elif cp < -0.05:
            score -= 10; reasons.append("negatif Coinbase primi")

    f4 = a.get("flows", {}).get("4h", {})
    cvd_p = f4.get("cvd", {}).get("perp", {}).get("delta")
    if cvd_p and abs(cvd_p) > 10_000_000:
        score += 8 if cvd_p > 0 else -8
        reasons.append(f"perp CVD {'alım' if cvd_p > 0 else 'satım'} baskısı")

    return {"score": max(-100, min(100, score)), "reasons": reasons, "status": "estimated"}

def extract_features(a, adv, mac):
    features = {
        "oi_change_4h": safe_num(a.get("derivatives", {}).get("oi_change_pct", {}).get("4h")),
        "funding_ann": safe_num(a.get("derivatives", {}).get("funding_ann_pct")),
        "coinbase_premium": safe_num(a.get("coinbase_premium_pct")),
        "cascade_score": safe_num(adv.get("cascade", {}).get("score")) if isinstance(adv, dict) else None,
        "dxy": safe_num(mac.get("dxy")) if isinstance(mac, dict) else None,
        "us10y": safe_num(mac.get("us10y")) if isinstance(mac, dict) else None,
    }
    return {k: v for k, v in features.items() if v is not None}

async def compute_scores(coin="BTC"):
    a = await analysis.analysis(coin)
    adv = await advanced.research(coin)
    mac = await macro.latest_macro()
    direction = score_direction(a)
    features = extract_features(a, adv, mac)
    return {
        "coin": coin,
        "generated_utc": datetime.now(UTC).isoformat(),
        "direction_bias": direction,
        "cascade": adv.get("cascade") if isinstance(adv, dict) else {"status": "unavailable"},
        "features": features,
        "macro_status": mac.get("status", "insufficient_data"),
        "status": "observed" if a else "insufficient_data"
    }
