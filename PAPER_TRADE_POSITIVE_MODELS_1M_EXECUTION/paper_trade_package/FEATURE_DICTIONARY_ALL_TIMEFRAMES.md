# 5m / 15m / 1h / 4h Paper-Trade Feature Sözlüğü

> Bu belge model girdisini açıklar. Model üretiminde kullanılan orijinal feature kodu bu klasörde bulunmadığı için `pipeline-tanımlı` işaretli feature'larda yaklaşık formül yazmak yerine aynı üretim kodunun birebir taşınması gerekir.

## Kritik kurallar

- Feature adlarındaki `5/15/50/200` dakika değil, **model zaman dilimindeki mum adedidir**. Örneğin `Return_5m`, 5m modelinde 25 dakika; 15m modelinde 75 dakika; 1h modelinde 5 saat; 4h modelinde 20 saat geçmişi kapsar.
- Tahmin yalnız kapanmış mum üzerinden yapılır; açık mum kullanılmaz.
- Model girdisi: aşağıdaki 132 sayısal sütun + son sütunda kategorik `symbol` = 133 sütun.
- `open_time` metadata'dır, model feature'ı değildir. Target sütunları modele asla verilmez.
- Sayısallar `float32`; `+/-inf` değerleri `NaN` yapılır. Eksikler modelin native missing-value mekanizmasına bırakılır.
- En az 200 bar, tercihen 250 bar warm-up tutulur.
- BTC/ETH ve piyasa toplam hacmi aynı `open_time` ile eşleştirilmelidir.

## Sıralı feature listesi

