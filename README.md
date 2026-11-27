# IMPERION CORE X - Advanced Market Data Platform

Multi-exchange crypto derivatives monitoring system with real-time streaming, advanced analytics, anomaly detection, cascade risk scoring, estimated + enhanced liquidation heatmaps, and on-chain signals.

## Architecture (v2)

```
Collectors (WS + REST) → NATS JetStream → Processors (heatmap, anomaly, cascade, aggregation)
                              ↓
                         Redis (hot state + cache) + TimescaleDB (historical)
                              ↓
                    API (FastAPI) + Dashboard (Streamlit) + Telegram Bot
```

### Services
- `services/collector` – WebSocket & REST collectors publishing to NATS
- `services/processor` – Consumes events, builds heatmaps, anomaly scores, cascade risk, writes to Redis/DB
- `services/api` – FastAPI serving enriched analysis
- `services/dashboard` – Streamlit real-time dashboard
- `services/onchain` – On-chain large transaction / exchange flow collector (skeleton)
- `shared/` – Common models, improved heatmap, anomaly engine

## Key Improvements over v1
- Decoupled collectors / processors via NATS
- Redis for hot state, alerts, recent flows
- Improved heatmap (OI delta + leverage distribution + order-book walls placeholder + cascade proximity score)
- Anomaly detection (z-score + IsolationForest skeleton) on CVD / whale / liquidation flows
- Cascade risk score combining heatmap clusters, funding, CVD divergence, recent liq volume
- On-chain integration skeleton (Whale Alert / public tx monitoring)
- Streamlit dashboard
- Better observability hooks

## Quick Start (Docker)
```bash
docker-compose up -d
```

Environment variables see `.env.example`.

## Data Sources
Same as v1 + optional order book depth + on-chain.

**Not investment advice. Models are estimates.**
