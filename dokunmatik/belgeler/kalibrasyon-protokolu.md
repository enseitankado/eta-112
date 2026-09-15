# Kalibrasyon protokolü — cihazdan doğrudan okuma/yazma

`eta-112.py dokunmatik kalibrasyon` komutunun dayandığı protokol. Her maddede
bilginin **nereden geldiği** ve **ne kadar kesin olduğu** yazılı; kodda tahmine
dayanan hiçbir şey sessizce kullanılmıyor.

---

## Kesinlik özeti

| Katman | Kaynak | Durum |
|---|---|---|
| ioctl → USB kontrol transferi | `OtdDrv.c` / `OpticalDrv.c` — **GPL kaynak kodu** | **Kesin** |
| 64 baytlık paket biçimi | `OpticalService` `packageBuild` @`0x401606` — sökülerek | **Kesin** |
| Cevap ayrıştırma | `OpticalService` `deviceGetFeature` @`0x4016d1` — sökülerek | **Kesin** |
| Optical komut haritası | `OpticalService` `main` @`0x405d74` — sökülerek | **Kesin** |
| Optical **yazma** komutları | — | **Varsayım** |
| OTD paket biçimi | `OtdTouchServer` paketleyici @`0x3351` — sökülerek | **Kesin** |
| OTD komut haritası | `OtdTouchServer` dinamik sembolleri + söküm | **Kesin** |
| OTD depolama/kayıt düzeni | `GetStorageBlock` @`0x3b9f`, `readRecord` @`0x131cd` | **Kesin** |
| OTD blok içeriğinin **anlamı** | — | **Kısmen** (bkz. 6.6) |
| OTD **blok yazma** komutu | `SetStorageBlock` `0xb2`/n=36 — adres bilinir | **Yük düzeni varsayım** (bkz. 7) |
| OTD **silme** komutu | `EraseStorage` `0xb1` — adres bilinir | **Kullanılmıyor** (bilerek) |

Sökme işlemi `eta-touchdrv 0.3.6~tbt1` paketindeki `OpticalService` ikilisi
üzerinde yapıldı; o sürüm sembol tablosunu taşıyor (`packageBuild`, `getCommand`,
`setCommand`, `deviceGetFeature`, `OptSetCalibPara` … 105 sembol). Aynı ikili
0.2.0'dan 0.5.3'e kadar hiç değişmedi, dolayısıyla bulgular bütün güncel
sürümler için geçerli.

OTD tarafı (6. bölüm) ayrı bir ikiliden çıkarıldı: `OtdTouchServer.x86_64`,
`eta-touchdrv 0.4.0`, BuildID `4b3bf76155aace1c9d0c7ea6f9e272a416680fd3`.

---

## 1. Taşıma katmanı — GPL kaynaktan, kesin

`/usr/src/eta-touchdrv-<sürüm>/touch4/OtdDrv.h`:

```c
#define OTD_IOCTL_CODE_TYPE_SET_REPORT   0x00100000u
#define OTD_IOCTL_CODE_TYPE_GET_REPORT   0x00110000u
#define OTD_IOCTL_CODE(type, length)     (((type) & 0x00ff0000u) | ((length) & 0xffffu))
```

`OtdDrv.c` içindeki karşılıkları:

```c
// SET_REPORT  ->  ioctl(fd, 0x00100040, buf)
usb_control_msg(dev, usb_sndctrlpipe(dev, 0), 0, 0x40, 0, 0, buf, length, 1000);
// GET_REPORT  ->  ioctl(fd, 0x00110040, buf)
usb_control_msg(dev, usb_rcvctrlpipe(dev, 0), 0, 0xc0, 0, 0, buf, length, 1000);
```

Yani `bRequest=0`, `wValue=0`, `wIndex=0` olan **satıcıya özel (vendor) kontrol
transferi**; `bmRequestType` yalnızca yönü belirliyor (`0x40` yazma, `0xc0`
okuma). Protokolün tamamı 64 baytlık yükün içinde.

Aygıt düğümü: `/dev/OtdUsbRaw000` (OTD) · `/dev/IRTouchOptical000` (Optical).

## 2. İstek paketi — `packageBuild` sökümünden, kesin

```
ofset  0    0xAA
       1    tür        1 = set, 2 = get
       2    komut
       3    yük_uzunluğu + 1
       4    indeks
       5..  yük
     5+n    sağlama = (0x55 + toplam(paket[0 .. n+4])) & 0xFF
```

