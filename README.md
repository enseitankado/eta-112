# ETA-112 — Parola Aracı

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Pardus%20ETAP%20%C2%B7%20Debian-informational)
![Python](https://img.shields.io/badge/python-3-blue)



- **Kullanıcı parolası** — Kurulu işletim sisteminin kullanıcı hesaplarının (örn. `etapadmin`) **
  parolasını değiştirir.**
- **BIOS parolası** — BIOS **yönetici/kullanıcı parolasını görüntüler, ayarlar veya parolasını kaldırır**
  (yalnızca desteklenen akıllı tahta modellerinde).
- **MAC adresi** — Onboard ethernet MAC'ini **görüntüler**, izinli **OUI'ye göre doğrular** ve
  (desteklenen modellerde) Realtek NIC'in eFuse'una **kalıcı ve işletim sisteminden bağımsız** olarak yazar.

Hem **canlı (USB) ortamdan** hem de **çalışan sistemden** kullanılabilir.

---

## Çalıştırma

Kurulum gerektirmez. Aşağıdaki komutu kopyalayarak bir terminale yapıştırın.

```bash
curl -fsSL https://raw.githubusercontent.com/enseitankado/eta-112/main/baslat.sh | sudo bash
```

Menüden **1) Kullanıcı parolası**, **2) BIOS parolası** veya **3) MAC adresi** seçilir.

---

Adımlar:
1. Bilgisayardaki kurulu işletim sistemi otomatik olarak bulunur
2. Hesaplar **numaralı bir liste** olarak gösterilir (root, etapadmin, ogretmen, ogrenci…).
3. Sıfırlamak istediğiniz hesabın **numarasını** girin. Tüm hesapları sıfırlamak için `tum` yazın.
4. Yeni parolayı iki kez girin. Parola uygulanır ve doğru ayarlandığı **teyit edilir**.

![Menüden "1) Kullanıcı parolası": sistem ve hesaplar listelenir, sıfırlanacak hesap numarayla seçilir](1.png)

Sıfırlama bittikten sonra, hedef disk serbest bırakılır; bilgisayarı normal başlatıp **yeni
parolayla** giriş yapabilirsiniz.

---

![Menüden "2) BIOS parolası": model algılanır, parola ayarlanır, yazılıp doğrulanır (✓ Tamam)](2.png)

- Değişiklikten önce onay sorulur. **BIOS parolası değişikliğinin etkili olması için bilgisayarı
  yeniden başlatın.**
- BIOS özelliği yalnızca **desteklenen modellerde** çalışır; desteklenmiyorsa işlem yapılmaz
  ("DESTEKLENMİYOR" mesajı).

---

### MAC adresi (3)

Onboard ethernet MAC'ini okur ve önerilen yeni MAC'in **Faz OUI**'sine ait olup olmadığını
doğrular — Faz'a **ait olmayan** bir MAC kabul edilmez. Desteklenen modellerde MAC, Realtek
NIC'in **eFuse**'una yazılır; değişiklik **donanım seviyesinde, işletim sisteminden bağımsız ve
kalıcıdır** (Debian bugün, Windows yarın — fark etmez; çip her açılışta MAC'i eFuse'dan yükler).

- ⚠️ **eFuse tek-yönlü kalıcı bellektir (OTP).** Her MAC değişikliği yaklaşık **7 bayt** tüketir
  ve **geri alınamaz** (yeni MAC eskisini silmez, boş alana eklenir). Boş alan dolunca MAC bir
  daha değiştirilemez. Araç, **kaç değişikliğin kaldığını** yazmadan önce gösterir ve onay ister.
- Yazma işlemi **geri-okunarak doğrulanır**. Gerekli programlama aracı (`rtnicpg` + `pgdrv`) ilk
  kullanımda otomatik indirilip derlenir (internet ve `linux-headers` gerekir).
- Komut satırı: `mac read` (oku), `mac check <MAC>` (doğrula), `mac set <MAC>` (eFuse'a yaz).

**Desteklenen donanımlar:**

<!-- DESTEKLENEN-DONANIM:START (otomatik üretilir; elle düzenlemeyin) -->
- **Faz 1 Vestel Intel (Siyah)** — VESTEL 14MB24A / Intel Core i3-2310M, AMI Aptio (BIOS 4.6.5) (60.180 / %11,19 / 2026)
- **Faz 2 Vestel AMD (Gri)** — VESTEL 14MB37C1 / AMD A10-5750M, AMI Aptio (BIOS L0.30) (53.733 / %9,99 / 2026)
- **Faz 2 Vestel Intel (Gri)** — VESTEL 14MB57 / Intel Core i3-4000M, AMI Aptio (BIOS 4.6.5) (205.399 / %38,18 / 2026)
<!-- DESTEKLENEN-DONANIM:END -->

---

## Notlar

- Aracın çalıştırılabilmesi için **sudo yetkisi** (`etapadmin`) gerekir.

---

## Geliştirici ve lisans

- Geliştirici: **Özgür Koca** — [ozgurkoca.com](https://ozgurkoca.com)
- Lisans: **GPL-3.0-or-later** — özgür yazılım.

---

