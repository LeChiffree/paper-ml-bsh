# Paper Trade Uygulama Rehberi

Bu paket yalnızca 1 dakikalık mumlarla yeniden test edildiğinde net kârı pozitif kalan yöntemleri içerir.

## Paket özeti

- Pozitif strateji/filtre kombinasyonu: 219
- Benzersiz model dosyası: 83
- Sinyal zaman dilimleri: 5m, 15m, 1h, 4h
- İşlem takibi: 1m mumlar
- Başlangıç kasa: 100.000 TL
- İşlem başına tutar: 10.000 TL
- 5m/15m/1h: kaldıraçsız, funding yok, giriş ve çıkışta %0,10 komisyon
- 4h: orijinal backtest varsayımı korunmuştur; %0,05 komisyon, %0,02 slippage ve ortalama funding yaklaşımı

## OHLC dışında borsadan alınması gereken ham alanlar

Her sinyal zaman diliminde kapanmış mumlar için `volume`, `quote_volume`, `trade_count`,
`taker_buy_base_volume` ve `taker_buy_quote_volume` alınmalıdır. Ayrıca aynı zaman damgasındaki
BTCUSDT ve ETHUSDT mumları ile izlenen bütün coinlerin `quote_volume` değerleri gerekir.
BTC bağıl getirileri, ETH/BTC göreli getirileri ve piyasa hacim payı bunlardan hesaplanır.

## Yerelde hazırlanacak bilgiler

- 132 sayısal feature, `FEATURE_DICTIONARY_ALL_TIMEFRAMES.md` içindeki sırayla hesaplanır.
- Son kolon kategorik `symbol` olur; toplam model girdisi 133 kolondur.
- `open_time` eşleştirme bilgisidir, feature değildir.
- Feature/target adlarında `5m`, `15m`, `50m`, `200m` yazsa da sayı dakika değil bar sayısıdır.
- En az 200 kapanmış mum, güvenli kullanım için 250 mum warm-up tutulmalıdır.
- Açık mumla tahmin yapılmaz. BTC, ETH ve piyasa bağlamı aynı `open_time` üzerinde birleştirilir.
- Sayısal kolonlar `float32`; sonsuz değerler `NaN`; kategorik symbol kodlaması model artifact içindeki `symbol_map` ile yapılır.
- Kolon sırası `feature_order.json` ile birebir aynı olmalıdır.

## Model ve filtre kullanımı

1. İlgili timeframe kapanınca feature satırını üretin.
2. Sınıflandırma modelinden sınıf olasılıklarını alın; `TP` sınıfının olasılığı `tp_score` olur.
3. Excel'deki `threshold` değerini uygulayın. `signal_only` için eşik yoktur.
4. `range_full`: tahmini future range >= TP yüzdesi.
5. `range_half_volatility`: tahmini range >= TP/2 ve volatilite eğitim bandında.
6. `range_full_volatility`: iki koşul birlikte; `volatility`: yalnız volatilite bandı.
7. Sinyal mumundan sonraki ilk mum açılışında giriş yapın; açık pozisyonu 1m mumlarla izleyin.
8. Aynı 1m mumda TP ve SL birlikte görülürse muhafazakâr olarak SL kabul edin.
9. Aynı coinde aynı anda yalnız bir pozisyon ve yeterli nakit kuralını uygulayın.

`model_manifest.csv` hangi modelin sınıflandırma veya regresyon amacıyla pakete konduğunu gösterir.
`BACKTEST_POSITIVE_METHODS_1M_EXECUTION.xlsx` içindeki `scenario_id`, sonuç ve işlem satırlarını bağlar.
