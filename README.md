# ETA-112

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Pardus%20ETAP%20%C2%B7%20Debian-informational)
![Python](https://img.shields.io/badge/python-3-blue)

Pardus ETAP akıllı tahtalar için bakım aracı. Canlı (USB) ortamdan da, çalışan
sistemden de kullanılabilir.

- **Kullanıcı hesapları** — kurulu sistemin hesap parolalarını sıfırlar.
- **BIOS parolası** — yönetici/kullanıcı parolasını okur, ayarlar, temizler.
- **MAC adresi** — onboard MAC'i okur, doğrular ve Realtek eFuse'una kalıcı yazar.
- **Windows ürün anahtarı** — BIOS'taki OEM anahtarı (ACPI MSDM) okur ve değiştirir.
- **Dokunmatik sürücü** — `eta-touchdrv` sürümlerini sırayla deneyip kalibrasyon
  sorununu düzelteni bulur; panelin EEPROM kalibrasyonunu okur ve karşılaştırır.

BIOS parolası, MAC adresi ve Windows ürün anahtarı firmware/donanım seviyesindedir —
disk silinse de kalıcıdır ve yalnızca **desteklenen modellerde** çalışır.

---

## Çalıştırma

Kurulum gerektirmez. Aşağıdaki komutu kopyalayarak bir terminale yapıştırın.

```bash
curl -fsSL https://raw.githubusercontent.com/enseitankado/eta-112/main/baslat.sh | sudo bash
```

Açılan **renkli açılır menüde** ok tuşlarıyla gezinip **Enter** ile seçim yapılır:

```
  ETA-112
────────────────────────────────────────────────────────────
  ▸ 1 Kullanıcı hesapları
    2 BIOS EEPROM
    3 Dokunmatik sürücü
      Çıkış
────────────────────────────────────────────────────────────
  ↑/↓ gez · Enter seç · 1-9 doğrudan · Esc → Çıkış
```

---

### Kullanıcı hesapları

Kurulu işletim sistemi otomatik bulunur. Diskte birden çok kurulum varsa önce hedef
seçilir:

```
  ETA-112 > Kullanıcı hesapları > Hedef kurulum
────────────────────────────────────────────────────────────
  ▸ 1 Pardus ETAP 23  (/dev/sda2, 120G)  ← çalışan sistem
    2 Debian 12  (/dev/sdb1, 500G)
    3 Pardus 21  (/dev/nvme0n1p3, 80G)
      İptal
────────────────────────────────────────────────────────────
  ↑/↓ gez · Enter seç · 1-9 doğrudan · Esc geri
```

Ardından hesap seçilir. Liste varsayılan olarak giriş yapabilen hesapları gösterir;
sondan bir önceki satır sistem hesaplarını da katar:

```
  ETA-112 > Kullanıcı hesapları > Hesap seç
────────────────────────────────────────────────────────────
  ▸ 1 root             UID 0      root
    2 etapadmin        UID 1000   ETAP Yonetici
    3 ogretmen         UID 1001   Ogretmen
    4 ogrenci          UID 1002   Ogrenci
    5 misafir          UID 1003   Misafir Hesabi
    6 Tüm hesapları göster (sistem hesapları dahil)
      İptal
────────────────────────────────────────────────────────────
  ↑/↓ gez · Enter seç · 1-9 doğrudan · Esc geri
```

Yeni parola iki kez girilir, uygulanır ve doğru ayarlandığı **kriptografik olarak
doğrulanır**. Bitince hedef disk serbest bırakılır.

---

### BIOS EEPROM

Firmware ve donanım seviyesindeki üç işlev:

```
  ETA-112 > BIOS EEPROM
────────────────────────────────────────────────────────────
  ▸ 1 BIOS parolası
    2 MAC adresi
    3 Windows ürün anahtarı
      Geri
────────────────────────────────────────────────────────────
  ↑/↓ gez · Enter seç · 1-9 doğrudan · Esc geri
```

**Desteklenen donanımlar (2026):**

<!-- DESTEKLENEN-DONANIM:START (otomatik üretilir — tablo biçimi; elle düzenlemeyin) -->
| Faz / Model | Anakart | İşlemci | BIOS | Adet | Oran |
|---|---|---|---|--:|--:|
| **Faz 1 Vestel Intel (Siyah)** | VESTEL 14MB24A | Intel Core i3-2310M | AMI Aptio 4.6.5 | 60.180 | %11,19 |
| **Faz 2 Vestel AMD (Gri)** | VESTEL 14MB37C1 | AMD A10-5750M | AMI Aptio L0.30 | 53.733 | %9,99 |
| **Faz 2 Vestel Intel (Gri)** | VESTEL 14MB57 | Intel Core i3-4000M | AMI Aptio 4.6.5 | 205.399 | %38,18 |
<!-- DESTEKLENEN-DONANIM:END -->

---

### BIOS EEPROM → BIOS parolası

```
  ETA-112 > BIOS EEPROM > BIOS parolası
────────────────────────────────────────────────────────────
  ▸ 1 Parolaları oku
    2 Parola ayarla
    3 Parola temizle
    4 Model / destek bilgisi
      Geri
────────────────────────────────────────────────────────────
  ↑/↓ gez · Enter seç · 1-9 doğrudan · Esc geri
```

- Değişiklikten önce onay sorulur. **BIOS parolası değişikliğinin etkili olması için bilgisayarı
  yeniden başlatın.**
- BIOS özelliği yalnızca **desteklenen modellerde** çalışır; desteklenmiyorsa işlem yapılmaz
  ("DESTEKLENMİYOR" mesajı).

---

### BIOS EEPROM → MAC adresi

```
  ETA-112 > BIOS EEPROM > MAC adresi
────────────────────────────────────────────────────────────
  ▸ 1 MAC oku
    2 MAC değiştir
    3 Bir MAC'i doğrula
      Geri
────────────────────────────────────────────────────────────
  ↑/↓ gez · Enter seç · 1-9 doğrudan · Esc geri
```

Değişiklik Realtek NIC'in eFuse'una yazılır: işletim sisteminden bağımsızdır, disk
değişse de kalır. **MAC değiştir** seçilince mevcut MAC, daha önce yazılanlar ve kalan
yazma hakkı gösterilir; onaylanınca yazılıp geri-okunarak doğrulanır.

- Yeni MAC modele ait izinli aralıkta olmalı.
- ⚠ eFuse yazması **sınırlı sayıdadır ve geri alınamaz**.

---

### BIOS EEPROM → Windows ürün anahtarı

```
  ETA-112 > BIOS EEPROM > Windows ürün anahtarı
────────────────────────────────────────────────────────────
  ▸ 1 Anahtarı oku
    2 Anahtarı değiştir
      Geri
────────────────────────────────────────────────────────────
  ↑/↓ gez · Enter seç · 1-9 doğrudan · Esc geri
```

BIOS'a gömülü OEM anahtarı (ACPI MSDM) okur; Windows kurulunca bu anahtarla
kendiliğinden etkinleşir. Yazma, MSDM tablosunu sağlama toplamıyla birlikte günceller
ve geri-okunarak doğrulanır; **etkili olması için yeniden başlatma gerekir**.

- Biçim: `XXXXX-XXXXX-XXXXX-XXXXX-XXXXX`
- Yalnızca MSDM içeren cihazlarda (Windows 8/10/11 dönemi). Windows 7 dönemi
  cihazlarda SLIC vardır; okunabilir anahtar içermez.

---

### Dokunmatik sürücü

`eta-touchdrv` sürümlerini sırayla deneyip "düzeldi mi?" diye sorar; onayladığınız
sürümü kalıcı hale getirir. Kalibrasyon kayması / yanlış dokunma noktası gibi
sorunların hangi sürücü sürümünden geldiğini bulmak içindir.

```
  ETA-112 > Dokunmatik sürücü
────────────────────────────────────────────────────────────
  ▸ 1 Durum
    2 Sürümleri listele
    3 Sürüm dene — hızlı
    4 Sürüm dene — tam
    5 Kalibrasyonu oku
    6 Kalibrasyon karşılaştır
    7 Başlangıç durumuna dön
    8 Sabitlemeyi kaldır
      Geri
────────────────────────────────────────────────────────────
  ↑/↓ gez · Enter seç · 1-9 doğrudan · Esc geri
```

Deneme iki kademeli. Kalibrasyon polinomunu kullanıcı uzayındaki sunucu uyguladığı
için kalibrasyon sorunlarında **hızlı** kademe hem doğru hem yeterlidir:

| | Ne değişir | Süre |
|---|---|---|
| **Hızlı** | yalnız sunucu ikilisi | saniyeler |
| **Tam** | kernel modülü + sunucu + servis + udev | dakikalar (DKMS derler) |

- Paketler çalışma anında GitHub'dan indirilip **sha256 ile doğrulanır**. İnternetsiz
  ortamda `--yerel <depo>/dokunmatik` kullanın.
- Başlangıç durumu yedeklenir; onay vermeden çıkarsanız otomatik geri yüklenir.
- Onaylanan sürüm `apt-mark hold` + apt pin ile sabitlenir (yoksa otomatik güncelleme
  geri alır). Kaldırmak için `dokunmatik serbest`.

Arşiv, sürüm matrisi ve deneme sırasının nasıl çıkarıldığı:
[`dokunmatik/README.md`](dokunmatik/README.md)

#### Kalibrasyonu cihazdan okuma

Sürüm denemesi sonuç vermezse sorun sürücüde değil, **panelin EEPROM'undaki
kalibrasyon verisinde** olabilir:

```bash
sudo eta-112.py dokunmatik kalibrasyon oku --cikti saglam.json    # sağlam tahtada
sudo eta-112.py dokunmatik kalibrasyon oku --cikti sorunlu.json   # sorunlu tahtada
     eta-112.py dokunmatik kalibrasyon karsilastir saglam.json sorunlu.json
```

Karşılaştırma blokları bayt ve `float32` olarak gösterir; kalibrasyon polinomunun
katsayıları doğrudan görülür. Tek fark `c1` katsayısının işaretiyse panelin eksenleri
takas olmuş demektir.

⚠ Okuma protokolü doğrulanmıştır, **yazma komutu doğrulanmamıştır**. `kalibrasyon yaz`
yalnızca aynı panelden alınmış bir yedeği geri yüklemek içindir; `--onayliyorum`,
otomatik yedek ve ayrı teyit ister, OTD (`2621:*`) panellerde kapalıdır. Kesinlik
dökümü: [`dokunmatik/belgeler/kalibrasyon-protokolu.md`](dokunmatik/belgeler/kalibrasyon-protokolu.md)

---

## Notlar

- Araç **root** ister (`sudo`).
- Menüsüz de kullanılabilir: `eta-112.py <kullanici|bios|mac|wkey|dokunmatik> --help`
- Geliştirirken: `eta-112.py` değiştirilince `./gom.sh` çalıştırılmalı — `baslat.sh`
  Python kaynağını gömülü taşır.

---

## Geliştirici ve lisans

- Geliştirici: **Özgür Koca** — [ozgurkoca.com](https://ozgurkoca.com)
- Lisans: **GPL-3.0-or-later** — özgür yazılım.
