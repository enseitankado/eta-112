# ETAP OTD Dokunmatik Ekran — Sistem Mimarisi ve Semantiği

> Bu belge `enseitankado/etap` deposundaki `arastirmalarim/dokunmatik/README.md`
> dosyasının kopyasıdır. eta-112 için eklenen bölümler: **6.1 — Kalibrasyon
> dönüşümü (0.4.0+ / 0.5.x)**. O bölümdeki bilgi kapalı kaynak ikilinin tersine
> mühendislikle çözülmesinden gelir ve 6. bölümdeki eski nesil tarifinin yerine
> geçer.

## Genel Mimari

```
USB Donanım (2621:4501)
        ↓
    OtdDrv.ko  (kernel modülü)
        ↓
  /dev/OtdUsbRaw000  (karakter aygıtı)
        ↓
  OtdTouchServer.x86_64  (kullanıcı uzayı daemon)
        ↓
  /dev/input/eventX  (standart Linux input)
        ↓
  X11 / Wayland
        ↓
  Masaüstü / ETAP Uygulaması
```

---

## Katman Katman State ve Mantık

### 1. Kernel Katmanı — OtdDrv.ko

**Stateless, salt taşıyıcı.**

```
USB interrupt gelir
    → 64 byte ham veriyi tampona yaz
    → /dev/OtdUsbRaw000'e okuma bekleyene ver
    → başka hiçbir şey yapmaz
```

- Kalibrasyon bilmez
- Koordinat yorumlamaz
- Sadece USB paketlerini userspace'e iletir
- Aygıt düğümü: `/dev/OtdUsbRaw%03d` formatı (ör. `/dev/OtdUsbRaw000`)

---

### 2. OtdTouchServer — Sistemin Beyni

**Stateful daemon. Tüm iş mantığı burada.**

```
Açılışta:
    → /dev/OtdUsbRaw000'i aç (fd=3)
    → Kalibrasyon parametrelerini yükle
    → /dev/input/eventX oluştur

Çalışma döngüsü:
    → read(fd, buf, 64)   ← ham USB verisi (64 byte)
    → nanosleep()
    → koordinatları kalibre et (polinom dönüşümü)
    → input_report_abs()  → X11'e ilet
```

**Tuttuğu state:**

| State | Açıklama |
|---|---|
| Kalibrasyon matrisi | A00..A30 polinom katsayıları |
| Aktif dokunma noktaları | Hangi parmak nerede (slot tablosu) |
| Cihaz modu | Normal / Touch / Rawtouch / CodedImage |
| ProductKey | 25 byte cihaz kimliği / EEPROM verisi |

**Önemli:** OtdTouchServer statik derlenmiş (`ldd` → "özdevimli değil"). libOtd.so içine gömülü, dışarıdan bağımlılığı yok.

---

### 3. Başlatma Zinciri

```
Sistem açılır
        ↓
systemd → eta-touchdrv.service
        ↓
/usr/bin/touchdrv_install  (bash script)
        ↓
lsusb ile cihaz tara (max 3 deneme, 0.5s aralık)
        ↓
2621:4501 bulundu
        ↓
modprobe OtdDrv     → kernel modülü yüklenir
        ↓             /dev/OtdUsbRaw000 oluşur
exec OtdTouchServer.x86_64  → daemon başlar
```

`touchdrv_install` içeriği:

```bash
#!/bin/bash
set -euo pipefail
max_try=3
try_count=0
while (( try_count < max_try )); do
    if lsusb | grep -qE "(6615:0084|...)"; then
        modprobe OpticalDrv
        exec /usr/bin/OpticalService
    elif lsusb | grep -qE "(2621:2201|2621:4501)"; then
        modprobe OtdDrv
        exec /usr/bin/OtdTouchServer.$(uname -m)
    fi
    ((try_count++))
    sleep 0.5
done
exit 1
```

`exec` kullanımı önemli — script PID'i yerine doğrudan server process çalışır.

---

### 4. udev Katmanı — Hot-Plug