Paketin kalanı üretici ikilisinde **yığından gelen çöp** — cihaz yok sayıyor.
eta-112 sıfırla dolduruyor (belirlenimli olsun diye).

Sınır: `5 + n + 1 ≤ 64` → yük en çok **58 bayt**.

Örnek — `get cmd=0x10 index=0`:  `aa 02 10 01 00 12`
Örnek — `set cmd=0x71 index=0 yük=01`:  `aa 01 71 02 00 01 74`

## 3. Cevap — `deviceGetFeature` sökümünden, kesin

```c
ioctl(fd, 0x00110040, paket);
gecerli = (paket[0] == 0xAA) || (paket[1] == 0x12) || (paket[2] == komut);
// yük yine ofset 5'ten başlar
```

Cevaptaki sağlama **doğrulanmıyor** — üretici ikilisi de doğrulamıyor.

> **Üretici ikilisinde yığın taşması.** `main`, `(0x30, 1)` bloğunu 72 bayt
> (`0x48`) olarak istiyor; `deviceGetFeature` ise 64 baytlık yığın tamponunun 5.
> ofsetinden 72 bayt kopyalıyor. Son **13 bayt bitişik yığın çöpü**. eta-112 bunu
> taklit etmiyor: okumayı tamponun gerçek sonuna, **59 bayta** kırpıyor.

## 4. Optical komut haritası — `main` sökümünden, kesin

`OpticalService` açılışta cihazı açtıktan sonra tam olarak bu diziyi çalıştırır:

| # | tür | komut | indeks | uzunluk | anlamı |
|--:|---|---|--:|--:|---|
| 1 | set | `0x71` | 0 | yük `01` | yapılandırma moduna gir |
| 2 | set | `0x71` | 1 | yük `01` | ” |
| 3 | get | `0x10` | 0 | `0x3a` | kamera bloğu 0 |
| 4 | get | `0x10` | 1 | `0x3a` | kamera bloğu 1 |
| 5 | get | `0x30` | 0 | `0x3a` | yapılandırma bloğu 0 |
| 6 | get | `0x30` | 1 | `0x48`¹ | yapılandırma bloğu 1 |
| 7 | get | `0x14` | 0 | `0x3a` | ekran bloğu 0 |
| 8 | get | `0x14` | 1 | `0x3a` | ekran bloğu 1 |
| 9 | set | `0x71` | 0 | yük `00` | yapılandırma modundan çık |
| 10 | set | `0x71` | 1 | yük `00` | ” |

¹ bkz. yukarıdaki yığın taşması notu — gerçekte 59 bayt okunabilir.

Bu bloklar `OptInitialize`'a beslenir; ardından kalibrasyon varsa
`OptSetCalibPara`, yoksa `OptSetCalibParaDefault` çağrılır.

`OptSetCalibPara` (@`0x404ffd`) yığından **iki adet 58 baytlık blok** (toplam
116 bayt) alıp context'in `+0xC6 .. +0x139` aralığına kopyalıyor, sonra iki
float'tan `sqrt(a² + b²)` hesaplayıp `+0x13A`'ya yazıyor. Yani okuduğumuz
bloklar doğrudan kalibrasyon durumunu oluşturuyor.

> `OptSetCalibParaDefault`'un varlığı önemli: **cihazdan kalibrasyon
> okunamazsa sürücü hata vermeden varsayılana düşüyor.** Kullanıcı bir uyarı
> görmez, yalnızca hizası kaymış bir ekran görür.

## 5. Yazma — varsayım

`setCommand` mekanizması kesin (tür baytı `1`, yük taşır). Ancak üretici
ikilisinde `setCommand` **yalnızca `0x71`** (mod aç/kapa) ile çağrılıyor.
Kalibrasyonu EEPROM'a işleyen komut kimliği gözlemlenmedi.

Aynı dönemin `calibrationTools` aracı incelendi ama işe yaramadı: o, eski
`optictouch` sürücüsüne karşı `/dev/bus/usb` üzerinden çalışıyor ve bambaşka
ioctl numaraları (`0xc1`, `0xc2`, `0xc5`, `0xc7`, `0xcd`) kullanıyor. Güncel
`OpticalDrv` yolunun karşılığı değil.

eta-112'nin `kalibrasyon yaz` komutu, okumada kullanılan **aynı komut/indeks
çiftlerinin set paketiyle geri yazılabileceğini varsayar.** Bu makul ama
doğrulanmamıştır. Bu yüzden:

- `--onayliyorum` bayrağı olmadan çalışmaz,
- yazmadan hemen önce **otomatik yedek** alır,
- ayrıca `yaz` yazarak teyit ister,
- anlık görüntünün panel tipi hedefle uyuşmazsa reddeder,
- OTD panellerde `--dene` olmadan tamamen kapalıdır.

## 6. OTD (2621) — çözüldü

> **Bu bölüm 2026-09-15'te yeniden yazıldı.** Önceki hâli "bilinmiyor" diyor ve
> gerekçe olarak `OtdTouchServer`'ın *statik derlenmiş ve sembolsüz* olduğunu
> gösteriyordu. **Bu, 0.4.0 için doğru değil:** ikili dinamik bağlı (`libm`,
> `libc`) ve dinamik sembol tablosunu taşıyor — `OpticalTouchDeviceGetStorageBlock`,
> `LoadCcbParameters`, `OpticalTouchDeviceEraseStorage` … Çözüm bu sayede mümkün
> oldu. `mimari.md` §2 de aynı yanlışı taşıyordu, o da düzeltildi.

Taşıma katmanı Optical ile aynı (`OtdDrv.h` ve `OpticalDrv.h` aynı ioctl
şemasını kullanıyor — bkz. 1. bölüm). **Paket biçimi ve komut haritası ise
tamamen farklı.** Eski kod Optical biçimini OTD'ye gönderdiği için panel her
isteği STALL ediyordu (`ioctl` → `-EPIPE` → "Broken pipe").

### 6.1 Paket biçimi — söküm, kesin

Paketleyici/alıcı-verici `@0x3351`:

```
    [0]        0x55                       (Optical'da 0xAA)
    [1]        b1 — çağrı başına sabit: 0x1e / 0x2d / 0x3c
    [2]        indeks / parametre
    [3]        komut
    [4]        n  — yük uzunluğu
    [5..5+n-1] yük
    [n+5]      sağlama = toplam(paket[2 .. n+4]) & 0xFF
    gönderilen uzunluk = n+6                (Optical'da sabit 64)
```

| | Optical (6615) | OTD (2621) |
|---|---|---|
| Başlık `[0]` | `0xAA` | `0x55` |
| Gönderilen uzunluk | sabit **64** | **n+6** |
| Sağlama | `0x55 + Σ(p[0..n+4])` | `Σ(p[2..n+4])`, ofset `n+5` |
| SET→GET arası bekleme | 10 ms | **50 ms** (`usleep(0xc350)` @`0x34b5`) |

**Uzunluk kritik.** `OTD_IOCTL_CODE(tip, uzunluk)` uzunluğu ioctl kodunun alt 16
bitinde taşır; 64'e sabitlemek USB kontrol transferinin STALL etmesine yol açar.

### 6.2 Cevap — söküm @`0x34f3`, kesin

```
    [0]        0x55
    [1]        durum:  0x87 = ACK (veri yok) · 0x4b = veri var
    [2]        veri uzunluğu                  (yalnız 0x4b'de)
    [3..]      veri                           (5'ten DEĞİL — 5 Optical'a ait)
    [3+n]      sağlama = Σ(cevap[2 .. 2+n]) & 0xFF
```

Sağlama cihazda doğrulandı: `55 4b 08 |04 00 00 10 80 00 20 00| bc` →
`0x08+0x04+0x10+0x80+0x20 = 0xBC`.

### 6.3 Komut haritası — dinamik semboller + söküm, kesin

Çağrı adresleri sembol aralıklarıyla eşleştirildi:

| komut | n | fonksiyon | adres |
|---|---|---|---|
| `0xb0` | 0 | `OpticalTouchDeviceGetStoragePartitionInformation` | `0x3da2` |
| `0xae` | 0 (indeks `0x80`) | `OtdGetAllFcb` | `0x3e36` |
| `0xb2` | 4 | `OpticalTouchDeviceGetStorageBlock` | `0x3b9f` |
| `0xb2` | 36 | `OpticalTouchDeviceSetStorageBlock` ⚠ | `0x3cda` |
| `0xb1` | 2 | `OpticalTouchDeviceEraseStorage` ⚠⚠ | `0x3ac8` |