| # | Feature | Grup | Açıklama | Birim |
|---:|---|---|---|---|
| 1 | `open` | Ham mum | Mum açılış fiyatı. | fiyat |
| 2 | `high` | Ham mum | Mum içindeki en yüksek fiyat. | fiyat |
| 3 | `low` | Ham mum | Mum içindeki en düşük fiyat. | fiyat |
| 4 | `close` | Ham mum | Kapanış fiyatı; tahmin yalnız mum kapandıktan sonra yapılır. | fiyat |
| 5 | `volume` | Hacim | Baz varlık işlem hacmi. | coin |
| 6 | `quote_volume` | Hacim | Kotasyon (USDT) cinsinden işlem hacmi. | USDT |
| 7 | `trade_count` | İşlem akışı | Mumdaki işlem adedi. | adet |
| 8 | `taker_buy_base_volume` | İşlem akışı | Piyasa alıcılarının baz varlık hacmi. | coin |
| 9 | `taker_buy_quote_volume` | İşlem akışı | Piyasa alıcılarının USDT hacmi. | USDT |
| 10 | `Return_5m` | Fiyat aksiyonu | Kapanışın 5 bar önceki kapanışa göre değişimi: 100×(C/C[-5]-1). | % |
| 11 | `Max_Drawdown_5` | Fiyat aksiyonu | Kapanışın son 5 bardaki en yüksek high'a uzaklığı: 100×(C/rolling_max(H,5)-1). | % |
| 12 | `Min_Runup_5` | Fiyat aksiyonu | Kapanışın son 5 bardaki en düşük low'a uzaklığı: 100×(C/rolling_min(L,5)-1). | % |
| 13 | `Close_vs_EMA_5_Pct` | Trend | Kapanışın EMA(5)'ya uzaklığı: 100×(C/EMA-1); EWM adjust=false. | % |
| 14 | `RSI_5` | Momentum | Wilder yöntemiyle RSI(5). | 0-100 |
| 15 | `Z_Score_5` | Rejim | Kapanışın 5 bar ortalama ve standart sapmasına göre z-skoru. | standart sapma |
| 16 | `ATR_vs_Close_5_Pct` | Volatilite | Wilder ATR(5) / kapanış ×100. | % |
| 17 | `Volume_vs_SMA_5_Pct` | Hacim | Hacmin SMA(5)'ya göre farkı: 100×(V/SMA-1). | % |
| 18 | `Return_15m` | Fiyat aksiyonu | Kapanışın 15 bar önceki kapanışa göre değişimi: 100×(C/C[-15]-1). | % |
| 19 | `Max_Drawdown_15` | Fiyat aksiyonu | Kapanışın son 15 bardaki en yüksek high'a uzaklığı: 100×(C/rolling_max(H,15)-1). | % |
| 20 | `Min_Runup_15` | Fiyat aksiyonu | Kapanışın son 15 bardaki en düşük low'a uzaklığı: 100×(C/rolling_min(L,15)-1). | % |
| 21 | `Close_vs_EMA_15_Pct` | Trend | Kapanışın EMA(15)'ya uzaklığı: 100×(C/EMA-1); EWM adjust=false. | % |
| 22 | `RSI_15` | Momentum | Wilder yöntemiyle RSI(15). | 0-100 |
| 23 | `Z_Score_15` | Rejim | Kapanışın 15 bar ortalama ve standart sapmasına göre z-skoru. | standart sapma |
| 24 | `ATR_vs_Close_15_Pct` | Volatilite | Wilder ATR(15) / kapanış ×100. | % |
| 25 | `Volume_vs_SMA_15_Pct` | Hacim | Hacmin SMA(15)'ya göre farkı: 100×(V/SMA-1). | % |
| 26 | `Return_50m` | Fiyat aksiyonu | Kapanışın 50 bar önceki kapanışa göre değişimi: 100×(C/C[-50]-1). | % |
| 27 | `Max_Drawdown_50` | Fiyat aksiyonu | Kapanışın son 50 bardaki en yüksek high'a uzaklığı: 100×(C/rolling_max(H,50)-1). | % |
| 28 | `Min_Runup_50` | Fiyat aksiyonu | Kapanışın son 50 bardaki en düşük low'a uzaklığı: 100×(C/rolling_min(L,50)-1). | % |
| 29 | `Close_vs_EMA_50_Pct` | Trend | Kapanışın EMA(50)'ya uzaklığı: 100×(C/EMA-1); EWM adjust=false. | % |
| 30 | `RSI_50` | Momentum | Wilder yöntemiyle RSI(50). | 0-100 |
| 31 | `Z_Score_50` | Rejim | Kapanışın 50 bar ortalama ve standart sapmasına göre z-skoru. | standart sapma |
| 32 | `ATR_vs_Close_50_Pct` | Volatilite | Wilder ATR(50) / kapanış ×100. | % |
| 33 | `Volume_vs_SMA_50_Pct` | Hacim | Hacmin SMA(50)'ya göre farkı: 100×(V/SMA-1). | % |
| 34 | `Return_200m` | Fiyat aksiyonu | Kapanışın 200 bar önceki kapanışa göre değişimi: 100×(C/C[-200]-1). | % |
| 35 | `Max_Drawdown_200` | Fiyat aksiyonu | Kapanışın son 200 bardaki en yüksek high'a uzaklığı: 100×(C/rolling_max(H,200)-1). | % |
| 36 | `Min_Runup_200` | Fiyat aksiyonu | Kapanışın son 200 bardaki en düşük low'a uzaklığı: 100×(C/rolling_min(L,200)-1). | % |
| 37 | `Close_vs_EMA_200_Pct` | Trend | Kapanışın EMA(200)'ya uzaklığı: 100×(C/EMA-1); EWM adjust=false. | % |
| 38 | `RSI_200` | Momentum | Wilder yöntemiyle RSI(200). | 0-100 |
| 39 | `Z_Score_200` | Rejim | Kapanışın 200 bar ortalama ve standart sapmasına göre z-skoru. | standart sapma |
| 40 | `ATR_vs_Close_200_Pct` | Volatilite | Wilder ATR(200) / kapanış ×100. | % |
| 41 | `Volume_vs_SMA_200_Pct` | Hacim | Hacmin SMA(200)'ya göre farkı: 100×(V/SMA-1). | % |
| 42 | `EMA5_vs_EMA15_Pct` | Trend | EMA(5)'nın EMA(15)'ye yüzde farkı. | % |
| 43 | `EMA15_vs_EMA50_Pct` | Trend | EMA(15)'nın EMA(50)'ye yüzde farkı. | % |
| 44 | `EMA50_vs_EMA200_Pct` | Trend | EMA(50)'nın EMA(200)'ye yüzde farkı. | % |
| 45 | `Close_vs_PSAR_Pct` | Trend | Kapanışın Parabolic SAR'a yüzde uzaklığı; PSAR step=0.02, max=0.2. | % |
| 46 | `Close_vs_Ichimoku_Kumo_Pct` | Trend | Kapanışın Ichimoku bulutuna yüzde konumu; 9/26/52, bulut 26 bar kaydırılmış. | % |
| 47 | `RSI_14_Slope_5` | Momentum | RSI(14)'ün son 5 bardaki pipeline-tanımlı eğimi. | RSI puanı/bar |
| 48 | `RSI_14_Slope_15` | Momentum | RSI(14)'ün son 15 bardaki pipeline-tanımlı eğimi. | RSI puanı/bar |
| 49 | `MACD_Line_vs_Signal_Pct` | Momentum | MACD(12,26) çizgisinin sinyal(9) çizgisine göre göreli farkı. | % |
| 50 | `MACD_Hist_Slope_5` | Momentum | MACD histogramının son 5 bardaki eğimi. | pipeline birimi |
| 51 | `Stoch_K_vs_D_Dist` | Momentum | Stochastic(14,3) K ile D çizgileri arasındaki mesafe. | puan |
| 52 | `BB_Width_vs_SMA50_Pct` | Volatilite | Bollinger bant genişliğinin SMA50'ye oranı. | % |
| 53 | `Close_vs_BB_Upper_Pct` | Volatilite | Kapanışın Bollinger üst bandına yüzde uzaklığı. | % |
| 54 | `Close_vs_BB_Lower_Pct` | Volatilite | Kapanışın Bollinger alt bandına yüzde uzaklığı. | % |
| 55 | `Squeeze_On` | Volatilite | Bollinger bantları Keltner kanalının içindeyse sıkışma bayrağı. | 0/1 |
| 56 | `Choppiness_Index_14` | Rejim | 14 bar Choppiness Index; yüksek değer yatay/düzensiz rejim. | 0-100 |
| 57 | `OBV_Slope_5` | Hacim | On-Balance Volume'un 5 barlık eğimi. | OBV/bar |
| 58 | `OBV_Slope_15` | Hacim | On-Balance Volume'un 15 barlık eğimi. | OBV/bar |
| 59 | `OBV_Slope_50` | Hacim | On-Balance Volume'un 50 barlık eğimi. | OBV/bar |
| 60 | `OBV_Slope_200` | Hacim | On-Balance Volume'un 200 barlık eğimi. | OBV/bar |
| 61 | `Volume_Pressure` | Hacim | Fiyat yönü ve hacmi birleştiren pipeline-tanımlı alış/satış baskısı. | pipeline birimi |
| 62 | `CMF_20` | Hacim | Chaikin Money Flow, 20 bar. | oran |
| 63 | `MFI_14_vs_SMA50_Pct` | Hacim | MFI(14)'ün kendi SMA50 seviyesine göre yüzde farkı. | % |
| 64 | `Close_Location_Value_CLV` | Mum yapısı | Kapanışın mum aralığındaki konumu: ((C-L)-(H-C))/(H-L). | -1..1 |
| 65 | `Upper_Wick_Pct` | Mum yapısı | Üst fitilin mum aralığına göre yüzdesi. | % |
| 66 | `Lower_Wick_Pct` | Mum yapısı | Alt fitilin mum aralığına göre yüzdesi. | % |
| 67 | `Candle_Body_Pct` | Mum yapısı | Mutlak mum gövdesinin mum aralığına göre yüzdesi. | % |
| 68 | `Hour_Sin` | Zaman | UTC saatin döngüsel sinüs kodlaması: sin(2π·saat/24). | -1..1 |
| 69 | `Hour_Cos` | Zaman | UTC saatin döngüsel kosinüs kodlaması: cos(2π·saat/24). | -1..1 |
| 70 | `Day_of_Week` | Zaman | UTC haftanın günü; eğitim pipeline'ındaki sayısal kodlama korunmalı. | 0..6 |
| 71 | `BTC_Ret_5m` | Piyasa bağlamı | Aynı timestamp'teki BTCUSDT perpetual 5 bar getirisi. | % |
| 72 | `BTC_Ret_15m` | Piyasa bağlamı | Aynı timestamp'teki BTCUSDT perpetual 15 bar getirisi. | % |
| 73 | `BTC_Ret_50m` | Piyasa bağlamı | Aynı timestamp'teki BTCUSDT perpetual 50 bar getirisi. | % |
| 74 | `BTC_Ret_200m` | Piyasa bağlamı | Aynı timestamp'teki BTCUSDT perpetual 200 bar getirisi. | % |
| 75 | `BTC_Z_Score_15` | Piyasa bağlamı | BTCUSDT kapanışının 15 barlık z-skoru. | standart sapma |
| 76 | `BTC_Z_Score_50` | Piyasa bağlamı | BTCUSDT kapanışının 50 barlık z-skoru. | standart sapma |
| 77 | `BTC_Volume_vs_SMA50_Pct` | Piyasa bağlamı | BTC quote/base hacminin 50 bar ortalamasına yüzde farkı. | % |
| 78 | `ETH_BTC_Ret_5m` | Piyasa bağlamı | ETHUSDT/BTCUSDT oranının 5 bar getirisi. | % |
| 79 | `ETH_BTC_Ret_15m` | Piyasa bağlamı | ETHUSDT/BTCUSDT oranının 15 bar getirisi. | % |
| 80 | `ETH_BTC_Ret_50m` | Piyasa bağlamı | ETHUSDT/BTCUSDT oranının 50 bar getirisi. | % |
| 81 | `ETH_BTC_Ret_200m` | Piyasa bağlamı | ETHUSDT/BTCUSDT oranının 200 bar getirisi. | % |
| 82 | `MomentumSpeed_Diff_5_15` | Etkileşim | 5 ve 15 bar momentum hızları arasındaki fark. | pipeline birimi |
| 83 | `MomentumSpeed_Diff_15_50` | Etkileşim | 15 ve 50 bar momentum hızları arasındaki fark. | pipeline birimi |
| 84 | `MomentumSpeed_Diff_50_200` | Etkileşim | 50 ve 200 bar momentum hızları arasındaki fark. | pipeline birimi |
| 85 | `Momentum_Curvature_5_15_50` | Etkileşim | 5/15/50 bar momentum hızlarından türetilen eğrilik/ivme. | pipeline birimi |
| 86 | `Momentum_Curvature_15_50_200` | Etkileşim | 15/50/200 bar momentum hızlarından türetilen eğrilik/ivme. | pipeline birimi |
| 87 | `Price_Efficiency_15` | Etkileşim | 15 bardaki net fiyat hareketinin toplam mutlak harekete oranı. | 0..1 |
| 88 | `Price_Efficiency_50` | Etkileşim | 50 bardaki net fiyat hareketinin toplam mutlak harekete oranı. | 0..1 |
| 89 | `Trend_Alignment_Score` | Etkileşim | Kısa/orta/uzun trend göstergelerinin aynı yönde olma skoru. | pipeline skoru |
| 90 | `Trend_Strength_ATR15` | Etkileşim | Trend hareketinin ATR(15)'e göre normalize edilmiş gücü. | oran |
| 91 | `Breakout_Position_50` | Etkileşim | Fiyatın son 50 bar aralığındaki normalize konumu. | oran |
| 92 | `ATR_Ratio_5_50` | Volatilite | ATR(5) / ATR(50) volatilite rejimi oranı. | oran |
| 93 | `ATR_Ratio_15_200` | Volatilite | ATR(15) / ATR(200) volatilite rejimi oranı. | oran |
| 94 | `ATR_Acceleration_5_15` | Volatilite | Kısa ATR(5) ile ATR(15) arasındaki volatilite ivmesi. | oran/pipeline |
| 95 | `ZScore_Spread_5_50` | Rejim | Z-score(5) ile Z-score(50) farkı. | standart sapma |
| 96 | `ZScore_Spread_15_200` | Rejim | Z-score(15) ile Z-score(200) farkı. | standart sapma |
| 97 | `BBWidth_to_ATR15` | Etkileşim | Bollinger genişliğinin ATR(15)'e oranı. | oran |
| 98 | `VolAdj_Return_5` | Etkileşim | 5 bar getirisinin volatiliteye göre normalize edilmiş hali. | oran |
| 99 | `VolAdj_Return_15` | Etkileşim | 15 bar getirisinin volatiliteye göre normalize edilmiş hali. | oran |
| 100 | `VolAdj_Return_50` | Etkileşim | 50 bar getirisinin volatiliteye göre normalize edilmiş hali. | oran |
| 101 | `Downside_Upside_Vol_Ratio_50` | Etkileşim | Son 50 bardaki negatif ve pozitif getiri volatilitesi oranı. | oran |
| 102 | `Volume_Ratio_5_50` | Hacim | 5 ve 50 bar hacim ortalamalarının oranı. | oran |
| 103 | `Volume_Ratio_15_200` | Hacim | 15 ve 200 bar hacim ortalamalarının oranı. | oran |
| 104 | `Price_Volume_Confirm_5` | Etkileşim | 5 bar fiyat yönü ile hacim değişiminin teyit skoru. | pipeline skoru |
| 105 | `Price_Volume_Confirm_15` | Etkileşim | 15 bar fiyat yönü ile hacim değişiminin teyit skoru. | pipeline skoru |
| 106 | `CMF_Return_Interaction_15` | Etkileşim | CMF ile 15-bar getirisinin pipeline-tanımlı etkileşimi. | pipeline birimi |
| 107 | `CLV_Volume_Interaction` | Etkileşim | CLV ile normalize hacmin etkileşimi. | pipeline birimi |
| 108 | `OBV_Normalized_15` | Hacim | OBV'nin 15 barlık ölçeğe göre normalize edilmiş hali. | oran |
| 109 | `OBV_Normalized_50` | Hacim | OBV'nin 50 barlık ölçeğe göre normalize edilmiş hali. | oran |
| 110 | `Taker_Buy_Imbalance` | İşlem akışı | Taker alış hacminin toplam hacme göre alış/satış dengesizliği. | oran/% |
| 111 | `Taker_Imbalance_Change_15` | İşlem akışı | Taker alış dengesizliğinin 15 barlık değişimi. | pipeline birimi |
| 112 | `Avg_Trade_Size_vs_SMA50_Pct` | İşlem akışı | Ortalama işlem büyüklüğünün SMA50'sine yüzde farkı. | % |
| 113 | `Avg_Trade_Size_Trend_15_Pct` | İşlem akışı | Ortalama işlem büyüklüğünün 15 barlık trendi. | % |
| 114 | `Signed_Body_Pct` | Mum yapısı | Yön işaretli mum gövdesi yüzdesi; yeşil pozitif, kırmızı negatif. | % |
| 115 | `Wick_Imbalance_Pct` | Mum yapısı | Üst ve alt fitil arasındaki normalize fark. | % |
| 116 | `Rejection_Strength` | Mum yapısı | Fitillerden türetilmiş fiyat reddi gücü. | pipeline skoru |
| 117 | `Range_vs_ATR15` | Mum yapısı | Mum high-low aralığının ATR(15)'e oranı. | oran |
| 118 | `Open_Gap_Pct` | Mum yapısı | Açılışın önceki kapanışa yüzde farkı. | % |
| 119 | `Inside_Bar` | Mum yapısı | Mevcut mum önceki mum aralığının tamamen içindeyse 1. | 0/1 |
| 120 | `Outside_Bar` | Mum yapısı | Mevcut mum önceki mumun high-low aralığını kapsıyorsa 1. | 0/1 |
| 121 | `Range_Compression_20` | Volatilite | Mevcut aralığın son 20 bar aralık rejimine göre sıkışması. | oran |
| 122 | `RSI_Spread_5_50` | Momentum | RSI(5) ile RSI(50) farkı. | puan |
| 123 | `RSI_Spread_15_200` | Momentum | RSI(15) ile RSI(200) farkı. | puan |
| 124 | `RSI_Slope_Acceleration` | Momentum | Kısa ve uzun RSI eğimleri arasındaki ivmelenme. | pipeline birimi |
| 125 | `RSI_EMA_Trend_Interaction` | Etkileşim | RSI momentumu ile EMA trend yönünün etkileşimi. | pipeline skoru |
| 126 | `MACD_Stoch_Agreement` | Etkileşim | MACD ve Stochastic aynı yönü gösteriyorsa yükselen uyum skoru. | pipeline skoru |
| 127 | `MeanReversion_Trend_Tension` | Etkileşim | Ortalama dönüş sinyali ile trend sinyali arasındaki gerilim. | pipeline skoru |
| 128 | `Alt_vs_BTC_Ret_5` | Piyasa bağlamı | Coinin 5 bar getirisi eksi BTC'nin aynı dönem getirisi. | yüzde puan |
| 129 | `Alt_vs_BTC_Ret_15` | Piyasa bağlamı | Coinin 15 bar getirisi eksi BTC'nin aynı dönem getirisi. | yüzde puan |
| 130 | `BTC_Corr_50` | Piyasa bağlamı | Coin getirisi ile BTC getirisinin 50 bar korelasyonu. | -1..1 |
| 131 | `BTC_Beta_200` | Piyasa bağlamı | Coinin BTC getirisine 200 barlık beta hassasiyeti. | katsayı |
| 132 | `Quote_Volume_Market_Share` | Piyasa bağlamı | Coin quote_volume / aynı timestamp'teki izlenen tüm kontratların quote_volume toplamı. | 0..1 |

