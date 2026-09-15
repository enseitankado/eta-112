#!/usr/bin/env bash
# Saglam bir OTD tahtasinin depolama dokumunu proje icine REFERANS olarak alir.
#
# Neden: kalibrasyon cihazin EEPROM'unda tutuluyor ve diskte hicbir kopyasi yok.
# Panel bozulur ya da bloklar 0xFF'e donerse geri yuklenecek bir sey olmasi icin
# saglam bir tahtanin dokumu depoda sabit durmali.
#
# Yalniz OKUMA komutlari gonderir (0xb0 bolum bilgisi, 0xb2/n=4 blok oku).
# Yazma (0xb2/n=36) ve silme (0xb1) bu scriptte YOK.
#
#   sudo dokunmatik/araclar/referans-al.sh            # depoya yaz
#   sudo dokunmatik/araclar/referans-al.sh /tmp/xyz   # baska dizine yaz
#
# Dosyalar cagiran kullaniciya devredilir (sudo ile root'a kalmasin).
set -euo pipefail

KOK="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$KOK/eta-112.py"
HEDEF="${1:-$KOK/dokunmatik/referans}"

[ "$(id -u)" = "0" ] || { echo "Bu script 'sudo' ile calistirilmali." >&2; exit 1; }
[ -x "$PY" ] || { echo "eta-112.py bulunamadi: $PY" >&2; exit 1; }

KIMLIK="$(lsusb | grep -o '2621:[0-9a-f]\{4\}' | head -1 || true)"
[ -n "$KIMLIK" ] || { echo "OTD paneli (2621:*) lsusb'de gorunmuyor." >&2; exit 1; }
ONEK="${KIMLIK/:/-}"

mkdir -p "$HEDEF"
echo "Panel: $KIMLIK   ->   $HEDEF"
echo

# CCB (bolum 0x80) -- kalibrasyon katsayilari burada. Kayit tablosunda en uzak
# kayit blok 32 + 512 bayt (17 blok) = 49; 64 blok fazlasiyla yeter. Bolum 4096
# blok bildiriyor ama gerisi 0xFF ve her blok ~100 ms, yani tamamini dokmek
# ~7 dakika ve gereksiz.
echo "[1/6] CCB (bolum 0x80, blok 0-63)"
"$PY" dokunmatik kalibrasyon depo --bolum 0x80 --blok 0 --blok-sayisi 64 \
    --cikti "$HEDEF/$ONEK-ccb.json" >/dev/null

# FCB (bolum 0-3) -- her kameranin seri + parametre kaydi; blok 0-3 kullaniliyor.
for b in 0 1 2 3; do
    echo "[$((b + 2))/6] FCB kamera $b (bolum $b, blok 0-7)"
    "$PY" dokunmatik kalibrasyon depo --bolum "$b" --blok 0 --blok-sayisi 8 \
        --cikti "$HEDEF/$ONEK-fcb$b.json" >/dev/null
done

# Yorumlanmis kayitlar (seri ASCII, float avcisi) -- ham blok degil, okunabilir hali.
echo "[6/6] Yorumlanmis kayit dokumu (kalibrasyon oku)"
"$PY" dokunmatik kalibrasyon oku --dene --cikti "$HEDEF/$ONEK-kayitlar.json" >/dev/null

# sudo ile calistigimiz icin dosyalar root'a ait; cagirana devret.
if [ -n "${SUDO_UID:-}" ]; then
    chown "$SUDO_UID:${SUDO_GID:-$SUDO_UID}" "$HEDEF"/"$ONEK"-*.json
fi
chmod 0644 "$HEDEF"/"$ONEK"-*.json     # referans veri; depoda paylasilacak

echo
echo "Alinan dosyalar:"
ls -l "$HEDEF"/"$ONEK"-*.json | sed 's/^/  /'
echo
echo "Seri numaralari:"
python3 - "$HEDEF/$ONEK-ccb.json" "$HEDEF"/"$ONEK"-fcb*.json <<'PYEOF'
import json, sys
for yol in sys.argv[1:]:
    with open(yol) as f:
        d = json.load(f)
    for bol in d.get("bolumler", []):
        for blok in bol.get("bloklar", []):
            if blok["no"] != 0:
                continue
            ham = bytes.fromhex(blok["veri"])
            if ham[:1] != b"\x01":
                continue
            metin = ham[1:].split(b"\x00")[0].decode("ascii", "replace")
            print("  bolum %-4s %s" % (bol["bolum"], metin))
PYEOF
echo
echo "Simdi depoya isleyin:"
echo "  git add dokunmatik/referans && git commit -m 'Referans: saglam OTD tahtasinin dokumu'"
