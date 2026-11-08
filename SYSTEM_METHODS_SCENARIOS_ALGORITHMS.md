# MotorLab Paper Trading System — Teknik Devir ve Aktarım Dokümanı

> Son doğrulama: 8 Kasım 2026 (Europe/Istanbul). Bu belge mevcut kodu, model paketini, backtest kataloğunu ve canlı paper-trading kurallarını birlikte tarif eder. Parola, API anahtarı veya `.env` değeri içermez.

## 1. Yönetici özeti

Sistem Binance USD-M kapalı mumlarını takip eder; 106 coin için 132 özellik üretir; LightGBM model paketini çalıştırır; seçili senaryolarda sinyal oluşturur ve aynı tekil sinyali dört bağımsız paper hesapta izler: Spot 1×, Futures 2×, Futures 5× ve Futures 10×. Şu anda Binance’a gerçek veya demo emir gönderimi kapalıdır.

- Tam senaryo kataloğu: **219** (`1..219`, boş ID yok).
- Güncel aktif senaryolar: **11** — `5, 9, 10, 15, 19, 49, 56, 64, 79, 108, 111`.
- Katalog dağılımı: **93×5m, 59×15m, 33×1h, 34×4h**.
- Model artefaktı: **83 model**.
- Feature sayısı: **132**; kolon sırası `feature_order.json` ile sabittir.
- Coin evreni: **106 benzersiz sembol**; model sembol haritasıyla başlangıçta birebir doğrulanır.
- Her tekil sinyal için marjin: **10 USDT / hesap**. Exposure: Spot 10, 2× 20, 5× 50, 10× 100 USDT.
- Her hesabın başlangıç paper bakiyesi: **1.000 USDT**.
- Paper geçmişi 1 Ağustos 2026 başlangıcına göre yeniden kurulmuştur. Snapshot sayıları çalışma anında dashboard/veritabanından okunmalıdır; bu dokümana sabitlenmez.

### Önemli kapsam ayrımı

Kaynak Excel dosyasının adı `BACKTEST_POSITIVE_METHODS_1M_EXECUTION.xlsx` ve `Pozitif_Sonuclar` sayfasındaki 219 satırın orijinal backtest getirisi pozitiftir (minimum yaklaşık %0,026). Bu nedenle kaynak katalogda backtest sonucu sıfır/eksi olan ayrı bir ID yoktur. Buna rağmen bu belgede, güncel paper sisteminde aktif olmayanlar ve canlıda kârsız kalabilecek olanlar dahil **219 ID’nin tamamı** listelenmiştir. Backtest kârı canlı kâr garantisi değildir.

## 2. Güncel çalışma ortamı

- Yerel proje: `D:\Paper_ML_BSH`
- MacBook proje dizini: `/Users/berfin/paper-ml-bsh`
- macOS launchd servisi: `com.motorlab.papertrade`
- Uvicorn: `0.0.0.0:8765`
- Yerel ağ dashboardu (IP değişmediyse): `http://192.168.1.102:8765/`
- Sağlık kontrolü: `/healthz`
- Ana API: `/api/motors`
- İşlem arşivi: `/history`
- Kimlik doğrulama güncel kurulumda kapalıdır.
- Public tunnel kalıcı altyapı değildir; farklı internetten erişim için yeniden güvenli tünel/VPN kurulmalıdır.

## 3. Mimari ve veri akışı

```text
Binance kapalı mum REST/WS
        │
        ├─ Tam geçmiş arşivi (SQLite/WAL, candle_history.db)
        ├─ Süreklilik ve son kapanmış mum kontrolü
        │
        ▼
Portable exact feature generator (132 feature)
        │  BTC/ETH bağlamı + 106 coin
        ▼
83 LightGBM artefaktı
        │  sınıflandırma + range + volatility
        ▼
219 senaryo filtresi → yalnızca seçili 11 aktif ID
        │
        ▼
Tekil signal/position kaydı
        ├─ Spot 1× paper
        ├─ Futures 2× isolated paper
        ├─ Futures 5× isolated paper
        └─ Futures 10× isolated paper
        │
        ▼
1m mumlarla entry / TP / SL / timeout / liquidation / fee / funding
        │
        ▼
SQLite ledger + canlı dashboard
```

## 4. Zamanlama ve tarama

- 5m: UTC dakika `00,05,10,...` kapanışlarından sonra.
- 15m: UTC dakika `00,15,30,45` kapanışlarından sonra.
- 1h: her UTC saat başında.
- 4h: UTC `00:00,04:00,08:00,12:00,16:00,20:00`.
- Mum kapanışından sonra varsayılan 15 saniye grace uygulanır.
- Timeframe taramaları paralel yürür; 4h/1h hesaplaması 5m/15m giriş penceresini bloke etmez.
- Aynı boundary iki kez kuyruğa alınmaz; timeframe başına ayrı async lock vardır.
- Feature+inference bitip giriş zamanı geçerse sinyal sadece `-5 saniye … +180 saniye` toleransında kabul edilir. Güncel `PT_ENTRY_MAX_LAG_SECONDS=180` tüm timeframe’ler için geçerlidir.
- Önceden tartışılan UTC 09:00–21:00 giriş filtresi **iptal edilmiştir**; güncel motorda saat filtresi yoktur.

## 5. Mum geçmişi, veri bütünlüğü ve backtest senkronizasyonu

1. `PT_FULL_HISTORY_ENABLED=true` iken her coin/timeframe geçmişi sayfalı olarak Binance’tan indirilir ve `candle_history.db` içinde saklanır.
2. Varsayılan başlangıç `2022-08-01T00:00:00Z`; fakat gerçek aktarımda backtest kaynağının mevcut en eski mumuyla eşleştirilmesi hedeflenmiştir.
3. Sonraki açılışlarda yalnızca son saklanan mumdan sonrası indirilip seri üzerine eklenir; duplicate `open_time` son kayıtla güncellenir.
4. Feature hesaplamasında sentetik mum üretilmez. Eksik/güncel olmayan seri Binance’tan tekrar çekilir.
5. En az 250 kapanmış mum gerekir. İç boşluk, stale mum veya açık/future mum tespit edilir.
6. Hatası olan coin 24 saatlik quote-volume ilk 20 içindeyse (BTC/ETH her zaman kritik) o timeframe çevrimi tamamen bloklanır ve UI warning üretir.
7. Kritik olmayan eksik coin sadece o timeframe çevriminden çıkarılır; diğer coinler devam eder.
8. `4h:PONSUSDT`, `4h:MARSCOINUSDT`, `4h:牛来USDT` genç listeleme/yetersiz geçmiş nedeniyle uyarıdan sessize alınmıştır; yeterli geçmiş yoksa kendileri işlenmez.
9. Feature matrisi ortak kapanmış zamanda oluşturulur. BTCUSDT ve ETHUSDT bağlamı yoksa inference yapılmaz.
10. Eski 'yalnızca son 1000 mum' riski, kalıcı tam geçmiş + incremental append yaklaşımıyla giderilmiştir. `warmup_bars` fallback mod içindir; tam geçmiş açıkken seri tail ile kesilmez.

## 6. Model sözleşmesi

- Modeller joblib artefaktıdır ve LightGBM modelini, sembol-kategori haritasını ve bazı modellerde sınıf listesini taşır.
- Başlangıçta her modelin `feature_name_` değeri tam olarak `132 feature + symbol` sırasıyla karşılaştırılır. Sıra farklıysa servis açılmaz.
- `symbol`, model eğitimindeki categorical kod haritasıyla pandas categorical olarak verilir.
- Sınıflandırıcı çıktısı için tahmin sınıfı `TP` olmalı; ayrıca `TP` sınıf olasılığı `tp_score` olarak kaydedilir.
- Regresyon modelleri `future_range_{h}mum_pct` ve `realized_volatility_{h}mum_pct` değerlerini üretir.
- Horizon 10 range/volatility modeli yoksa açıkça onaylanan fallback kullanılır: aynı timeframe’de `h5` ve `h15` tahminlerinin aritmetik ortalaması.
- Model paketi eğitim ayrımı: coin başına kronolojik, validation %15, purge 200 mum. Sınıflandırmada `AYNI_MUM_TP_SL` ve `YETERSIZ_VERI` etiketleri eğitimden çıkarılmıştır.

## 7. Senaryo adının okunması

Örnek: `h50_tp10__range_full_volatility__t70`

- `h50`: pozisyon en fazla 50 adet kendi timeframe mumu tutulur.
- `tp10`: take-profit +%10; katalogdaki SL genellikle TP’nin yarısıdır ve tabloda ayrıca verilmiştir.
- `range_full_volatility`: sınıflandırıcı sinyaline range ve volatility filtreleri birlikte eklenir.
- `t70`: `tp_score >= 0.70` eşik filtresi.
- Horizon fiziksel süre örneği: 1h × 50 = 50 saat; 4h × 10 = 40 saat; 15m × 200 = 50 saat.

### Filtrelerin kesin anlamı

| Filter | Kabul koşulu (TP sınıfı ve varsa threshold sonrasında) |
|---|---|
| `signal_only` | Ek regresyon filtresi yok. |
| `threshold` | Ek range/vol filtresi yok; `tp_score` eşiği zaten üst adımda uygulanır. |
| `range_full` | Tahmin edilen future range ≥ TP yüzdesi. |
| `volatility` | Tahmin edilen realized volatility, `[vol_low, vol_high]` aralığında. |
| `range_half_volatility` | Future range ≥ TP/2 ve volatility aralık içinde. |
| `range_full_volatility` | Future range ≥ TP ve volatility aralık içinde. |

Adaylar önce `tp_score` azalan, sonra range tahmini azalan, sonra sembol alfabetik sıralanır. Güncel aktif ID’ler stackable’dır: aynı coin + aynı senaryo yeni bir mumda tekrar sinyal üretirse yeni tekil pozisyon eklenir. Aynı mum/senaryo/sembol duplicate’i veritabanı unique anahtarlarıyla engellenir.

## 8. Pozisyon yaşam döngüsü

1. Sinyal mumu kapanır; `signal_time` ortak son kapanmış mumdur.
2. `entry_time = signal_time + timeframe süresi`; durum `PENDING` olur.
3. Coinin ilgili 1m mumu geldiğinde, `open_time >= entry_time` ise giriş 1m mum açılış fiyatından yapılır.
4. 4h temel/backtest uyumu için girişe `×1.0002`, çıkışa `×0.9998` slip uygulanır.
5. `TP = entry × (1 + tp_pct/100)`, `SL = entry × (1 - sl_pct/100)`.
6. `deadline = entry_time + horizon × timeframe`.
7. Her kapanmış 1m mumda önce liquidation, sonra SL, sonra TP, sonra timeout kontrol edilir.
8. Aynı 1m mum hem TP hem SL’ye dokunursa kodun önceliği **SL**’dir (korumacı varsayım).
9. Timeout’ta ilgili 1m mum kapanış fiyatı kullanılır.
10. Tekil temel pozisyon kapandığında dört execution account sonucu da kendi exposure, fee, funding ve liquidation koşuluyla kapatılır.

## 9. Dört paper hesap ve maliyetler

| Hesap | Piyasa | Marjin | Kaldıraç | Exposure | Fee (tek yön) |
|---|---:|---:|---:|---:|---:|
| Spot 1× | SPOT | 10 USDT | 1× | 10 USDT | %0,10 |
| Futures 2× | FUTURES isolated simülasyon | 10 USDT | 2× | 20 USDT | %0,05 |
| Futures 5× | FUTURES isolated simülasyon | 10 USDT | 5× | 50 USDT | %0,05 |
| Futures 10× | FUTURES isolated simülasyon | 10 USDT | 10× | 100 USDT | %0,05 |

- `quantity = exposure / entry_price`.
- Futures yaklaşık liquidation fiyatı: `entry × (1 - 1/leverage + maintenance_rate)`; maintenance varsayılan %0,5.
- Entry ve exit fee exposure/notional üzerinden ayrı hesaplanır.
- Futures funding, Binance funding geçmişinden giriş ve çıkış arasındaki oranların toplamı × exposure olarak hesaplanır. API başarısızsa funding 0 kalır ve `funding_error` saklanır.
- Net P&L = gross − entry fee − exit fee − funding.
- Return % = net P&L / 10 USDT marjin × 100.
- Dashboard sinyal sayısını yalnızca Spot kanonik kaydından gösterir; hesap kayıtlarını dörtle çarpıp sinyal sayısına eklemez.
- Binance Demo/gerçek emir aynalama kodu mevcuttur ama güncel ayarda `PT_MOTORLAB_DEMO_ENABLED=false`; aktarım sırasında yanlışlıkla açılmamalıdır.

