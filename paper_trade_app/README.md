# Model Arena Paper Trade

Binance USD-M mumlarını canlı izleyen, arşivdeki 83 LightGBM modelini 106 coin üzerinde çalıştıran ve 219 senaryoyu birbirinden bağımsız sanal portföyler olarak takip eden yerel servis.

## Çalıştırma

```powershell
cd D:\Paper_ML_BSH\paper_trade_app
C:\Users\GIGABAYT\AppData\Local\Programs\Python\Python311\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8765
```

Arayüz: `http://127.0.0.1:8765`

Kimlik bilgileri `.env` içindedir. Gerçek emir gönderilmez; API anahtarı kullanılmaz.

## Tasarım notları

- Her senaryo 100.000 TL ayrı başlangıç bakiyesi ve 10.000 TL işlem büyüklüğüyle izlenir.
- Giriş bir sonraki mum açılışında; TP/SL ve zaman aşımı 1 dakikalık kapalı mumlarla uygulanır.
- 4 saat senaryolarında paketle uyumlu giriş/çıkış kayması ve taker komisyonu uygulanır.
- 29 adet h10 senaryosu gerekli regresyon modeli pakette olmadığı için güvenli biçimde blokludur.
- 132 feature `feature_generator_portable` içindeki taşınabilir üreticiyle ve `portable-exact-v1` sözleşmesiyle hesaplanır. Kolon sırası ve 83 modelin tamamıyla inference uyumu otomatik test edilmiştir.
- 4 saatlik pozisyonlarda funding şimdilik sıfırdır; panelde sonuçlar bu sınırlamayla okunmalıdır.