> **`0xb1` silme komutudur.** Bu yüzden `kalibrasyon tara`, `--aralik` ile geniş
> tarama istense bile `0xb1` ve `0xb2`'yi listeden zorla çıkarır. Sağlam bir
> panelin kalibrasyonunu silmek bir tarama kazası olamaz.

### 6.4 Depolama ve blok adresleme — `GetStorageBlock` @`0x3b9f`, kesin

`0xb0` sekiz bayt döndürür — uint16 LE dört alan:

```
    [0:2] tip/bayrak   [2:4] toplam blok   [4:6] bölen   [6:8] blok boyu
```

`GetStorageBlock`'un 4 baytlık yükü bu bölenle hesaplanır (`divw 0x4(%r8)`):

```
    yük = uint16(blok_no // bölen) + uint16(blok_no % bölen)
```

Cihazda ölçülen: `tip=4` (bölüm 0) / `7` (1–3) / `2` (0x80), toplam blok `4096`,
bölen `128`, blok boyu `32`. İndeks 0–3 ve `0x80` yanıt verir; `0x04`–`0x7f`
STALL eder.

### 6.5 Kayıt birleştirme — `readRecord` @`0x131cd`, kesin

Veri ham blok değil **kayıt** hâlinde duruyor:

```
    blok sayısı = (uzunluk + 32) // 32
    İLK blok : [0] baytı 0x01 OLMALI (geçerlilik); yalnız [1..31] alınır → 31 bayt
    Sonraki  : 32 baytın tamamı
    sonuç 'uzunluk' bayta kırpılır
```

Kayıt tabloları (`LoadFcbParameters` @`0x143e5`, `LoadCcbParameters` @`0x13ee2`):

| bölüm | başl. blok | uzunluk | içerik |
|---|---|---|---|
| 0–3 (kameralar) | 0 | 25 | ASCII seri (`mimari.md`'deki "ProductKey") |
| 0–3 | 1 | 82 | kamera parametreleri (31+32+19) |
| **0x80 (CCB)** | 0 | 25 | ASCII seri |
| 0x80 | 1 | 150 | aktif alan sınırları + double'lar |
| 0x80 | 6 | 186 | **kalibrasyon katsayıları** |
| 0x80 | 12 | 34 | küçük tamsayı/bayraklar |
| 0x80 | 16 | 169 | (cihazda tamamen sıfır — kullanılmıyor) |
| 0x80 | 22 | 186 | blok 6'nın **bayt bayt aynısı** (yedek kopya) |
| 0x80 | 30, 31 | 25 | — |
| 0x80 | 32 | 512 | tablo |

`StaticLoadCcbParameters` @`0x14c0e` bölüm olarak **`0x80`** verir (`esi=0x80`);
kalibrasyon kameraların bölümlerinde değil, oradadır.

### 6.6 İçerik — kısmen çözüldü

Bu bölümdeki ölçümler **referans tahtadan** geliyor: `2621:4501`, makine
`etap-92f896`, sürücü `eta-touchdrv 0.5.1` — dokunmatiği ve kalibrasyonu
sağlıklı olduğu elle teyit edilmiş tahta. Dökümleri depoda sabit duruyor:
[`../referans/`](../referans/) (alma scripti:
`dokunmatik/araclar/referans-al.sh`). Bir tahtada şüphe varsa karşılaştırma
tarafı orasıdır.

Sağlam bir tahtada (2621:4501) ölçülen:

- Bölüm 0–3 seri: `M4-T-01-140823006218`, `...140824000832`, `...140823003161`,
  `...140824000817`. Bölüm 0x80 seri: `M4-**C**-01-140823006219` — `T` = touch
  kamera, `C` = kontrolcü; numaralar ardışık.
- Blok 4+ tamamen `0xFF` (silinmiş flash): kayıtlar bittiği yerde duruyor.
- **Katsayılar `float64`, `float32` değil.** Hizalama 8'e göre değil (kayıt
  birleştirme 31+32 olduğu için kayıyor).

`ccb/blok 6` içinden, ofset 17'den itibaren:

```
    17: -5.64964     25: -0.00086415475   33: 1.0141756    41: 0
    49: -15.73958    57: 1.032757         65: -0.0060187163  73: 0
```

İki takım dörder double — ölçek (`1.0142`, `1.0328`), küçük çapraz terimler ve
kaydırma (`-5.65`, `-15.74`). Kaydın başı `0x15` + dört `float32`
(`5.0, 1452.0, 5.0, 817.0` — aktif alan sınırları).

