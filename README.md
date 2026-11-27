# IMPERION Market Data

Çoklu borsa türev piyasası veri sistemi: likidasyon akışı, tahmini likidasyon ısı haritası, balina akışı ve CVD, long/short, OI, funding, opsiyon verisi; Telegram botu ve analiz API'si. Railway'de (Singapur) çalışır.

- `marketdata/` — ana servis: toplayıcılar, ısı haritası modeli, analiz, Telegram botu, HTTP API.

## Veri kaynakları
WebSocket: Binance (perp + spot işlemler, likidasyonlar), Bybit (işlemler, likidasyonlar), OKX (işlemler, likidasyonlar), Coinbase (spot işlemler), Hyperliquid (işlemler).
REST (5 dk): Binance, Bybit, OKX, Bitget, Gate, Hyperliquid, Deribit OI/funding/long-short. Deribit opsiyonları (15 dk).
Emir defteri (1 dk): Binance perp + spot, Bybit, OKX (tam derinlik), Coinbase.
Makro (saatlik): DXY, US 10Y, SPX (Yahoo Finance). BTC ETF flow placeholder.

Funding oranları her borsanın gerçek ödeme aralığına (1/4/8 saat) göre 8 saatliğe normalize edilir.

## Veri bütünlüğü
Veritabanına yalnızca en son başlayan dağıtım yazabilir (`writer_lock` tablosu ve tetikleyiciler).
Eski bir süreç kapanmadan çalışmaya devam ederse yazmaları reddedilir; yeni kod kilidi kaybettiğinde kendini kapatır.

## Ortam değişkenleri
| Değişken | Açıklama |
|---|---|
| `DATABASE_URL` | PostgreSQL bağlantısı |
| `API_KEY` | `/api/*` uçları için anahtar (`X-API-Key` başlığı) |
| `PUBLIC_READ` | `1` ise `/api/*` okuma uçları anahtarsız açılır, IP başına dakikada 60 istek |
| `TELEGRAM_BOT_TOKEN` | Bot token'ı |
| `TELEGRAM_CHAT_ID` | Komut ve alarm alacak sohbet kimlikleri (virgülle) |
| `COINS` | Varsayılan `BTC,ETH,SOL` |
| `WHALE_MIN_USD` | Balina eşiği, varsayılan 250000 (altcoinlerde ×0.4) |
| `ALERT_LIQ_USD`, `ALERT_CASCADE_USD`, `ALERT_WHALE_USD` | Alarm eşikleri |
| `REPORT_HOURS_UTC` | Zamanlı BTC raporu saatleri, örn. `5,13,21` |

## Web paneli
`/panel` — fiyat, OI, funding, likidasyon, balina, CVD, Coinbase primi, emir defteri dengesizliği, opsiyon özetiyle
tahmini likidasyon ısı haritası, emir defteri ısı haritası, CVD ve gerçekleşen likidasyon grafikleri. 60 sn'de bir yenilenir.

## API
`/health`, `/api/analysis?coin=BTC`, `/api/heatmap?coin=BTC&window=1h|4h|24h|7d|30d`, `/api/heatmap.png`, `/api/liquidations`, `/api/whales`, `/api/derivatives`, `/api/options`, `/api/alts`, `/api/cvd`.

Yeni araştırma uçları:
- `/api/research?coin=BTC` — 4 ajanlı orkestrasyon (structure, flow, macro, synthesis) + skorlar
- `/api/macro` — DXY, 10Y, SPX son gözlem
- `/api/scores?coin=BTC` — yön bias + cascade heuristic skorlar

Emir defteri ve grafikler: `/api/depth?coin=BTC` (bantlar, dengesizlik, duvarlar), `/api/orderbook.png?coin=BTC&hours=4`,
`/api/cvd.png?coin=BTC&hours=24`, `/api/liquidations.png?coin=BTC&window=24h`, `/api/card.png?coin=BTC` (canlı analiz kartı), `/api/coins`.

