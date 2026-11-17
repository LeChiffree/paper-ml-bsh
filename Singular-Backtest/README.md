# Singular-Backtest — Tek Açık İşlem Kuralı

Bu klasör, `paper_trade_app` / `BACKTEST_POSITIVE_METHODS_1M_EXECUTION.xlsx`
paketindeki 219 senaryoyu **tekrar simüle etmeden**, hazır işlem listesi
üzerinden yeniden değerlendiren ayrı bir backtest varyantıdır. Normal
dashboard'a ve mevcut sonuçlara dokunmaz; tüm çıktılar
`Singular-Backtest/results/` altında üretilir.

## Kural

Her **senaryo** için bir coinde **aynı anda en fazla bir açık işlem** bulunabilir.

- Coinde açık işlem yoksa gelen sinyal onaylanır, işlem açılır.
- Coinde aynı senaryoya ait açık işlem varken yeni sinyal gelirse **sinyal
  bloke edilir** (yeni pozisyon eklenmez).
- Bloke edilen sinyal, açık işlem kapandıktan sonra tekrar sinyal üretirse
  değerlendirilir; kuyruğa alınmaz.

Neden gerekli: canlı paper motoru aynı coin + aynı senaryo için yeni mumda
gelen sinyalleri **stackable** kabul ediyor (`SYSTEM_METHODS_SCENARIOS_ALGORITHMS.md`,
bölüm 7). Uygulama rehberi ise "aynı coinde aynı anda yalnızca bir pozisyon"
kuralını söylüyor (`PAPER_TRADE_SYSTEM_GUIDE.md`, madde 9). Paketlenmiş
backtest bu kuralı uygulamıyor: normal listede aynı senaryo + coin için
**10.059 sinyal** açık pozisyon varken geliyor. Bu varyant o boşluğu kapatır.

## Kapsam (scope)

| `--scope` | Kilit anahtarı | Bloke |
|---|---|---:|
| `scenario` (varsayılan) | `scenario_id + symbol` | 10.059 (%18,52) |
| `scenario_name` | `scenario adı + symbol` (TF'ler arası) | 12.130 (%22,33) |
| `strategy` | `strategy ailesi + symbol` | 39.437 (%72,59) |

Varsayılan `scenario`, dashboard'daki her satırı (219 senaryo, her biri kendi
100.000 TL portföyü) ayrı bir strateji kabul eder. Aynı senaryo adının 5m/15m/1h/4h
varyantları ayrı `scenario_id` olduğu için birbirini bloke etmez. Daha katı
"aynı konfigürasyon tüm TF'lerde tek pozisyon" için `scenario_name`, "aynı
gösterge ailesi tüm varyantlarda tek pozisyon" için `strategy` kullanılabilir.

## Veri

- Kaynak: `PAPER_TRADE_POSITIVE_MODELS_1M_EXECUTION/paper_trade_package/BACKTEST_POSITIVE_METHODS_1M_EXECUTION.xlsx`
  → `Detayli_Islemler` sayfası.
- 54.325 işlem; 219 senaryo (170 benzersiz senaryo adı, 29 strateji ailesi),
  105 sembol, 5m/15m/1h/4h.
- Dönem: 09.12.2025 03:05 → 09.09.2026 02:20 (**Türkiye saati**, kaynak dosyadaki gibi).
- Kasa modeli: senaryo başına 100.000 TL, işlem başına sabit 10.000 TL.
  `retAll = toplam net P/L / 100.000 × 100`.
- Giriş sinyalden sonraki ilk 1m mumun açılışında; TP/SL/zaman aşımı 1m
  mumlarla çözülür, aynı mumda TP+SL varsa **stop-first**.
- 5m/15m/1h: kaldıraçsız, funding yok, %0,10 komisyon. 4h: %0,05 komisyon,
  %0,02 slippage ve ortalama funding yaklaşımı (kaynak paketteki gibi).

## Çalıştırma

```bash
pip install pandas openpyxl pyarrow

python Singular-Backtest/run_singular.py
python Singular-Backtest/verify_singular.py
python Singular-Backtest/summarize_singular.py
```

Faydalı bayraklar:

- `--scope scenario|scenario_name|strategy`
- `--from-cache`: workbook'u tekrar okumadan `results/normal_trades.parquet`
  üzerinden çalışır (kapsam karşılaştırması için).
