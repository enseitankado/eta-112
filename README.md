# ETA-112 — Parola Aracı

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Pardus%20ETAP%20%C2%B7%20Debian-informational)
![Python](https://img.shields.io/badge/python-3-blue)



- **Kullanıcı hesapları** — Kurulu işletim sisteminin kullanıcı hesaplarının (örn. `etapadmin`) **
  parolasını değiştirir.**
- **BIOS parolası** — BIOS **yönetici/kullanıcı parolasını görüntüler, ayarlar veya parolasını kaldırır**
  (yalnızca desteklenen akıllı tahta modellerinde).
- **MAC adresi** — Onboard ethernet MAC'ini **görüntüler**, izinli **OUI'ye göre doğrular** ve
  (desteklenen modellerde) Realtek NIC'in eFuse'una **kalıcı ve işletim sisteminden bağımsız** olarak yazar.
- **Windows ürün anahtarı** — BIOS firmware'indeki OEM Windows ürün anahtarını (ACPI **MSDM**)
  **görüntüler** ve (desteklenen modellerde) **değiştirir**.
- **Dokunmatik sürücü** — Akıllı tahtanın dokunmatik sürücüsünü (`eta-touchdrv`) **sürüm sürüm
  dener**, kalibrasyon sorununu düzelten sürümü bulur ve **kalıcı hale getirir**.

Hem **canlı (USB) ortamdan** hem de **çalışan sistemden** kullanılabilir.

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

Başlık satırı her zaman **bulunduğunuz tam patikayı** gösterir, böylece üç kademe
aşağıda da nerede olduğunuz belli olur:

```
ETA-112 > BIOS EEPROM > MAC adresi
ETA-112 > Kullanıcı hesapları > Hesap seç
```

**Kullanıcı hesapları** akışının içindeki seçimler de açılır menüdür: birden çok kurulum
bulunursa hedef disk, ardından sıfırlanacak hesap ok tuşlarıyla seçilir. Hesap
listesi ekrana sığmazsa kaydırılır (`↑ n öğe daha` / `↓ n öğe daha`); listedeki
son iki satır **tüm hesapları göster / yalnız girişli hesaplar** geçişi ve
**İptal**'dir.

| Tuş | İşlev |
|---|---|
| `↑` `↓` | gez (başta/sonda sarar) |
| `Enter` | seç |
| `1`–`9` | doğrudan seç |
| `Esc` · `q` · `0` | alt menüde: geri · ana menüde: imleci **Çıkış**'a taşır |
| `Home` `End` | ilk / son öğe |

**Menü sizi dışarı atmaz.** Bir işlem hata verse, iptal edilse veya desteklenmeyen
donanımda çalışsa bile menüye geri dönülür; programdan yalnızca **Çıkış** seçilerek
çıkılır. Ana menüde `Esc` ve `Ctrl-C` imleci Çıkış'a taşır ama tek başına çıkmaz —
ayrıca `Enter` gerekir.

Açılır menü yalnızca gerçek bir terminalde devreye girer. Çıktı bir dosyaya/boruya
yönlendirilmişse veya `TERM` tanımsızsa **eski numaralı menü** kullanılır; bu kipte
menü eskisi gibi tek seferliktir. Açılır menüyü elle kapatmak için:

```bash
ETA112_BASIT_MENU=1 sudo eta-112.py
```

> Komut satırı arayüzü (`bios`, `kullanici`, `mac`, `wkey`, `dokunmatik`, `--json`)
> bu değişiklikten **etkilenmez**. Menü yalnızca araç hiç argümansız çağrıldığında
> gösterilir; `tiha` gibi eta-112'yi parametrik kullanan programlar aynı sözleşmeyle
> çalışmaya devam eder.

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

Ardından hesap seçilir. Liste varsayılan olarak **giriş yapabilen** hesapları gösterir;
sondan bir önceki satır sistem hesaplarını da listeye katar:

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

Hesap listesi ekrana sığmazsa kaydırılır (`↑ n öğe daha` / `↓ n öğe daha`).
Seçimden sonra yeni parola iki kez girilir; parola uygulanır ve doğru ayarlandığı
**kriptografik olarak teyit edilir**.

Sıfırlama bittikten sonra, hedef disk serbest bırakılır; bilgisayarı normal başlatıp **yeni
parolayla** giriş yapabilirsiniz.

---

### BIOS EEPROM

Firmware ve donanım seviyesindeki üç işlev — hiçbiri işletim sistemine bağlı değil,
hepsi disk silinse/değiştirilse de kalıcı:

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

Onboard ethernet MAC adresini gösterir ve (desteklenen modellerde) **kalıcı olarak değiştirir**.
Değişiklik **donanım seviyesindedir, işletim sisteminden bağımsızdır** — sonradan farklı bir
işletim sistemi (ör. Windows) kurulsa bile yeni MAC geçerli olur.

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

**MAC değiştir** seçildiğinde mevcut MAC, daha önce yazılan MAC'ler ve eFuse'ta kalan
yazma hakkı gösterilir; onaylanınca yazılır ve geri-okunarak doğrulanır.

- Yeni MAC, modele **ait izinli aralıkta** olmalıdır; aksi halde kabul edilmez.
- MAC yalnızca **sınırlı sayıda** değiştirilebilir ve değişiklik **geri alınamaz**; araç işlem
  öncesinde kaç hakkın kaldığını gösterir.

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

BIOS firmware'inde gömülü **OEM Windows ürün anahtarını** (ACPI MSDM tablosu) gösterir ve
(desteklenen modellerde) değiştirir. Windows kurulduğunda anahtarı buradan okuyup otomatik
etkinleşir; değişiklik **firmware seviyesindedir**, işletim sisteminden bağımsızdır.

- Anahtar biçimi: `XXXXX-XXXXX-XXXXX-XXXXX-XXXXX`.
- Değiştirme, flash'taki MSDM tablosunu (ACPI sağlama toplamı dahil) günceller; **etkili olması
  için yeniden başlatma** gerekir ve yazma **geri-okunarak doğrulanır**.
- Yalnızca **MSDM içeren cihazlarda** (Windows 8/10/11 dönemi) çalışır. Windows 7 dönemi
  cihazlarda **SLIC** bulunur; bu okunabilir bir anahtar içermez.

**Desteklenen donanımlar (2026):**

<!-- DESTEKLENEN-DONANIM:START (otomatik üretilir — tablo biçimi; elle düzenlemeyin) -->
| Faz / Model | Anakart | İşlemci | BIOS | Adet | Oran |
|---|---|---|---|--:|--:|
| **Faz 1 Vestel Intel (Siyah)** | VESTEL 14MB24A | Intel Core i3-2310M | AMI Aptio 4.6.5 | 60.180 | %11,19 |
| **Faz 2 Vestel AMD (Gri)** | VESTEL 14MB37C1 | AMD A10-5750M | AMI Aptio L0.30 | 53.733 | %9,99 |
| **Faz 2 Vestel Intel (Gri)** | VESTEL 14MB57 | Intel Core i3-4000M | AMI Aptio 4.6.5 | 205.399 | %38,18 |
<!-- DESTEKLENEN-DONANIM:END -->

---

### Dokunmatik sürücü

Akıllı tahtanın dokunmatik katmanını (`eta-touchdrv`) yönetir: **hangi sürümün kurulu olduğunu
gösterir, arşivdeki sürümleri sırayla deneyip "düzeldi mi?" diye sorar** ve onay verilen sürümü
kalıcı hale getirir. Kalibrasyon kayması / yanlış dokunma noktası gibi sorunların hangi sürücü
sürümünden geldiğini bulmak içindir.

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

Aynı işlevler komut satırından da çağrılabilir:

```bash
sudo eta-112.py dokunmatik durum       # panel, kurulu sürüm, servis, girdi aygıtı
sudo eta-112.py dokunmatik liste       # arşivdeki sürümler ve deneme sırası
sudo eta-112.py dokunmatik dene        # sırayla dene (hızlı kademe)
sudo eta-112.py dokunmatik geri        # başlangıç durumuna dön
```

Deneme **iki kademelidir**:

| | Ne değişir | Süre | Ne zaman |
|---|---|---|---|
| **Kademe 1** (varsayılan) | yalnız kullanıcı uzayı sunucu ikilisi | saniyeler | kalibrasyon sorunları — koordinat polinomunu uygulayan katman budur |
| **Kademe 2** (`--kademe 2`) | tam `.deb`: kernel modülü + sunucu + servis + udev | dakikalar (DKMS derler) | dokunmatik hiç çalışmıyorsa, servis açılmıyorsa |

Kernel modülü durumsuzdur — ham USB paketlerini taşır, koordinat yorumlamaz — bu yüzden
kalibrasyon için Kademe 1 hem doğru hem hızlı olandır.

- Sürücü paketleri çalışma anında GitHub'dan indirilir ve **sha256 ile doğrulanır**;
  `baslat.sh` içine gömülmez. İnternetsiz ortamda `--yerel <depo>/dokunmatik` kullanın.
- Deneme başlamadan **başlangıç durumu yedeklenir**; onay verilmeden çıkılırsa otomatik
  geri yüklenir.
- Onaylanan sürüm `apt-mark hold` + apt pin ile sabitlenir — yoksa `eta-unattended-upgrade`
  bir sonraki güncellemede geri alır. Sabitlemeyi kaldırmak için `dokunmatik serbest`.
- Aynı sunucu ve kernel modülünü taşıyan sürümler tek adımda toplanır; 13 resmi sürüm
  **9 ayırt edici denemeye** iner. Ayrıntı: [`dokunmatik/README.md`](dokunmatik/README.md).

#### Kalibrasyonu cihazdan okuma

Sürüm denemesi sonuç vermezse sorun sürücüde değil, **panelin EEPROM'undaki kalibrasyon
verisinde** olabilir. Araç bu veriyi doğrudan okuyup karşılaştırabilir:

```bash
sudo eta-112.py dokunmatik kalibrasyon oku --cikti saglam.json    # sağlam tahtada
sudo eta-112.py dokunmatik kalibrasyon oku --cikti sorunlu.json   # sorunlu tahtada
eta-112.py dokunmatik kalibrasyon karsilastir saglam.json sorunlu.json
```

Karşılaştırma blokları hem bayt hem `float32` olarak gösterir; kalibrasyon polinomunun
katsayıları doğrudan görülür. Sağlam bir tahtayla tek farkın `c1` katsayısının işareti
olması, panelin eksenlerinin takas olduğu anlamına gelir.

- Okuma protokolü kesindir: taşıma katmanı GPL kernel modülü kaynağından, paket biçimi
  `OpticalService` ikilisinden sökülerek çıkarıldı. Ayrıntı ve kesinlik dökümü:
  [`dokunmatik/belgeler/kalibrasyon-protokolu.md`](dokunmatik/belgeler/kalibrasyon-protokolu.md).
- **Yazma komutu doğrulanmadı.** `kalibrasyon yaz` aynı komut/indeks çiftlerinin geri
  yazılabileceğini varsayar; `--onayliyorum` bayrağı, otomatik yedek ve ayrı bir teyit
  ister. Yalnızca **aynı panelden alınmış** bir yedeği geri yüklemek için kullanın.
- Optical (`6615:*`) panellerde doğrulanmıştır. OTD (`2621:*`) panellerde komut
  kimlikleri bilinmiyor; okuma yalnızca `--dene` ile denenebilir, yazma kapalıdır.

---

## Notlar

- Aracın çalıştırılabilmesi için **sudo yetkisi** (`etapadmin`) gerekir.
- `eta-112.py` değiştirildiğinde `./gom.sh` çalıştırılmalıdır — `baslat.sh` Python kaynağını
  gömülü taşır. `./gom.sh --kontrol` iki kopyanın aynı olduğunu doğrular.

---

## Geliştirici ve lisans

- Geliştirici: **Özgür Koca** — [ozgurkoca.com](https://ozgurkoca.com)
- Lisans: **GPL-3.0-or-later** — özgür yazılım.

---