## Telegram komutları
`/btc`, `/eth`, `/sol`, `/analiz COIN` — tam analiz · `/kart [COIN]` — canlı analiz kartı (1920×1080), `/kart arsiv` — 26.11 tarihli 4K kart ·
`/isi [COIN] [1s|4s|24s|7g|30g]` · `/likidasyon` · `/balina` · `/akis` · `/oi` · `/ls` · `/opsiyon` · `/altlar` ·
`/derinlik [COIN]` — emir defteri derinliği ve duvarlar · `/defter [COIN] [1s|4s|24s]` — emir defteri ısı haritası ·
`/cvd [COIN] [4s|24s|7g]` — spot/perp CVD grafiği · `/durum` — akış sağlığı.

Yeni araştırma komutları: `/arastirma [COIN]`, `/senaryo [COIN]`, `/makro`, `/skor [COIN]`.

## Geliştirme
```
cd marketdata
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q            # TEST_DATABASE_URL verilirse veritabanı kilidi testi de çalışır
```
GitHub Actions her push ve PR'da testleri PostgreSQL 16 ile çalıştırır.

Isı haritası tahmini bir modeldir; veriler yatırım tavsiyesi değildir.

### İleri düzey piyasa araştırma katmanı (yeni)

- **Makro veri** (`md/macro.py`): DXY, US 10Y, SPX saatlik poll. `macro_snap` tablosu. Status: observed / insufficient_data.
- **Araştırma orkestratörü** (`md/research.py`): Market Structure, Flow & Liquidity, Macro & Sentiment, Synthesis ajanları mevcut analiz + makro üzerinden çalışır. Yapılandırılmış JSON + Türkçe özet.
- **Skorlama** (`md/scoring.py`): Yön bias (-100..+100) ve cascade stress heuristic. Feature extraction (OI change, funding, Coinbase premium vb.).
- Yeni API: `/api/research`, `/api/macro`, `/api/scores`.
- Telegram: `/arastirma`, `/senaryo`, `/makro`, `/skor`.

On-chain, haber/sentiment ve ML modelleri gelecek entegrasyonlar olarak planlanmıştır. Mevcut sistem gözlenen veriden üretilir; sentetik veri yok.

### Büyük işlem akışı ve fiyat tepkisi

`GET /api/whale-flow?coin=BTC&window=15m&min_price=61000&max_price=62500&limit=20`
borsa ve piyasa türü bazında agresif alış/satış toplamlarını ve en son işlemleri
döndürür. Fiyat sınırları isteğe bağlıdır. Pencereler: `5m`, `15m`, `1h`, `4h`.
Telegram örneği: `/akis BTC 15m 61000 62500`.

Kapsam, her kaynak için veri gözlenen kapalı dakikaların sayısıdır.
Olay listesi mevcut dakikadaki işlemleri de içerir. Fiyat tepkisi,
işlem fiyatından aynı borsa ve piyasanın 1, 5 ve 15 dakika sonraki dakika
kovası kapanışına yüzde değişimdir. Kova henüz kapanmamış veya eksikse
değer `null` kalır. Büyük işlem verisi katılımcı kimliği veya pozisyon açma/kapama
bilgisi içermez.

`/api/analysis` akış pencerelerinde borsa ve piyasa bazında gözlenen dakika
sayısını raporlar. `/health` süreç durumuna ek olarak `data_status` ve
`stale_streams` alanlarını döndürür.

### Telegram 4K kartı ve opsiyon analizi

`/kart arsiv`, 26 Kasım 2026 15:54 TSİ BTC kartını özgün 3840 × 2160
boyutuyla Telegram belgesi olarak gönderir (`/kart` artık canlı kart üretir). Kart, bu sürümde yapılandırılmış
Telegram sohbetlerine bir kez otomatik gönderilir.

