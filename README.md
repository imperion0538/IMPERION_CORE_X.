# IMPERION Market Data

IMPERION, kripto işlemi öncesinde spot alım-satımı, vadeli pozisyonlanmayı, görünür likiditeyi ve dış piyasa verilerini karşılaştırır. Güncel araştırma panelde, Telegram'da ve 3840 × 2160 PNG kartta açılır. Piyasa verileri için borsa hesabı gerekmez.

| İşlem öncesinde bakılacak soru | Nerede görülür? |
|---|---|
| Borsalar hareketi birlikte mi fiyatlıyor? | `/panel` veya `/api/live?coin=BTC` ile spot/vadeli fiyat medyanı ve borsa farkları |
| Spot ve vadeli tarafta kim baskın? | `/arastirma BTC` ve panelde 15 dk delta, uyumsuzluklar, borsa ve gözlem kapsamı |
| Büyük işlemi fiyat takip etti mi? | `/akis BTC 15m`, panel ve `/api/whale-flow` ile borsa, piyasa ve işlem sonrası tepki |
| Likidite ve işlem maliyeti nasıl? | `/api/intelligence?coin=BTC` ile L2 spread, 10 bin/100 bin/1 milyon USD için iki yönde tahmini kayma |
| Pozisyonlanma veya makro risk var mı? | Güncel OI, 8 saate normalize fonlama, Deribit opsiyonları, haberler, tarihli ETF akışı ve ABD makro takvimi |
| Veri yeterli mi? | `/kalite BTC`, `/api/quality?coin=BTC` ve `/ready` ile kaynak yaşı, kayıt gecikmesi ve yeniden senkronizasyon |

`/api/intelligence` aynı şemada kapanmış 15 dk, 1 saat, 4 saat ve günlük mumları, RSI/ATR/EMA, akışları, fiyat aralıklarını, karşı kanıtları ve eksik kaynakları döndürür. Kısa dönem geçmiş karşılaştırması benzer mum rejimlerinin 4 saat sonraki gözlenen getirilerini ve örnek sayısını verir. Yetersiz geçmişte dağılım gösterilmez. `/api/research-history?coin=BTC` en son araştırma kayıtlarını açar.

- `marketdata/` — ana servis: toplayıcılar, ısı haritası modeli, analiz, Telegram botu, HTTP API.

## Veri kaynakları
WebSocket: Binance (perp + spot işlemler, likidasyonlar), Bybit ve OKX (spot + perp işlemleri ve vadeli likidasyonlar), Coinbase (spot işlemler), Hyperliquid (işlemler).
REST (5 dk): Binance, Bybit, OKX, Bitget, Gate, Hyperliquid, Deribit OI/funding/long-short. Deribit opsiyonları (15 dk).
Canlı L2: Binance spot ve Bybit spot/perp. Binance sıra zincirinde boşluk görülürse defter yeniden kurulur. Bybit yeni snapshot ve artan güncelleme kimliğiyle izlenir. Dakikalık REST derinlik görüntüleri Binance spot/perp, Bybit, OKX ve Coinbase üzerinden alınır. Defterlerin görünür kademe sayıları sınırlıdır.

Ücretsiz bağlam verileri Farside Investors günlük BTC/ETH ETF akışları, DefiLlama stablecoin arzı, Fair Economy haftalık USD takvimi, CoinDesk RSS ve Bitcoin için mempool.space ağ ölçümlerini içerir. Kaynak kesintisi, eski değer, tamamlanmamış ETF günü ve desteklenmeyen varlık ayrı belirtilir. Zincir üstü adres sahipliği için doğrulanmış etiket sağlayıcısı gerekir.

Funding oranları her borsanın gerçek ödeme aralığına (1/4/8 saat) göre 8 saatliğe normalize edilir.

## Veri bütünlüğü

WebSocket olayları normalleştirilir ve sınırlı kuyruğa alınır. PostgreSQL işlemi, yeni olaylarla dakika özetleri ve eşik aşımı alarmlarını birlikte kaydeder. Borsa kimliği bulunan olaylar yeniden gönderildiğinde bir kez sayılır. Ham kayıtta borsa kimliği bulunmayan olaylar içerikten türetilen anahtarla ayıklanır. Veritabanına yazılmadan önce sürecin kapanması kuyruktaki verileri kaybettirebilir. `/api/replay-check?coin=BTC` kaydedilmiş olaylarla dakika toplamlarını karşılaştırır. Telegram gönderimleri alıcı bazında yeniden denenir. Telegram'ın teslim onayı ile veritabanındaki teslim kaydı arasındaki bir kesinti aynı bildirimin tekrarlanmasına yol açabilir.