**Kesinlik sınırı:** alanların *konumu* ölçülmüş ve tekrarlanabilir; hangi
katsayının hangi terime karşılık geldiği (sıra, çapraz terim var mı) **tek bir
tahtadan** çıkarıldı ve doğrulanmadı. `eta-112.py` bu yüzden yorumlamıyor,
"makul float64 avcısı" ile ofset+değer listeler — ham dökümle birlikte okunmalı.
Avcı üst üste binen `float32` alanlarında yanlış pozitif verebilir (ör. ofset 5).

---

## 7. OTD blok yazma — no-op ile doğrulanabilir varsayım

Okuma çözüldüğü için geri yazma için içeriğin **anlamını** bilmek gerekmiyor:
32 baytlık bloklar okundukları gibi geri konabilir. Eksik olan tek şey yazma
komutunun yük düzeni.

### 7.1 Varsayım

```
    b1 = 0x2d · komut = 0xb2 · n = 36
    yük = uint16(blok_no // bölen) + uint16(blok_no % bölen) + 32 bayt veri
```

Dayanağı **okuma yolunun simetrisi**: aynı komut kimliği (`0xb2`) okumada n=4
ile yalnız adresi taşıyor; yazmada n=36 ile aynı adres + bir blok (`0xb0`
bölüm bilgisi blok boyunu 32 bildiriyor, `4 + 32 = 36`). Sökümdeki çağrı için
daha önce "9 × float32" yorumu düşülmüştü; 4+32 bölünmesi adresleme
simetrisiyle daha tutarlı, ama **cihazda doğrulanması gerekiyor**.

### 7.2 No-op testi — `kalibrasyon yazma-testi`

Varsayımı brick riski almadan sınamanın yolu, bir bloğu **kendi okunan
değeriyle** yazmak:

- Hücre NOR flash olup silme gerektirse bile içerik değişmez: `x & x = x`.
- Geriye tek risk kalır: **adres düzeni yanlışsa yazma başka bloğa gider.**
  Test bunu, yazmadan önce ve sonra aynı blokları dökerek yakalar.

**Test bloğu rastgele seçilmiyor.** En sinsi hata biçimi, iki adres alanının
ters sırada olması: o zaman cihaz `alçak*bölen + yüksek` adresine yazar ve bu
*geçerli* bir blok olabilir — yanlış varsayım sessizce başka bir bloğu bozar.
Bu yüzden test bloğu, ters okuma bölümün **dışına** düşecek şekilde seçilir:

```
    blok = bölen + k,   k = ceil(toplam_blok / bölen) + 1
    ölçülen değerlerle (toplam 4096, bölen 128):  k = 33 → blok 161
    ters okuma: 33*128 + 1 = 4225 > 4096  →  panel STALL eder, veri kaybı yok
```

Blok 161 ayrıca kayıt bölgesinin (ilk ~48 blok) dışında ve `0xFF` (silinmiş
flash). `--blok N` ile elle seçilebilir; o zaman bu güvence kalkar.

Akış: bölüm bilgisi → pencere dökümü (referans) → bloğu oku → aynı baytları
yaz → bloğu geri oku → pencereyi yeniden dök → karşılaştır.

Üç ayrı sonuç, üçü de güvenli:

| Sonuç | Anlamı | Veri |
|---|---|---|
| ACK + içerik aynı + yan etki yok | yük düzeni **doğrulandı** | değişmedi |
| STALL / bilinmeyen durum | panel bu komutu tanımıyor | değişmedi |
| başka blok değişti | adres düzeni **yanlış** | referans döküm dosyada |

Kıyas dökümü iki parçalı: **kayıt bölgesi** (baştan 64 blok) + **test bloğunun
çevresi** (±8 blok). Bölüm 4096 blok bildiriyor ve her blok bir SET+GET+2×50 ms
demek — tam döküm ~7 dakika, bu iki parça ise ~16 saniye. Aradaki bloklar `0xFF`
ve bir şey söylemiyor. İlk parça `--blok-sayisi` ile büyütülebilir.

### 7.3 Geri yükleme — `kalibrasyon depo-yaz`

`depo` dökümünü (ham 32 baytlık bloklar) cihaza geri yazar. Sınırlar:

- yalnız OTD; panel tipi ve dosya biçimi doğrulanır,
- **blok 0 (ASCII seri / ProductKey) varsayılan olarak atlanır** — başka
  panelin kimliğini yazmamak için; `--seri-dahil` ile açılır,