## 133. kategorik feature: `symbol`

- LightGBM: Her `.joblib` içindeki `symbol_map` kullanılarak sembol integer değere çevrilir ve categorical dtype yapılır.
- CatBoost: Ham sembol metni kullanılır; modelde kategorik sütun indeksi 132'dir.
- Modelin tanımadığı sembolde işlem açılmaz.

## Paper-trade targetları

| Target | Ufuk | TP | SL | Öncelikli model |
|---|---:|---:|---:|---|
| `sonuc_15mum_tp2p5_sl1p25` | 15 bar / 3,75 saat | %2,5 | %1,25 | LightGBM |
| `sonuc_50mum_tp5_sl2p5` | 50 bar / 12,5 saat | %5 | %2,5 | LightGBM |
| `sonuc_10mum_tp2p5_sl1p25` | 10 bar / 2,5 saat | %2,5 | %1,25 | LightGBM |
| `sonuc_5mum_tp2p5_sl1p25` | 5 bar / 1,25 saat | %2,5 | %1,25 | LightGBM |
| `sonuc_200mum_tp10_sl5` | 200 bar / 50 saat | %10 | %5 | CatBoost |

Target sınıfları: `SL`, `TETIKLENMEDI`, `TP`. Aynı mumda TP ve SL görülürse OHLC sıralamayı çözemediği için backtest/paper değerlendirmesinde konservatif olarak SL kabul edilmelidir.