`/opsiyon BTC` ve `/api/options?coin=BTC`, Deribit vadeleri için strike
bazında açık pozisyon, USD içsel ödeme eğrisi ve max pain seviyesini gösterir.
Greeks, zincir genelinde mark IV ve forward fiyatından Black-76 ile hesaplanır.
En yüksek OI'lı dört kontrat için Deribit ticker Greeks değerleri ayrıca
`exchange_greeks` alanında bulunur. Greeks model kapsamı OI yüzdesiyle verilir.

GEX, %1 fiyat hareketi başına yaklaşık USD duyarlılığıdır. İmzalı GEX
call pozitif / put negatif varsayımını kullanır. Piyasa yapıcı yönü bilinmez.
Opsiyon alarmları yeni snapshot'ları karşılaştırır. Eşikler:
`OPTIONS_DVOL_CHANGE` (3 puan), `OPTIONS_MAX_PAIN_CHANGE_PCT` (%2),
`OPTIONS_GEX_FLIP_MIN_USD` (1 milyon USD, işaret değişiminin her iki tarafında).

### Piyasa araştırma raporu

`/api/report?coin=BTC&window=15m&min_price=61000&max_price=62500`,
seçilen fiyat bölgesindeki büyük işlemleri spot/perp ayrımı ve işlem sonrası
fiyat tepkileriyle sunar. Rapor, veri kapsamı ile kaynak durumunu da içerir.
Telegram'da `/arastirma BTC` komutunu kullanabilirsin.
4K kartın teknik yapısı kapanmış mumlardan hesaplanır. Opsiyon API'si
son kayıt saatini ve 30 dakikayı aşan kayıtlar için eskime durumunu gösterir.

### Advanced research (production integration)

The original Advanced V2 prototype is superseded by the existing Railway single-writer architecture. No synthetic price/OI/funding/flow is injected into production. Redis/NATS/TimescaleDB and the standalone prototype dashboard are not required or deployed; PostgreSQL and the existing web panel remain authoritative.

- `/api/advanced?coin=BTC`: observed per-source closed five-minute flow anomaly z-scores, explicitly heuristic cascade stress estimate (coverage-gated), and Deribit options premium flow.
- `/api/options/flow?coin=BTC`: BTC/ETH inverse options premium flow over the last 15 minutes. Capped responses and invalid records are marked partial; unavailable is not zero. Put/call premium ratio differs from OI ratio.
- `TELEGRAM_CARD_ONLY=1` (default): original 4K design, full-resolution PNG document only. /kart, /btc, /eth, /sol, /analiz and /kart arsiv; other analysis remains available via HTTP API. Scheduled reports and rate-limited alerts send 4K documents. Set 0 only to restore legacy text and summary commands.
- No live order execution or automatic position opening is added. On-chain data and independent durable event streaming remain future integrations, not advertised as live.

### IMPERION CORE X interactive command center

`/panel` is the responsive 4K-capable HTML/CSS dashboard with an animated projected 3D torus/coin, real candle chart, observed CVD, liquidity estimates, orderbook walls, derivatives, Deribit options, whale prints, anomaly and coverage, correlation, health, and original 4K card. No invented trade signals or PnL. `/panel/classic` retains the previous panel. `/api/candles?coin=BTC&frame=15m` accepts monitored coins and 15m/1h/4h. Dashboard polls real APIs every 30s, health every 10s; API key remains in page memory only. 3D geometry is decorative, not an AI model. Fullscreen adapts to the display; the original analysis card is 3840×2160.

### Reference-layout command center
The /panel interface follows the supplied 16:9 reference's four instrument banks and lower analysis panels; responsive stacking on smaller screens. The animated engraved coin and reactor are procedurally drawn, not a flat image of fictional prices. Market sentiment is observed aggressor volume share. Trade feed is limited to recorded whale prints; AI signal/portfolio execution remains unavailable. No fake values are embedded.
