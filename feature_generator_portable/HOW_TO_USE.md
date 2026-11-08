# 132 Feature Generator — Kullanım ve Model Sözleşmesi

Bu paket, paper sistemde kullanılan feature hesaplama kodunun taşınabilir kopyasıdır. Her timeframe için **132 sayısal feature** üretir; sonuna kategorik `symbol` eklendiğinde model girdisi **133 kolon** olur.

## En önemli kural

Feature adlarındaki `5`, `15`, `50`, `200` dakika değil, **mum sayısıdır**. Aynı kod ayrı ayrı 5m, 15m, 1h ve 4h mumlarına uygulanır. Örneğin `Return_5m`:

- 5m datasetinde son 5 × 5 dakika,
- 15m datasetinde son 5 × 15 dakika,
- 1h datasetinde son 5 saat,
- 4h datasetinde son 20 saat

değişimini ifade eder.

## Dosyalar

- `feature_engine/features.py`: 132 feature'ın gerçek hesaplama motoru.
- `feature_engine/model_input.py`: LightGBM artifact'ına uygun float32 ve kategorik symbol dönüşümü.
- `feature_order.json`: Modelin zorunlu kolon sırası.
- `generate_features.py`: CSV/Parquet klasöründen son kapanmış mum için model matrisi üretir.
- `FEATURE_REFERENCE_132.md`: 132 feature'ın tek tek açıklaması.
- `tests/test_feature_generator.py`: Kolon sayısı, sıra ve temel üretim testi.

## Ham mum sözleşmesi

Her coin ayrı dosya olmalı: `BTCUSDT.parquet`, `ETHUSDT.parquet`, `ADAUSDT.parquet` gibi. Gerekli kolonlar:

```text
open_time, open, high, low, close, volume, close_time,
quote_volume, trade_count, taker_buy_base_volume, taker_buy_quote_volume
```

`open_time` ve `close_time` Unix milisaniyedir. Diğer alanlar sayısal olmalıdır. Yalnız kapanmış mumlar kullanılır; `close_time >= server_time_ms` olan açık mum otomatik dışlanır.

BTCUSDT ve ETHUSDT zorunludur. Piyasa payı feature'ı nedeniyle üretimde takip edilecek bütün coinler aynı klasörde ve aynı timeframe'de bulunmalıdır. Coinlerin timestamp'leri `open_time` üzerinden hizalanır.

En az 201 kapanmış mum matematiksel olarak gerekir; canlı sistemle aynı güvenli çalışma için **en az 260**, EMA/PSAR başlangıç etkisini azaltmak için tercihen daha fazla geçmiş verin.

## Kurulum

```powershell
cd C:\path\FEATURE_GENERATOR_132
python -m pip install -r requirements.txt
python -m pytest -q
```

## Feature üretme

Her timeframe'i ayrı çalıştırın:

```powershell
python generate_features.py `
  --input-dir C:\data\15m `
  --output C:\output\15m_latest_features.parquet
```

Belirli bir geçmiş zamanda yalnız o ana kadar kapanmış mumlarla üretmek için:

```powershell
python generate_features.py `
  --input-dir C:\data\5m `
  --output C:\output\features.csv `
  --server-time-ms 1790095200000
```

Bir LightGBM artifact'ıyla kolon/dtype/symbol uyumunu da doğrulamak için:

```powershell
python generate_features.py `
  --input-dir C:\data\1h `
  --output C:\output\1h_latest.parquet `
  --model C:\models\1h\lightgbm\sonuc_15mum_tp2p5_sl1p25.joblib
```

## Python içinde kullanım

```python
import joblib
from pathlib import Path
from generate_features import load_universe
from feature_engine import FEATURE_ORDER, compute_latest_matrix, prepare_lightgbm_input

frames = load_universe(Path(r"C:\data\15m"))
matrix, enriched = compute_latest_matrix(frames, server_time_ms)

artifact = joblib.load(r"C:\models\15m\lightgbm\sonuc_15mum_tp2p5_sl1p25.joblib")
selected, X = prepare_lightgbm_input(artifact, matrix)
probabilities = artifact["model"].predict_proba(X)
```

`X` tam olarak şu yapıdadır:

1. `feature_order.json` sırasıyla 132 adet `float32` sayısal kolon,
2. son kolonda artifact'ın kendi `symbol_map` sözlüğüyle integer'a çevrilmiş kategorik `symbol`.

Modelin `symbol_map` içinde tanımadığı coinler `selected` ve `X` dışında bırakılır. CatBoost kullanılırsa ilk 132 kolon aynı kalır, 133. kolonda ham symbol metni kategorik olarak verilmelidir.

## Kritik doğruluk kuralları

1. Açık mum kullanmayın.
2. Timeframe'leri karıştırmayın; 15m modele 15m mum verin.
3. BTC, ETH ve diğer coinleri aynı `open_time` üzerinde tutun.
4. `Quote_Volume_Market_Share`, klasördeki evrenin toplam quote volume'üne bağlıdır; eksik coin evreni bu feature'ı değiştirir.
5. Sayısal kolonlar model öncesi `float32`; sonsuz değerler `NaN` olmalıdır.
6. NaN değerlerini sıfırla doldurmayın; LightGBM'in native missing-value davranışı kullanılır.
7. Kolonları alfabetik sıralamayın; `feature_order.json` sırası zorunludur.
8. Target, gelecek fiyat, `open_time` ve `close_time` modele verilmez.
9. Her model artifact'ının `symbol_map` ve LightGBM kategori listesi kendi içinden okunmalıdır.

## Feature grupları

- 1–9: ham OHLCV ve işlem akışı
- 10–41: 5/15/50/200 bar getiri, drawdown/runup, EMA, RSI, z-score, ATR ve hacim
- 42–70: trend, PSAR, Ichimoku, MACD, Stochastic, Bollinger, hacim/mum ve UTC zaman
- 71–81: BTC ve ETH/BTC piyasa bağlamı
- 82–132: momentum, volatilite, hacim, mum yapısı ve çapraz etkileşimler

Her feature'ın formülü ve birimi `FEATURE_REFERENCE_132.md` içinde numaralı olarak açıklanmıştır.
