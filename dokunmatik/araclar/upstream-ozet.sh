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

o() { git cat-file -e "$1:$2" 2>/dev/null && git cat-file -p "$1:$2" | sha256sum | cut -c1-16 || echo "-"; }

for t in $(git tag -l 'debian/0.[1-9]*' | sort -V); do
    v="${t#debian/}"; v="${v/_/\~}"
    if git cat-file -e "$t:touch4/otdServer/OtdTouchServer.x86_64" 2>/dev/null; then
        sp=touch4/otdServer/OtdTouchServer.x86_64
    else
        sp=touch4/otdServer/OtdTouchServer
    fi
    printf '    "%s": {"otd_sunucu": "%s", "otd_modul": "%s", "optik_sunucu": "%s", "optik_modul": "%s"},\n' \
        "$v" \
        "$(o "$t" "$sp")" \
        "$(o "$t" touch4/kernel/OtdDrv.c)" \
        "$(o "$t" touch2/opticServer/OpticalService)" \
        "$(o "$t" touch2/kernelSrc/OpticalDrv.c)"
done
