# IMPERION CORE X - Advanced Market Data Platform

Multi-exchange crypto derivatives monitoring system with real-time streaming, advanced analytics, anomaly detection, cascade risk scoring, estimated + enhanced liquidation heatmaps, **Deribit options flow**, and on-chain signals.

## Architecture (v2)

```
Collectors (WS + REST + Deribit Options) → NATS JetStream → Processors (heatmap, anomaly, cascade, options aggregate)
                              ↓
                         Redis (hot state + cache) + TimescaleDB (historical)
                              ↓
                    API (FastAPI) + Dashboard (Streamlit) + Telegram Bot
```

### Services
- `services/collector` – WebSocket & REST collectors (Binance + **Deribit options trades & summaries**) publishing to NATS
- `services/processor` – Consumes events, builds heatmaps, anomaly scores, cascade risk, **options premium flow & max pain**, writes to Redis
- `services/api` – FastAPI serving enriched analysis including `/api/options/{coin}` and `/api/options/flow/{coin}`
- `services/dashboard` – Streamlit real-time dashboard with options section
- `services/onchain` – On-chain large transaction / exchange flow collector (skeleton)
- `shared/` – Common models, improved heatmap, anomaly engine, **options analytics**

## Key Improvements over v1
- Decoupled collectors / processors via NATS
- Redis for hot state, alerts, recent flows
- Improved heatmap (OI delta + leverage distribution + cascade proximity score)
- Anomaly detection (z-score + IsolationForest skeleton) on CVD / whale / liquidation flows
- Cascade risk score combining heatmap clusters, funding, CVD divergence, recent liq volume
- **Deribit options flow**: real-time trades, premium volume (buy/sell), put/call ratio, large options trades, max pain from OI, IV summaries
- On-chain integration skeleton (Whale Alert / public tx monitoring)
- Streamlit dashboard
- Better observability hooks

## Deribit Options Endpoints
- `GET /api/options/{coin}` – Summary (total OI, max pain, top instruments)
- `GET /api/options/flow/{coin}` – Aggregated flow (net premium, P/C ratio, large trades)
- Included in `/api/analysis/{coin}`

## Quick Start (Docker)
```bash
docker-compose up -d
```

Environment variables see `.env.example`.

## Data Sources
Binance, Bybit, OKX, Coinbase, Hyperliquid (v1) + **Deribit public options (WS trades + REST book summary)** + optional order book depth + on-chain.

**Not investment advice. Models are estimates.**
