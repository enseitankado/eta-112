#!/usr/bin/env bash
# Bir ISO / disk imaji icinde eta-touchdrv izlerini arar. root ISTEMEZ.
#
# Neden boyle: ETAP tahtasinda mount(8) icin sudo parolasi gerekir, ama udisks2
# kullanicinin kendi oturumunda dongu aygiti kurup baglamasina izin verir. Ag
# paylasimindaki (gvfs/smb) bir ISO da dogrudan bu yolla baglanabilir; dosyanin
# tamamini indirmek gerekmez, yalnizca okunan bloklar aktarilir.
#
# Iki imaj tipi:
#   canli ISO / ham disk imaji -> live/filesystem.squashfs baglanip dogrudan okunur
#   Clonezilla imaji           -> partclone verisi COZULMEDEN akista taranir;
#                                 dpkg kayitlari ve md5sums satirlari yakalanir
#                                 (gz / xz / zst / lzip destekli)
#
# Kullanim:
#   araclar/iso-tara.sh /yol/imaj.iso
#   araclar/iso-tara.sh "/run/user/1000/gvfs/smb-share:server=X,share=Y/imaj.iso"
set -uo pipefail
IMAJ="${1:?kullanim: iso-tara.sh <iso-veya-img>}"
KOK="$(cd "$(dirname "$0")" && pwd)"

U() { timeout 240 udisksctl "$@" --no-user-interaction 2>&1; }
mpof() { lsblk -no MOUNTPOINT "$1" 2>/dev/null | grep -v '^$' | head -1; }

bagla() {                                      # $1=dosya -> mount noktasi (stdout)
    local L p
    L=$(U loop-setup -r -f "$1" | grep -o '/dev/loop[0-9]*')
    [ -n "$L" ] || return 1
    sleep 2
    for d in "$L" "$L"p1 "$L"p2; do
        [ -b "$d" ] && U mount -b "$d" >/dev/null 2>&1
    done
    sleep 1
    for d in "$L" "$L"p1 "$L"p2; do
        p=$(mpof "$d"); [ -n "$p" ] && { echo "$p"; return 0; }
    done
    return 1
}

ozet() {                                       # $1=kok dizin
    local R="$1" p bid
    echo "  sürüm: $(awk '/^Package: eta-touchdrv$/,/^$/' "$R"/var/lib/dpkg/status \
                     2>/dev/null | awk '/^Version:/{print $2}')"
    for f in OtdTouchServer OtdTouchServer.x86_64 OpticalService opticServer \
             OpticalTouchServer.x86_64 OtdCalibrationTool calibrationTools; do
        p="$R/usr/bin/$f"; [ -e "$p" ] || continue
        bid=$(readelf -n "$p" 2>/dev/null | grep -A1 'Build ID' | tr -d '\n' \
              | grep -o '[0-9a-f]\{40\}')
        printf '  %-26s %8s  sha=%s  bid=%s\n' "$f" "$(stat -c%s "$p")" \
               "$(sha256sum "$p" | cut -c1-16)" "${bid:0:16}"
    done
    find "$R/usr/src" -maxdepth 3 -name '*.c' ! -name '*.mod.c' 2>/dev/null \
        | grep -iE 'otd|optic' | xargs -r sha256sum 2>/dev/null | cut -c1-16,65-
}

# partclone akisini coz: uzantiya gore dogru cozucuyu sec
coz() {
    case "$1" in
        *.gz.*)   zcat ;;
        *.xz.*)   xzcat ;;
        *.zst.*)  zstdcat ;;
        *.lzip.*) python3 "$KOK/lzipcat.py" ;;
        *)        cat ;;
    esac
}

echo "########## $(basename "$IMAJ")"
R=$(bagla "$IMAJ") || { echo "  baglanamadi"; exit 1; }
echo "  baglandi: $R"

for SQ in "$R"/live/*.squashfs "$R"/casper/*.squashfs; do
    [ -e "$SQ" ] || continue
    S=$(bagla "$SQ") || continue
    echo "  squashfs: $(basename "$SQ") -> $S"
    ozet "$S"
done

# Clonezilla imaji: kok dosya sistemini tasiyan en buyuk ptcl-img'i tara
mapfile -t PARCA < <(find "$R/home/partimag" -name '*ptcl-img*' 2>/dev/null \
                     | grep -vE 'vfat|ntfs|swap' | sort)
if [ ${#PARCA[@]} -gt 0 ]; then
    echo "  Clonezilla imaji: ${#PARCA[@]} parca -> akis taramasi"
    PAT='^Package: eta-touchdrv$|^[0-9a-f]{32}  usr/(bin|src)/[^ ]*(Otd|ptic|touchdrv)'
    PAT="$PAT"'|eta-touchdrv_[0-9][^ ]*\.deb|^Module: *eta-touchdrv'
    cat "${PARCA[@]}" | coz "${PARCA[0]}" 2>/dev/null \
        | tr -c '\40-\176\n' '\n' \
        | LC_ALL=C grep -a -A16 -E "$PAT" > /tmp/iso-tara-$$.txt 2>/dev/null
    echo "  kurulu sürüm(ler):"
    awk '/^Package: eta-touchdrv$/{f=1} f&&/^Version:/{print "   ", $2; f=0}' \
        /tmp/iso-tara-$$.txt | sort -u
    echo "  paket dosyalarinin md5sum kayitlari:"
    grep -E '^[0-9a-f]{32}  usr/(bin|src)/[^ ]*(Otd|ptic|touchdrv)' \
        /tmp/iso-tara-$$.txt | sort -u | sed 's/^/    /'
    echo "  ham cikti: /tmp/iso-tara-$$.txt"
fi

echo "  Bitti. Cozmek icin:  lsblk | grep loop  →  udisksctl unmount/loop-delete"
