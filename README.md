# IGA Istanbul Airport (IST) Flight & Gate Tracker

İGA İstanbul Havalimanı (IST / LTFM) gerçek zamanlı uçuş ve kapı (gate) bilgi sorgulama kütüphanesi ve CLI aracı.

---

## Özellikler

- **Kapı (Gate) Bilgisi**: İlgili uçuşun atanan kapı kodunu (`A1G`, `G5A`, `E4`, vb.) anlık çeker.
- **Check-in Kontuarı**: Uçuşa ait kontuar (`D-E`, `E-F`) bilgisini getirir.
- **Uçuş Durumu**: `Kapı Kapandı`, `Son Çağrı`, `Uçağa Gidiniz`, `Kapıya Gidiniz` gibi güncel durumları bildirir.
- **İç & Dış Hatlar**: Hem iç hatlar hem dış hatlar uçuşlarını tarar.
- **Ortak Uçuş (Codeshare) Desteği**: Kod paylaşımlı uçuş numaralarını otomatik eşleştirir.
- **Hazır CLI & HTTP REST API**: Komut satırından ya da yerel HTTP servisi üzerinden doğrudan kullanılabilir.

---

## Kurulum

Python 3.8+ yeterlidir (Harici bağımlılık gerektirmez, Python standart kütüphanesi kullanır).

---

## Kullanım

### 1. Python Kodu ile Kullanım

```python
from iga_client import IGAClient

client = IGAClient()

# Belirli bir uçuşun kapı bilgisini sorgula
flight = client.get_flight_gate("TK2170")

if flight:
    print(f"Uçuş: {flight.flight_number}")
    print(f"Havayolu: {flight.airline_name}")
    print(f"Hedef: {flight.to_city}")
    print(f"Kapı (Gate): {flight.gate}")
    print(f"Kontuar: {flight.counter}")
    print(f"Durum: {flight.status}")
else:
    print("Uçuş bulunamadı.")
```

### 2. Komut Satırı (CLI) Kullanımı

```bash
# Uçuş detaylarını ve kapısını görüntüle
python cli.py TK2170

# Sadece kapı numarasını çıktı al (script entegrasyonu için)
python cli.py --gate-only TK2170
# Çıktı: G5A

# Şehre veya havayoluna göre uçuş ara
python cli.py --search PARIS

# JSON formatında çıktı al
python cli.py TK2170 --json

# Canlı kalkış tablosunu listele
python cli.py
```

### 3. Yerel HTTP REST API Sunucusu

```bash
python server.py
```

Endpoint'ler:
- `GET http://localhost:8080/api/gate/TK2170`
- `GET http://localhost:8080/api/search?q=ANKARA`
- `GET http://localhost:8080/api/departures?limit=30`