```
USB cihaz takılır / çıkarılır
        ↓
60-eta-touchdrv.rules tetiklenir
        ↓
/usr/bin/touchdrv_restart çalışır
        ↓
eta-touchdrv.service yeniden başlar
```

Desteklenen USB ID'leri (`/usr/lib/udev/rules.d/60-eta-touchdrv.rules`):

| Vendor:Product | Sürücü |
|---|---|
| 6615:0084 — 6615:0c20 | OpticalDrv + OpticalService |
| 2621:2201 | OtdDrv + OtdTouchServer |
| 2621:4501 | OtdDrv + OtdTouchServer |

---

### 5. X11 Katmanı

OtdTouchServer standart Linux multitouch eventleri üretir:

```
/dev/input/eventX
        ↓
libinput veya evdev
        ↓
X11 → pencere yöneticisi → uygulama
```

ETAP uygulaması standart X11 dokunmatik eventleri alır, OTD'ye özel bir şey bilmesi gerekmez. `After=lightdm.service` ile X11 başlamadan önce çalışması engellenir.

---

### 6. Kalibrasyon State'i

```
İlk kurulumda:
    → Kalibrasyon uygulaması açılır
    → Kullanıcı ekrana N noktaya dokunur
    → RawtouchData (kalibrasyonsuz) toplanır
    → Polinom katsayıları hesaplanır (en küçük kareler yöntemi)
    → OtdSetProductKey() ile cihaza yazılır (EEPROM)
      veya yerel dosyaya kaydedilir

Sonraki açılışlarda:
    → OtdGetProductKey() ile cihazdan okunur
    → Server belleğe yükler
    → Her dokunmada uygulanır
```

Kalibrasyon iki modda çalışır:

| Mod | Katsayı | Kullanım |
|---|---|---|
| Simple | A00, A01, A10, A11 | Hafif kayma/ölçek düzeltmesi |
| Distorted | 10 katsayı (3. derece polinom) | Lens bozulması, açı sapması |

> Yukarıdaki iki mod, sürücünün **eski** nesli için geçerli. 0.4.0+ / 0.5.x
> ikilileri farklı bir dönüşüm kullanıyor — bkz. 6.1.

---

### 6.1. Kalibrasyon dönüşümü — 0.4.0+ / 0.5.x

`OtdTouchServer` kapalı kaynak; aşağıdaki bilgi ikilinin tersine mühendislikle
çözülmesinden geliyor. Kaynak: `github.com/vrdons/eta-touchdrv`,
`notes/touch4/subs/sub_406680.md` (Binary Ninja ile çözümlenmiş). Çözümlenen
ikili `--ellipse-backend` yardım metnini içeriyor, yani **0.4.0 ve sonrası**.

Dönüşüm, **8 katsayılı (7. derece) bir polinomun** x ve y'ye **ayrı ayrı**,
**aynı katsayı bloğuyla** uygulanmasından ibaret. Horner formunda:

```
x' = ((((((c7·x + c6)·x + c5)·x + c4)·x + c3)·x + c2)·x + c1)·x + c0
y' = ((((((c7·y + c6)·y + c5)·y + c4)·y + c3)·y + c2)·y + c1)·y + c0
```

**Çapraz terim yok** (x'·y veya y'·x biçiminde bir çarpan bulunmuyor). Bunun
pratik sonucu önemli:

- Bu dönüşüm her eksende **ayrı ölçek + eğrilik** düzeltebilir.
- **Dönme (rotation) ve eğiklik (shear) düzeltemez.** Panel çerçeveye hafifçe
  dönük takılmışsa bu sürücü katmanı onu düzeltemez; sorun kalibrasyonla
  kapatılamaz, mekanik olarak çözülmesi gerekir.

Katsayı bloğu, dokunma yuvası (slot) başına context içinde tutuluyor:

```c
float *coeff = (float *)((char *)ctx + slot_index * 0x34 + 0x30);
```

Yani **her slot kendi katsayı setini taşıyor** — 10 parmağın her biri için ayrı
bir düzeltme eğrisi mümkün.

