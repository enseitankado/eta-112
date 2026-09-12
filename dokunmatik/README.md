# Dokunmatik sürücü arşivi

Pardus ETAP akıllı tahtaların dokunmatik katmanı (`eta-touchdrv`) için sürüm arşivi.
Amaç: kalibrasyon/dokunma sorunu yaşayan bir tahtada sürücü ve sunucu sürümlerini
**sırayla deneyip** düzelten sürümü kalıcı hale getirmek.

Paketler eta-112 tarafından çalışma anında GitHub'dan indirilir; `baslat.sh`
içine gömülmez.

---

## Teyit: paket dışında yapılandırma yok

> "Sürücü güncellendiğinde muhtemelen gerekli olan her şey güncelleniyor, yani
> sürücü ve server haricinde bir şeye ihtiyaç olmayabilir."

**Doğru.** Çalışan bir ETAP 23.4 tahtası (`eta-touchdrv 0.5.1`) üzerinde kontrol edildi:

| Kontrol | Sonuç |
|---|---|
| `dpkg -L eta-touchdrv` | Yalnızca `/usr/bin/*`, `/usr/src/eta-touchdrv-*/`, `/lib/systemd/system/eta-touchdrv@.service`, `/lib/udev/rules.d/60-eta-touchdrv.rules` |
| `eta-touchdrv.conffiles` | **Yok** — paketin `/etc` altında yönettiği hiçbir dosya yok |
| `/etc/X11/xorg.conf`, `xorg.conf.d/` | Dokunmatiğe ait girdi yok (yalnızca `10-keyboard.conf`) |
| `/etc/libinput/`, `/etc/udev/hwdb.d/` | Boş / yok — `EVDEV_ABS_*` kalibrasyon ezmesi yok |
| `/etc/udev/rules.d/` | Dokunmatik kuralı yok (paketinki `/lib/udev/rules.d`'de) |
| `/etc/systemd/system/eta-touchdrv.service.d/` | Yok — drop-in override yok |
| `/etc/modprobe.d/` | `OtdDrv`/`OpticalDrv` parametresi yok |
| `/etc/default/grub` | `GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"` — dokunmatikle ilgili çekirdek parametresi yok |
| Sunucu ikililerinde `strings` taraması | Hiçbiri harici yapılandırma dosyası açmıyor; yalnızca `/dev/OtdUsbRaw%03d` ve `/dev/IRTouchOptical%03d` |

Yani **tek müdahale yüzeyi `.deb` paketinin kendisidir.** Paket; kernel modülü
kaynağını (DKMS), kullanıcı uzayı sunucusunu, systemd birimini ve udev kurallarını
bir arada taşır. Eski ISO'lardan ayrıca yapılandırma toplamaya gerek yok.

**İki istisna** — bunlar pakete ait değil, denemeler sırasında ayrıca ele alınmalı:

1. **Kalibrasyon verisi cihazın EEPROM'undadır** (`ProductKey`), diskte değil.
   Sürücü değiştirmek onu sıfırlamaz; farklı sunucu sürümleri aynı EEPROM
   verisini farklı yorumlayabilir. Bu zaten aradığımız şey.
2. **`eta-unattended-upgrade` kurulu.** Eski bir sürümde karar kılınırsa
   `apt-mark hold eta-touchdrv` + `/etc/apt/preferences.d/` pin'i gerekir;
   yoksa bir sonraki otomatik güncellemede 0.5.1'e geri döner.

Ayrıntılı mimari: [`belgeler/mimari.md`](belgeler/mimari.md).
Kalibrasyonu cihazdan doğrudan okuma/yazma protokolü:
[`belgeler/kalibrasyon-protokolu.md`](belgeler/kalibrasyon-protokolu.md).

### Sürüm denemesi işe yaramazsa

Kalibrasyon verisi diskte değil, **panelin EEPROM'unda**. `eta-112.py dokunmatik
kalibrasyon oku` bu veriyi doğrudan okur; sağlam bir tahtadan alınan kayıtla
`karsilastir` çoğu zaman sürüm denemesinden daha kesin bir teşhis verir.
Okuma protokolü doğrulanmıştır; yazma komutu **varsayıma dayanır** — ayrıntı için
protokol belgesine bakın.

---

## Dizin düzeni

```
dokunmatik/
├── surumler.json        Makine-okunur manifest — eta-112 bunu kullanır
├── paketler/            Resmi Pardus/ETAP sürümleri (13 adet)
├── varyantlar/          Aynı sürüm numarasının farklı derlemesi
├── ucuncu-taraf/        Resmi olmayan çatal (vrdons)
├── kaynak/              Kernel modülü kaynakları — diff için elde tutulan kopyalar
├── araclar/             Arşivi yeniden üretmek için script'ler
└── belgeler/            Mimari notu ve tam upstream changelog
```

---

## Sürüm matrisi

Kalibrasyon davranışını belirleyen iki şey var: **sunucu ikilisi** (koordinat
polinomunu uygulayan kapalı kaynak daemon) ve **kernel modülü**. Aşağıdaki
tabloda aynı harfi taşıyan sürümler bu iki açıdan birebir aynıdır — denemeye
gerek yok.

| Sürüm | Tarih | Sunucu | Modül | Servis / başlatıcı | Kaynak |
|---|---|:---:|:---:|---|---|
| 0.1.8 | 2019-12 | **A** | — | `eta-touchdrv.service` / `touchdrv_install` | yerel arşiv |
| 0.2.0 | 2020-06 | **B** | **M1** | aynı | git'ten paketlendi |
| 0.3.0 | 2022-10 | **B** | **M2** | aynı | git'ten paketlendi |
| 0.3.1 | 2022-10 | **C** | **M3** | aynı | ETAP deposu |
| 0.3.2 | 2025-05 | **C** | **M3** | aynı | ETAP deposu |
| 0.3.3 | 2025-05 | **C** | **M4** | aynı | git'ten paketlendi |
| 0.3.4 | 2025-08 | **C** | **M4** | aynı | ETAP deposu |
| 0.3.5 | 2025-09 | **C** | **M4** | `+ touchdrv_restart` | ETAP deposu |
| 0.4.0~beta1 | 2025-04 | **E** | **M1** | `eta-touchdrv.service` | yerel arşiv |
| 0.3.6~tbt1 | 2026-03 | **D** | **M5** | `+ touchdrv_restart` | git'ten paketlendi |
| 0.4.0 | 2026-03 | **F** | **M5** | `+ touchdrv_restart` | yerel arşiv |
| 0.5.0 | 2026-04 | **G** | **M5** | `eta-touchdrv@.service` / `touchdrv_launcher` | ETAP deposu |
| 0.5.1 | 2026-06 | **G** | **M5** | aynı | ETAP deposu |

Bu yüzden `surumler.json` içindeki `deneme_sirasi` 13 değil **9 adım**:

```
0.5.1 → 0.4.0 → 0.4.0~beta1 → 0.3.6~tbt1 → 0.3.5 → 0.3.2 → 0.3.0 → 0.2.0 → 0.1.8
```

Yeniden eskiye gider; kalibrasyon regresyonlarının çoğu yakın tarihli bir
güncellemeden geldiği için ilk birkaç adımda sonuç alınması beklenir.

---

## Denemenin iki kademesi

Mimari notundaki kritik ayrım: **kernel modülü kalibrasyon bilmez.** `OtdDrv.ko`
64 baytlık ham USB paketlerini `/dev/OtdUsbRaw000`'e taşıyan durumsuz bir
aktarıcıdır; koordinat polinomunu uygulayan taraf kullanıcı uzayındaki sunucudur.
Bu, denemeyi ikiye ayırmayı mümkün kılıyor:

**Kademe 1 — yalnızca sunucu ikilisini değiştir (saniyeler).**
`.deb` içinden `usr/bin/OtdTouchServer.x86_64` çıkarılır, mevcut dosyanın üzerine
yazılır, servis yeniden başlatılır. Kurulu kernel modülüne dokunulmaz, DKMS
derlemesi yapılmaz. Kalibrasyon sorunu için doğru ve hızlı olan kademe budur.

**Kademe 2 — `.deb`'i tam kur (dakikalar).**
Modül + sunucu + servis + udev birlikte değişir; DKMS yeniden derler.

### Çekirdek kısıtı — Kademe 2'yi sınırlar

Bu tahtada çekirdek **6.12.85**. Changelog'a göre DKMS derleme düzeltmesi
0.3.3'te geldi (*"fix dkms build after 6.8.0"*). Dolayısıyla **M1/M2/M3 modülü
taşıyan sürümlerin 6.12'de derlenmesi beklenmez**:

| Modül | Sürümler | 6.12'de derlenir mi |
|---|---|---|
| — / M1 / M2 / M3 | 0.1.8, 0.2.0, 0.3.0, 0.3.1, 0.3.2, 0.4.0~beta1 | Hayır (beklenmiyor) |
| M4 | 0.3.3, 0.3.4, 0.3.5 | Evet |
| M5 | 0.3.6~tbt1, 0.4.0, 0.5.0, 0.5.1 | Evet |

Yani tam kurulumla denenebilecek gerçekçi küme **0.5.1 → 0.4.0 → 0.3.6~tbt1 → 0.3.5**.
Daha eski sunucuları görmek istiyorsanız Kademe 1'i kullanın.

### Kademe 1'de uyumluluk uyarısı

Mimari notu `OtdDrv.ko ↔ OtdTouchServer` arasındaki **ioctl protokolünün sürüme
bağlı** olduğunu söylüyor. Bu yüzden sunucu değiş-tokuşu her kombinasyon için
güvenli değil:

- **Güvenli:** kurulu modül M5 iken **D, F, G** sunucuları (0.3.6~tbt1, 0.4.0,
  0.5.0/0.5.1) — hepsi aynı modülle birlikte yayınlandı.
- **Riskli:** A, B, C, E sunucuları farklı modül nesilleriyle geldi; M5 modülü
  üzerinde çalışmayabilir. Denenebilir, ama başarısızlık "bu sürüm kötü"
  anlamına gelmez.

**0.4.0~beta1 bir aykırı değer:** 2025-04 tarihli, changelog kaydı yalnızca
`* Test`. Kendine özgü bir sunucu ikilisi (**E**) taşıyor ama kernel kaynakları
2020 tarihli **M1**. Muhtemelen atılmış bir test derlemesi; hem Kademe 2'de
derlenmesi beklenmiyor hem de Kademe 1'de riskli kümede.

### Sürüm serisi hakkında notlar

- **0.4.0** yeni nesil sunucu: `--ellipse-backend=legacy|custom`, `--verbose`,
  `--debug-touch-pipeline=PATH` (NDJSON) ve `ELLIPSE_FITTER` / `TOUCH_DEBUG_LOG`
  ortam değişkenleri. Elips uydurma algoritmasının değişmesi kalibrasyon
  şikâyetleri için **birinci dereceden şüpheli**.
- **0.5.0** systemd/udev katmanını yeniden yazdı: tek servis yerine
  `eta-touchdrv@optical` / `eta-touchdrv@otd` şablonu, `touchdrv_install` yerine
  `touchdrv_launcher`, `Restart=on-failure`. Sunucu ikilisi de değişti
  (`otd:c5290aa3b2`).
- **0.3.6~tbt1** ve **0.4.0** ETAP deposunda yayınlanmadı; deponun sürüm listesi
  0.3.5'ten 0.5.0'a atlıyor.
- **0.1.x serisinin tamamı** (0.1.1-2 … 0.1.8) birebir aynı ikilileri taşır;
  arşive yalnızca 0.1.8 alındı.
- **vrdons 0.5.2/0.5.3** resmi değil: ikili adları farklı
  (`OpticalTouchServer.x86_64`), tek sürücü kullanıyor, dizin düzeni başka.
  Son çare olarak denenmek üzere `ucuncu-taraf/` altında.

---

## Üçüncü taraf çatal: `vrdons/eta-touchdrv` değerlendirmesi

**Sunucunun kaynak kodu değil.** Depo, Vestel ikililerini `bin/` altında aynen taşıyor ve
README'si upstream ile aynı cümleyi tekrarlıyor: *"source code of server daemons are
unavailable. They are provided by Vestel."* Release'lerde de blob paketleniyor.

Depoda olan şey **tersine mühendislik notları**: `notes/touch4/` ve `nodes/touch2/` altında
130 markdown dosyası, fonksiyon başına decompile edilmiş sözde-C. Binary Ninja ile yapılmış
(`OpticalTouchServer.x86_64.bndb` veritabanı da repoda), Obsidian kasası olarak tutuluyor.
Analiz edilen ikili `--ellipse-backend` yardım metnini içeriyor, yani **0.4.0+ / 0.5.x** nesli.

Bizim açımızdan iki şey çıktı:

### 1. Kalibrasyon polinomu (`notes/touch4/subs/sub_406680.md`)

x ve y'ye **ayrı ayrı**, aynı 8 katsayılı blokla uygulanan Horner formunda 7. derece polinom:

```
x' = ((((((c7·x + c6)·x + c5)·x + c4)·x + c3)·x + c2)·x + c1)·x + c0
y' = ((((((c7·y + c6)·y + c5)·y + c4)·y + c3)·y + c2)·y + c1)·y + c0
```

Katsayılar context'te slot başına `ctx + i*0x34 + 0x30`. `c1 <= 0` ise noktalar ters sırayla
(aynalanmış) yazılıyor.

> **`belgeler/mimari.md` bu noktada güncel değil.** Orada kalibrasyon "Simple: A00/A01/A10/A11,
> Distorted: 10 katsayılı 3. derece" olarak anlatılıyor. Analiz edilen 0.4.0+/0.5.x ikilisinde
> 8 katsayılı ve **çapraz terimsiz** (x ve y birbirinden bağımsız) bir polinom var. Çapraz
> terim olmaması önemli: bu sürücü dönme/eğiklik düzeltemez, yalnız her eksende ayrı
> ölçek + eğrilik düzeltir.

touch2 tarafında sembol adları da kurtarılmış: `OptSetCalibPara` (cihazdan okunan kalibrasyonu
uygular), `OptSetCalibParaDefault` (kalibrasyon yoksa varsayılan), `getCommand`/`setCommand`
(ioctl `0x100040` ile USB feature oku/yaz — kalibrasyonun cihaza yazıldığı yol),
`OptTranslatePoint`, `OptClickFilter*`, `OptInterpolator*`.

### 2. `nodaemon` dalı — daemon'suz sürücü

`src/OpticalDrv.c` yeniden yazılmış: kernel modülü doğrudan `/dev/input/eventX` üretiyor
(`input_mt_init_slots(..., INPUT_MT_DIRECT)`, `input_report_abs(ABS_MT_POSITION_X/Y)`),
kullanıcı uzayı daemon'una hiç ihtiyaç duymuyor. Hem `6615:*` hem `2621:*` kimliklerini tek
sürücüde topluyor (`variant_irtouch` / `variant_raw`).

**Kalibrasyon uygulamıyor** — kernelde float aritmetiği yok; ham cihaz koordinatlarını
0–32767 aralığında olduğu gibi gönderiyor.

Bu, sorunlu tahta için **üçüncü bir yol** demek: EEPROM'daki kalibrasyon bozuksa veya sunucu
onu yanlış yorumluyorsa, bu sürücü kalibrasyonu tamamen devre dışı bırakır; düzeltme X11
tarafında (`libinput` `TransformationMatrix`, `xinput_calibrator`) yapılır.

Şimdilik eta-112'ye dahil **edilmedi**: `nodaemon` dalı hiçbir release'e bağlı değil, hazır
`.deb`'i yok ve 6.12 çekirdeğinde derlendiği doğrulanmadı. İlk iki kademe sonuç vermezse
değerlendirilecek. Arşivdeki `ucuncu-taraf/0.5.2` ve `0.5.3` paketleri `main` dalından gelir
ve hâlâ daemon kullanır.

---

## Paketlerin kökeni

| Köken | Sürümler | Nasıl |
|---|---|---|
| ETAP deposu | 0.3.1, 0.3.2, 0.3.4, 0.3.5, 0.5.0, 0.5.1 | `apt-get download eta-touchdrv=<sürüm>` — `depo.etap.org.tr/etap yirmiuc/main` |
| Upstream git | 0.2.0, 0.3.0, 0.3.3, 0.3.6~tbt1 | `araclar/paketle.sh` ile `debian/*` etiketlerinden yeniden paketlendi |
| Yerel arşiv | 0.1.8, 0.4.0~beta1, 0.4.0, 0.3.1 (varyant) | Elde bulunan, hiçbir depoda olmayan `.deb`'ler |
| Üçüncü taraf | 0.5.2, 0.5.3 | `gh release download -R vrdons/eta-touchdrv` |

### Yeniden paketleme hakkında

`araclar/paketle.sh`, `github.com/pardus/eta-touchdrv` deposundaki `debian/<sürüm>`
etiketinden `debian/rules`'un yaptığı işi birebir tekrarlayarak `.deb` üretir.
Yöntem 0.3.5 üzerinde doğrulandı: üretilen paketin dosya listesi resmi `.deb` ile
aynı, tüm kaynak/script/servis/udev dosyaları **bayt bayt** özdeş. Tek fark
ikililerin `dh_strip`'ten geçmemiş olması (daha büyük, işlevsel olarak aynı).

Bu yüzden denklik grupları paket içeriğinden değil, upstream git'teki ham blob
özetlerinden hesaplanır — bkz. `manifest-uret.py` içindeki `UPSTREAM` tablosu.

---

## Arşivi güncellemek

```bash
# 1) Depodaki yeni sürümleri indir
apt-get download eta-touchdrv=<yeni-sürüm>
mv eta-touchdrv_*.deb dokunmatik/paketler/

# 2) upstream git'te olup depoda olmayan bir sürüm varsa
git clone https://github.com/pardus/eta-touchdrv /tmp/eta-touchdrv
dokunmatik/araclar/paketle.sh /tmp/eta-touchdrv debian/<etiket> \
    dokunmatik/paketler/<en-yakın-sürüm>.deb dokunmatik/paketler/

# 3) Blob özet tablosunu tazele (yeni sürüm eklendiyse)
dokunmatik/araclar/upstream-ozet.sh /tmp/eta-touchdrv
#    çıktıyı araclar/manifest-uret.py içindeki UPSTREAM sözlüğüne yapıştır,
#    KOKEN ve NOTLAR sözlüklerine de yeni sürümü ekle

# 4) Manifesti yeniden üret
python3 dokunmatik/araclar/manifest-uret.py
```

---

## Lisans

`eta-touchdrv` paketleri GPL-3.0+ olarak dağıtılır (`usr/share/doc/eta-touchdrv/copyright`).
Sunucu daemon'larının kaynak kodu Vestel tarafından sağlanmaz; ikililer upstream
tarafından da bu şekilde, `github.com/pardus/eta-touchdrv` üzerinde herkese açık
biçimde dağıtılmaktadır. Bu arşiv o dağıtımın bir aynasıdır.