- `--force`: xlsx cache'ini yok sayıp kaynağı yeniden okur.
- `--out <klasör>`: çıktı klasörünü değiştirir.

## Çıktılar (`results/`)

| Dosya | İçerik |
|---|---|
| `normal_trades.parquet` | Workbook'taki tüm aday işlemler (kilit öncesi). |
| `singular_trades.parquet` | Kural sonrası kabul edilen işlemler. |
| `singular_results.json` | Dashboard için kompakt payload (özet + 219 senaryo + 170 isim + 29 strateji + 8.910 coin satırı). |
| `singular_scenarios.csv` | Senaryo bazında normal/singular metrikleri. |
| `singular_scenario_names.csv` | Senaryo adı bazında (TF'ler birleşik) metrikler. |
| `singular_strategies.csv` | Strateji ailesi bazında metrikler. |
| `singular_coins.csv` | `scenario_id × coin` kırılımı. |
| `run_manifest.json` | Kaynak satır, kabul, bloke ve kapsam bilgisi. |

`results/` gitignore'dadır. Dashboard için iki dosya klasörün köküne kopyalanır:

- `Singular-Backtest/singular_results.json`
- `Singular-Backtest/run_manifest.json`

## Sonuç özeti (varsayılan `scenario` kapsamı)

| | Normal | Singular | Fark |
|---|---:|---:|---:|
| İşlem | 54.325 | 44.266 | −10.059 (bloke %18,52) |
| Kazanma oranı | %41,04 | %41,52 | +0,48 puan |
| Ort. işlem getirisi | +0,3660% | +0,3884% | +0,0224 puan |
| `retAll` (21,9M TL sermaye) | +9,08% | +7,85% | −1,23 puan |
| `ret2026` | +9,63% | +8,29% | −1,34 puan |
| Net P/L | +1.988.396 TL | +1.719.136 TL | −269.260 TL |
| Maks. düşüş (eşit ağırlık) | −3,32% | −2,82% | +0,51 puan |
| Kârlı / zararlı gün | 125 / 150 | 124 / 151 | — |

Zaman dilimi bazında bloke:

| TF | Normal işlem | Bloke | Bloke % |
|---|---:|---:|---:|
| 5m | 20.407 | 3.701 | 18,14 |
| 15m | 12.944 | 2.483 | 19,18 |
| 1h | 11.945 | 2.385 | 19,97 |
| 4h | 9.029 | 1.490 | 16,50 |

Yorum:

- Kural, işlem sayısını %18,5 azaltırken **işlem başına kaliteyi** artırıyor:
  kazanma oranı +0,48 puan, ortalama getiri +0,0224 puan, maksimum düşüş
  3,32% → 2,82%.
- Toplam kâr düşüyor çünkü bloke edilen sinyallerin bir kısmı kazançlıydı;
  bu, tek-pozisyon kuralının doğal maliyeti.
- 219 senaryonun **64'ü** `retAll` bazında iyileşiyor. En az 100 işlemi olan
  101 senaryonun **39'u** iyileşiyor.
- En iyi iyileşmeler: `sid 18 5m` +14,44 puan (29,53% → 43,97%, %32,1 bloke),
  `sid 4 15m` +8,03, `sid 23 5m` +7,72, `sid 3 15m` +7,58, `sid 150 5m` +7,32.
- En kötü etkilenenler: `sid 5/6 4h` −20,57 puan (35,27% → 14,70%),
  `sid 28 15m` −19,85, `sid 1 5m` −17,93.
- Senaryo adı bazında en iyi: `h50_tp15__range_full__t70` (+7,32 puan),
  `h200_tp20__range_full__t40` (+6,52), `h200_tp20__range_full__t60` (+4,48).

## Doğrulama

`verify_singular.py`:

1. Aynı `scenario_id + symbol` için çakışan singular işlem yok → **0**.
2. Her singular işlem normal aday kümesinde birebir mevcut → **0 eksik**.
3. Tekrar eden satır yok → **0**.

## Notlar

- Zaman damgaları kaynak xlsx'te Türkiye saati (UTC+3) olarak tutulur; gün
  kırılımları bu saat dilimine göredir.
- `threshold` alanı `signal_only` senaryolarında boştur.
- 5m/15m/1h senaryolarında funding sıfırdır; 4h sonuçları funding yaklaşımı
  içerir.
- Bu çalışma yatırım tavsiyesi değildir.
