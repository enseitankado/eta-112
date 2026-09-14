#!/usr/bin/env python3
"""stdin'deki lzip akisini cozup stdout'a yazar.

Clonezilla imajlarinin bir kismi partclone verisini lzip ile sikistirir
(`*.ptcl-img.lzip.*`). ETAP tahtalarinda `lzip` kurulu degildir ve kurmak root
ister; bu script ayni isi Python'un `lzma` modulu ile yapar.

lzip uyesi:  "LZIP" + surum(1) + sozluk(1) + ham LZMA1 akisi + 20 bayt fuye.
LZMA1 ozellikleri lzip'te sabittir (lc=3, lp=0, pb=2), bu yuzden 13 baytlik
.lzma (FORMAT_ALONE) basligi sentezlenip cozucuye verilebilir. Dosya cok uyeli
olabilir; her uye bittiginde fuye atlanip bastan baslanir.

Kullanim:
    cat img.lzip.a? | lzipcat.py | tr -c '\\40-\\176\\n' '\\n' | grep -a ...
"""
import lzma
import struct
import sys

PROPS = bytes([(2 * 5 + 0) * 9 + 3])          # pb=2, lp=0, lc=3
FUYE = 20                                      # CRC32 + veri boyu + uye boyu


def sozluk(b: int) -> int:
    ds = 1 << (b & 0x1F)
    ds -= (ds // 16) * ((b >> 5) & 7)
    return ds


def main() -> int:
    ham = sys.stdin.buffer
    cik = sys.stdout.buffer
    kalan = b""
    d = None
    while True:
        if d is None:                          # uye basligi bekleniyor
            while len(kalan) < 6:
                p = ham.read(1 << 20)
                if not p:
                    return 0
                kalan += p
            if kalan[:4] != b"LZIP":
                print(f"lzip basligi yok: {kalan[:4]!r}", file=sys.stderr)
                return 1
            d = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
            basa = PROPS + struct.pack("<I", sozluk(kalan[5])) + b"\xff" * 8
            kalan = kalan[6:]
            cik.write(d.decompress(basa))
        if not kalan:
            kalan = ham.read(1 << 20)
            if not kalan:
                return 0
        cik.write(d.decompress(kalan))
        kalan = b""
        if d.eof:
            art = d.unused_data
            while len(art) < FUYE:
                p = ham.read(1 << 20)
                if not p:
                    return 0
                art += p
            kalan = art[FUYE:]
            d = None


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
