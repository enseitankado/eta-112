# ETA-112 — Parola Aracı

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Pardus%20ETAP%20%C2%B7%20Debian-informational)
![Python](https://img.shields.io/badge/python-3-blue)

Tek araçta iki iş yapar:

- **Kullanıcı parolası** — Bilgisayardaki bir Linux hesabının (örn. `etapadmin`) **unutulan
  parolasını yeni bir parolayla değiştirir.**
- **BIOS parolası** — BIOS **yönetici/kullanıcı parolasını okur, ayarlar veya temizler**
  (yalnızca desteklenen modellerde).

Hem **canlı (USB) ortamdan** hem de **çalışan sistemden** kullanılabilir.

---

## Çalıştırma

İnternet üzerinden (kurulum gerektirmez):

```bash
curl -fsSL https://raw.githubusercontent.com/enseitankado/eta-112/main/baslat.sh | sudo bash
```

Menüden **1) Kullanıcı parolası** veya **2) BIOS parolası** seçilir.

---

Adımlar:
1. Bilgisayardaki sistem(ler) bulunur; birden fazlaysa hangisi olduğu sorulur.
2. Hesaplar **numaralı bir liste** olarak gösterilir (root, etapadmin, ogretmen, ogrenci…).
   Tüm hesapları görmek için `tum` yazın.
3. Sıfırlamak istediğiniz hesabın **numarasını** girin.
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

**Desteklenen donanımlar:**

<!-- DESTEKLENEN-DONANIM:START (otomatik üretilir; elle düzenlemeyin) -->
- **Faz 2 Vestel (Gri)** — VESTEL 14MB37C1 / AMD A10-5750M, AMI Aptio (BIOS L0.30)
<!-- DESTEKLENEN-DONANIM:END -->

---

## Notlar

- Aracın çalıştırılabilmesi için **sudo yetkisi** (`etapadmin`) gerekir.

---

## Geliştirici ve lisans

- Geliştirici: **Özgür Koca** — [ozgurkoca.com](https://ozgurkoca.com)
- Lisans: **GPL-3.0-or-later** — özgür yazılım.

---

## In short (English)

**ETA-112** is a single tool with two functions for Pardus ETAP / Debian systems:

- **User password** — reset a forgotten Linux account password (e.g. `etapadmin`), either from a
  live USB or the running system.
- **BIOS password** — read, set, or clear the BIOS supervisor/user password (supported models only).

Run it (no install needed):

```bash
curl -fsSL https://raw.githubusercontent.com/enseitankado/eta-112/main/baslat.sh | sudo bash
```

Then pick **1) user password** or **2) BIOS password** from the menu. Requires root (`sudo`).
Licensed under **GPL-3.0-or-later**. Author: Özgür Koca — [ozgurkoca.com](https://ozgurkoca.com).
