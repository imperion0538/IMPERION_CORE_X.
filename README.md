# IMPERION Market Data

Çoklu borsa türev piyasası veri sistemi: likidasyon akışı, tahmini likidasyon ısı haritası, balina akışı ve CVD, long/short, OI, funding, opsiyon verisi; Telegram botu ve analiz API'si. Railway'de (Singapur) çalışır.

- `probe/` — Railway bölgesinden borsa API erişim testi.
- `marketdata/` — ana servis: toplayıcılar, ısı haritası modeli, analiz, Telegram botu, HTTP API.

## Veri kaynakları
WebSocket: Binance (perp + spot işlemler, likidasyonlar), Bybit (işlemler, likidasyonlar), OKX (işlemler, likidasyonlar), Coinbase (spot işlemler), Hyperliquid (işlemler).
REST (5 dk): Binance, Bybit, OKX, Bitget, Gate, Hyperliquid, Deribit OI/funding/long-short. Deribit opsiyonları (15 dk).

## Ortam değişkenleri
| Değişken | Açıklama |
|---|---|
| `DATABASE_URL` | PostgreSQL bağlantısı |
| `API_KEY` | `/api/*` uçları için anahtar (`X-API-Key` başlığı) |
| `TELEGRAM_BOT_TOKEN` | Bot token'ı |
| `TELEGRAM_CHAT_ID` | Komut ve alarm alacak sohbet kimlikleri (virgülle) |
| `COINS` | Varsayılan `BTC,ETH,SOL` |
| `WHALE_MIN_USD` | Balina eşiği, varsayılan 250000 (altcoinlerde ×0.4) |
| `ALERT_LIQ_USD`, `ALERT_CASCADE_USD`, `ALERT_WHALE_USD` | Alarm eşikleri |
| `REPORT_HOURS_UTC` | Zamanlı BTC raporu saatleri, örn. `5,13,21` |

## API
`/health`, `/api/analysis?coin=BTC`, `/api/heatmap?coin=BTC&window=1h|4h|24h|7d|30d`, `/api/heatmap.png`, `/api/liquidations`, `/api/whales`, `/api/derivatives`, `/api/options`, `/api/alts`, `/api/cvd`.

Isı haritası tahmini bir modeldir; veriler yatırım tavsiyesi değildir.