- yazmadan önce hedefin dökümü otomatik yedeklenir,
- hedefte zaten aynı olan blok yazılmaz,
- her blok yazıldıktan sonra **geri okunup doğrulanır**; ilk uyuşmazlıkta
  durulur, kalan bloklara dokunulmaz,
- `--onayliyorum` + ayrıca `yaz` teyidi ister.

`0xb1` (silme) bu yola da konulmadı. Yazma erase gerektiriyorsa no-op testi
bunu "kabul edildi ama içerik bozuldu" olarak bildirir.

> **Kalibrasyon panele özgüdür.** Kamera konumu, cam kalınlığı ve montaj
> toleransı her tahtada farklı; başka panelin blokları doğru hizalama vermez.
> Bu yol bir **kurtarma** adımıdır (bloklar `00`/`0xFF` ise sürücü sessizce
> `OptSetCalibParaDefault`'a düşmüştür), kalıcı çözüm değil — ardından panelin
> kendi kalibrasyon aracıyla yeniden kalibre edilmeli.

---

## Kullanım

```bash
# Sağlam bir tahtada
sudo eta-112.py dokunmatik kalibrasyon oku --cikti saglam.json

# Sorunlu tahtada
sudo eta-112.py dokunmatik kalibrasyon oku --cikti sorunlu.json

# Farkı gör
eta-112.py dokunmatik kalibrasyon karsilastir saglam.json sorunlu.json

# Tek bir komutu elle sorgula (protokol keşfi)
sudo eta-112.py dokunmatik kalibrasyon ham --cmd 0x30 --indeks 1 --uzunluk 59

# Yalnızca aynı panelden alınmış bir yedeği geri yaz
sudo eta-112.py dokunmatik kalibrasyon yaz sorunlu-yedek.json --onayliyorum
```

OTD (2621) panellerde komut kümesi ikiliden türetilmiş ama cihazda
doğrulanmadığı için `--dene` istenir:

```bash
# CCB (bölüm 0x80) + kameraların FCB kayıtlarını oku
sudo eta-112.py dokunmatik kalibrasyon oku --dene

# Hangi komut/indeks çifti yanıt veriyor (silme/yazma komutları taranmaz)
sudo eta-112.py dokunmatik kalibrasyon tara

# Ham depolama dökümü — kayıt sınırlarını görmek için
sudo eta-112.py dokunmatik kalibrasyon depo --bolum 0x80 --blok 0 --blok-sayisi 48
```

Blok yazma yolu (7. bölüm) — önce sına, sonra geri yükle:

```bash
# 1) Yük düzeni varsayımını cihazda sına (hiçbir bayt değişmez)
sudo eta-112.py dokunmatik kalibrasyon yazma-testi --onayliyorum

# 2) Sağlam tahtanın ham blok dökümünü al
sudo eta-112.py dokunmatik kalibrasyon depo --bolum 0x80 --tam --cikti saglam-ccb.json

# 3) Arızalı tahtaya geri yaz (seri bloğu atlanır, her blok geri okunup doğrulanır)
sudo eta-112.py dokunmatik kalibrasyon depo-yaz saglam-ccb.json --onayliyorum
```

`oku` sırasında sunucu servisi geçici olarak durdurulur (cihazı sürekli okuduğu
için kontrol transferleriyle çakışır) ve iş bitince eski durumuna döndürülür.
Anlık görüntüler `0600` izniyle `/var/backups/eta-112-dokunmatik/` altına yazılır.

### Karşılaştırmada ne aranmalı

Mimari notundaki (`mimari.md` §6.1) polinom katsayıları float32 olarak
saklanıyor; `karsilastir` blokları hem bayt hem float olarak gösteriyor.
Özellikle dikkat edilecek:

- **`c1`'in işareti.** Negatifse sürücü x/y eksenlerini takas edip noktaları
  ters sırayla yazıyor. İki tahta arasında yalnızca bu işaret farklıysa
  neden bulunmuş demektir.
- **Bloğun tamamen sıfır veya `0xFF` olması.** EEPROM okunamamış demektir;
  sürücü sessizce `OptSetCalibParaDefault`'a düşmüştür.
- **Kamera/ekran bloklarının farklı olması.** Kalibrasyondan değil, farklı
  panel donanımından kaynaklanır — sürüm denemesiyle çözülmez.