`c1 <= 0` olduğunda kod ikinci bir dala giriyor: x ile y okuma sırası
değiş-tokuş ediliyor ve sonuçlar slot dizisine **sondan başa** yazılıyor. Yani
`c1`'in işareti bir katsayı olmanın yanında **yön/ayna bayrağı** olarak da
kullanılıyor. Bozuk bir EEPROM'da `c1` işaretinin değişmesi, dokunmanın
eksen değiştirmiş ya da aynalanmış görünmesine yol açar.

Çözümlenen fonksiyonun sözde kodu (kısaltılmış):

```c
void sub_406680(void *ctx, unsigned char *report, unsigned char *dst, int *len_state)
{
    if (!report || !dst || !ctx) return;
    if (report[0xa8] != *((unsigned char *)ctx + 0xd0)) return;   // slot sayisi uyusmuyor

    for (short i = 0; i < report[0xa8]; i++) {
        unsigned char *slot   = dst + (uint16_t)i * 0x54;
        float         *points = (float *)(report + (uint16_t)i * 0x2a);
        float         *coeff  = (float *)((char *)ctx + (uint16_t)i * 0x34 + 0x30);

        slot[0x50] = *((unsigned char *)ctx + 0xd1);
        unsigned char n = slot[0x50];

        if (coeff[1] > 0.0f) {                      // normal yon
            for (short j = 0; j < n; j++) {
                float x = points[j*2 + 0], y = points[j*2 + 1];
                ((float *)slot)[j*2 + 0] = P(coeff, x);
                ((float *)slot)[j*2 + 1] = P(coeff, y);
            }
        } else {                                    // eksenler takas + ters sira
            for (short j = 0; j < n; j++) {
                float x = points[j*2 + 1], y = points[j*2 + 0];
                int   k = n - j - 1;
                ((float *)slot)[k*2 + 0] = P(coeff, x);
                ((float *)slot)[k*2 + 1] = P(coeff, y);
            }
        }
    }
}
/* P(c, v) = ((((((c[7]*v + c[6])*v + c[5])*v + c[4])*v + c[3])*v + c[2])*v + c[1])*v + c[0] */
```

#### Optical (2 kameralı) taraf

`OpticalService` dinamik bağlı olduğu için sembol adları kurtarılabilmiş.
Kalibrasyonla ilgili olanlar:

| Sembol | İşlevi |
|---|---|
| `OptSetCalibPara` | Cihazdan okunan kalibrasyon parametrelerini context'e uygular |
| `OptSetCalibParaDefault` | Cihazda kalibrasyon yoksa varsayılanı yazar |
| `OptTranslatePoint` | Ham/ara nokta → ekran koordinatı; sonuç `0..0x7fff` aralığına kırpılır |
| `getCommand` / `setCommand` | USB feature oku/yaz — `ioctl` request `0x100040`. Kalibrasyonun cihaza yazıldığı yol budur |
| `OptClickFilter*` | Tıklama filtresi (titreme bastırma) |
| `OptInterpolator*` | Nokta arası ara değer üretimi (çizgi sürekliliği) |

`OptSetCalibParaDefault`'un varlığı önemli: **cihazda kalibrasyon bulunamazsa
sürücü sessizce varsayılana düşüyor.** Bu, "bazı tahtalarda kalibrasyon tutmuyor"
şikâyetinin olası bir açıklaması — EEPROM okuması başarısız olduğunda kullanıcı
hata görmez, sadece yanlış hizalanmış bir ekran görür.

#### 0.4.0 ile gelen çalışma anı seçenekleri

```
--ellipse-backend=legacy|custom   elips uydurma algoritmasi (env: ELLIPSE_FITTER)
--debug-touch-pipeline=PATH       NDJSON dokunma kaydi (env: TOUCH_DEBUG_LOG)
--verbose                         dokunma cerceveleri + codec gunlugu
```

Elips uydurma, dokunma alanının genişlik/yüksekliğini ve dolayısıyla temas
merkezini belirler; `legacy` ile `custom` arasındaki fark doğrudan algılanan
dokunma noktasını kaydırabilir. Kalibrasyon şikâyetlerinde ilk denenecek
ayardır:

```bash
sudo systemctl edit eta-touchdrv@otd.service
# [Service]
# Environment=ELLIPSE_FITTER=legacy
```

---

### 7. Veri Akış Diyagramı

```
Parmak ekrana değer
        ↓
IR sensör matrisi kesilir
        ↓
USB interrupt (64 byte, ~125Hz)
        ↓
OtdDrv.ko tampona yazar
        ↓
OtdTouchServer read() ile alır
        ↓
    [State: kalibrasyon matrisi]
        ↓
Ham X,Y  →  Kalibre X,Y  (polinom dönüşümü)
        ↓
input_mt_slot() + input_report_abs()
        ↓
/dev/input/eventX
        ↓
X11 → uygulama
        ↓
Parmak kalkar → UP eventi → slot temizlenir
```

---

### 8. Dokunma Noktası Veri Yapısı

`OtdDrv-0.4.0.h`'den:

```c
typedef struct {
    uint8_t  state;    // 0x01=IsValid, 0x02=IsTouched
    int16_t  x;        // 0-32767 normalize koordinat
    int16_t  y;        // 0-32767 normalize koordinat
    int16_t  width;    // dokunma alanı genişliği
    int16_t  height;   // dokunma alanı yüksekliği
} TouchPoint;

typedef struct {
    TouchPoint points[10];  // max 10 parmak
    uint16_t   scanTime;    // zaman damgası
} MultiTouchReport;         // toplam 92 byte
```

---

### 9. Sürücü Versiyonları

| Versiyon | Değişiklik |
|---|---|
| 0.1.8 | İlk versiyon, `optictouch.c` kaynak kodu mevcut |
| 0.3.5 | Global state, sabit major:minor (45:193), kalibrasyon kernel'de |
| 0.4.0 | Per-device context, dinamik aygıt numarası, kalibrasyon SDK'ya taşındı |
| 0.4.0~beta | Mevcut kurulu versiyon |

---

### 10. Kritik Bağımlılıklar

```
OtdDrv.ko       ←→  OtdTouchServer    [ioctl protokolü — versiyon bağımlı]
OtdTouchServer  ←→  libOtd.so         [statik — içine gömülü]
OtdTouchServer  ←→  X11               [lightdm.service'ten sonra başlar]
eta-touchdrv    ←→  udevd             [hot-plug için]
```

---

### 11. Bilinen Zayıf Noktalar

| Sorun | Etki | Çözüm |
|---|---|---|
| `touchdrv_install` max 3 deneme | Cihaz geç tanınırsa servis başlamaz | udev restart tetikler |
| `Restart=` tanımlı değil | OtdTouchServer çöküne otomatik restart yok | `sudo systemctl restart eta-touchdrv` |
| Kalibrasyon kaybı | EEPROM yazma başarısız olursa yeniden kalibrasyon gerekir | — |
| libOtd SDK uyumsuzluğu | Harici SDK (v1.7.6230) OtdDrv 0.4.0 ile çalışmıyor | OtdTouchServer içindeki statik libOtd kullanılmalı |

---

### 12. Dosya Konumları

| Dosya | Açıklama |
|---|---|
| `/usr/bin/OtdTouchServer.x86_64` | Ana daemon (statik derlenmiş) |
| `/usr/bin/touchdrv_install` | Başlatma scripti |
| `/usr/bin/touchdrv_restart` | udev hot-plug scripti |
| `/usr/lib/systemd/system/eta-touchdrv.service` | systemd servis tanımı |
| `/usr/lib/udev/rules.d/60-eta-touchdrv.rules` | udev kuralları |
| `/usr/src/eta-touchdrv-0.4.0~beta1/` | Kernel modülü kaynak kodu |
| `/lib/modules/.../updates/dkms/OtdDrv.ko` | Derlenmiş kernel modülü |
| `/dev/OtdUsbRaw000` | Karakter aygıt düğümü (cihaz takılıyken) |
| `/etc/systemd/system/multi-user.target.wants/eta-touchdrv.service` | Servis symlink |
