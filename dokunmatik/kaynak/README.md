# Kernel modülü kaynakları

Buradaki dosyalar `paketler/` altındaki `.deb`'lerin içinden çıkarılmış
**kopyalardır** (`usr/src/eta-touchdrv-<sürüm>/touch4/`). Tek amaçları sürümler
arası `diff` almayı kolaylaştırmak; kurulumda kullanılmazlar.

Her biri kaynağı olan paketle bayt bayt aynıdır:

| Dosya | Geldiği paket | Modül nesli |
|---|---|---|
| `OtdTouchDriver-0.1.8.c` | 0.1.8 | — (OtdDrv öncesi) |
| `OtdDrv-0.3.1.c` / `.h` | 0.3.1 (ve 0.3.2) | M3 |
| `OtdDrv-0.3.5.c` / `.h` | 0.3.5 (ve 0.3.3, 0.3.4) | M4 |
| `OtdDrv-0.4.0~beta1.c` / `.h` | 0.4.0~beta1 | M1 |

> **Düzeltme:** Son satırdaki iki dosya arşive `OtdDrv-0.4.0.c` / `.h` adıyla
> gelmişti, ama içerikleri resmi 0.4.0'ın modülü **değil**. Özetleri
> `0.4.0~beta1` paketininkiyle birebir tutuyor ve o da kernel kaynaklarını
> 0.2.0'dan (2020) devralmış. Resmi 0.4.0'ın modülü M5'tir
> (`d349bb857badd283`), buradaki ise M1 (`844eca6074da0f7b`). Karışıklığı
> önlemek için dosyalar yeniden adlandırıldı.

Tam listeyi ve özetleri `../surumler.json` içinde bulabilirsiniz. Herhangi bir
sürümün kaynağını doğrudan paketten almak için:

```bash
dpkg-deb -x paketler/eta-touchdrv_<sürüm>_amd64.deb /tmp/x
ls /tmp/x/usr/src/eta-touchdrv-<sürüm>/touch4/
```
