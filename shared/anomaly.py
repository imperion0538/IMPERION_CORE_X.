"""Anomaly detection and cascade risk helpers.

Uses simple statistical methods + optional IsolationForest.
"""
from typing import List, Dict
import statistics
import math

try:
    from sklearn.ensemble import IsolationForest
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


def zscore_anomaly(values: List[float], window: int = 60) -> List[float]:
    """Return z-scores for recent values. High absolute z = anomaly."""
    if len(values) < 10:
        return [0.0] * len(values)
    recent = values[-window:]
    mean = statistics.mean(recent)
    std = statistics.stdev(recent) or 1.0
    return [(v - mean) / std for v in values]


def detect_flow_anomaly(cvd_series: List[float], whale_series: List[float]) -> Dict:
    """Simple combined anomaly score for flows."""
    cvd_z = zscore_anomaly(cvd_series)
    whale_z = zscore_anomaly(whale_series)
    latest_score = abs(cvd_z[-1]) * 0.6 + abs(whale_z[-1]) * 0.4
    return {
        "anomaly_score": round(latest_score, 2),
        "is_anomalous": latest_score > 2.5,
        "cvd_z": round(cvd_z[-1], 2),
        "whale_z": round(whale_z[-1], 2)
    }


def isolation_forest_score(features: List[List[float]]) -> List[float]:
    """Skeleton for IsolationForest. features = [[cvd, whale_usd, liq_usd, ...], ...]"""
    if not HAS_SKLEARN or len(features) < 20:
        return [0.0] * len(features)
    clf = IsolationForest(contamination=0.05, random_state=42)
    clf.fit(features)
    scores = clf.decision_function(features)  # higher = more normal
    # Invert and normalize roughly to 0-100 anomaly
    anomaly = [max(0, -s * 20) for s in scores]
    return anomaly


def compute_cascade_risk(
    heatmap_clusters: List[Dict],
    current_price: float,
    funding_ann: float,
    cvd_4h: float,
    recent_liq_5m: float,
) -> Dict:
    """Composite cascade risk."""
    from .heatmap import cascade_score
    base = cascade_score(heatmap_clusters, current_price, recent_liq_5m)

    # Funding extreme adds risk
    funding_boost = 0.0
    if abs(funding_ann) > 30:
        funding_boost = min(abs(funding_ann) / 100 * 15, 20)

    # CVD divergence (price up but CVD down or vice versa) - simplified
    divergence_boost = min(abs(cvd_4h) / 20_000_000 * 10, 15)

    total = min(base + funding_boost + divergence_boost, 100.0)
    level = "LOW" if total < 30 else "MEDIUM" if total < 60 else "HIGH"

    return {
        "cascade_risk": round(total, 1),
        "level": level,
        "components": {
            "heatmap_proximity": round(base, 1),
            "funding_extreme": round(funding_boost, 1),
            "flow_pressure": round(divergence_boost, 1)
        }
    }