## 10. Dashboard

- Ana ekran açık/kapanmış tekil işlem satırlarını göstermez; ayrıntılar `/history` arşivindedir.
- Her senaryo kartı `açık / kapanmış` tekil Spot sayısını ve Spot/2×/5×/10× return yüzdelerini ayrı gösterir.
- Coin histogramında Spot net P&L: kâr sağa yeşil, zarar sola kırmızı.
- 15m/1h/4h özetinde tekil açık-kapalı sayısı ve Spot return gösterilir.
- Dört hesabın equity eğrisi yalnızca kapanmış trade’lerle kümülatif oluşur; açık P&L ayrı anlık metriklerde hesaba katılır.
- UI ve grafikler 10 saniyede bir `/api/motors` üzerinden yenilenir.

## 11. Veritabanı ana tabloları

| Tablo | Amaç |
|---|---|
| `scenarios` | Senaryo tanımı, sayaçlar, temel portföy özeti. |
| `signals` | Kabul/red kararı, score, range/vol tahmini. |
| `positions` | Tekil/kanonik sinyal pozisyonu; dört hesaba çoğaltılmaz. |
| `execution_account_positions` | Her `position_id` için Spot/2×/5×/10× ayrı sonuç. Unique `(position_id, account_id)`. |
| `candles` (`candle_history.db`) | Timeframe+symbol+open_time anahtarlı kalıcı kapalı mum arşivi. |
| `events` | Cycle, warning, data integrity ve pozisyon olayları. |
| `futures_demo_mirrors` | Opsiyonel Binance Futures Demo aynalama durumları; güncel ayarda kullanılmıyor. |

## 12. 132 feature envanteri

Feature adlarındaki `5/15/50/200`, içinde bulunulan timeframe’in mum sayısını ifade eder; örneğin 4h modelindeki `Return_5m` etiketi tarihsel isimdir ve 5 adet 4h mum üzerinden hesaplanır, gerçek 5 dakika anlamına gelmez. Üretimde `paper_trade_app/exact_features.py` üzerinden `feature_generator_portable` kullanılır; `paper_trade_app/features.py` yaklaşık/fallback uygulamadır ve ana sözleşme değildir.

1. `open`
2. `high`
3. `low`
4. `close`
5. `volume`
6. `quote_volume`
7. `trade_count`
8. `taker_buy_base_volume`
9. `taker_buy_quote_volume`
10. `Return_5m`
11. `Max_Drawdown_5`
12. `Min_Runup_5`
13. `Close_vs_EMA_5_Pct`
14. `RSI_5`
15. `Z_Score_5`
16. `ATR_vs_Close_5_Pct`
17. `Volume_vs_SMA_5_Pct`
18. `Return_15m`
19. `Max_Drawdown_15`
20. `Min_Runup_15`
21. `Close_vs_EMA_15_Pct`
22. `RSI_15`
23. `Z_Score_15`
24. `ATR_vs_Close_15_Pct`
25. `Volume_vs_SMA_15_Pct`
26. `Return_50m`
27. `Max_Drawdown_50`
28. `Min_Runup_50`
29. `Close_vs_EMA_50_Pct`
30. `RSI_50`
31. `Z_Score_50`
32. `ATR_vs_Close_50_Pct`
33. `Volume_vs_SMA_50_Pct`
34. `Return_200m`
35. `Max_Drawdown_200`
36. `Min_Runup_200`
37. `Close_vs_EMA_200_Pct`
38. `RSI_200`
39. `Z_Score_200`
40. `ATR_vs_Close_200_Pct`
41. `Volume_vs_SMA_200_Pct`
42. `EMA5_vs_EMA15_Pct`
43. `EMA15_vs_EMA50_Pct`
44. `EMA50_vs_EMA200_Pct`
45. `Close_vs_PSAR_Pct`
46. `Close_vs_Ichimoku_Kumo_Pct`
47. `RSI_14_Slope_5`
48. `RSI_14_Slope_15`
49. `MACD_Line_vs_Signal_Pct`
50. `MACD_Hist_Slope_5`
51. `Stoch_K_vs_D_Dist`
52. `BB_Width_vs_SMA50_Pct`
53. `Close_vs_BB_Upper_Pct`
54. `Close_vs_BB_Lower_Pct`
55. `Squeeze_On`
56. `Choppiness_Index_14`
57. `OBV_Slope_5`
58. `OBV_Slope_15`
59. `OBV_Slope_50`
60. `OBV_Slope_200`
61. `Volume_Pressure`
62. `CMF_20`
63. `MFI_14_vs_SMA50_Pct`
64. `Close_Location_Value_CLV`
65. `Upper_Wick_Pct`
66. `Lower_Wick_Pct`
67. `Candle_Body_Pct`
68. `Hour_Sin`
69. `Hour_Cos`
70. `Day_of_Week`
71. `BTC_Ret_5m`
72. `BTC_Ret_15m`
73. `BTC_Ret_50m`
74. `BTC_Ret_200m`
75. `BTC_Z_Score_15`
76. `BTC_Z_Score_50`
77. `BTC_Volume_vs_SMA50_Pct`
78. `ETH_BTC_Ret_5m`
79. `ETH_BTC_Ret_15m`
80. `ETH_BTC_Ret_50m`
81. `ETH_BTC_Ret_200m`
82. `MomentumSpeed_Diff_5_15`
83. `MomentumSpeed_Diff_15_50`
84. `MomentumSpeed_Diff_50_200`
85. `Momentum_Curvature_5_15_50`
86. `Momentum_Curvature_15_50_200`
87. `Price_Efficiency_15`
88. `Price_Efficiency_50`
89. `Trend_Alignment_Score`
90. `Trend_Strength_ATR15`
91. `Breakout_Position_50`
92. `ATR_Ratio_5_50`
93. `ATR_Ratio_15_200`
94. `ATR_Acceleration_5_15`
95. `ZScore_Spread_5_50`
96. `ZScore_Spread_15_200`
97. `BBWidth_to_ATR15`
98. `VolAdj_Return_5`
99. `VolAdj_Return_15`
100. `VolAdj_Return_50`
101. `Downside_Upside_Vol_Ratio_50`
102. `Volume_Ratio_5_50`
103. `Volume_Ratio_15_200`
104. `Price_Volume_Confirm_5`
105. `Price_Volume_Confirm_15`
106. `CMF_Return_Interaction_15`
107. `CLV_Volume_Interaction`
108. `OBV_Normalized_15`
109. `OBV_Normalized_50`
110. `Taker_Buy_Imbalance`
111. `Taker_Imbalance_Change_15`
112. `Avg_Trade_Size_vs_SMA50_Pct`
113. `Avg_Trade_Size_Trend_15_Pct`
114. `Signed_Body_Pct`
115. `Wick_Imbalance_Pct`
116. `Rejection_Strength`
117. `Range_vs_ATR15`
118. `Open_Gap_Pct`
119. `Inside_Bar`
120. `Outside_Bar`
121. `Range_Compression_20`
122. `RSI_Spread_5_50`
123. `RSI_Spread_15_200`
124. `RSI_Slope_Acceleration`
125. `RSI_EMA_Trend_Interaction`
126. `MACD_Stoch_Agreement`
127. `MeanReversion_Trend_Tension`
128. `Alt_vs_BTC_Ret_5`
129. `Alt_vs_BTC_Ret_15`
130. `BTC_Corr_50`
131. `BTC_Beta_200`
132. `Quote_Volume_Market_Share`

### Feature ailelerinin anlamı

- Ham piyasa: OHLC, base/quote volume, trade count, taker-buy hacimleri.
- Momentum/getiri: 5/15/50/200 mum return, momentum speed ve curvature.
- Trend: EMA mesafeleri, PSAR, Ichimoku, alignment, breakout, price efficiency.
- Osilatör: RSI, RSI slope/spread, Stochastic, MFI, MACD.
- Volatilite/risk: ATR oranları, Bollinger width, squeeze, z-score, downside/upside vol.
- Hacim/akış: OBV, CMF, volume pressure, taker imbalance, average trade size.
- Mum yapısı: body/wick, CLV, rejection, inside/outside bar, gap, compression.
- Piyasa bağlamı: BTC return/z-score/volume, ETH/BTC relative return, alt-vs-BTC, BTC correlation/beta ve market share.
- Zaman: saat sin/cos ve haftanın günü.

## 13. 106 coin evreni

`SNXUSDT`, `MTLUSDT`, `COMPUSDT`, `QTUMUSDT`, `SKLUSDT`, `ALICEUSDT`, `DASHUSDT`, `OGNUSDT`, `ICPUSDT`, `KSMUSDT`, `LITUSDT`, `XTZUSDT`, `ZENUSDT`, `ONTUSDT`, `ZRXUSDT`, `ZECUSDT`, `MASKUSDT`, `BELUSDT`, `1000SHIBUSDT`, `IOTAUSDT`, `GRTUSDT`, `EGLDUSDT`, `ZILUSDT`, `AXSUSDT`, `SANDUSDT`, `MANAUSDT`, `ENJUSDT`, `THETAUSDT`, `RVNUSDT`, `1INCHUSDT`, `C98USDT`, `ANKRUSDT`, `ONEUSDT`, `GTCUSDT`, `CRVUSDT`, `INJUSDT`, `XRPUSDT`, `COTIUSDT`, `RSRUSDT`, `SUSHIUSDT`, `AAVEUSDT`, `ADAUSDT`, `BANDUSDT`, `CELRUSDT`, `CHRUSDT`, `CHZUSDT`, `DOGEUSDT`, `DOTUSDT`, `SOLUSDT`, `TRBUSDT`, `UNIUSDT`, `YFIUSDT`, `HBARUSDT`, `BCHUSDT`, `ETCUSDT`, `IOTXUSDT`, `KNCUSDT`, `KAVAUSDT`, `HOTUSDT`, `1000PEPEUSDT`, `NEOUSDT`, `RLCUSDT`, `WLDUSDT`, `ALGOUSDT`, `FILUSDT`, `XLMUSDT`, `BATUSDT`, `VETUSDT`, `ENAUSDT`, `ONDOUSDT`, `TRUMPUSDT`, `VVVUSDT`, `SFPUSDT`, `FORMUSDT`, `ARBUSDT`, `HEMIUSDT`, `AKEUSDT`, `USELESSUSDT`, `UAIUSDT`, `IOSTUSDT`, `SOPHUSDT`, `XPLUSDT`, `SUIUSDT`, `FFUSDT`, `FLOCKUSDT`, `KATUSDT`, `牛来USDT`, `AVAXUSDT`, `XMRUSDT`, `NEARUSDT`, `PUMPUSDT`, `ASTERUSDT`, `LINKUSDT`, `BNBUSDT`, `ETHUSDT`, `TAOUSDT`, `HYPEUSDT`, `RAYSOLUSDT`, `ATOMUSDT`, `RUNEUSDT`, `LTCUSDT`, `BTCUSDT`, `MARSCOINUSDT`, `TRXUSDT`, `PAXGUSDT`, `PONSUSDT`

ModelRegistry başlangıçta hem sayının 106 ve unique olduğunu hem de bütün model artefaktlarındaki sembol kümeleriyle birebir eşleştiğini doğrular. Eşleşmezse servis fail-fast olur.

## 14. 83 model artefaktı

