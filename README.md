# IMPERION CORE X — Advanced Real-Time Market Research System

**Real-time multi-exchange crypto derivatives intelligence: whale liquidity tracking, exchange flows, liquidation heatmaps, CVD, OI/funding, options, and automated analysis reports.**

This system is a complete, layered ecosystem for continuous market monitoring and research-grade analysis. It focuses exclusively on global crypto markets (Binance, Bybit, OKX, Coinbase, Hyperliquid, Bitget, Gate, Deribit).

## Ecosystem Layers

### 1. Infrastructure & Operations
- Single-writer lock for safe deployments
- Health monitoring of all streams
- Railway / container ready
- GitHub Actions CI with PostgreSQL

### 2. Data Sources & Ingestion
- **WebSocket (real-time)**: trades + liquidations from Binance (perp+spot), Bybit, OKX, Coinbase, Hyperliquid
- **REST (periodic)**: OI, funding, long/short ratios, Deribit options
- **Order book** snapshots every minute
- Whale detection on large trades

### 3. Processing & Event Handling
- In-memory minute buckets → PostgreSQL
- Real-time alert queue (liquidations, whales, cascades)
- Coverage tracking for data quality

### 4. Storage
- PostgreSQL with retention policies
- Tables: trades_1m, liquidations, whale_trades, deriv_snap, options_snap, depth_snap
- Writer lock prevents concurrent writers

### 5. Analytics & Features
- Estimated liquidation heatmap (OI-based model + leverage distribution)
- Realized liquidations by price band
- CVD (spot vs perp)
- Whale flow with price reaction
- Options: max pain, GEX, Greeks, DVOL
- Order book imbalance & walls
- Altcoin correlation / beta
- Price structure (range analysis)

### 6. Intelligence / Research Layer
- Structured analysis engine producing live summaries
- Automated report generation focused on whale liquidity and exchange movements
- Multi-source synthesis (flows + derivatives + options + orderbook)
- Telegram-delivered analysis reports and alerts

### 7. Delivery & User Interface
- HTTP API (analysis, heatmap, whales, CVD, options, depth, PNG charts)
- Telegram bot with commands and real-time alerts
- Static web panel
- High-resolution analysis cards (including 4K)

## Key Capabilities
- **Whale Liquidity & Exchange Movements**: Real-time tracking of large trades across venues, net flow, price reaction, cascade detection.
- **Live Analysis Reports**: On-demand and scheduled reports synthesizing all layers into actionable (non-advice) market context.
- **Multi-venue Aggregation**: Normalized funding, combined OI, cross-exchange liquidations and CVD.

## Environment Variables
See original for full list. Core: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `COINS`, `API_KEY`, alert thresholds.

## Running
```bash
cd marketdata
pip install -r requirements.txt
# Set env vars, then:
python main.py
```

Isı haritası tahmini bir modeldir; veriler yatırım tavsiyesi değildir.

---
Built on the original IMPERION Market Data pipeline, evolved into a full research ecosystem focused on real-time whale liquidity and exchange flow intelligence.
