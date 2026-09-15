#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eta-112 OTD blok yazma yolu icin sahte cihaz testi.

Kullanim:  python3 dokunmatik/araclar/yazma-yolu-testi.py
Cikis kodu 0 = tum testler gecti. Gercek panele DOKUNMAZ; _k_ioctl'i
bellekteki bir sahte OTD depolamasiyla degistirir.

Gercek panele hic dokunmadan sunlari dogrular:
  * OTD paket bicimi (0x55, saglama, gonderilen uzunluk n+6)
  * yazma yuku duzeni (4 bayt adres + 32 bayt veri = 36)
  * blok adres cozumlemesi (blok//bolen, blok%bolen)
  * 'yazma-testi' akisi: no-op yazma icerigi degistirmiyor
  * 'yazma-testi' yanlis adreslemeyi YAKALIYOR (kasten bozuk sahte cihaz)
  * 'yazma-testi' panel komutu reddedince (STALL) guvenli cikiyor
  * 'depo' -> 'depo-yaz' JSON gidis-donusu
  * 'depo-yaz': seri blogu atlanir, ayni olan blok yazilmaz, geri okuma dogrular
"""
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import contextlib

# Depo kokundeki eta-112.py (bu dosya dokunmatik/araclar/ altinda)
KOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "eta-112.py")

spec = importlib.util.spec_from_file_location("eta112", KOK)
E = importlib.util.module_from_spec(spec)
sys.modules["eta112"] = E
spec.loader.exec_module(E)

BLOK = 32
BOLEN = 16
TOPLAM = 24


class SahteOtd:
    """Bellekte bir OTD depolamasi. yanlis_adres: adreslemeyi kasten bozar."""

    def __init__(self, yanlis_adres=False, yazma_stall=False, erase_gerek=False,
                 toplam=None):
        self.toplam = toplam or TOPLAM
        self.yanlis_adres = yanlis_adres
        self.yazma_stall = yazma_stall
        self.erase_gerek = erase_gerek
        self.bolumler = {}
        for bolum in (0, 1, 2, 3, 0x80):
            bloklar = {}
            for n in range(self.toplam):
                if n == 0:
                    g = b"\x01" + b"OTD-SERI-%02d-%03d" % (bolum & 0xFF, n)
                    bloklar[n] = (g + bytes(BLOK))[:BLOK]
                else:
                    bloklar[n] = bytes(((bolum * 7 + n * 13 + i) & 0xFF)
                                       for i in range(BLOK))
            self.bolumler[bolum] = bloklar
        self.son_set = None
        self.yazma_sayisi = 0
        self.okuma_sayisi = 0
        self.gonderilen_uzunluklar = []
        self.paket_hatalari = []

    # --- paket dogrulama ---------------------------------------------------
    def _paketi_dogrula(self, p, uzunluk):
        if p[0] != 0x55:
            self.paket_hatalari.append(f"baslik 0x{p[0]:02x} (0x55 bekleniyordu)")
        n = p[4]
        bekle = sum(p[2:n + 5]) & 0xFF
        if p[n + 5] != bekle:
            self.paket_hatalari.append(
                f"saglama 0x{p[n + 5]:02x} != 0x{bekle:02x}")
        if uzunluk != n + 6:
            self.paket_hatalari.append(f"uzunluk {uzunluk} != n+6 ({n + 6})")
        return p[1], p[2], p[3], n, bytes(p[5:5 + n])

    def ioctl(self, fd, taban, veri, uzunluk=None):
        if uzunluk is None:
            uzunluk = 0x40
        p = bytes(veri)
        if taban == E.KALIB_SET_REPORT:
            self.gonderilen_uzunluklar.append(uzunluk)
            self.son_set = self._paketi_dogrula(p, uzunluk)
            return p
        # GET: son SET'e gore cevap uret
        cevap = bytearray(0x40)
        cevap[0] = 0x55
        if not self.son_set:
            return bytes(cevap)
        b1, bolum, komut, n, yuk = self.son_set

        if komut == E.KALIB_OTD_BOLUM_BILGI:
            veri_ = struct.pack("<4H", 1, self.toplam, BOLEN, BLOK)
            cevap[1] = E.KALIB_OTD_VERI
            cevap[2] = len(veri_)
            cevap[3:3 + len(veri_)] = veri_
            return bytes(cevap)

        if komut == E.KALIB_OTD_BLOK_OKU and n == 4:
            self.okuma_sayisi += 1
            blok = self._adres(yuk[:4])
            b = self.bolumler.get(bolum, {}).get(blok)
            if b is None:
                raise OSError(32, "Broken pipe")
            cevap[1] = E.KALIB_OTD_VERI
            cevap[2] = len(b)
            cevap[3:3 + len(b)] = b
            return bytes(cevap)

        if komut == E.KALIB_OTD_BLOK_YAZ and n == E.KALIB_OTD_YAZ_YUK:
            if self.yazma_stall:
                raise OSError(32, "Broken pipe")
            blok = self._adres(yuk[:4])
            if self.yanlis_adres:          # adres duzeni varsayimi yanlis olsaydi
                blok = (blok + 1) % self.toplam
            icerik = yuk[4:4 + BLOK]
            hedef = self.bolumler.setdefault(bolum, {})
            if self.erase_gerek:           # NOR flash: silinmeden yazma AND'lenir
                eski = hedef.get(blok, b"\xff" * BLOK)
                icerik = bytes(x & y for x, y in zip(eski, icerik))
            hedef[blok] = icerik
            self.yazma_sayisi += 1
            cevap[1] = E.KALIB_OTD_TAMAM
            return bytes(cevap)

        raise OSError(32, "Broken pipe")   # taninmayan komut -> STALL

    def _adres(self, dort):
        yuksek, alcak = struct.unpack("<HH", dort)
        return yuksek * BOLEN + alcak

    def goruntu(self, bolum=0):
        return dict(self.bolumler[bolum])


@contextlib.contextmanager
def kosum(cihaz, yedek_dizin, cevap="yaz"):
    """eta-112'nin donanima/sisteme dokunan yollarini sahteyle degistir."""
    ilk = {}
    aygit = os.path.join(yedek_dizin, "sahte-OtdUsbRaw000")
    open(aygit, "wb").close()
    yamalar = {
        "_k_ioctl": cihaz.ioctl,
        "_k_aygit_yolu": lambda tip: aygit,
        "_t_kok": lambda: 0,
        "_t_tip_coz": lambda a, islem: ("otd", "2621:4501"),
        "_t_aygit": lambda: ("otd", "2621:4501"),
        "_k_servis_durdur": lambda tip: ("sahte.service", False),
        "_t_run": lambda cmd, inp=None: (0, "", ""),
        "_t_kurulu": lambda: "0.4.0-sahte",
        "ask": lambda prompt="": cevap,
        "TOUCH_YEDEK": yedek_dizin,
        "progress_timed": lambda label, fn, est=30.0: fn(),
    }
    for ad, deger in yamalar.items():
        ilk[ad] = getattr(E, ad)
        setattr(E, ad, deger)
    try:
        yield
    finally:
        for ad, deger in ilk.items():
            setattr(E, ad, deger)


def calistir(argv, cihaz, dizin, cevap="yaz"):
    """etatouch_main'i sahte kosumda calistir. -> (cikis_kodu, cikti)"""
    tampon = io.StringIO()
    with kosum(cihaz, dizin, cevap):
        with contextlib.redirect_stdout(tampon), contextlib.redirect_stderr(tampon):
            try:
                rc = E.etatouch_main(argv)
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 1
    return rc, tampon.getvalue()


# ---------------------------------------------------------------- testler
GECTI = []
KALDI = []


def kontrol(ad, kosul, ek=""):
    (GECTI if kosul else KALDI).append(ad)
    im = "GECTI" if kosul else "KALDI"
    print(f"  [{im}] {ad}" + (f"   {ek}" if ek and not kosul else ""))


def test_paket_bicimi():
    print("\n1) Yazma paketi biçimi ve adres çözümlemesi")
    cihaz = SahteOtd()
    aygit = tempfile.mkdtemp()
    fd = os.open(os.path.join(aygit, "x"), os.O_RDWR | os.O_CREAT)
    eski = E._k_ioctl
    E._k_ioctl = cihaz.ioctl
    try:
        veri = bytes(range(32))
        E._k_otd_blok_yaz(fd, 0, 19, veri, BOLEN)
        b1, bolum, komut, n, yuk = cihaz.son_set
        kontrol("yazma b1 baytı 0x2d", b1 == 0x2d, f"0x{b1:02x}")
        kontrol("yazma komutu 0xb2", komut == 0xb2, f"0x{komut:02x}")
        kontrol("yük uzunluğu 36", n == 36, str(n))
        kontrol("gönderilen uzunluk n+6 = 42",
                cihaz.gonderilen_uzunluklar[-1] == 42,
                str(cihaz.gonderilen_uzunluklar[-1]))
        kontrol("adres = blok//bölen, blok%bölen",
                struct.unpack("<HH", yuk[:4]) == (19 // BOLEN, 19 % BOLEN))
        kontrol("yük[4:36] = blok verisi", yuk[4:] == veri)
        kontrol("blok cihazda güncellendi", cihaz.bolumler[0][19] == veri)
        kontrol("paket doğrulama hatası yok", not cihaz.paket_hatalari,
                str(cihaz.paket_hatalari))
        # yanlis boyut reddedilmeli
        try:
            E._k_otd_blok_yaz(fd, 0, 1, b"kisa", BOLEN)
            kontrol("31/33 baytlık veri reddedilir", False, "istisna atılmadı")
        except ValueError:
            kontrol("31/33 baytlık veri reddedilir", True)
    finally:
        E._k_ioctl = eski
        os.close(fd)


def test_yazma_testi_temiz():
    print("\n2) 'yazma-testi' — sağlam cihaz (no-op gerçekten no-op mu?)")
    cihaz = SahteOtd()
    dizin = tempfile.mkdtemp()
    once = cihaz.goruntu(0)
    rc, cikti = calistir(["kalibrasyon", "yazma-testi", "--onayliyorum"], cihaz, dizin)
    kontrol("çıkış kodu 0", rc == 0, f"rc={rc}\n{cikti[-700:]}")
    kontrol("yazma yolu DOĞRULANDI mesajı", "DOĞRULANDI" in cikti)
    kontrol("hiçbir blok değişmedi", cihaz.goruntu(0) == once)
    kontrol("tam olarak 1 yazma gönderildi", cihaz.yazma_sayisi == 1,
            str(cihaz.yazma_sayisi))
    kontrol("varsayılan test bloğu 4", "/ 4" in cikti or "blok   : 0 / 4" in cikti)
    dosyalar = [f for f in os.listdir(dizin) if f.startswith("yazma-testi-")]
    kontrol("JSON çıktı kaydedildi", len(dosyalar) == 1, str(dosyalar))
    if dosyalar:
        j = json.load(open(os.path.join(dizin, dosyalar[0])))
        kontrol("JSON: yazma kabul edildi", j["yazma"]["kabul"] is True)
        kontrol("JSON: blok korundu", j["blok_korundu"] is True)
        kontrol("JSON: yan etki yok", j["degisen_bloklar"] == [])
        kontrol("JSON: referans döküm tam", len(j["referans"]) == TOPLAM,
                str(len(j["referans"])))
        kontrol("JSON izni 0600",
                oct(os.stat(os.path.join(dizin, dosyalar[0])).st_mode)[-3:] == "600")


def test_yazma_testi_yanlis_adres():
    print("\n3) 'yazma-testi' — adres düzeni YANLIŞ olsaydı yakalar mı?")
    cihaz = SahteOtd(yanlis_adres=True)
    dizin = tempfile.mkdtemp()
    rc, cikti = calistir(["kalibrasyon", "yazma-testi", "--onayliyorum"], cihaz, dizin)
    kontrol("çıkış kodu 1", rc == 1, f"rc={rc}")
    kontrol("YAN ETKİ tespit edildi", "YAN ETKİ" in cikti, cikti[-500:])
    kontrol("değişen blok bildirildi", "blok" in cikti.lower())
    dosyalar = [f for f in os.listdir(dizin) if f.startswith("yazma-testi-")]
    if dosyalar:
        j = json.load(open(os.path.join(dizin, dosyalar[0])))
        kontrol("JSON: değişen blok listelendi", j["degisen_bloklar"] == [5],
                str(j["degisen_bloklar"]))
        kontrol("JSON: referans geri yükleme için saklandı",
                len(j["referans"]) == TOPLAM)


def test_yazma_testi_stall():
    print("\n4) 'yazma-testi' — panel yazmayı reddederse (STALL)")
    cihaz = SahteOtd(yazma_stall=True)
    dizin = tempfile.mkdtemp()
    once = cihaz.goruntu(0)
    rc, cikti = calistir(["kalibrasyon", "yazma-testi", "--onayliyorum"], cihaz, dizin)
    kontrol("çıkış kodu 1", rc == 1, f"rc={rc}")
    kontrol("ÇALIŞMIYOR olarak bildirildi", "ÇALIŞMIYOR" in cikti, cikti[-400:])
    kontrol("panel sağlam mesajı", "hiçbir bayt değişmedi" in cikti)
    kontrol("gerçekten hiçbir blok değişmedi", cihaz.goruntu(0) == once)


def test_yazma_testi_erase():
    print("\n5) 'yazma-testi' — NOR flash (silme gerekiyorsa) no-op yine güvenli mi?")
    cihaz = SahteOtd(erase_gerek=True)
    dizin = tempfile.mkdtemp()
    once = cihaz.goruntu(0)
    rc, cikti = calistir(["kalibrasyon", "yazma-testi", "--onayliyorum"], cihaz, dizin)
    kontrol("x & x = x → içerik korundu", cihaz.goruntu(0) == once)
    kontrol("çıkış kodu 0 (no-op başarılı)", rc == 0, f"rc={rc}")


def test_onay_zorunlu():
    print("\n6) Onay olmadan yazma komutu gönderilmiyor")
    cihaz = SahteOtd()
    dizin = tempfile.mkdtemp()
    rc, cikti = calistir(["kalibrasyon", "yazma-testi"], cihaz, dizin)
    kontrol("--onayliyorum olmadan reddedilir", rc != 0)
    kontrol("hiç yazma gönderilmedi", cihaz.yazma_sayisi == 0,
            str(cihaz.yazma_sayisi))


def test_depo_tam_ve_geri_yazma():
    print("\n7) 'depo --tam' → 'depo-yaz' gidiş-dönüşü")
    cihaz = SahteOtd()
    dizin = tempfile.mkdtemp()
    dokum = os.path.join(dizin, "dokum.json")
    rc, cikti = calistir(["kalibrasyon", "depo", "--bolum", "0", "--tam",
                          "--cikti", dokum], cihaz, dizin)
    kontrol("depo --tam çıkış kodu 0", rc == 0, f"rc={rc}\n{cikti[-400:]}")
    j = json.load(open(dokum))
    kontrol("tüm bloklar döküldü", len(j["bolumler"][0]["bloklar"]) == TOPLAM,
            str(len(j["bolumler"][0]["bloklar"])))
    kontrol("varsayılan pencere korundu (--tam yoksa 8)",
            len(json.loads(open(
                (lambda: (calistir(["kalibrasyon", "depo", "--bolum", "0",
                                    "--cikti", os.path.join(dizin, "k.json")],
                                   cihaz, dizin), os.path.join(dizin, "k.json"))[1])()
            ).read())["bolumler"][0]["bloklar"]) == 8)

    # ayni dokumu geri yaz -> yazacak blok yok
    rc, cikti = calistir(["kalibrasyon", "depo-yaz", dokum, "--onayliyorum"],
                         cihaz, dizin)
    kontrol("aynı döküm: yazılacak blok yok", rc == 0 and "zaten dökümle aynı" in cikti,
            f"rc={rc}\n{cikti[-400:]}")
    kontrol("hiç yazma gönderilmedi", cihaz.yazma_sayisi == 0)

    # cihazi boz, dokumden geri yukle
    bozuk = bytes(32)
    cihaz.bolumler[0][5] = bozuk
    cihaz.bolumler[0][9] = bozuk
    seri_once = cihaz.bolumler[0][0]
    cihaz.bolumler[0][0] = bozuk          # seri blogu da bozuk
    rc, cikti = calistir(["kalibrasyon", "depo-yaz", dokum, "--onayliyorum"],
                         cihaz, dizin)
    kontrol("bozuk bloklar geri yüklendi", rc == 0, f"rc={rc}\n{cikti[-600:]}")
    kontrol("blok 5 düzeltildi",
            cihaz.bolumler[0][5] == bytes.fromhex(j["bolumler"][0]["bloklar"][5]["veri"]))
    kontrol("blok 9 düzeltildi",
            cihaz.bolumler[0][9] == bytes.fromhex(j["bolumler"][0]["bloklar"][9]["veri"]))
    kontrol("SERİ bloğu yazılmadı (varsayılan)", cihaz.bolumler[0][0] == bozuk)
    kontrol("yalnız 2 blok yazıldı", cihaz.yazma_sayisi == 2, str(cihaz.yazma_sayisi))
    kontrol("yazmadan önce yedek alındı",
            any(f.startswith("depo-yazmadan-once-") for f in os.listdir(dizin)))

    # --seri-dahil ile seri de yazilir
    cihaz.yazma_sayisi = 0
    rc, cikti = calistir(["kalibrasyon", "depo-yaz", dokum, "--onayliyorum",
                          "--seri-dahil"], cihaz, dizin)
    kontrol("--seri-dahil: seri bloğu yazıldı",
            cihaz.bolumler[0][0] == seri_once, f"rc={rc}")


def test_depo_yaz_guvenlik():
    print("\n8) 'depo-yaz' güvenlik sınırları")
    cihaz = SahteOtd()
    dizin = tempfile.mkdtemp()
    dokum = os.path.join(dizin, "d.json")
    calistir(["kalibrasyon", "depo", "--bolum", "0", "--tam", "--cikti", dokum],
             cihaz, dizin)

    cihaz.bolumler[0][5] = bytes(32)
    rc, cikti = calistir(["kalibrasyon", "depo-yaz", dokum], cihaz, dizin)
    kontrol("--onayliyorum olmadan reddedilir", rc != 0 and cihaz.yazma_sayisi == 0)

    rc, cikti = calistir(["kalibrasyon", "depo-yaz", dokum, "--onayliyorum"],
                         cihaz, dizin, cevap="iptal")
    kontrol("teyit 'iptal' → hiçbir şey yazılmaz",
            rc == 1 and cihaz.yazma_sayisi == 0 and "İptal" in cikti,
            f"rc={rc} yazma={cihaz.yazma_sayisi}")

    # yanlis bicim
    kotu = os.path.join(dizin, "kotu.json")
    json.dump({"bicim": "eta-112-dokunmatik-kalibrasyon/1", "bloklar": []},
              open(kotu, "w"))
    rc, cikti = calistir(["kalibrasyon", "depo-yaz", kotu, "--onayliyorum"],
                         cihaz, dizin)
    kontrol("kalibrasyon anlık görüntüsü 'depo-yaz'a verilirse reddedilir",
            rc != 0 and "depo" in cikti)

    # yanlis panel tipi
    yanlis = os.path.join(dizin, "optical.json")
    d = json.load(open(dokum))
    d["panel"]["tip"] = "optical"
    json.dump(d, open(yanlis, "w"))
    rc, cikti = calistir(["kalibrasyon", "depo-yaz", yanlis, "--onayliyorum"],
                         cihaz, dizin)
    kontrol("farklı panel tipi reddedilir", rc != 0 and "panel tipine yazılamaz" in cikti)

    # geri okuma uyusmazliginda durur
    cihaz2 = SahteOtd(yanlis_adres=True)
    dizin2 = tempfile.mkdtemp()
    dokum2 = os.path.join(dizin2, "d2.json")
    calistir(["kalibrasyon", "depo", "--bolum", "0", "--tam", "--cikti", dokum2],
             cihaz2, dizin2)
    cihaz2.bolumler[0][5] = bytes(32)
    cihaz2.bolumler[0][9] = bytes(32)
    rc, cikti = calistir(["kalibrasyon", "depo-yaz", dokum2, "--onayliyorum"],
                         cihaz2, dizin2)
    kontrol("geri okuma uyuşmazlığında durur", rc == 1 and "uyuşmadı" in cikti,
            f"rc={rc}\n{cikti[-400:]}")
    kontrol("ilk hatada durdu (2 blok yerine 1 yazma)",
            cihaz2.yazma_sayisi == 1, str(cihaz2.yazma_sayisi))


def test_yazma_testi_ciktisi_geri_yuklenebilir():
    print("\n9) 'yazma-testi' çıktısı geri yükleme için kullanılabilir mi?")
    cihaz = SahteOtd()
    dizin = tempfile.mkdtemp()
    calistir(["kalibrasyon", "yazma-testi", "--onayliyorum"], cihaz, dizin)
    dosya = os.path.join(dizin, [f for f in os.listdir(dizin)
                                 if f.startswith("yazma-testi-")][0])
    cihaz.bolumler[0][7] = bytes(32)
    rc, cikti = calistir(["kalibrasyon", "depo-yaz", dosya, "--onayliyorum"],
                         cihaz, dizin)
    kontrol("yazma-testi referansından geri yükleme çalışır", rc == 0,
            f"rc={rc}\n{cikti[-400:]}")
    kontrol("blok 7 düzeltildi", cihaz.bolumler[0][7] != bytes(32))


def test_eski_komutlar_bozulmadi():
    print("\n10) Mevcut komutlar bozulmadı (geriye uyumluluk)")
    cihaz = SahteOtd()
    dizin = tempfile.mkdtemp()
    rc, cikti = calistir(["kalibrasyon", "depo", "--bolum", "0"], cihaz, dizin)
    kontrol("depo (bayraksız) çalışıyor", rc == 0, f"rc={rc}")
    kontrol("depo hiçbir şey yazmıyor", cihaz.yazma_sayisi == 0)
    rc, cikti = calistir(["kalibrasyon", "tara"], cihaz, dizin)
    kontrol("tara çalışıyor", rc in (0, 1), f"rc={rc}")
    kontrol("tara hiçbir şey yazmıyor", cihaz.yazma_sayisi == 0)
    rc, cikti = calistir(["kalibrasyon", "bilinmeyen-alt-komut"], cihaz, dizin)
    kontrol("bilinmeyen alt komut hata veriyor", rc != 0)
    rc, cikti = calistir(["kalibrasyon", "oku", "--dene",
                          "--cikti", os.path.join(dizin, "oku.json")], cihaz, dizin)
    kontrol("kalibrasyon oku çalışıyor", rc == 0, f"rc={rc}\n{cikti[-300:]}")
    kontrol("oku hiçbir şey yazmıyor", cihaz.yazma_sayisi == 0)


def test_kiyas_penceresi():
    print("\n11) 4096 bloklu gerçekçi bölümde kıyas penceresi sınırlı mı?")
    cihaz = SahteOtd(toplam=4096)
    dizin = tempfile.mkdtemp()
    once = dict(cihaz.bolumler[0])
    rc, cikti = calistir(["kalibrasyon", "yazma-testi", "--onayliyorum"], cihaz, dizin)
    kontrol("çıkış kodu 0", rc == 0, f"rc={rc}\n{cikti[-400:]}")
    kontrol("4096 blok DEĞİL, ~2x64 blok okundu",
            cihaz.okuma_sayisi < 200, str(cihaz.okuma_sayisi))
    kontrol("pencere 64 olarak bildirildi", "blok 0-63" in cikti, cikti[:400])
    kontrol("içerik korundu", dict(cihaz.bolumler[0]) == once)
    dosya = os.path.join(dizin, [f for f in os.listdir(dizin)
                                 if f.startswith("yazma-testi-")][0])
    j = json.load(open(dosya))
    kontrol("JSON: kiyas_penceresi = 64", j.get("kiyas_penceresi") == 64,
            str(j.get("kiyas_penceresi")))
    # --blok-sayisi ile buyutulebilir
    cihaz2 = SahteOtd(toplam=4096)
    rc, cikti = calistir(["kalibrasyon", "yazma-testi", "--onayliyorum",
                          "--blok-sayisi", "128"], cihaz2, tempfile.mkdtemp())
    kontrol("--blok-sayisi penceresi büyütür", "blok 0-127" in cikti, cikti[:300])


for t in (test_paket_bicimi, test_yazma_testi_temiz, test_yazma_testi_yanlis_adres,
          test_yazma_testi_stall, test_yazma_testi_erase, test_onay_zorunlu,
          test_depo_tam_ve_geri_yazma, test_depo_yaz_guvenlik,
          test_yazma_testi_ciktisi_geri_yuklenebilir, test_eski_komutlar_bozulmadi,
          test_kiyas_penceresi):
    try:
        t()
    except Exception as e:
        import traceback
        KALDI.append(f"{t.__name__} (istisna)")
        print(f"  [KALDI] {t.__name__} istisna: {e}")
        traceback.print_exc()

print("\n" + "=" * 60)
print(f"  GECTI: {len(GECTI)}   KALDI: {len(KALDI)}")
for k in KALDI:
    print(f"    - {k}")
sys.exit(1 if KALDI else 0)