## Model yolları

Seçilen dosyalar `BTT/models/15m/lightgbm` ve `BTT/models/15m/catboost` altındadır. Tam inference sözleşmesi `paper_trade_15m_spec.json` dosyasındadır.

## Canlı veri kontrol listesi

1. Coin, BTCUSDT ve ETHUSDT 15m mumlarının kapanmasını bekle.
2. Mumları aynı `open_time` üzerinde hizala.
3. 132 feature'ı bu tablodaki sırayla üret.
4. LightGBM için model içindeki `symbol_map` değerini ekle; CatBoost için ham sembolü ekle.
5. Modelden sınıf ve `TP` olasılığını al.
6. Yalnız tahmin `TP` ve seçilen olasılık eşiği sağlanıyorsa aday sinyal oluştur.
7. Fill fiyatını sinyal kapanışından ayrı kaydet; aradaki fark slippage'dır.
8. Aynı sembolde açık pozisyon varsa yeni sinyali reddet.
9. TP/SL olmazsa target ufku sonunda time-exit uygula.

## Doğruluk uyarısı

`pipeline-tanımlı` feature'ların yalnız isimden yeniden yorumlanması eğitim-canlı uyumsuzluğu yaratabilir. Paper trade başlamadan önce aynı tarihli bir geçmiş mum için yeni ortamın ürettiği 132 değer, mevcut CSV satırıyla toleranslı olarak karşılaştırılmalıdır.
