# Referans panel dökümleri

## Bu dökümün alındığı tahta

| | |
|---|---|
| Durum | **Dokunmatik ve kalibrasyon sağlıklı** (2026-09-15'te elle teyit edildi) |
| Panel | `2621:4501` — OTD, 4 kameralı |
| Makine | `etap-92f896` |
| Sürücü | `eta-touchdrv 0.5.1` (ETAP deposu), `eta-touchdrv.service` aktif |
| Çekirdek modülü | `OtdDrv`, aygıt düğümü `/dev/OtdUsbRaw000` |
| Bölüm düzeni | toplam 4096 blok · bölen 128 · blok boyu 32 bayt |

Bu, projenin **altın kopyası**: başka bir tahtada bir şey şüpheli görünürse
karşılaştırma tarafı burasıdır. Dökümü yenilerken aynı tahtayı kullanın; başka
bir tahtadan alınan döküm bu dosyaların yerine geçmemeli (kalibrasyon panele
özgü — aşağıdaki "Önemli sınır").

---

Sağlam bir OTD (`2621:*`) tahtasının depolama dökümü. **Kalibrasyon cihazın
EEPROM'unda tutuluyor ve diskte hiçbir kopyası yok** — panel bozulur ya da
bloklar `0xFF`'e dönerse geri yüklenecek bir şey olması için sağlam bir tahtanın
dökümü burada sabit duruyor.

## Nasıl alınır

```bash
sudo dokunmatik/araclar/referans-al.sh
```

Yalnız okuma komutları gönderir (`0xb0` bölüm bilgisi, `0xb2`/n=4 blok oku);
yazma ve silme komutları o scriptte yok. ~15 saniye sürer.

## Dosyalar

| Dosya | İçerik |
|---|---|
| `2621-4501-ccb.json` | Bölüm `0x80`, blok 0–63 — **kalibrasyon katsayıları** burada |
| `2621-4501-fcb0..3.json` | Bölüm 0–3, blok 0–7 — dört kameranın seri + parametre kaydı |
| `2621-4501-kayitlar.json` | Yorumlanmış kayıt dökümü (ASCII seri, float avcısı) |

İlk ikisi `eta-112-dokunmatik-depo/1` biçiminde — ham 32 baytlık bloklar,
`depo-yaz` doğrudan bunları geri yazabilir. Üçüncüsü
`eta-112-dokunmatik-kalibrasyon/1` biçiminde ve `karsilastir` ile kullanılır.

Bölüm 4096 blok bildiriyor ama kayıtlar ilk ~49 blokta bitiyor ve gerisi `0xFF`;
her blok ~100 ms okuma demek, bu yüzden döküm pencereleri yukarıdaki gibi
sınırlı tutuldu.

## Nasıl kullanılır

Arızalı bir tahtayı sağlam referansla karşılaştırmak:

```bash
sudo eta-112.py dokunmatik kalibrasyon oku --dene --cikti /tmp/sorunlu.json
eta-112.py dokunmatik kalibrasyon karsilastir \
    dokunmatik/referans/2621-4501-kayitlar.json /tmp/sorunlu.json
```

Blokları geri yüklemek — **önce yazma yolunu sına:**

```bash
sudo eta-112.py dokunmatik kalibrasyon yazma-testi --onayliyorum
sudo eta-112.py dokunmatik kalibrasyon depo-yaz \
    dokunmatik/referans/2621-4501-ccb.json --onayliyorum
```

`depo-yaz` blok 0'ı (ASCII seri / `ProductKey`) varsayılan olarak atlar, yani
referans tahtanın kimliği hedefe kopyalanmaz.

## Önemli sınır

**Kalibrasyon panele özgüdür.** Kamera konumu, cam kalınlığı ve montaj
toleransı her tahtada farklı; bu dosyalar başka bir tahtada *doğru* hizalama
vermez. Değerleri şu iki iş için güvenilir:

1. **Teşhis** — arızalı tahtanın blokları `00`/`0xFF` mi, yoksa makul ama farklı
   mı? (İlki, sürücünün sessizce `OptSetCalibParaDefault`'a düştüğü anlamına
   gelir.)
2. **Kurtarma** — blokları tamamen gitmiş bir panele çalışır bir başlangıç
   noktası yazmak. Ardından panelin kendi kalibrasyon aracıyla yeniden
   kalibre edilmeli.

Protokolün tamamı ve kesinlik dökümü:
[`../belgeler/kalibrasyon-protokolu.md`](../belgeler/kalibrasyon-protokolu.md)
