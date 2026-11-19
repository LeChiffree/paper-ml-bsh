# Expanded-Backtest — tüm coinler, ilk tarihten itibaren

Bu klasör, `paper-ml-bsh` paketindeki 219 senaryoyu **105 coinlik workbook
kapsamından çıkarıp** `D:\1mdatabinance\1m` arşivindeki tüm coinlere uygular.
Her coin kendi ilk 1m mumundan başlar; ortak bir başlangıç tarihi zorlanmaz.

## Kapsam

- Veri: `D:\1mdatabinance\1m` — 526 coin, 2020-01 → 2026-11-17, ~39,7 GB.
  Bazı aylar `.parquet`, bazıları `.csv` (UTF-8 BOM); ikisi de okunur.
- Senaryo: `BACKTEST_POSITIVE_METHODS_1M_EXECUTION.xlsx` içindeki 219 satır.
- Model: paketteki 83 LightGBM artifact'i (47 benzersiz timeframe + hedef).
- Timeframe: 5m, 15m, 1h, 4h; hepsi 1m arşivinden türetilir.
- Özellikler: `feature_generator_portable/feature_engine/features.py` ile
  birebir 132 özellik. BTC/ETH macro serileri ve 526 coinin piyasa hacmi payı
  aynı arşivden hesaplanır.
- Model `symbol_map` içinde olmayan coinler `NaN` kategori ile skorlanır.
  Bu, sembol özelliğinin öneminden dolayı bir yaklaşımdır; sonuçlarda
  `unseen_symbol_handling` alanında belirtilir.

## İşlem kuralları

- Sinyal, tamamlanmış timeframe mumunun kapanışında; giriş bir sonraki 1m
  mumun açılışında.
- 5m/15m/1h: TP/SL giriş fiyatına göre, %0,10 komisyon (giriş + çıkış), funding
  yok.
- 4h: giriş sinyal kapanışı × 1,0002; TP/SL sinyal kapanışına göre, %0,05
  komisyon, 8 saatte %0,0108 sabit funding.
- TP/SL 1m high/low ile izlenir; aynı mumda ikisi de vurulursa **stop-first**.
- Zaman aşımı: `horizon × timeframe dakikası`; çıkış son 1m mumun kapanışı.
- Normal portföy: senaryo başına 100.000 TL, işlem başına 10.000 TL, en fazla
  10 eşzamanlı pozisyon. Aynı coin üst üste binebilir (stackable).
- Singular: normal kabul edilen işlemler içinde aynı `scenario_id + symbol`
  için açık işlem varken gelen sinyal bloke edilir.

## Çalıştırma

```bash
# 1) Piyasa hacmi + BTC/ETH macro serileri (bir kez)
python Expanded-Backtest/prepare_context.py

# 2) Coin bazında özellik + tahmin + aday işlemler
python Expanded-Backtest/run_expanded.py run --workers 8

# 3) Senaryo bazında 10-slot kabul, singular kural, metrikler
python Expanded-Backtest/run_expanded.py merge

# 4) Workbook alt kümesiyle doğrulama
python Expanded-Backtest/validate.py
```

Yararlı bayraklar:

- `--symbols SNXUSDT,BTCUSDT` yalnızca seçilen coinler.
- `--limit 20` ilk N coin.
- `--force` mevcut coin çıktılarını yok sayıp yeniden üretir.
- `PREDICT_THREADS=6` her worker'ın LightGBM iş parçacığı sayısı.

`candidates/<SYMBOL>.done` işaretleri sayesinde çalışma kesilirse kaldığı
yerden devam eder; tamamlanmış timeframe parquetleri yeniden üretilmez.

## Çıktılar

| Dosya | İçerik |
|---|---|
| `candidates/<SYMBOL>__<tf>.parquet` | Coin/timeframe bazında ham aday sinyaller ve simüle edilmiş çıkışlar |
| `cache/market_<tf>.parquet` | 526 coinin toplam quote volume serisi |
| `cache/macro_<tf>.parquet` | BTC/ETH macro özellikleri |
| `cache/by_scenario/scenario_id=*` | Senaryo bazında partition edilmiş adaylar |
| `results/normal_trades.parquet` | 10-slot kuralı sonrası normal işlemler |
| `results/singular_trades.parquet` | Tek açık işlem kuralı sonrası işlemler |
| `results/singular_results.json` | Dashboard payload'ı (219 senaryo + coin kırılımı) |
| `results/run_manifest.json` | Kapsam, satır sayıları, veri/model notları |
| `results/validation.json` | Workbook alt kümesiyle karşılaştırma |

## Doğrulama

`validate.py`, workbook'un 105 sembolünü ve 2025-12-09 → 2026-09-09 dönemini
filtreleyip aynı 10-slot kabul kuralını uygular. Amaç, genişletilmiş motorun
workbook sinyallerini yakalama oranını ve kabul edilen işlem eşleşmesini
ölçmektir.

## Sonuçlar (final)

- 526 coin, 77.48M ham aday → **1.977M normal** işlem (10-slot kuralı) →
  **1.070M singular** işlem (bloke oranı %45,9).
- `retAll`: normal **+958,42%**, singular **+479,83%** (100.000 TL sermaye
  üzerinden).
- Workbook alt kümesiyle doğrulama: %95,3 ham sinyal kapsamı, %45,9 kabul
  eşleşmesi (genişletilmiş evren çok daha büyük olduğundan beklenen).
- Dashboard: `all-in-dashboard` içindeki 219 Paper ML satırı singular sonuçlarla
  değiştirildi; `index.html` `ROWS`/`META` ve `singular/summary.js` yeniden
  üretildi.

## Notlar

- Zaman damgaları TRT (UTC+3) olarak raporlanır; 1m arşivi UTC'dir.
- 4h funding gerçek funding verisi değil, sabit %0,0108/8s yaklaşımıdır.
- Kaynak arşivde eksik aylar varsa ilgili coinin serisi atlanır; mumlar
  forward-fill edilmez.
- Bu çalışma yatırım tavsiyesi değildir.