| Timeframe | Rol | Target | Artefakt |
|---|---|---|---|
| 15m | classifier | `sonuc_10mum_tp2p5_sl1p25` | `models/15m/lightgbm/sonuc_10mum_tp2p5_sl1p25.joblib` |
| 15m | classifier | `sonuc_10mum_tp5_sl2p5` | `models/15m/lightgbm/sonuc_10mum_tp5_sl2p5.joblib` |
| 15m | classifier | `sonuc_15mum_tp2p5_sl1p25` | `models/15m/lightgbm/sonuc_15mum_tp2p5_sl1p25.joblib` |
| 15m | classifier | `sonuc_15mum_tp5_sl2p5` | `models/15m/lightgbm/sonuc_15mum_tp5_sl2p5.joblib` |
| 15m | classifier | `sonuc_200mum_tp10_sl5` | `models/15m/lightgbm/sonuc_200mum_tp10_sl5.joblib` |
| 15m | classifier | `sonuc_200mum_tp15_sl7p5` | `models/15m/lightgbm/sonuc_200mum_tp15_sl7p5.joblib` |
| 15m | classifier | `sonuc_200mum_tp20_sl10` | `models/15m/lightgbm/sonuc_200mum_tp20_sl10.joblib` |
| 15m | classifier | `sonuc_200mum_tp2p5_sl1p25` | `models/15m/lightgbm/sonuc_200mum_tp2p5_sl1p25.joblib` |
| 15m | classifier | `sonuc_200mum_tp5_sl2p5` | `models/15m/lightgbm/sonuc_200mum_tp5_sl2p5.joblib` |
| 15m | classifier | `sonuc_50mum_tp10_sl5` | `models/15m/lightgbm/sonuc_50mum_tp10_sl5.joblib` |
| 15m | classifier | `sonuc_50mum_tp5_sl2p5` | `models/15m/lightgbm/sonuc_50mum_tp5_sl2p5.joblib` |
| 15m | classifier | `sonuc_5mum_tp2p5_sl1p25` | `models/15m/lightgbm/sonuc_5mum_tp2p5_sl1p25.joblib` |
| 15m | regression | `future_range_15mum_pct` | `models/15m/lightgbm/future_range_15mum_pct.joblib` |
| 15m | regression | `future_range_200mum_pct` | `models/15m/lightgbm/future_range_200mum_pct.joblib` |
| 15m | regression | `future_range_50mum_pct` | `models/15m/lightgbm/future_range_50mum_pct.joblib` |
| 15m | regression | `future_range_5mum_pct` | `models/15m/lightgbm/future_range_5mum_pct.joblib` |
| 15m | regression | `realized_volatility_15mum_pct` | `models/15m/lightgbm/realized_volatility_15mum_pct.joblib` |
| 15m | regression | `realized_volatility_200mum_pct` | `models/15m/lightgbm/realized_volatility_200mum_pct.joblib` |
| 15m | regression | `realized_volatility_50mum_pct` | `models/15m/lightgbm/realized_volatility_50mum_pct.joblib` |
| 15m | regression | `realized_volatility_5mum_pct` | `models/15m/lightgbm/realized_volatility_5mum_pct.joblib` |
| 1h | classifier | `sonuc_10mum_tp5_sl2p5` | `models/1h/lightgbm/sonuc_10mum_tp5_sl2p5.joblib` |
| 1h | classifier | `sonuc_15mum_tp5_sl2p5` | `models/1h/lightgbm/sonuc_15mum_tp5_sl2p5.joblib` |
| 1h | classifier | `sonuc_3mum_tp2p5_sl1p25` | `models/1h/lightgbm/sonuc_3mum_tp2p5_sl1p25.joblib` |
| 1h | classifier | `sonuc_50mum_tp10_sl5` | `models/1h/lightgbm/sonuc_50mum_tp10_sl5.joblib` |
| 1h | classifier | `sonuc_5mum_tp5_sl2p5` | `models/1h/lightgbm/sonuc_5mum_tp5_sl2p5.joblib` |
| 1h | regression | `future_range_15mum_pct` | `models/1h/lightgbm/future_range_15mum_pct.joblib` |
| 1h | regression | `future_range_3mum_pct` | `models/1h/lightgbm/future_range_3mum_pct.joblib` |
| 1h | regression | `future_range_50mum_pct` | `models/1h/lightgbm/future_range_50mum_pct.joblib` |
| 1h | regression | `future_range_5mum_pct` | `models/1h/lightgbm/future_range_5mum_pct.joblib` |
| 1h | regression | `realized_volatility_15mum_pct` | `models/1h/lightgbm/realized_volatility_15mum_pct.joblib` |
| 1h | regression | `realized_volatility_3mum_pct` | `models/1h/lightgbm/realized_volatility_3mum_pct.joblib` |
| 1h | regression | `realized_volatility_50mum_pct` | `models/1h/lightgbm/realized_volatility_50mum_pct.joblib` |
| 1h | regression | `realized_volatility_5mum_pct` | `models/1h/lightgbm/realized_volatility_5mum_pct.joblib` |
| 4h | classifier | `sonuc_10mum_tp10_sl5` | `models/4h/lightgbm/sonuc_10mum_tp10_sl5.joblib` |
| 4h | classifier | `sonuc_200mum_tp10_sl5` | `models/4h/lightgbm/sonuc_200mum_tp10_sl5.joblib` |
| 4h | classifier | `sonuc_3mum_tp5_sl2p5` | `models/4h/lightgbm/sonuc_3mum_tp5_sl2p5.joblib` |
| 4h | classifier | `sonuc_50mum_tp5_sl2p5` | `models/4h/lightgbm/sonuc_50mum_tp5_sl2p5.joblib` |
| 4h | classifier | `sonuc_5mum_tp10_sl5` | `models/4h/lightgbm/sonuc_5mum_tp10_sl5.joblib` |
| 4h | regression | `future_range_15mum_pct` | `models/4h/lightgbm/future_range_15mum_pct.joblib` |
| 4h | regression | `future_range_200mum_pct` | `models/4h/lightgbm/future_range_200mum_pct.joblib` |
| 4h | regression | `future_range_3mum_pct` | `models/4h/lightgbm/future_range_3mum_pct.joblib` |
| 4h | regression | `future_range_50mum_pct` | `models/4h/lightgbm/future_range_50mum_pct.joblib` |
| 4h | regression | `future_range_5mum_pct` | `models/4h/lightgbm/future_range_5mum_pct.joblib` |
| 4h | regression | `realized_volatility_15mum_pct` | `models/4h/lightgbm/realized_volatility_15mum_pct.joblib` |
| 4h | regression | `realized_volatility_200mum_pct` | `models/4h/lightgbm/realized_volatility_200mum_pct.joblib` |
| 4h | regression | `realized_volatility_3mum_pct` | `models/4h/lightgbm/realized_volatility_3mum_pct.joblib` |
| 4h | regression | `realized_volatility_50mum_pct` | `models/4h/lightgbm/realized_volatility_50mum_pct.joblib` |
| 4h | regression | `realized_volatility_5mum_pct` | `models/4h/lightgbm/realized_volatility_5mum_pct.joblib` |
| 5m | classifier | `sonuc_10mum_tp10_sl5` | `models/5m/lightgbm/sonuc_10mum_tp10_sl5.joblib` |
| 5m | classifier | `sonuc_10mum_tp15_sl7p5` | `models/5m/lightgbm/sonuc_10mum_tp15_sl7p5.joblib` |
| 5m | classifier | `sonuc_10mum_tp20_sl10` | `models/5m/lightgbm/sonuc_10mum_tp20_sl10.joblib` |
| 5m | classifier | `sonuc_10mum_tp2p5_sl1p25` | `models/5m/lightgbm/sonuc_10mum_tp2p5_sl1p25.joblib` |
| 5m | classifier | `sonuc_15mum_tp10_sl5` | `models/5m/lightgbm/sonuc_15mum_tp10_sl5.joblib` |
| 5m | classifier | `sonuc_15mum_tp15_sl7p5` | `models/5m/lightgbm/sonuc_15mum_tp15_sl7p5.joblib` |
| 5m | classifier | `sonuc_15mum_tp20_sl10` | `models/5m/lightgbm/sonuc_15mum_tp20_sl10.joblib` |
| 5m | classifier | `sonuc_15mum_tp5_sl2p5` | `models/5m/lightgbm/sonuc_15mum_tp5_sl2p5.joblib` |
| 5m | classifier | `sonuc_200mum_tp10_sl5` | `models/5m/lightgbm/sonuc_200mum_tp10_sl5.joblib` |
| 5m | classifier | `sonuc_200mum_tp15_sl7p5` | `models/5m/lightgbm/sonuc_200mum_tp15_sl7p5.joblib` |
| 5m | classifier | `sonuc_200mum_tp20_sl10` | `models/5m/lightgbm/sonuc_200mum_tp20_sl10.joblib` |
| 5m | classifier | `sonuc_200mum_tp2p5_sl1p25` | `models/5m/lightgbm/sonuc_200mum_tp2p5_sl1p25.joblib` |
| 5m | classifier | `sonuc_200mum_tp5_sl2p5` | `models/5m/lightgbm/sonuc_200mum_tp5_sl2p5.joblib` |
| 5m | classifier | `sonuc_3mum_tp10_sl5` | `models/5m/lightgbm/sonuc_3mum_tp10_sl5.joblib` |
| 5m | classifier | `sonuc_3mum_tp15_sl7p5` | `models/5m/lightgbm/sonuc_3mum_tp15_sl7p5.joblib` |
| 5m | classifier | `sonuc_3mum_tp2p5_sl1p25` | `models/5m/lightgbm/sonuc_3mum_tp2p5_sl1p25.joblib` |
| 5m | classifier | `sonuc_50mum_tp10_sl5` | `models/5m/lightgbm/sonuc_50mum_tp10_sl5.joblib` |
| 5m | classifier | `sonuc_50mum_tp15_sl7p5` | `models/5m/lightgbm/sonuc_50mum_tp15_sl7p5.joblib` |
| 5m | classifier | `sonuc_50mum_tp20_sl10` | `models/5m/lightgbm/sonuc_50mum_tp20_sl10.joblib` |
| 5m | classifier | `sonuc_50mum_tp2p5_sl1p25` | `models/5m/lightgbm/sonuc_50mum_tp2p5_sl1p25.joblib` |
| 5m | classifier | `sonuc_50mum_tp5_sl2p5` | `models/5m/lightgbm/sonuc_50mum_tp5_sl2p5.joblib` |
| 5m | classifier | `sonuc_5mum_tp10_sl5` | `models/5m/lightgbm/sonuc_5mum_tp10_sl5.joblib` |
| 5m | classifier | `sonuc_5mum_tp15_sl7p5` | `models/5m/lightgbm/sonuc_5mum_tp15_sl7p5.joblib` |
| 5m | classifier | `sonuc_5mum_tp20_sl10` | `models/5m/lightgbm/sonuc_5mum_tp20_sl10.joblib` |
| 5m | classifier | `sonuc_5mum_tp5_sl2p5` | `models/5m/lightgbm/sonuc_5mum_tp5_sl2p5.joblib` |
| 5m | regression | `future_range_15mum_pct` | `models/5m/lightgbm/future_range_15mum_pct.joblib` |
| 5m | regression | `future_range_200mum_pct` | `models/5m/lightgbm/future_range_200mum_pct.joblib` |
| 5m | regression | `future_range_3mum_pct` | `models/5m/lightgbm/future_range_3mum_pct.joblib` |
| 5m | regression | `future_range_50mum_pct` | `models/5m/lightgbm/future_range_50mum_pct.joblib` |
| 5m | regression | `future_range_5mum_pct` | `models/5m/lightgbm/future_range_5mum_pct.joblib` |
| 5m | regression | `realized_volatility_15mum_pct` | `models/5m/lightgbm/realized_volatility_15mum_pct.joblib` |
| 5m | regression | `realized_volatility_200mum_pct` | `models/5m/lightgbm/realized_volatility_200mum_pct.joblib` |
| 5m | regression | `realized_volatility_3mum_pct` | `models/5m/lightgbm/realized_volatility_3mum_pct.joblib` |
| 5m | regression | `realized_volatility_50mum_pct` | `models/5m/lightgbm/realized_volatility_50mum_pct.joblib` |
| 5m | regression | `realized_volatility_5mum_pct` | `models/5m/lightgbm/realized_volatility_5mum_pct.joblib` |

## 15. Tüm 219 formasyon

`Aktif=Evet`, güncel Mac motorunda tarandığı anlamına gelir. `Aktif=Hayır`, katalogdan silindiği veya başarısız olduğu anlamına gelmez; yalnızca şu anda paper motor seçiminde değildir. `BT getiri` orijinal paket backtest sonucudur; canlı paper sonucu değildir.

