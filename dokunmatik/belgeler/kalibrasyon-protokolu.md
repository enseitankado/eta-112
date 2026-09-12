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
| OTD (2621) komut haritası | — | **Bilinmiyor** |

Sökme işlemi `eta-touchdrv 0.3.6~tbt1` paketindeki `OpticalService` ikilisi
üzerinde yapıldı; o sürüm sembol tablosunu taşıyor (`packageBuild`, `getCommand`,
`setCommand`, `deviceGetFeature`, `OptSetCalibPara` … 105 sembol). Aynı ikili
0.2.0'dan 0.5.3'e kadar hiç değişmedi, dolayısıyla bulgular bütün güncel
sürümler için geçerli.

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

## 6. OTD (2621) — bilinmiyor

Taşıma katmanı aynı (`OtdDrv.h` ile `OpticalDrv.h` aynı ioctl şemasını
kullanıyor), ancak `OtdTouchServer` **statik derlenmiş ve sembolsüz**; komut
haritası çıkarılmadı. `OtdCalibrationTool` sembollü (`OtdSendReport`,
`OtdResetDevice`, `AlgInitialize` …) ve ileride bu boşluğu doldurabilecek en
umut verici hedef.

Şimdilik OTD panellerde `kalibrasyon oku` yalnızca `--dene` ile, Optical komut
kümesini deneyerek çalışır; sonuç anlamsız çıkabilir.

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