Veritabanına yalnızca en son başlayan dağıtım yazabilir (`writer_lock` tablosu ve tetikleyiciler).
Eski bir süreç kapanmadan çalışmaya devam ederse yazmaları reddedilir; yeni kod kilidi kaybettiğinde kendini kapatır.

## Ortam değişkenleri
| Değişken | Açıklama |
|---|---|
| `DATABASE_URL` | PostgreSQL bağlantısı |
| `API_KEY` | `/api/*` uçları için anahtar (`X-API-Key` başlığı) |
| `PUBLIC_READ` | `1` ise `/api/*` okuma uçları anahtarsız açılır, IP başına dakikada 60 istek. DB bağlantı tanılaması daima anahtar ister |
| `TELEGRAM_BOT_TOKEN` | Bot token'ı |
| `TELEGRAM_CHAT_ID` | Komut ve alarm alacak sohbet kimlikleri (virgülle) |
| `COINS` | Varsayılan `BTC,ETH,SOL` |
| `RAW_RETENTION_HOURS` | Ham olayların saklama süresi. Aralık 1–48 saat, varsayılan 6 saat |
| `WHALE_MIN_USD` | Balina eşiği, varsayılan 250000 (altcoinlerde ×0.4) |
| `ALERT_LIQ_USD`, `ALERT_CASCADE_USD`, `ALERT_WHALE_USD` | Alarm eşikleri |
| `REPORT_HOURS_UTC` | Zamanlı BTC raporu saatleri, örn. `5,13,21` |

## Web paneli
`/panel` — fiyat, OI, funding, likidasyon, balina, CVD, Coinbase primi, emir defteri dengesizliği, opsiyon özetiyle
tahmini likidasyon ısı haritası, emir defteri ısı haritası, CVD ve gerçekleşen likidasyon grafikleri. Görünür fiyat 3 sn, araştırma 15 sn, grafikler 60 sn aralıkla sorgulanır. Her veri sağlayıcısının kendi güncelleme sıklığı geçerlidir.

## API

`/api/research4k.png?coin=BTC` yeni araştırma kartını döndürür. `/api/card4k.png` mevcut teknik kartı, `/api/card.png` hızlı özeti sunar. `/api/risk?equity=10000&risk_pct=0.5&entry=60000&stop=59000&fee_bps=5&slippage_bps=5` girilen varsayımlarla pozisyon büyüklüğünü ve maliyeti hesaplar.

`/health`, `/api/analysis?coin=BTC`, `/api/heatmap?coin=BTC&window=1h|4h|24h|7d|30d`, `/api/heatmap.png`, `/api/liquidations`, `/api/whales`, `/api/derivatives`, `/api/options`, `/api/alts`, `/api/cvd`.

Emir defteri ve grafikler: `/api/depth?coin=BTC` (bantlar, dengesizlik, duvarlar), `/api/orderbook.png?coin=BTC&hours=4`,
`/api/cvd.png?coin=BTC&hours=24`, `/api/liquidations.png?coin=BTC&window=24h`, `/api/card.png?coin=BTC` (canlı analiz kartı), `/api/coins`.

## Telegram komutları
`/btc`, `/eth`, `/sol`, `/analiz COIN` — tam analiz · `/kart [COIN]` — canlı araştırma kartı (3840×2160 PNG), `/kart BTC ozet` — 1920×1080 özet, `/kart arsiv` — 26.11 tarihli 4K kart ·
`/isi [COIN] [1s|4s|24s|7g|30g]` · `/likidasyon` · `/balina` · `/akis` · `/oi` · `/ls` · `/opsiyon` · `/altlar` ·
`/derinlik [COIN]` — emir defteri derinliği ve duvarlar · `/defter [COIN] [1s|4s|24s]` — emir defteri ısı haritası ·
`/cvd [COIN] [4s|24s|7g]` — spot/perp CVD grafiği · `/durum` — akış sağlığı.

## Geliştirme
```
cd marketdata
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q            # TEST_DATABASE_URL verilirse veritabanı kilidi testi de çalışır
```
GitHub Actions her push ve PR'da testleri PostgreSQL 16 ile çalıştırır.

Isı haritası tahmini bir modeldir; veriler yatırım tavsiyesi değildir.

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
Telegram'da `/arastirma BTC` tüm katmanları birleştiren güncel değerlendirmeyi, `/akis BTC 15m` işlem ayrıntılarını sunar.
4K kartın teknik yapısı kapanmış mumlardan hesaplanır. Opsiyon API'si
son kayıt saatini ve 30 dakikayı aşan kayıtlar için eskime durumunu gösterir.