| ID | Aktif | TF | Batch | Senaryo | Filtre | Eşik | H | TP% | SL% | Vol aralığı | BT işlem | BT net TL | BT getiri % | MDD % | PF |
|---:|:---:|:---:|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|
| 1 | Hayır | 5m | 5m_all | `h200_tp20__threshold__t70` | `threshold` | 0.70 | 200 | 20.00 | 10.00 | 0.194…0.514 | 1125 | 50496.12 | 50.4961 | 27.62 | 1.125 |
| 2 | Hayır | 5m | 5m_all | `h200_tp20__range_full__t70` | `range_full` | 0.70 | 200 | 20.00 | 10.00 | 0.194…0.514 | 436 | 44516.23 | 44.5162 | 15.33 | 1.198 |
| 3 | Hayır | 15m | 15m_b | `h200_tp20__threshold__t70` | `threshold` | 0.70 | 200 | 20.00 | 10.00 | 0.340…0.834 | 594 | 43125.26 | 43.1253 | 20.17 | 1.143 |
| 4 | Hayır | 15m | 15m_b | `h200_tp20__range_full__t70` | `range_full` | 0.70 | 200 | 20.00 | 10.00 | 0.340…0.834 | 490 | 40540.38 | 40.5404 | 19.87 | 1.152 |
| 5 | Evet | 4h | 4h | `h10_tp10__threshold__t60` | `threshold` | 0.60 | 10 | 10.00 | 5.00 | 0.988…3.057 | 707 | 35267.04 | 35.2670 | 19.08 | 1.165 |
| 6 | Hayır | 4h | 4h | `h10_tp10__range_full__t60` | `range_full` | 0.60 | 10 | 10.00 | 5.00 | 0.988…3.057 | 707 | 35267.04 | 35.2670 | 19.08 | 1.165 |
| 7 | Hayır | 5m | 5m_all | `h200_tp10__threshold__t70` | `threshold` | 0.70 | 200 | 10.00 | 5.00 | 0.194…0.514 | 964 | 35003.42 | 35.0034 | 17.60 | 1.119 |
| 8 | Hayır | 5m | 5m_all | `h200_tp10__range_full__t70` | `range_full` | 0.70 | 200 | 10.00 | 5.00 | 0.194…0.514 | 955 | 34403.87 | 34.4039 | 18.27 | 1.117 |
| 9 | Evet | 15m | 15m_a | `h200_tp10__threshold__t70` | `threshold` | 0.70 | 200 | 10.00 | 5.00 | 0.340…0.834 | 207 | 33733.16 | 33.7332 | 13.65 | 1.603 |
| 10 | Evet | 1h | 1h | `h50_tp10__threshold__t60` | `threshold` | 0.60 | 50 | 10.00 | 5.00 | 0.639…1.616 | 1249 | 33417.10 | 33.4171 | 32.10 | 1.085 |
| 11 | Hayır | 1h | 1h | `h50_tp10__range_full__t60` | `range_full` | 0.60 | 50 | 10.00 | 5.00 | 0.639…1.616 | 1249 | 33417.10 | 33.4171 | 32.10 | 1.085 |
| 12 | Hayır | 1h | 1h | `h50_tp10__threshold__t70` | `threshold` | 0.70 | 50 | 10.00 | 5.00 | 0.639…1.616 | 153 | 32929.49 | 32.9295 | 9.99 | 1.873 |
| 13 | Hayır | 1h | 1h | `h50_tp10__range_full__t70` | `range_full` | 0.70 | 50 | 10.00 | 5.00 | 0.639…1.616 | 151 | 32469.99 | 32.4700 | 9.41 | 1.873 |
| 14 | Hayır | 15m | 15m_a | `h200_tp10__range_full__t70` | `range_full` | 0.70 | 200 | 10.00 | 5.00 | 0.340…0.834 | 207 | 30736.16 | 30.7362 | 13.65 | 1.539 |
| 15 | Evet | 1h | 1h | `h50_tp10__range_half_volatility__t60` | `range_half_volatility` | 0.60 | 50 | 10.00 | 5.00 | 0.639…1.616 | 476 | 30037.58 | 30.0376 | 19.33 | 1.233 |
| 16 | Hayır | 1h | 1h | `h50_tp10__range_full_volatility__t60` | `range_full_volatility` | 0.60 | 50 | 10.00 | 5.00 | 0.639…1.616 | 476 | 30037.58 | 30.0376 | 19.33 | 1.233 |
| 17 | Hayır | 1h | 1h | `h50_tp10__volatility__t60` | `volatility` | 0.60 | 50 | 10.00 | 5.00 | 0.639…1.616 | 476 | 30037.58 | 30.0376 | 19.33 | 1.233 |
| 18 | Hayır | 5m | 5m_all | `h200_tp20__range_full__t40` | `range_full` | 0.40 | 200 | 20.00 | 10.00 | 0.194…0.514 | 770 | 29531.21 | 29.5312 | 29.12 | 1.073 |
| 19 | Evet | 4h | 4h | `h10_tp10__volatility__t60` | `volatility` | 0.60 | 10 | 10.00 | 5.00 | 0.988…3.057 | 296 | 28978.71 | 28.9787 | 10.42 | 1.379 |
| 20 | Hayır | 4h | 4h | `h10_tp10__range_full_volatility__t60` | `range_full_volatility` | 0.60 | 10 | 10.00 | 5.00 | 0.988…3.057 | 296 | 28978.71 | 28.9787 | 10.42 | 1.379 |
| 21 | Hayır | 4h | 4h | `h10_tp10__range_half_volatility__t60` | `range_half_volatility` | 0.60 | 10 | 10.00 | 5.00 | 0.988…3.057 | 296 | 28978.71 | 28.9787 | 10.42 | 1.379 |
| 22 | Hayır | 15m | 15m_b | `h200_tp20__threshold__t50` | `threshold` | 0.50 | 200 | 20.00 | 10.00 | 0.340…0.834 | 1203 | 28278.86 | 28.2789 | 56.81 | 1.049 |
| 23 | Hayır | 5m | 5m_all | `h200_tp20__range_full__t50` | `range_full` | 0.50 | 200 | 20.00 | 10.00 | 0.194…0.514 | 758 | 26112.20 | 26.1122 | 29.45 | 1.065 |
| 24 | Hayır | 1h | 1h | `h50_tp10__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 50 | 10.00 | 5.00 | 0.639…1.616 | 72 | 25534.93 | 25.5349 | 6.69 | 2.961 |
| 25 | Hayır | 1h | 1h | `h50_tp10__volatility__t70` | `volatility` | 0.70 | 50 | 10.00 | 5.00 | 0.639…1.616 | 72 | 25534.93 | 25.5349 | 6.69 | 2.961 |
| 26 | Hayır | 1h | 1h | `h50_tp10__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 50 | 10.00 | 5.00 | 0.639…1.616 | 72 | 25534.93 | 25.5349 | 6.69 | 2.961 |
| 27 | Hayır | 15m | 15m_b | `h200_tp15__threshold__t70` | `threshold` | 0.70 | 200 | 15.00 | 7.50 | 0.340…0.834 | 544 | 25047.28 | 25.0473 | 28.09 | 1.100 |
| 28 | Hayır | 15m | 15m_b | `h200_tp15__range_full__t70` | `range_full` | 0.70 | 200 | 15.00 | 7.50 | 0.340…0.834 | 539 | 20077.57 | 20.0776 | 27.61 | 1.081 |
| 29 | Hayır | 5m | 5m_all | `h200_tp20__range_full__t60` | `range_full` | 0.60 | 200 | 20.00 | 10.00 | 0.194…0.514 | 636 | 19715.49 | 19.7155 | 29.00 | 1.058 |
| 30 | Hayır | 5m | 5m_all | `h200_tp10__volatility__t70` | `volatility` | 0.70 | 200 | 10.00 | 5.00 | 0.194…0.514 | 61 | 18757.44 | 18.7574 | 3.08 | 3.024 |
| 31 | Hayır | 5m | 5m_all | `h200_tp10__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 200 | 10.00 | 5.00 | 0.194…0.514 | 61 | 18757.44 | 18.7574 | 3.08 | 3.024 |
| 32 | Hayır | 1h | 1h | `h5_tp5__range_full__t70` | `range_full` | 0.70 | 5 | 5.00 | 2.50 | 0.406…1.496 | 1088 | 18177.75 | 18.1778 | 8.70 | 1.104 |
| 33 | Hayır | 1h | 1h | `h5_tp5__threshold__t70` | `threshold` | 0.70 | 5 | 5.00 | 2.50 | 0.406…1.496 | 1088 | 18177.75 | 18.1778 | 8.70 | 1.104 |
| 34 | Hayır | 5m | 5m_all | `h200_tp10__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 200 | 10.00 | 5.00 | 0.194…0.514 | 52 | 18157.89 | 18.1579 | 3.09 | 3.423 |
| 35 | Hayır | 15m | 15m_b | `h50_tp10__volatility__t70` | `volatility` | 0.70 | 50 | 10.00 | 5.00 | 0.305…0.822 | 149 | 17410.37 | 17.4104 | 4.82 | 1.678 |
| 36 | Hayır | 15m | 15m_b | `h50_tp10__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 50 | 10.00 | 5.00 | 0.305…0.822 | 149 | 17410.37 | 17.4104 | 4.82 | 1.678 |
| 37 | Hayır | 4h | 4h | `h10_tp10__threshold__t70` | `threshold` | 0.70 | 10 | 10.00 | 5.00 | 0.988…3.057 | 75 | 15581.85 | 15.5819 | 3.98 | 1.821 |
| 38 | Hayır | 4h | 4h | `h10_tp10__range_full__t70` | `range_full` | 0.70 | 10 | 10.00 | 5.00 | 0.988…3.057 | 75 | 15581.85 | 15.5819 | 3.98 | 1.821 |
| 39 | Hayır | 15m | 15m_a | `h50_tp5__range_full__t70` | `range_full` | 0.70 | 50 | 5.00 | 2.50 | 0.305…0.822 | 187 | 14647.98 | 14.6480 | 5.41 | 1.549 |
| 40 | Hayır | 15m | 15m_a | `h50_tp5__threshold__t70` | `threshold` | 0.70 | 50 | 5.00 | 2.50 | 0.305…0.822 | 187 | 14647.98 | 14.6480 | 5.41 | 1.549 |
| 41 | Hayır | 1h | 1h | `h15_tp5__range_full__t70` | `range_full` | 0.70 | 15 | 5.00 | 2.50 | 0.543…1.598 | 195 | 13762.58 | 13.7626 | 9.05 | 1.492 |
| 42 | Hayır | 1h | 1h | `h15_tp5__threshold__t70` | `threshold` | 0.70 | 15 | 5.00 | 2.50 | 0.543…1.598 | 195 | 13762.58 | 13.7626 | 9.05 | 1.492 |
| 43 | Hayır | 4h | 4h | `h5_tp10__volatility__t60` | `volatility` | 0.60 | 5 | 10.00 | 5.00 | 0.833…2.975 | 466 | 13563.40 | 13.5634 | 10.18 | 1.120 |
| 44 | Hayır | 4h | 4h | `h5_tp10__range_half_volatility__t60` | `range_half_volatility` | 0.60 | 5 | 10.00 | 5.00 | 0.833…2.975 | 466 | 13563.40 | 13.5634 | 10.18 | 1.120 |
| 45 | Hayır | 1h | 1h | `h15_tp5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 15 | 5.00 | 2.50 | 0.543…1.598 | 85 | 13505.52 | 13.5055 | 4.11 | 2.484 |
| 46 | Hayır | 1h | 1h | `h15_tp5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 15 | 5.00 | 2.50 | 0.543…1.598 | 85 | 13505.52 | 13.5055 | 4.11 | 2.484 |
| 47 | Hayır | 1h | 1h | `h15_tp5__volatility__t70` | `volatility` | 0.70 | 15 | 5.00 | 2.50 | 0.543…1.598 | 85 | 13505.52 | 13.5055 | 4.11 | 2.484 |
| 48 | Hayır | 5m | 5m_all | `h50_tp15__range_full__t60` | `range_full` | 0.60 | 50 | 15.00 | 7.50 | 0.173…0.509 | 638 | 13453.97 | 13.4540 | 20.70 | 1.054 |
| 49 | Evet | 4h | 4h | `h200_tp10__volatility__t70` | `volatility` | 0.70 | 200 | 10.00 | 5.00 | 1.547…3.091 | 341 | 12991.76 | 12.9918 | 22.66 | 1.117 |
| 50 | Hayır | 4h | 4h | `h200_tp10__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 200 | 10.00 | 5.00 | 1.547…3.091 | 341 | 12991.76 | 12.9918 | 22.66 | 1.117 |
| 51 | Hayır | 4h | 4h | `h200_tp10__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 200 | 10.00 | 5.00 | 1.547…3.091 | 341 | 12991.76 | 12.9918 | 22.66 | 1.117 |
| 52 | Hayır | 4h | 4h | `h10_tp10__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 10 | 10.00 | 5.00 | 0.988…3.057 | 23 | 11775.25 | 11.7753 | 2.11 | 4.817 |
| 53 | Hayır | 4h | 4h | `h10_tp10__volatility__t70` | `volatility` | 0.70 | 10 | 10.00 | 5.00 | 0.988…3.057 | 23 | 11775.25 | 11.7753 | 2.11 | 4.817 |
| 54 | Hayır | 4h | 4h | `h10_tp10__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 10 | 10.00 | 5.00 | 0.988…3.057 | 23 | 11775.25 | 11.7753 | 2.11 | 4.817 |
| 55 | Hayır | 15m | 15m_b | `h200_tp15__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 200 | 15.00 | 7.50 | 0.340…0.834 | 72 | 11709.29 | 11.7093 | 9.64 | 1.490 |
| 56 | Evet | 15m | 15m_b | `h200_tp15__volatility__t70` | `volatility` | 0.70 | 200 | 15.00 | 7.50 | 0.340…0.834 | 72 | 11709.29 | 11.7093 | 9.64 | 1.490 |
| 57 | Hayır | 1h | 1h | `h5_tp5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 5 | 5.00 | 2.50 | 0.406…1.496 | 79 | 11161.10 | 11.1611 | 3.08 | 2.895 |
| 58 | Hayır | 1h | 1h | `h5_tp5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 5 | 5.00 | 2.50 | 0.406…1.496 | 79 | 11161.10 | 11.1611 | 3.08 | 2.895 |
| 59 | Hayır | 1h | 1h | `h5_tp5__volatility__t70` | `volatility` | 0.70 | 5 | 5.00 | 2.50 | 0.406…1.496 | 79 | 11161.10 | 11.1611 | 3.08 | 2.895 |
| 60 | Hayır | 15m | 15m_b | `h200_tp20__signal_only__predTP` | `signal_only` | — | 200 | 20.00 | 10.00 | 0.340…0.834 | 1184 | 11131.22 | 11.1312 | 62.02 | 1.020 |
| 61 | Hayır | 4h | 4h | `h5_tp10__range_full_volatility__t60` | `range_full_volatility` | 0.60 | 5 | 10.00 | 5.00 | 0.833…2.975 | 412 | 10337.22 | 10.3372 | 9.79 | 1.100 |
| 62 | Hayır | 15m | 15m_b | `h200_tp15__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 200 | 15.00 | 7.50 | 0.340…0.834 | 71 | 10230.79 | 10.2308 | 8.91 | 1.428 |
| 63 | Hayır | 15m | 15m_a | `h200_tp10__volatility__t70` | `volatility` | 0.70 | 200 | 10.00 | 5.00 | 0.340…0.834 | 45 | 10093.65 | 10.0937 | 6.86 | 1.943 |
| 64 | Evet | 15m | 15m_a | `h200_tp10__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 200 | 10.00 | 5.00 | 0.340…0.834 | 45 | 10093.65 | 10.0937 | 6.86 | 1.943 |
| 65 | Hayır | 1h | 1h | `h3_tp2p5__volatility__t70` | `volatility` | 0.70 | 3 | 2.50 | 1.25 | 0.293…1.370 | 118 | 9936.05 | 9.9361 | 1.71 | 2.565 |
| 66 | Hayır | 1h | 1h | `h3_tp2p5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 3 | 2.50 | 1.25 | 0.293…1.370 | 118 | 9936.05 | 9.9361 | 1.71 | 2.565 |
| 67 | Hayır | 1h | 1h | `h3_tp2p5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 3 | 2.50 | 1.25 | 0.293…1.370 | 118 | 9936.05 | 9.9361 | 1.71 | 2.565 |
| 68 | Hayır | 1h | 1h | `h15_tp5__range_half_volatility__t60` | `range_half_volatility` | 0.60 | 15 | 5.00 | 2.50 | 0.543…1.598 | 985 | 9829.35 | 9.8294 | 15.67 | 1.063 |
| 69 | Hayır | 1h | 1h | `h15_tp5__volatility__t60` | `volatility` | 0.60 | 15 | 5.00 | 2.50 | 0.543…1.598 | 985 | 9829.35 | 9.8294 | 15.67 | 1.063 |
| 70 | Hayır | 1h | 1h | `h15_tp5__range_full_volatility__t60` | `range_full_volatility` | 0.60 | 15 | 5.00 | 2.50 | 0.543…1.598 | 985 | 9829.35 | 9.8294 | 15.67 | 1.063 |
| 71 | Hayır | 4h | 4h | `h3_tp5__volatility__t70` | `volatility` | 0.70 | 3 | 5.00 | 2.50 | 0.598…2.743 | 55 | 9623.35 | 9.6234 | 4.51 | 2.685 |
| 72 | Hayır | 4h | 4h | `h3_tp5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 3 | 5.00 | 2.50 | 0.598…2.743 | 55 | 9623.35 | 9.6234 | 4.51 | 2.685 |
| 73 | Hayır | 4h | 4h | `h3_tp5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 3 | 5.00 | 2.50 | 0.598…2.743 | 55 | 9623.35 | 9.6234 | 4.51 | 2.685 |
| 74 | Hayır | 1h | 1h | `h3_tp2p5__threshold__t70` | `threshold` | 0.70 | 3 | 2.50 | 1.25 | 0.293…1.370 | 230 | 8733.37 | 8.7334 | 2.41 | 1.516 |
| 75 | Hayır | 1h | 1h | `h3_tp2p5__range_full__t70` | `range_full` | 0.70 | 3 | 2.50 | 1.25 | 0.293…1.370 | 230 | 8733.37 | 8.7334 | 2.41 | 1.516 |
| 76 | Hayır | 15m | 15m_a | `h200_tp10__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 200 | 10.00 | 5.00 | 0.340…0.834 | 45 | 8595.15 | 8.5952 | 6.86 | 1.766 |
| 77 | Hayır | 4h | 4h | `h3_tp5__range_full__t70` | `range_full` | 0.70 | 3 | 5.00 | 2.50 | 0.598…2.743 | 85 | 8584.88 | 8.5849 | 6.61 | 1.765 |
| 78 | Hayır | 4h | 4h | `h3_tp5__threshold__t70` | `threshold` | 0.70 | 3 | 5.00 | 2.50 | 0.598…2.743 | 85 | 8584.88 | 8.5849 | 6.61 | 1.765 |
| 79 | Evet | 4h | 4h | `h200_tp10__threshold__t70` | `threshold` | 0.70 | 200 | 10.00 | 5.00 | 1.547…3.091 | 306 | 8563.19 | 8.5632 | 21.31 | 1.086 |
| 80 | Hayır | 4h | 4h | `h200_tp10__range_full__t70` | `range_full` | 0.70 | 200 | 10.00 | 5.00 | 1.547…3.091 | 306 | 8563.19 | 8.5632 | 21.31 | 1.086 |
| 81 | Hayır | 15m | 15m_b | `h200_tp2p5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 200 | 2.50 | 1.25 | 0.340…0.834 | 126 | 8344.12 | 8.3441 | 2.69 | 2.047 |
| 82 | Hayır | 15m | 15m_b | `h200_tp2p5__volatility__t70` | `volatility` | 0.70 | 200 | 2.50 | 1.25 | 0.340…0.834 | 126 | 8344.12 | 8.3441 | 2.69 | 2.047 |
| 83 | Hayır | 15m | 15m_b | `h200_tp2p5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 200 | 2.50 | 1.25 | 0.340…0.834 | 126 | 8344.12 | 8.3441 | 2.69 | 2.047 |
| 84 | Hayır | 15m | 15m_b | `h200_tp20__range_full__t50` | `range_full` | 0.50 | 200 | 20.00 | 10.00 | 0.340…0.834 | 959 | 8336.12 | 8.3361 | 66.45 | 1.015 |
| 85 | Hayır | 15m | 15m_b | `h200_tp20__range_full_volatility__t60` | `range_full_volatility` | 0.60 | 200 | 20.00 | 10.00 | 0.340…0.834 | 134 | 8292.76 | 8.2928 | 9.86 | 1.151 |
| 86 | Hayır | 15m | 15m_b | `h200_tp2p5__range_full__t70` | `range_full` | 0.70 | 200 | 2.50 | 1.25 | 0.340…0.834 | 156 | 8205.43 | 8.2054 | 2.77 | 1.782 |
| 87 | Hayır | 15m | 15m_b | `h200_tp2p5__threshold__t70` | `threshold` | 0.70 | 200 | 2.50 | 1.25 | 0.340…0.834 | 156 | 8205.43 | 8.2054 | 2.77 | 1.782 |
| 88 | Hayır | 5m | 5m_all | `h50_tp15__range_full__t50` | `range_full` | 0.50 | 50 | 15.00 | 7.50 | 0.173…0.509 | 815 | 8073.33 | 8.0733 | 28.19 | 1.025 |
| 89 | Hayır | 5m | 5m_all | `h3_tp10__range_full__t40` | `range_full` | 0.40 | 3 | 10.00 | 5.00 | 0.085…0.418 | 45 | 7608.67 | 7.6087 | 2.19 | 1.848 |
| 90 | Hayır | 5m | 5m_all | `h3_tp10__range_full__t50` | `range_full` | 0.50 | 3 | 10.00 | 5.00 | 0.085…0.418 | 45 | 7608.67 | 7.6087 | 2.19 | 1.848 |
| 91 | Hayır | 1h | 1h | `h10_tp5__volatility__t70` | `volatility` | 0.70 | 10 | 5.00 | 2.50 | 0.475…1.547 | 60 | 7570.33 | 7.5703 | 3.55 | 2.158 |
| 92 | Hayır | 1h | 1h | `h10_tp5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 10 | 5.00 | 2.50 | 0.475…1.547 | 60 | 7570.33 | 7.5703 | 3.55 | 2.158 |
| 93 | Hayır | 1h | 1h | `h10_tp5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 10 | 5.00 | 2.50 | 0.475…1.547 | 60 | 7570.33 | 7.5703 | 3.55 | 2.158 |
| 94 | Hayır | 5m | 5m_all | `h200_tp5__volatility__t60` | `volatility` | 0.60 | 200 | 5.00 | 2.50 | 0.194…0.514 | 905 | 7537.22 | 7.5372 | 18.88 | 1.053 |
| 95 | Hayır | 5m | 5m_all | `h200_tp5__range_full_volatility__t60` | `range_full_volatility` | 0.60 | 200 | 5.00 | 2.50 | 0.194…0.514 | 905 | 7537.22 | 7.5372 | 18.88 | 1.053 |
| 96 | Hayır | 5m | 5m_all | `h200_tp5__range_half_volatility__t60` | `range_half_volatility` | 0.60 | 200 | 5.00 | 2.50 | 0.194…0.514 | 905 | 7537.22 | 7.5372 | 18.88 | 1.053 |
| 97 | Hayır | 4h | 4h | `h3_tp5__range_half_volatility__t60` | `range_half_volatility` | 0.60 | 3 | 5.00 | 2.50 | 0.598…2.743 | 649 | 7389.15 | 7.3892 | 10.11 | 1.076 |
| 98 | Hayır | 4h | 4h | `h3_tp5__range_full_volatility__t60` | `range_full_volatility` | 0.60 | 3 | 5.00 | 2.50 | 0.598…2.743 | 649 | 7389.15 | 7.3892 | 10.11 | 1.076 |
| 99 | Hayır | 4h | 4h | `h3_tp5__volatility__t60` | `volatility` | 0.60 | 3 | 5.00 | 2.50 | 0.598…2.743 | 649 | 7389.15 | 7.3892 | 10.11 | 1.076 |
| 100 | Hayır | 5m | 5m_all | `h200_tp10__range_full_volatility__t60` | `range_full_volatility` | 0.60 | 200 | 10.00 | 5.00 | 0.194…0.514 | 731 | 7220.12 | 7.2201 | 19.38 | 1.041 |
| 101 | Hayır | 15m | 15m_b | `h200_tp20__range_full_volatility__t50` | `range_full_volatility` | 0.50 | 200 | 20.00 | 10.00 | 0.340…0.834 | 195 | 7122.05 | 7.1220 | 16.30 | 1.086 |
| 102 | Hayır | 15m | 15m_a | `h50_tp5__volatility__t70` | `volatility` | 0.70 | 50 | 5.00 | 2.50 | 0.305…0.822 | 43 | 7091.01 | 7.0910 | 2.68 | 2.546 |
| 103 | Hayır | 15m | 15m_a | `h50_tp5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 50 | 5.00 | 2.50 | 0.305…0.822 | 43 | 7091.01 | 7.0910 | 2.68 | 2.546 |
| 104 | Hayır | 15m | 15m_a | `h50_tp5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 50 | 5.00 | 2.50 | 0.305…0.822 | 43 | 7091.01 | 7.0910 | 2.68 | 2.546 |
| 105 | Hayır | 5m | 5m_all | `h50_tp2p5__threshold__t70` | `threshold` | 0.70 | 50 | 2.50 | 1.25 | 0.173…0.509 | 219 | 6913.52 | 6.9135 | 4.54 | 1.418 |
| 106 | Hayır | 5m | 5m_all | `h50_tp2p5__range_full__t70` | `range_full` | 0.70 | 50 | 2.50 | 1.25 | 0.173…0.509 | 219 | 6913.52 | 6.9135 | 4.54 | 1.418 |
| 107 | Hayır | 5m | 5m_all | `h200_tp15__volatility__t70` | `volatility` | 0.70 | 200 | 15.00 | 7.50 | 0.194…0.514 | 237 | 6228.22 | 6.2282 | 13.02 | 1.107 |
| 108 | Evet | 15m | 15m_b | `h200_tp5__threshold__t60` | `threshold` | 0.60 | 200 | 5.00 | 2.50 | 0.340…0.834 | 673 | 6068.95 | 6.0690 | 28.27 | 1.054 |
| 109 | Hayır | 15m | 15m_b | `h200_tp5__range_full__t60` | `range_full` | 0.60 | 200 | 5.00 | 2.50 | 0.340…0.834 | 673 | 6068.95 | 6.0690 | 28.27 | 1.054 |
| 110 | Hayır | 4h | 4h | `h50_tp5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 50 | 5.00 | 2.50 | 1.350…3.231 | 215 | 5849.37 | 5.8494 | 9.66 | 1.168 |
| 111 | Evet | 4h | 4h | `h50_tp5__volatility__t70` | `volatility` | 0.70 | 50 | 5.00 | 2.50 | 1.350…3.231 | 215 | 5849.37 | 5.8494 | 9.66 | 1.168 |
| 112 | Hayır | 4h | 4h | `h50_tp5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 50 | 5.00 | 2.50 | 1.350…3.231 | 215 | 5849.37 | 5.8494 | 9.66 | 1.168 |
| 113 | Hayır | 5m | 5m_all | `h200_tp15__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 200 | 15.00 | 7.50 | 0.194…0.514 | 221 | 5841.23 | 5.8412 | 13.91 | 1.105 |
| 114 | Hayır | 5m | 5m_all | `h10_tp10__range_full__t70` | `range_full` | 0.70 | 10 | 10.00 | 5.00 | 0.135…0.474 | 200 | 4729.68 | 4.7297 | 12.89 | 1.092 |
| 115 | Hayır | 5m | 5m_all | `h5_tp10__range_full__t50` | `range_full` | 0.50 | 5 | 10.00 | 5.00 | 0.117…0.455 | 165 | 4655.47 | 4.6555 | 5.42 | 1.115 |
| 116 | Hayır | 15m | 15m_a | `h10_tp2p5__range_full__t70` | `range_full` | 0.70 | 10 | 2.50 | 1.25 | 0.233…0.774 | 360 | 4601.46 | 4.6015 | 4.06 | 1.157 |
| 117 | Hayır | 15m | 15m_a | `h10_tp2p5__threshold__t70` | `threshold` | 0.70 | 10 | 2.50 | 1.25 | 0.233…0.774 | 360 | 4601.46 | 4.6015 | 4.06 | 1.157 |
| 118 | Hayır | 5m | 5m_all | `h15_tp15__range_full__t70` | `range_full` | 0.70 | 15 | 15.00 | 7.50 | 0.153…0.493 | 36 | 4373.60 | 4.3736 | 4.28 | 1.523 |
| 119 | Hayır | 15m | 15m_a | `h15_tp2p5__threshold__t70` | `threshold` | 0.70 | 15 | 2.50 | 1.25 | 0.263…0.804 | 150 | 4372.07 | 4.3721 | 2.41 | 1.383 |
| 120 | Hayır | 15m | 15m_a | `h15_tp2p5__range_full__t70` | `range_full` | 0.70 | 15 | 2.50 | 1.25 | 0.263…0.804 | 150 | 4372.07 | 4.3721 | 2.41 | 1.383 |
| 121 | Hayır | 5m | 5m_all | `h15_tp20__range_full__t40` | `range_full` | 0.40 | 15 | 20.00 | 10.00 | 0.153…0.493 | 22 | 4345.90 | 4.3459 | 7.65 | 1.439 |
| 122 | Hayır | 15m | 15m_b | `h50_tp10__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 50 | 10.00 | 5.00 | 0.305…0.822 | 12 | 4273.03 | 4.2730 | 1.23 | 4.139 |
| 123 | Hayır | 15m | 15m_a | `h5_tp2p5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 5 | 2.50 | 1.25 | 0.202…0.743 | 67 | 4200.63 | 4.2006 | 1.12 | 2.254 |
| 124 | Hayır | 15m | 15m_a | `h5_tp2p5__volatility__t70` | `volatility` | 0.70 | 5 | 2.50 | 1.25 | 0.202…0.743 | 67 | 4200.63 | 4.2006 | 1.12 | 2.254 |
| 125 | Hayır | 15m | 15m_a | `h5_tp2p5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 5 | 2.50 | 1.25 | 0.202…0.743 | 67 | 4200.63 | 4.2006 | 1.12 | 2.254 |
| 126 | Hayır | 5m | 5m_all | `h5_tp10__range_full__t40` | `range_full` | 0.40 | 5 | 10.00 | 5.00 | 0.117…0.455 | 166 | 4135.97 | 4.1360 | 5.91 | 1.101 |
| 127 | Hayır | 4h | 4h | `h50_tp5__range_full__t70` | `range_full` | 0.70 | 50 | 5.00 | 2.50 | 1.350…3.231 | 255 | 4063.14 | 4.0631 | 11.19 | 1.096 |
| 128 | Hayır | 4h | 4h | `h50_tp5__threshold__t70` | `threshold` | 0.70 | 50 | 5.00 | 2.50 | 1.350…3.231 | 255 | 4063.14 | 4.0631 | 11.19 | 1.096 |
| 129 | Hayır | 5m | 5m_all | `h5_tp10__range_full__t60` | `range_full` | 0.60 | 5 | 10.00 | 5.00 | 0.117…0.455 | 72 | 4049.40 | 4.0494 | 2.64 | 1.237 |
| 130 | Hayır | 5m | 5m_all | `h15_tp20__range_full__t50` | `range_full` | 0.50 | 15 | 20.00 | 10.00 | 0.153…0.493 | 21 | 3927.94 | 3.9279 | 7.65 | 1.397 |
| 131 | Hayır | 5m | 5m_all | `h200_tp5__threshold__t70` | `threshold` | 0.70 | 200 | 5.00 | 2.50 | 0.194…0.514 | 71 | 3770.88 | 3.7709 | 4.94 | 1.349 |
| 132 | Hayır | 5m | 5m_all | `h200_tp5__range_full__t70` | `range_full` | 0.70 | 200 | 5.00 | 2.50 | 0.194…0.514 | 71 | 3770.88 | 3.7709 | 4.94 | 1.349 |
| 133 | Hayır | 4h | 4h | `h5_tp10__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 5 | 10.00 | 5.00 | 0.833…2.975 | 31 | 3684.76 | 3.6848 | 5.18 | 1.525 |
| 134 | Hayır | 4h | 4h | `h5_tp10__volatility__t70` | `volatility` | 0.70 | 5 | 10.00 | 5.00 | 0.833…2.975 | 31 | 3684.76 | 3.6848 | 5.18 | 1.525 |
| 135 | Hayır | 15m | 15m_b | `h200_tp5__threshold__t70` | `threshold` | 0.70 | 200 | 5.00 | 2.50 | 0.340…0.834 | 20 | 3596.00 | 3.5960 | 2.50 | 2.666 |
| 136 | Hayır | 15m | 15m_b | `h200_tp5__range_full__t70` | `range_full` | 0.70 | 200 | 5.00 | 2.50 | 0.340…0.834 | 20 | 3596.00 | 3.5960 | 2.50 | 2.666 |
| 137 | Hayır | 5m | 5m_all | `h200_tp20__signal_only__predTP` | `signal_only` | — | 200 | 20.00 | 10.00 | 0.194…0.514 | 2044 | 3482.21 | 3.4822 | 74.89 | 1.006 |
| 138 | Hayır | 5m | 5m_all | `h50_tp15__range_full__t40` | `range_full` | 0.40 | 50 | 15.00 | 7.50 | 0.173…0.509 | 832 | 3406.28 | 3.4063 | 29.83 | 1.010 |
| 139 | Hayır | 15m | 15m_b | `h200_tp5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 200 | 5.00 | 2.50 | 0.340…0.834 | 13 | 3236.50 | 3.2365 | 1.66 | 4.000 |
| 140 | Hayır | 15m | 15m_b | `h200_tp5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 200 | 5.00 | 2.50 | 0.340…0.834 | 13 | 3236.50 | 3.2365 | 1.66 | 4.000 |
| 141 | Hayır | 15m | 15m_b | `h200_tp5__volatility__t70` | `volatility` | 0.70 | 200 | 5.00 | 2.50 | 0.340…0.834 | 13 | 3236.50 | 3.2365 | 1.66 | 4.000 |
| 142 | Hayır | 15m | 15m_b | `h10_tp5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 10 | 5.00 | 2.50 | 0.233…0.774 | 77 | 3137.38 | 3.1374 | 1.91 | 1.569 |
| 143 | Hayır | 15m | 15m_b | `h10_tp5__volatility__t70` | `volatility` | 0.70 | 10 | 5.00 | 2.50 | 0.233…0.774 | 77 | 3137.38 | 3.1374 | 1.91 | 1.569 |
| 144 | Hayır | 15m | 15m_a | `h15_tp2p5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 15 | 2.50 | 1.25 | 0.263…0.804 | 21 | 2951.62 | 2.9516 | 0.99 | 5.075 |
| 145 | Hayır | 15m | 15m_a | `h15_tp2p5__volatility__t70` | `volatility` | 0.70 | 15 | 2.50 | 1.25 | 0.263…0.804 | 21 | 2951.62 | 2.9516 | 0.99 | 5.075 |
| 146 | Hayır | 15m | 15m_a | `h15_tp2p5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 15 | 2.50 | 1.25 | 0.263…0.804 | 21 | 2951.62 | 2.9516 | 0.99 | 5.075 |
| 147 | Hayır | 5m | 5m_all | `h10_tp15__range_full__t50` | `range_full` | 0.50 | 10 | 15.00 | 7.50 | 0.135…0.474 | 50 | 2833.01 | 2.8330 | 4.58 | 1.151 |
| 148 | Hayır | 5m | 5m_all | `h200_tp20__threshold__t40` | `threshold` | 0.40 | 200 | 20.00 | 10.00 | 0.194…0.514 | 2200 | 2758.96 | 2.7590 | 70.07 | 1.004 |
| 149 | Hayır | 5m | 5m_all | `h10_tp15__range_full__t40` | `range_full` | 0.40 | 10 | 15.00 | 7.50 | 0.135…0.474 | 50 | 2728.24 | 2.7282 | 4.58 | 1.145 |
| 150 | Hayır | 5m | 5m_all | `h50_tp15__range_full__t70` | `range_full` | 0.70 | 50 | 15.00 | 7.50 | 0.173…0.509 | 352 | 2713.22 | 2.7132 | 13.67 | 1.020 |
| 151 | Hayır | 15m | 15m_b | `h50_tp10__range_full_volatility__t40` | `range_full_volatility` | 0.40 | 50 | 10.00 | 5.00 | 0.305…0.822 | 245 | 2649.61 | 2.6496 | 7.36 | 1.052 |
| 152 | Hayır | 5m | 5m_all | `h50_tp5__volatility__t70` | `volatility` | 0.70 | 50 | 5.00 | 2.50 | 0.173…0.509 | 46 | 2590.65 | 2.5906 | 2.07 | 1.585 |
| 153 | Hayır | 5m | 5m_all | `h50_tp5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 50 | 5.00 | 2.50 | 0.173…0.509 | 46 | 2590.65 | 2.5906 | 2.07 | 1.585 |
| 154 | Hayır | 1h | 1h | `h10_tp5__range_full__t70` | `range_full` | 0.70 | 10 | 5.00 | 2.50 | 0.475…1.547 | 246 | 2582.44 | 2.5824 | 7.74 | 1.065 |
| 155 | Hayır | 1h | 1h | `h10_tp5__threshold__t70` | `threshold` | 0.70 | 10 | 5.00 | 2.50 | 0.475…1.547 | 246 | 2582.44 | 2.5824 | 7.74 | 1.065 |
| 156 | Hayır | 5m | 5m_all | `h200_tp15__volatility__t60` | `volatility` | 0.60 | 200 | 15.00 | 7.50 | 0.194…0.514 | 1012 | 2515.79 | 2.5158 | 33.07 | 1.011 |
| 157 | Hayır | 5m | 5m_all | `h50_tp5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 50 | 5.00 | 2.50 | 0.173…0.509 | 41 | 2356.25 | 2.3563 | 2.32 | 1.588 |
| 158 | Hayır | 5m | 5m_all | `h10_tp15__range_full__t60` | `range_full` | 0.60 | 10 | 15.00 | 7.50 | 0.135…0.474 | 30 | 2249.10 | 2.2491 | 4.84 | 1.203 |
| 159 | Hayır | 5m | 5m_all | `h15_tp20__range_full__t60` | `range_full` | 0.60 | 15 | 20.00 | 10.00 | 0.153…0.493 | 11 | 1972.69 | 1.9727 | 4.71 | 1.387 |
| 160 | Hayır | 5m | 5m_all | `h5_tp20__threshold__t60` | `threshold` | 0.60 | 5 | 20.00 | 10.00 | 0.117…0.455 | 188 | 1800.75 | 1.8008 | 6.37 | 1.055 |
| 161 | Hayır | 5m | 5m_all | `h3_tp10__range_full__t60` | `range_full` | 0.60 | 3 | 10.00 | 5.00 | 0.085…0.418 | 26 | 1734.97 | 1.7350 | 2.07 | 1.247 |
| 162 | Hayır | 5m | 5m_all | `h50_tp2p5__volatility__t70` | `volatility` | 0.70 | 50 | 2.50 | 1.25 | 0.173…0.509 | 39 | 1642.88 | 1.6429 | 2.00 | 1.597 |
| 163 | Hayır | 5m | 5m_all | `h50_tp2p5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 50 | 2.50 | 1.25 | 0.173…0.509 | 39 | 1642.88 | 1.6429 | 2.00 | 1.597 |
| 164 | Hayır | 5m | 5m_all | `h50_tp2p5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 50 | 2.50 | 1.25 | 0.173…0.509 | 39 | 1642.88 | 1.6429 | 2.00 | 1.597 |
| 165 | Hayır | 15m | 15m_a | `h10_tp2p5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 10 | 2.50 | 1.25 | 0.233…0.774 | 28 | 1581.70 | 1.5817 | 1.37 | 1.952 |
| 166 | Hayır | 15m | 15m_a | `h10_tp2p5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 10 | 2.50 | 1.25 | 0.233…0.774 | 28 | 1581.70 | 1.5817 | 1.37 | 1.952 |
| 167 | Hayır | 15m | 15m_a | `h10_tp2p5__volatility__t70` | `volatility` | 0.70 | 10 | 2.50 | 1.25 | 0.233…0.774 | 28 | 1581.70 | 1.5817 | 1.37 | 1.952 |
| 168 | Hayır | 5m | 5m_all | `h200_tp5__volatility__t70` | `volatility` | 0.70 | 200 | 5.00 | 2.50 | 0.194…0.514 | 15 | 1578.28 | 1.5783 | 1.99 | 1.836 |
| 169 | Hayır | 5m | 5m_all | `h200_tp5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 200 | 5.00 | 2.50 | 0.194…0.514 | 15 | 1578.28 | 1.5783 | 1.99 | 1.836 |
| 170 | Hayır | 5m | 5m_all | `h200_tp5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 200 | 5.00 | 2.50 | 0.194…0.514 | 15 | 1578.28 | 1.5783 | 1.99 | 1.836 |
| 171 | Hayır | 5m | 5m_all | `h5_tp10__range_full__t70` | `range_full` | 0.70 | 5 | 10.00 | 5.00 | 0.117…0.455 | 32 | 1518.55 | 1.5186 | 2.54 | 1.194 |
| 172 | Hayır | 5m | 5m_all | `h3_tp15__range_full__t70` | `range_full` | 0.70 | 3 | 15.00 | 7.50 | 0.085…0.418 | 1 | 1478.50 | 1.4785 | 0.02 | — |
| 173 | Hayır | 5m | 5m_all | `h15_tp5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 15 | 5.00 | 2.50 | 0.153…0.493 | 139 | 1302.02 | 1.3020 | 1.94 | 1.118 |
| 174 | Hayır | 4h | 4h | `h5_tp10__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 5 | 10.00 | 5.00 | 0.833…2.975 | 30 | 1201.63 | 1.2016 | 5.30 | 1.160 |
| 175 | Hayır | 15m | 15m_b | `h200_tp20__volatility__t70` | `volatility` | 0.70 | 200 | 20.00 | 10.00 | 0.340…0.834 | 208 | 1079.34 | 1.0793 | 20.17 | 1.013 |
| 176 | Hayır | 15m | 15m_b | `h200_tp20__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 200 | 20.00 | 10.00 | 0.340…0.834 | 208 | 1079.34 | 1.0793 | 20.17 | 1.013 |
| 177 | Hayır | 15m | 15m_b | `h50_tp10__range_full_volatility__t50` | `range_full_volatility` | 0.50 | 50 | 10.00 | 5.00 | 0.305…0.822 | 199 | 1065.54 | 1.0655 | 6.68 | 1.025 |
| 178 | Hayır | 5m | 5m_all | `h15_tp5__volatility__t70` | `volatility` | 0.70 | 15 | 5.00 | 2.50 | 0.153…0.493 | 148 | 1052.07 | 1.0521 | 1.88 | 1.089 |
| 179 | Hayır | 5m | 5m_all | `h5_tp15__volatility__t50` | `volatility` | 0.50 | 5 | 15.00 | 7.50 | 0.117…0.455 | 60 | 1009.42 | 1.0094 | 1.80 | 1.284 |
| 180 | Hayır | 5m | 5m_all | `h5_tp15__range_full__t60` | `range_full` | 0.60 | 5 | 15.00 | 7.50 | 0.117…0.455 | 10 | 997.38 | 0.9974 | 3.00 | 1.259 |
| 181 | Hayır | 5m | 5m_all | `h200_tp15__range_full_volatility__t40` | `range_full_volatility` | 0.40 | 200 | 15.00 | 7.50 | 0.194…0.514 | 28 | 900.03 | 0.9000 | 6.01 | 1.108 |
| 182 | Hayır | 5m | 5m_all | `h200_tp2p5__threshold__t70` | `threshold` | 0.70 | 200 | 2.50 | 1.25 | 0.194…0.514 | 21 | 703.87 | 0.7039 | 1.21 | 1.442 |
| 183 | Hayır | 5m | 5m_all | `h200_tp2p5__range_full__t70` | `range_full` | 0.70 | 200 | 2.50 | 1.25 | 0.194…0.514 | 21 | 703.87 | 0.7039 | 1.21 | 1.442 |
| 184 | Hayır | 5m | 5m_all | `h200_tp2p5__volatility__t70` | `volatility` | 0.70 | 200 | 2.50 | 1.25 | 0.194…0.514 | 21 | 703.87 | 0.7039 | 1.21 | 1.442 |
| 185 | Hayır | 5m | 5m_all | `h200_tp2p5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 200 | 2.50 | 1.25 | 0.194…0.514 | 21 | 703.87 | 0.7039 | 1.21 | 1.442 |
| 186 | Hayır | 5m | 5m_all | `h200_tp2p5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 200 | 2.50 | 1.25 | 0.194…0.514 | 21 | 703.87 | 0.7039 | 1.21 | 1.442 |
| 187 | Hayır | 5m | 5m_all | `h10_tp20__range_full__t40` | `range_full` | 0.40 | 10 | 20.00 | 10.00 | 0.135…0.474 | 7 | 682.13 | 0.6821 | 3.16 | 1.167 |
| 188 | Hayır | 5m | 5m_all | `h10_tp20__range_full__t50` | `range_full` | 0.50 | 10 | 20.00 | 10.00 | 0.135…0.474 | 7 | 682.13 | 0.6821 | 3.16 | 1.167 |
| 189 | Hayır | 5m | 5m_all | `h10_tp20__range_full__t70` | `range_full` | 0.70 | 10 | 20.00 | 10.00 | 0.135…0.474 | 2 | 671.79 | 0.6718 | 2.26 | 1.659 |
| 190 | Hayır | 5m | 5m_all | `h200_tp15__range_full_volatility__t50` | `range_full_volatility` | 0.50 | 200 | 15.00 | 7.50 | 0.194…0.514 | 20 | 635.68 | 0.6357 | 4.73 | 1.094 |
| 191 | Hayır | 15m | 15m_b | `h200_tp20__range_full__t40` | `range_full` | 0.40 | 200 | 20.00 | 10.00 | 0.340…0.834 | 974 | 561.53 | 0.5615 | 67.38 | 1.001 |
| 192 | Hayır | 15m | 15m_b | `h15_tp5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 15 | 5.00 | 2.50 | 0.263…0.804 | 51 | 525.83 | 0.5258 | 2.48 | 1.097 |
| 193 | Hayır | 5m | 5m_all | `h10_tp2p5__volatility__t70` | `volatility` | 0.70 | 10 | 2.50 | 1.25 | 0.135…0.474 | 22 | 453.13 | 0.4531 | 0.86 | 1.418 |
| 194 | Hayır | 5m | 5m_all | `h10_tp2p5__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 10 | 2.50 | 1.25 | 0.135…0.474 | 22 | 453.13 | 0.4531 | 0.86 | 1.418 |
| 195 | Hayır | 5m | 5m_all | `h5_tp15__volatility__t40` | `volatility` | 0.40 | 5 | 15.00 | 7.50 | 0.117…0.455 | 81 | 411.56 | 0.4116 | 2.14 | 1.091 |
| 196 | Hayır | 5m | 5m_all | `h15_tp5__range_full_volatility__t40` | `range_full_volatility` | 0.40 | 15 | 5.00 | 2.50 | 0.153…0.493 | 10 | 298.77 | 0.2988 | 0.71 | 1.399 |
| 197 | Hayır | 5m | 5m_all | `h50_tp15__range_full_volatility__t40` | `range_full_volatility` | 0.40 | 50 | 15.00 | 7.50 | 0.173…0.509 | 3 | 289.34 | 0.2893 | 0.66 | — |
| 198 | Hayır | 5m | 5m_all | `h50_tp10__range_full_volatility__t40` | `range_full_volatility` | 0.40 | 50 | 10.00 | 5.00 | 0.173…0.509 | 5 | 278.84 | 0.2788 | 0.54 | 2.155 |
| 199 | Hayır | 5m | 5m_all | `h50_tp20__range_half_volatility__t50` | `range_half_volatility` | 0.50 | 50 | 20.00 | 10.00 | 0.173…0.509 | 5 | 275.63 | 0.2756 | 0.61 | 2.165 |
| 200 | Hayır | 15m | 15m_b | `h10_tp5__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 10 | 5.00 | 2.50 | 0.233…0.774 | 3 | 223.30 | 0.2233 | 0.43 | 1.872 |
| 201 | Hayır | 5m | 5m_all | `h3_tp15__volatility__t60` | `volatility` | 0.60 | 3 | 15.00 | 7.50 | 0.085…0.418 | 6 | 211.70 | 0.2117 | 0.24 | 4.169 |
| 202 | Hayır | 5m | 5m_all | `h5_tp5__range_half_volatility__t60` | `range_half_volatility` | 0.60 | 5 | 5.00 | 2.50 | 0.117…0.455 | 4 | 208.33 | 0.2083 | 0.41 | 1.751 |
| 203 | Hayır | 5m | 5m_all | `h200_tp20__range_full_volatility__t70` | `range_full_volatility` | 0.70 | 200 | 20.00 | 10.00 | 0.194…0.514 | 1 | 205.17 | 0.2052 | 0.54 | — |
| 204 | Hayır | 5m | 5m_all | `h5_tp5__range_half_volatility__t40` | `range_half_volatility` | 0.40 | 5 | 5.00 | 2.50 | 0.117…0.455 | 8 | 193.63 | 0.1936 | 0.53 | 1.459 |
| 205 | Hayır | 5m | 5m_all | `h5_tp20__volatility__t40` | `volatility` | 0.40 | 5 | 20.00 | 10.00 | 0.117…0.455 | 6 | 161.69 | 0.1617 | 0.36 | 1.684 |
| 206 | Hayır | 5m | 5m_all | `h5_tp20__volatility__t50` | `volatility` | 0.50 | 5 | 20.00 | 10.00 | 0.117…0.455 | 6 | 161.69 | 0.1617 | 0.36 | 1.684 |
| 207 | Hayır | 5m | 5m_all | `h50_tp20__range_full_volatility__t50` | `range_full_volatility` | 0.50 | 50 | 20.00 | 10.00 | 0.173…0.509 | 1 | 155.13 | 0.1551 | 0.13 | — |
| 208 | Hayır | 5m | 5m_all | `h200_tp20__range_full_volatility__t60` | `range_full_volatility` | 0.60 | 200 | 20.00 | 10.00 | 0.194…0.514 | 3 | 132.89 | 0.1329 | 1.16 | 1.130 |
| 209 | Hayır | 5m | 5m_all | `h15_tp5__range_full_volatility__t50` | `range_full_volatility` | 0.50 | 15 | 5.00 | 2.50 | 0.153…0.493 | 5 | 129.67 | 0.1297 | 0.48 | 1.308 |
| 210 | Hayır | 5m | 5m_all | `h50_tp15__range_full_volatility__t50` | `range_full_volatility` | 0.50 | 50 | 15.00 | 7.50 | 0.173…0.509 | 1 | 123.58 | 0.1236 | 0.13 | — |
| 211 | Hayır | 5m | 5m_all | `h50_tp20__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 50 | 20.00 | 10.00 | 0.173…0.509 | 2 | 117.86 | 0.1179 | 0.65 | 4.474 |
| 212 | Hayır | 5m | 5m_all | `h3_tp15__volatility__t70` | `volatility` | 0.70 | 3 | 15.00 | 7.50 | 0.085…0.418 | 2 | 112.64 | 0.1126 | 0.17 | — |
| 213 | Hayır | 5m | 5m_all | `h5_tp5__range_half_volatility__t50` | `range_half_volatility` | 0.50 | 5 | 5.00 | 2.50 | 0.117…0.455 | 6 | 111.39 | 0.1114 | 0.46 | 1.298 |
| 214 | Hayır | 5m | 5m_all | `h10_tp20__volatility__t70` | `volatility` | 0.70 | 10 | 20.00 | 10.00 | 0.135…0.474 | 22 | 88.58 | 0.0886 | 0.85 | 1.090 |
| 215 | Hayır | 5m | 5m_all | `h5_tp20__volatility__t70` | `volatility` | 0.70 | 5 | 20.00 | 10.00 | 0.117…0.455 | 1 | 84.53 | 0.0845 | 0.06 | — |
| 216 | Hayır | 5m | 5m_all | `h15_tp10__range_half_volatility__t70` | `range_half_volatility` | 0.70 | 15 | 10.00 | 5.00 | 0.153…0.493 | 1 | 57.26 | 0.0573 | 0.07 | — |
| 217 | Hayır | 5m | 5m_all | `h3_tp2p5__range_full_volatility__t50` | `range_full_volatility` | 0.50 | 3 | 2.50 | 1.25 | 0.085…0.418 | 1 | 38.65 | 0.0387 | 0.08 | — |
| 218 | Hayır | 5m | 5m_all | `h5_tp15__volatility__t70` | `volatility` | 0.70 | 5 | 15.00 | 7.50 | 0.117…0.455 | 3 | 30.93 | 0.0309 | 0.13 | 1.620 |
| 219 | Hayır | 5m | 5m_all | `h5_tp20__volatility__t60` | `volatility` | 0.60 | 5 | 20.00 | 10.00 | 0.117…0.455 | 4 | 26.34 | 0.0263 | 0.36 | 1.111 |

