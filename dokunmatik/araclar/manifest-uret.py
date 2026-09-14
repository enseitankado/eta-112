#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""dokunmatik/surumler.json dosyasini paketlerden uretir.

Her .deb acilir; icindeki sunucu ikilisi, kernel modulu kaynagi, systemd birimi,
udev kurali ve baslatici script'ler kaydedilir.

Denklik (hangi surumleri denemenin ayni sonucu verecegi) paketin icindeki
baytlardan HESAPLANMAZ: resmi paketlerdeki ikililer dh_strip'ten gecmis,
git etiketinden yeniden paketlediklerimiz gecmemistir; ayni program farkli
ozet verir. Bunun yerine upstream deposundaki (github.com/pardus/eta-touchdrv)
ham blob ozetleri tablo olarak tutulur -- tek karsilastirilabilir kaynak odur.

Tabloyu yeniden uretmek icin:
    araclar/upstream-ozet.sh <eta-touchdrv-git-dizini>

Kullanim:  python3 araclar/manifest-uret.py
"""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent

DIZINLER = {
    "paketler": "resmi",
    "varyantlar": "varyant",
    "ucuncu-taraf": "ucuncu-taraf",
}

# Paketin nereden geldigi.
KOKEN = {
    "0.1.7": "git-yeniden-paketleme",
    "0.1.8": "yerel-arsiv",
    "0.2.0": "git-yeniden-paketleme",
    "0.3.0": "git-yeniden-paketleme",
    "0.3.1": "etap-deposu",
    "0.3.2": "etap-deposu",
    "0.3.3": "git-yeniden-paketleme",
    "0.3.4": "etap-deposu",
    "0.3.5": "etap-deposu",
    "0.3.6~tbt1": "git-yeniden-paketleme",
    "0.4.0~beta1": "yerel-arsiv",
    "0.4.0": "yerel-arsiv",
    "0.5.0": "etap-deposu",
    "0.5.1": "etap-deposu",
    "0.5.2": "ucuncu-taraf-surum",
    "0.5.3": "ucuncu-taraf-surum",
    ("varyant", "0.3.1"): "yerel-arsiv",
}

# upstream git etiketlerindeki ham (strip'siz) blob ozetleri -- sha256, ilk 16 hane.
# "-" = o surumde o dosya yok.
UPSTREAM = {
    # 0.1.x agaci farkli adlar kullanir (opticServer / optictouch.c /
    # OtdTouchDriver.c); upstream-ozet.sh artik bu adlari da tarar. Once "-"
    # yazili olan uc alan bu yuzden bostu -- dosyalar yoktu degil, adlari
    # farkliydi.
    "0.1.7":      {"otd_sunucu": "2b5861ca6266fe38", "otd_modul": "513502ee8a57721e",
                   "optik_sunucu": "a9096af09f34cbef", "optik_modul": "41ff9cf8b16abced"},
    "0.1.8":      {"otd_sunucu": "2b5861ca6266fe38", "otd_modul": "513502ee8a57721e",
                   "optik_sunucu": "a9096af09f34cbef", "optik_modul": "026c40205ee38f1f"},
    "0.2.0":      {"otd_sunucu": "d3bd5abd382c9447", "otd_modul": "844eca6074da0f7b",
                   "optik_sunucu": "98f8ee35b8cd7241", "optik_modul": "8e89e58e152799a4"},
    "0.3.0":      {"otd_sunucu": "d3bd5abd382c9447", "otd_modul": "fa24c98856490244",
                   "optik_sunucu": "98f8ee35b8cd7241", "optik_modul": "2d362ae9da92de57"},
    "0.3.1":      {"otd_sunucu": "3fa38f3ab0db5e34", "otd_modul": "4678e40bd9ea2846",
                   "optik_sunucu": "98f8ee35b8cd7241", "optik_modul": "2d362ae9da92de57"},
    "0.3.2":      {"otd_sunucu": "3fa38f3ab0db5e34", "otd_modul": "4678e40bd9ea2846",
                   "optik_sunucu": "98f8ee35b8cd7241", "optik_modul": "2d362ae9da92de57"},
    "0.3.3":      {"otd_sunucu": "3fa38f3ab0db5e34", "otd_modul": "e95ddf7f011483ed",
                   "optik_sunucu": "98f8ee35b8cd7241", "optik_modul": "2e9ea9c7316c187e"},
    "0.3.4":      {"otd_sunucu": "3fa38f3ab0db5e34", "otd_modul": "e95ddf7f011483ed",
                   "optik_sunucu": "98f8ee35b8cd7241", "optik_modul": "2e9ea9c7316c187e"},
    "0.3.5":      {"otd_sunucu": "3fa38f3ab0db5e34", "otd_modul": "e95ddf7f011483ed",
                   "optik_sunucu": "98f8ee35b8cd7241", "optik_modul": "2e9ea9c7316c187e"},
    "0.3.6~tbt1": {"otd_sunucu": "f7b2a63e85c0129d", "otd_modul": "d349bb857badd283",
                   "optik_sunucu": "98f8ee35b8cd7241", "optik_modul": "2e9ea9c7316c187e"},
    # 0.4.0~beta1 upstream'de yok; degerleri paketin kendisinden cikarildi.
    #  * modul kaynaklari metin dosyasi, strip'ten etkilenmez -> dogrudan olculdu,
    #    0.2.0'inkilerle birebir ayni cikti.
    #  * OpticalService, 0.3.1/0.3.2 paketlerindekiyle BAYT BAYT ayni (ce5cd65a...,
    #    35168 bayt) -> onlarin upstream blob'u neyse bu da odur.
    #  * OtdTouchServer dosya ozeti benzersiz (3e7cbe4c..., 88336 bayt) ve bu
    #    yuzden once "tekil-beta1" sayilmisti -- YANLIS. Dosya farki yalnizca
    #    strip'ten geliyor: GNU Build ID'si 0.2.0/0.3.0 ikilisiyle AYNI
    #    (77d9b0323809388d...) ve .text/.rodata/.data/.init/.fini/.plt/.got/
    #    .dynsym/.dynstr bolumlerinin tamami bayt bayt esit. Tek fark 0.2.0'in
    #    debug bolumlerini + symtab'i tasimasi, beta1'in ise strip'lenip
    #    .gnu_debuglink almasi. Ayni programin iki paketlemesi -> ayni blob.
    #    (Dogrulama: readelf -n / objcopy --only-section, 2026-09-13.)
    "0.4.0~beta1": {"otd_sunucu": "d3bd5abd382c9447", "otd_modul": "844eca6074da0f7b",
                    "optik_sunucu": "98f8ee35b8cd7241", "optik_modul": "8e89e58e152799a4"},
    "0.4.0":      {"otd_sunucu": "d665aa827e684e74", "otd_modul": "d349bb857badd283",
                   "optik_sunucu": "98f8ee35b8cd7241", "optik_modul": "2e9ea9c7316c187e"},
    "0.5.0":      {"otd_sunucu": "5b9e38a5d22230e4", "otd_modul": "d349bb857badd283",
                   "optik_sunucu": "98f8ee35b8cd7241", "optik_modul": "2e9ea9c7316c187e"},
    "0.5.1":      {"otd_sunucu": "5b9e38a5d22230e4", "otd_modul": "d349bb857badd283",
                   "optik_sunucu": "98f8ee35b8cd7241", "optik_modul": "2e9ea9c7316c187e"},
}

# Neden denenmeye deger / neye dikkat etmeli.
NOTLAR = {
    "0.1.7": "Pardus ETAP 5.2 ve 2019 tarihli tahta imajlarindan cikti; ETAP deposunda "
             "yok, upstream etiketinden paketlendi. Sunucu ikilileri 0.1.8 ile birebir "
             "AYNI; tek fark touch2 kernel modulu (optictouch.c). 0.1.8 bunu 'phase 1 "
             "ssd edition' icin hizlandirmis: COMMAND_RETRY_COUNT 10->8, msleep 200->50 "
             "ve acilistaki msleep 8000->300. Yavas/kararsiz 2 kameralali panellerde "
             "0.1.7'nin sabirli zamanlamasi denenmeye deger. Guncel cekirdekte DKMS "
             "derlemesi beklenmez.",
    "0.1.8": "ETAP 19 oncesi; kernel modulu OtdTouchDriver.c, optik tarafta opticServer. "
             "Sunucularin ikisi de 0.1.7 ile ayni; 0.1.7'den tek farki hizlandirilmis "
             "touch2 modulu. Guncel cekirdekte DKMS derlemesi buyuk ihtimalle basarisiz olur.",
    "0.2.0": "OtdDrv.c'ye gecis. ETAP 19 icin guncellenen ilk surum.",
    "0.3.0": "Cekirdek 5.10 uyumu, aygit kaydi duzeltmesi.",
    "0.3.1": "AMD islemci duzeltmesi + yeni sunucu blob'u.",
    "0.3.2": "aarch64 blob eklendi; Vestel disi tahtalarda servis artik dongu yapmiyor.",
    "0.3.3": "6.8 sonrasi DKMS derleme duzeltmesi (modul kaynagi degisti).",
    "0.3.4": "Makefile KVER duzeltmesi; sunucu ve modul 0.3.3 ile ayni.",
    "0.3.5": "Servis ve udev sorunlari duzeltildi; touchdrv_restart eklendi. "
             "Sunucu ve modul 0.3.3/0.3.4 ile ayni.",
    "0.3.6~tbt1": "Kernel kaynaklari + touch4 blob'u guncellendi. ETAP deposunda "
                  "yayinlanmadi; yalnizca upstream git etiketinde var.",
    "0.4.0~beta1": "Upstream'de karsiligi olmayan ara derleme; changelog kaydi yalnizca "
                   "'* Test'. Sunucu ikilisi de kernel kaynaklari da 0.2.0/0.3.0 ile "
                   "ayni (sunucu yalnizca strip'lenmis; Build ID 77d9b032... esit). "
                   "Denemeye deger yeni bir ikili tasimiyor; 6.12 cekirdeginde DKMS "
                   "derlemesi de beklenmiyor.",
    "0.4.0": "Yeni nesil sunucu: --ellipse-backend / --verbose / --debug-touch-pipeline "
             "destegi ve ELLIPSE_FITTER, TOUCH_DEBUG_LOG ortam degiskenleri. "
             "ETAP deposunda yayinlanmadi.",
    "0.5.0": "systemd/udev yeniden yazildi: eta-touchdrv@.service sablonu + "
             "touchdrv_launcher. Restart=on-failure eklendi.",
    "0.5.1": "0.5.0 ile ayni ikililer; yalnizca StartLimitIntervalSec konumu duzeltildi.",
    "0.5.2": "Resmi degil (github.com/vrdons/eta-touchdrv catali). Ikili adlari ve "
             "dizin duzeni farkli; tek surucu (OpticalDrv) kullanir.",
    "0.5.3": "Resmi degil (vrdons catali).",
    ("varyant", "0.3.1"): "ETAP kurulum kalibindan cikan, 0.3.1 surum numarasini tasiyan "
                          "ikinci bir derleme. Dosya listesi ve boyutlar depodakiyle ayni, "
                          "sunucu ikilisinin Build ID'si farkli (af045fb2... / 6372a1ee...). "
                          "Ayni kaynagin farkli derlemesi olmasi kuvvetle muhtemel; yine de "
                          "ayri bir deneme adimi sayilir.",
}


# Changelog 0.3.3: "fix dkms build after 6.8.0". Bu duzeltmeyi iceren modul
# kaynaklari; daha eskilerin 6.8+ cekirdekte derlenmesi beklenmez.
GUNCEL_CEKIRDEKTE_DERLENEN = {
    "e95ddf7f011483ed",  # M4 -- 0.3.3, 0.3.4, 0.3.5
    "d349bb857badd283",  # M5 -- 0.3.6~tbt1, 0.4.0, 0.5.0, 0.5.1
}


def ozet(yol: Path) -> str:
    return hashlib.sha256(yol.read_bytes()).hexdigest()


def alan(deb: Path, ad: str) -> str:
    return subprocess.run(
        ["dpkg-deb", "-f", str(deb), ad], capture_output=True, text=True, check=True
    ).stdout.strip()


def changelog_tarihi(kok: Path) -> str:
    clog = kok / "usr/share/doc/eta-touchdrv/changelog.gz"
    if not clog.exists():
        return ""
    metin = subprocess.run(["zcat", str(clog)], capture_output=True, text=True).stdout
    m = re.search(r"^ -- .*>  (.+)$", metin, re.M)
    return m.group(1).strip() if m else ""


def incele(deb: Path, sinif: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        kok = Path(td)
        subprocess.run(["dpkg-deb", "-x", str(deb), str(kok)], check=True)
        surum = alan(deb, "Version")
        ikili = kok / "usr/bin"

        sunucular = sorted(
            p.name for p in ikili.rglob("*")
            if p.is_file() and p.name.startswith(("OtdTouchServer", "OpticalService",
                                                  "opticServer", "OpticalTouchServer"))
        )
        moduller = sorted(
            p.name for p in (kok / "usr/src").rglob("*.c") if p.parent.name != "demo"
        )
        servisler = sorted(p.name for p in kok.rglob("eta-touchdrv*.service"))
        baslaticilar = sorted(p.name for p in ikili.rglob("touchdrv_*") if p.is_file())
        udev = next(iter(sorted(kok.rglob("60-eta-touchdrv.rules"))), None)

        return {
            "surum": surum,
            "sinif": sinif,
            "koken": KOKEN.get((sinif, surum), KOKEN.get(surum, "bilinmiyor")),
            "dosya": str(deb.relative_to(KOK)),
            "boyut": deb.stat().st_size,
            "sha256": ozet(deb),
            "tarih": changelog_tarihi(kok),
            "strip_edilmis": KOKEN.get(surum) != "git-yeniden-paketleme",
            "upstream_blob": UPSTREAM.get(surum),
            "sunucu": sunucular,
            "modul_kaynagi": moduller,
            "servis": servisler,
            "baslatici": baslaticilar,
            "udev_sha256": ozet(udev)[:16] if udev else None,
            "not": NOTLAR.get((sinif, surum), NOTLAR.get(surum, "")),
        }


def surum_anahtari(s: str):
    ana, _, son = s.partition("~")
    return ([int(x) for x in ana.split(".")], son == "", son)


def etiket(k: dict) -> str:
    return k["surum"] if k["sinif"] == "resmi" else f"{k['surum']} ({k['sinif']})"


def kalibrasyon_imzasi(k: dict):
    """Dokunma koordinatini ureten katman: sunucu ikilisi + kernel modulu.

    Upstream'de karsiligi olmayan her paket (varyant derlemeler, ucuncu taraf
    catal) kendi basina bir gruptur -- ayni surum numarasini tasisa bile ayni
    ikili oldugu varsayilamaz."""
    if k["sinif"] != "resmi":
        return ("tekil", k["dosya"])
    u = k["upstream_blob"]
    if not u:
        return ("tekil", k["dosya"])
    return (u["otd_sunucu"], u["otd_modul"], u["optik_sunucu"], u["optik_modul"])


def main() -> int:
    kayitlar = []
    for altdizin, sinif in DIZINLER.items():
        for deb in sorted((KOK / altdizin).glob("*.deb")):
            kayitlar.append(incele(deb, sinif))

    kayitlar.sort(key=lambda k: (k["sinif"] != "resmi", surum_anahtari(k["surum"])))

    gruplar: dict[tuple, list[str]] = {}
    for k in kayitlar:
        gruplar.setdefault(kalibrasyon_imzasi(k), []).append(etiket(k))
    for k in kayitlar:
        k["kalibrasyon_esdegeri"] = [
            e for e in gruplar[kalibrasyon_imzasi(k)] if e != etiket(k)
        ]

    # Nesil etiketleri, surum sirasina gore. Dort ayri seri var; panel tipi
    # hangisine bakacagini belirler:
    #   OTD (2621 / 4 kamera)      sunucu A..    modul M1..
    #   Optical (6615 / 2 kamera)  sunucu a..    modul o1..
    nesiller: dict[str, dict[str, str]] = {a: {} for a in
                                           ("otd_sunucu", "otd_modul",
                                            "optik_sunucu", "optik_modul")}

    def etiketle(alan: str, deger: str) -> str:
        n = len(nesiller[alan])
        return {"otd_sunucu": lambda: chr(ord("A") + n),
                "optik_sunucu": lambda: chr(ord("a") + n),
                "otd_modul": lambda: f"M{n + 1}",
                "optik_modul": lambda: f"o{n + 1}"}[alan]()

    for k in kayitlar:
        u = k["upstream_blob"] if k["sinif"] == "resmi" else None
        for alan in nesiller:
            d = u[alan] if u else None
            if d and d != "-" and d not in nesiller[alan]:
                nesiller[alan][d] = etiketle(alan, d)
    for k in kayitlar:
        u = k["upstream_blob"] if k["sinif"] == "resmi" else None
        s = u["otd_sunucu"] if u else None
        m = u["otd_modul"] if u else None
        k["sunucu_nesli"] = nesiller["otd_sunucu"].get(s or "", "tekil")
        k["modul_nesli"] = nesiller["otd_modul"].get(m or "", "tekil" if m != "-" else "yok")
        for alan, ad in (("optik_sunucu", "optik_sunucu_nesli"),
                         ("optik_modul", "optik_modul_nesli")):
            d = u[alan] if u else None
            k[ad] = nesiller[alan].get(d or "", "tekil" if d != "-" else "yok")
        # Kademe 2 (tam .deb kurulumu) 6.8+ cekirdekte gerceklesebilir mi.
        k["guncel_cekirdekte_derlenir"] = m in GUNCEL_CEKIRDEKTE_DERLENEN
        # Kademe 1 (yalnizca sunucu degis-tokusu): ayni modul nesliyle yayinlanan
        # sunucular birbirinin yerine guvenle konabilir.
        k["kademe1_uyumlu_modul"] = m if m and m != "-" else None

    # Deneme sirasi: kalibrasyon acisindan ayirt edici her gruptan en yeni
    # temsilci, yeniden eskiye. Resmi olmayan catal en sona.
    gorulen = set()
    sira = []
    for k in sorted(
        [x for x in kayitlar if x["sinif"] == "resmi"],
        key=lambda x: surum_anahtari(x["surum"]),
        reverse=True,
    ):
        imza = kalibrasyon_imzasi(k)
        if imza in gorulen:
            continue
        gorulen.add(imza)
        sira.append(k["surum"])

    cikti = {
        "paket": "eta-touchdrv",
        "mimari": "amd64",
        "aciklama": (
            "Pardus ETAP akilli tahta dokunmatik surucu paketleri. Paket; kernel "
            "modulu kaynagini (DKMS), kullanici uzayi sunucu ikilisini, systemd "
            "birimini ve udev kurallarini birlikte tasir. Sistemde bunlarin "
            "disinda dokunmatige ait yapilandirma dosyasi yoktur; kalibrasyon "
            "verisi cihazin EEPROM'unda (ProductKey) tutulur."
        ),
        "deneme_sirasi": sira,
        "surumler": kayitlar,
    }
    (KOK / "surumler.json").write_text(
        json.dumps(cikti, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{len(kayitlar)} paket -> surumler.json")
    print(f"deneme sirasi ({len(sira)} adim):", " -> ".join(sira))
    for k in kayitlar:
        if k["kalibrasyon_esdegeri"]:
            print(f"  {k['surum']:<12} = {', '.join(k['kalibrasyon_esdegeri'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
