#!/usr/bin/env bash
# manifest-uret.py icindeki UPSTREAM tablosunu yeniden uretir.
#
# Resmi .deb'lerdeki ikililer dh_strip'ten gecmis, git etiketinden yeniden
# paketlediklerimiz gecmemistir; ayni programin ozeti bu yuzden tutmaz.
# Karsilastirilabilir tek kaynak upstream deposundaki ham blob'lardir.
#
# Kullanim:
#   git clone https://github.com/pardus/eta-touchdrv /tmp/eta-touchdrv
#   araclar/upstream-ozet.sh /tmp/eta-touchdrv
set -euo pipefail

GITDIR="${1:?kullanim: upstream-ozet.sh <eta-touchdrv-git-dizini>}"
cd "$GITDIR"

o() { [ -n "${2:-}" ] && git cat-file -e "$1:$2" 2>/dev/null \
      && git cat-file -p "$1:$2" | sha256sum | cut -c1-16 || echo "-"; }

# Etikette var olan ilk yolu dondur. 0.2.0'da agac yeniden adlandirildi:
#   opticServer -> OpticalService ·  optictouch.c  -> OpticalDrv.c
#   OtdTouchDriver.c -> OtdDrv.c  ·  OtdTouchServer -> OtdTouchServer.x86_64 (0.3.2+)
# Eski adlar 0.1.x etiketleri icin gerekli; onlar olmadan tablo "-" ile dolar ve
# ayni ikiliyi tasiyan surumler farkli sanilir.
p() {
    local t="$1"; shift
    for c in "$@"; do
        git cat-file -e "$t:$c" 2>/dev/null && { echo "$c"; return; }
    done
    echo ""
}

for t in $(git tag -l 'debian/0.[1-9]*' | sort -V); do
    v="${t#debian/}"; v="${v/_/\~}"
    printf '    "%s": {"otd_sunucu": "%s", "otd_modul": "%s", "optik_sunucu": "%s", "optik_modul": "%s"},\n' \
        "$v" \
        "$(o "$t" "$(p "$t" touch4/otdServer/OtdTouchServer.x86_64 touch4/otdServer/OtdTouchServer)")" \
        "$(o "$t" "$(p "$t" touch4/kernel/OtdDrv.c touch4/kernel/OtdTouchDriver.c)")" \
        "$(o "$t" "$(p "$t" touch2/opticServer/OpticalService touch2/opticServer/opticServer)")" \
        "$(o "$t" "$(p "$t" touch2/kernelSrc/OpticalDrv.c touch2/kernelSrc/optictouch.c)")"
done