## 16. Güncel aktif 11 formasyonun kısa listesi

| ID | TF | Senaryo | Açıklama |
|---:|:---:|---|---|
| 5 | 4h | `h10_tp10__threshold__t60` | 10 4h mumu; TP %10, SL %5; threshold; TP skoru ≥ 0.60. |
| 9 | 15m | `h200_tp10__threshold__t70` | 200 15m mumu; TP %10, SL %5; threshold; TP skoru ≥ 0.70. |
| 10 | 1h | `h50_tp10__threshold__t60` | 50 1h mumu; TP %10, SL %5; threshold; TP skoru ≥ 0.60. |
| 15 | 1h | `h50_tp10__range_half_volatility__t60` | 50 1h mumu; TP %10, SL %5; range_half_volatility; TP skoru ≥ 0.60. |
| 19 | 4h | `h10_tp10__volatility__t60` | 10 4h mumu; TP %10, SL %5; volatility; TP skoru ≥ 0.60. |
| 49 | 4h | `h200_tp10__volatility__t70` | 200 4h mumu; TP %10, SL %5; volatility; TP skoru ≥ 0.70. |
| 56 | 15m | `h200_tp15__volatility__t70` | 200 15m mumu; TP %15, SL %7.5; volatility; TP skoru ≥ 0.70. |
| 64 | 15m | `h200_tp10__range_half_volatility__t70` | 200 15m mumu; TP %10, SL %5; range_half_volatility; TP skoru ≥ 0.70. |
| 79 | 4h | `h200_tp10__threshold__t70` | 200 4h mumu; TP %10, SL %5; threshold; TP skoru ≥ 0.70. |
| 108 | 15m | `h200_tp5__threshold__t60` | 200 15m mumu; TP %5, SL %2.5; threshold; TP skoru ≥ 0.60. |
| 111 | 4h | `h50_tp5__volatility__t70` | 50 4h mumu; TP %5, SL %2.5; volatility; TP skoru ≥ 0.70. |

