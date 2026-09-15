# WCHS-IST Transfer & Konum Radarı — Azure 7/24 Canlı Dağıtım Raporu

İstanbul Havalimanı (IST / LTFM) tekerlekli sandalye (WCHS / PRM) yolcu transfer operasyonları için geliştirilen otonom radar sistemi, Microsoft Azure Cloud üzerinde kesintisiz çalışacak şekilde başarıyla devreye alınmıştır.

---

## ☁️ Bulut Altyapısı ve Canlı Durum

- **Platform:** Microsoft Azure App Service (Linux)
- **Bölge:** `SwedenCentral` (Öğrenci politikası uyumlu)
- **App Service Plan:** `plan-wchs-bot` (B1 Basic - 7/24 Kesintisiz)
- **Web App Adı:** `app-wchs-radar`
- **Sürüm:** `2026-09-14-crawler-v3`
- **Her Zaman Açık (AlwaysOn):** `true` (Container uyumaz, bilgisayarın kapalıyken de çalışır)
- **Canlı URL:** [https://app-wchs-radar.azurewebsites.net](https://app-wchs-radar.azurewebsites.net)
- **Sağlık Durumu:** [https://app-wchs-radar.azurewebsites.net/health](https://app-wchs-radar.azurewebsites.net/health) (HTTP 200 Online)
- **Radar API:** [https://app-wchs-radar.azurewebsites.net/api/radar](https://app-wchs-radar.azurewebsites.net/api/radar)
- **GitHub Deposu:** [https://github.com/mirazbey/wchs-bot](https://github.com/mirazbey/wchs-bot) (`main` dalı senkronize)

---

## 🚀 Sürüm 3.0: Rolling Window Arka Plan Tarayıcısı & Anlık Kapı Radarı

### 1. WhatsApp Asimetrisinin Çözümü (Arka Plan Geliş Tarayıcısı)
- **Problem:** WhatsApp sadece `Uçuş Kodu ➔ Kapı` kabul ederken, Telegram'da operatör sahada `Kapı Kodu ➔ Uçuşlar` (örn: `a11`, `b5`) sorguluyordu.
- **Mimari Çözüm:**
  - **SADECE Dış Hatlar Gelişler:** İç hatlar sorgulanmaz. Gidiş uçuşlarının kapıları zaten FIDS'ten %100 doğrulukla doğrudan alınır; gidişler için tek bir WhatsApp mesajı dahi harcanmaz.
  - **Zaman Penceresi (`-25 dk` ila `+15 dk`):** Uçak teker koyduktan sonra kapıya yanaşması ~15-20 dakika sürdüğünden, inişi üzerinden en fazla 25 dakika geçmiş veya 15 dakika içinde inecek dış hatlar THY (TK) uçuşları havuza alınır.
  - **İnsan Temsilcisi & Canlı Destek Koruma Kilidi (Circuit Breaker):** Eğer iGA sistemi bir mesajı canlı desteğe aktarırsa ("müşteri temsilcisi", "operatör", "destek ekibi" vb. algılandığında), bot **anında tüm mesaj kuyruğunu boşaltır ve 15 dakika boyunca iGA WhatsApp hattına tek bir mesaj dahi göndermez.** İnsan görevliye otomatik bot mesajı spamlama riski %0'a indirilmiştir.
  - **Güvenli Tempo (75 Saniye Dinlenme):** iGA WhatsApp botunu art arda mesajlarla boğmamak ve insan desteğine aktarımı engellemek için iki uçuş sorgusu arasına **75 saniyelik** doğal dinlenme süresi eklenmiştir.
  - **Doğrudan Uçuş Kodu Gönderimi:** Cümle kalıpları yerine doğrudan `TK...` kodu gönderilerek iGA'nın otomatik yanıt motoruyla %100 uyum sağlandı.
  - **Kapı Soneki Dönüşümü (Left / Right):** Havalimanı körük yapısındaki `Left (L) ➔ B` ve `Right (R) ➔ A` eşleşmesi entegre edildi. `F8L` kapısı otomatik olarak `F8B` olarak hafızaya alınır ve Telegram'da `f8`, `f8b` veya `f8l` sorgularının tümüyle anında eşleşir.
  - **Ön Ek ve Benzer Uçuş Koruması:** iGA benzer bir uçuş önerdiğinde (örn: `TK274` yerine `TK2746`) işlem anında `not_found` ile kapatılarak oturumun kilitlenmesi önlenir.
  - **Tarih Seçimi Uyumu:** iGA'nın interaktif butonlarında sunduğu tam metin (`Bugün, 15 Eyl`) otomatik eşleştirilerek tarih seçimi hatasız onaylanır.
  - **30 Dakika Önbellek (Cache):** Doğrulanan kapı numarası 1800 saniye (30 dk) boyunca hafızada saklanır.

### 2. Kapı Sorgularında Anlık Yanıt (0.01 Saniye)
- Operatör Telegram'a `a11`, `b5` veya `ben f3deyim` yazdığında bot **ASLA beklemez** veya kullanıcıyı 60 saniye boyunca "WhatsApp 1/2 sorgulanıyor..." diye oyalamaz.
- FIDS ve önbellekten beslenerek **0.01 saniyede doğrudan nihai sonucu** gönderir.

### 3. Temel Kapı & Alt Kapı Gösterim Düzeltmesi
- **Önceki Hata:** `a11` yazıldığında A11A ve A11B için "Doğrulanmış uçuş bilgisi yok" yazıp gerçek `TK203` uçuşunu en alta dipnot olarak gömüyordu.
- **Yeni Tasarım:** Kaynakta kapı `A11` olarak görünüyorsa doğrudan `🚪 A11` başlığı altında `TK203 ➔ SEATTLE` olarak gösterilir.
- Eğer fiziki olarak `B5A` ve `B5B` gibi ayrışan uçuşlar varsa iki alt kapı da net ve ayrı bölümler halinde listelenir.

---

## 🎯 Bot Yetenekleri ve Minimalist Kullanım Rehberi

Telegram botun: **[@wchs_bot](https://t.me/wchs_bot)** (Transfer Yolcu Bilgi)

Sahadaki operasyonel hızı maksimize etmek için tüm kalabalık butonlar, iskele seçiciler ve genel iniş listeleri kaldırıldı. Klavye tek satırlık 3 kompakt butona indirildi:

```
[ 📍 Kapım (F3) ]   [ 📊 Durum ]   [ ❓ Yardım ]
```

### 1. 🚪 Doğrudan Kapı Sorgusu
Herhangi bir menüye tıklamadan doğrudan kapıyı yazabilirsin:
- **Örnekler:** `a11`, `b5`, `f3`, `b5a`
- **Konum Güncelleme:** `ben f4deyim`, `f3teyim` (kapını F4 olarak kaydeder ve kapıdaki uçuşları anında döker)

### 2. ✈️ Doğrudan Uçuş Sorgusu (Uçuş Kartı)
- **Örnekler:** `TK0630`, `TK203`
- Uçuşun doğrulanmış kapısını, iniş/kalkış saatini ve WCHS yolcusunun aktarılacağı bağlantılı kalkış kapısını gösterir.

### 3. ✏️ Manuel Kapı Kaydı
- **Örnek:** `TK1234 B5` (Telsizden veya anonsla duyulan kapıyı anında hafızaya kaydeder).

### 4. 📊 Canlı Durum ve Hafıza Takibi
- **Komut:** `/durum` veya **📊 Durum** butonu
- 30 dakikalık hafızada tutulan kapıları, kalan dakikalarını ve arka plan WhatsApp tarayıcısının durumunu raporlar.

---

## 🏃 Kapılar ve İskeleler Arası Yürüyüş Matrisi

| Güzergah | Ortalama İntikal Süresi | Güvenlik / Risk Rozeti |
|---|---|---|
| **Aynı Kapı** (Örn: F3 ➔ F3) | 0 dk | 🎯 SENİN KAPIN |
| **Bitişik Kapılar** (±2 kapı, Örn: F3 ➔ F1B/F4) | ~1 dk | 🟢 ÇOK YAKIN |
| **Aynı İskele** (±5 kapı, Örn: F3 ➔ F8A) | ~3 dk | 🟡 AYNI İSKELE |
| **Aynı İskele Sonu** (>5 kapı, Örn: F3 ➔ F12B) | ~6 dk | 🟡 AYNI İSKELE |
| **Komşu İskeleler** (E ➔ F veya A ➔ B) | ~7 dk | 🟡 KOMŞU İSKELE |
| **Merkez Bölgeye Geçiş** (F/E/A/B ➔ D) | ~10-12 dk | 🟡 MERKEZ BÖLGE |
| **Uzak Bloklar** (A/B ➔ E/F) | ~22 dk | 🔴 UZAK BLOK |
| **İç Hatlar Geçişi** (A/B/D/E/F ➔ G) | ~20 dk | 🔵 İÇ HATLAR |

> [!NOTE]
> **Emniyet Payı Formülü:**
> `Emniyet Payı = Kalkışa Kalan Gerçek Dakika - Yürüme Süresi - 20 dk Boarding Kapanış Payı`
> Emniyet payı **15 dk altındaysa** sistem otomatik olarak **🚨 ÇOK ACİL** alarmı üretir.