## 17. Güvenli aktarım sırası

1. Proje, model paketi ve `feature_generator_portable` aynı göreli dizin yapısıyla kopyalanır.
2. `.env` dosyası ayrı ve güvenli kanaldan oluşturulur; repoya/MD’ye yazılmaz.
3. Python ortamı `paper_trade_app/requirements.txt` ile kurulur.
4. Mevcut `paper_trade.db` ve `candle_history.db` servis kapalıyken WAL/SHM dosyalarıyla tutarlı biçimde taşınır veya yeni ortamın backfill yapmasına izin verilir.
5. `python -m py_compile`, testler ve model/feature sözleşmesi doğrulanır.
6. Önce `PT_MOTORLAB_DEMO_ENABLED=false` ve paper-only çalıştırılır.
7. `/healthz`, `/api/motors`, aktif ID listesi, 106 coin coverage, timeframe `last_close`, feature missing ve event warning’leri kontrol edilir.
8. Dashboard sayımları için Spot açık/kapalı toplamının senaryo kartları toplamıyla birebir olduğu doğrulanır.
9. Servis yöneticisi (launchd/systemd) tek instance çalıştırmalıdır; instance lock korunur.
10. Yerel ağ erişimi açılacaksa host `0.0.0.0`; internet erişimi için doğrudan port yönlendirme yerine VPN/Tailscale/kimlik doğrulamalı tunnel tercih edilir.

## 18. Bilinen riskler ve yeniden doğrulanması gerekenler

- Orijinal training veri kökü paket run-config içinde eski Windows yolunu gösterir; aktarım için gerekli artefaktlar pakette mevcut olsa da yeniden eğitim yapılacaksa ham veri yolu yeniden kurulmalıdır.
- Backtest pozitifliği selection bias içerir; 219 yöntem aynı out-of-sample güven düzeyinde kabul edilmemelidir.
- Çok sayıda eşzamanlı açık pozisyon, sabit 1.000 USDT hesap varsayımında available-equity kısıtı uygulanmadan simüle edilebilir. Dashboard exposure ve available equity bu yüzden ayrıca izlenmelidir.
- Funding alınamazsa 0 yazılır ve hata saklanır; sonuçlar funding yönünden iyimser olabilir.
- Liquidation formülü borsa maintenance tier/mark price sisteminin yaklaşık simülasyonudur.
- TP/SL 1m OHLC ile çözülür; aynı 1m mum içi gerçek tick sırası bilinmediğinden SL-first varsayımı kullanılır.
- Delist/listing değişiklikleri 106 coin evrenini ve Binance erişilebilirliğini etkileyebilir.
- Dashboard yüzde metriği kullanılan toplam marjine göre hesaplanır; equity return ile aynı kavram değildir.
- Aktif senaryo seçimi `.env` ile override edilebilir; aktarımda kod default’u ile gerçek runtime env mutlaka karşılaştırılmalıdır.

## 19. Kritik dosya haritası

| Dosya | Rol |
|---|---|
| `paper_trade_app/engine.py` | Scheduler, integrity, inference, sinyal ve pozisyon yaşam döngüsü. |
| `paper_trade_app/model_registry.py` | Model/feature/symbol sözleşmesi, senaryo yükleme, h10 fallback. |
| `paper_trade_app/exact_features.py` | Portable exact generator adaptörü. |
| `feature_generator_portable/feature_engine/features.py` | Üretimde kullanılan feature hesaplayıcı. |
| `paper_trade_app/candle_history.py` | Kalıcı tam mum geçmişi. |
| `paper_trade_app/db.py` | SQLite şeması ve erişim katmanı. |
| `paper_trade_app/main.py` | FastAPI, health/API ve dashboard aggregation. |
| `paper_trade_app/execution_accounts.py` | Dört hesap/leverage/fee tanımı. |
| `paper_trade_app/config.py` | Env tabanlı çalışma ayarları. |
| `paper_trade_app/static/motors-dashboard.*` | Güncel ana dashboard. |
| `paper_trade_app/coin_universe.json` | 106 coin listesi. |
| `paper_trade_package/model_manifest.csv` | 83 model envanteri. |
| `paper_trade_package/feature_order.json` | 132 kolon sırası. |
| `BACKTEST_POSITIVE_METHODS_1M_EXECUTION.xlsx` | 219 senaryo ve 54.325 backtest işlem detayı. |

## 20. Devir kontrol listesi

- [ ] 219 scenario satırı ve 83 model dosyası mevcut.
- [ ] Feature order 132 ve model `feature_name_` ile aynı.
- [ ] Coin evreni 106 unique ve model symbol-map ile aynı.
- [ ] Aktif ID’ler beklenen 11 ID.
- [ ] Full-history açık; başlangıç tarihi ve DB yolu doğrulandı.
- [ ] BTC/ETH context tüm timeframe’lerde güncel.
- [ ] Son kapanmış mum ve gap kontrolü temiz.
- [ ] Giriş toleransı 180 saniye ve UTC saat filtresi kapalı.
- [ ] Demo/API execution kapalı.
- [ ] Her sinyalde 10 USDT marjin; exposure 10/20/50/100.
- [ ] Spot sayımı duplicate olmadan senaryo toplamlarına eşit.
- [ ] Fee, funding, liquidation ve timeout testleri geçti.
- [ ] Dashboard ve `/history` erişilebilir.
- [ ] Servis reboot sonrası otomatik başlıyor ve Mac uykuya geçmiyor.

---

Bu belge bir yatırım tavsiyesi değildir. Sistem deneysel paper-trading ve model doğrulama altyapısıdır.
