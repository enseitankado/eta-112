#!/usr/bin/env bash
# Upstream git etiketinden (pardus/eta-touchdrv) .deb yeniden paketler.
# Kullanim: paketle.sh <git-dizini> <etiket> <sablon.deb> <cikti-dizini>
set -euo pipefail

GITDIR="$1"; TAG="$2"; TEMPLATE="$3"; OUTDIR="$4"
cd "$GITDIR"

VER="$(git cat-file -p "$TAG:debian/changelog" | head -1 | sed -E 's/.*\(([^)]+)\).*/\1/')"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
ROOT="$WORK/root"
mkdir -p "$ROOT/usr/bin" "$ROOT/usr/src/eta-touchdrv-$VER/touch2" \
         "$ROOT/usr/src/eta-touchdrv-$VER/touch4" \
         "$ROOT/usr/share/doc/eta-touchdrv" "$ROOT/usr/share/lintian/overrides" \
         "$ROOT/lib/systemd/system" "$ROOT/lib/udev/rules.d" "$WORK/deb/DEBIAN"

g() { git cat-file -p "$TAG:$1" > "$2"; }
has() { git cat-file -e "$TAG:$1" 2>/dev/null; }

# --- debian/rules'a gore yuk (payload)
g touch2/kernelSrc/Makefile     "$ROOT/usr/src/eta-touchdrv-$VER/touch2/Makefile"
g touch2/kernelSrc/OpticalDrv.c "$ROOT/usr/src/eta-touchdrv-$VER/touch2/OpticalDrv.c"
g touch2/kernelSrc/OpticalDrv.h "$ROOT/usr/src/eta-touchdrv-$VER/touch2/OpticalDrv.h"
g touch4/kernel/Makefile        "$ROOT/usr/src/eta-touchdrv-$VER/touch4/Makefile"
g touch4/kernel/OtdDrv.c        "$ROOT/usr/src/eta-touchdrv-$VER/touch4/OtdDrv.c"
g touch4/kernel/OtdDrv.h        "$ROOT/usr/src/eta-touchdrv-$VER/touch4/OtdDrv.h"

g touch2/opticServer/OpticalService        "$ROOT/usr/bin/OpticalService"
g touch2/calibrationTools/calibrationTools "$ROOT/usr/bin/calibrationTools"
g touch4/calibration/OtdCalibrationTool    "$ROOT/usr/bin/OtdCalibrationTool"

if has touch4/otdServer/OtdTouchServer.x86_64; then
    g touch4/otdServer/OtdTouchServer.x86_64 "$ROOT/usr/bin/OtdTouchServer.x86_64"
else
    g touch4/otdServer/OtdTouchServer "$ROOT/usr/bin/OtdTouchServer"
fi

for s in touchdrv_install touchdrv_restart touchdrv_launcher; do
    has "$s" && g "$s" "$ROOT/usr/bin/$s" || true
done

# --- systemd + udev
if has debian/eta-touchdrv@.service; then
    g debian/eta-touchdrv@.service "$ROOT/lib/systemd/system/eta-touchdrv@.service"
else
    g debian/eta-touchdrv.service  "$ROOT/lib/systemd/system/eta-touchdrv.service"
fi
g debian/eta-touchdrv.udev "$ROOT/lib/udev/rules.d/60-eta-touchdrv.rules"

# --- dkms.conf (dh_dkms davranisi: __VERSION__ yerinde birakilir)
g debian/eta-touchdrv.dkms "$ROOT/usr/src/eta-touchdrv-$VER/dkms.conf"

# --- belgeler
g debian/copyright "$ROOT/usr/share/doc/eta-touchdrv/copyright"
g debian/eta-touchdrv.lintian-overrides "$ROOT/usr/share/lintian/overrides/eta-touchdrv"
git cat-file -p "$TAG:debian/changelog" | gzip -9n > "$ROOT/usr/share/doc/eta-touchdrv/changelog.gz"

# --- izinler (debian/rules: sunucular 744, kaynaklar 644)
chmod 755 "$ROOT/usr/bin"/* 2>/dev/null || true
find "$ROOT/usr/src" -type f -exec chmod 644 {} +
find "$ROOT/usr/share" "$ROOT/lib" -type f -exec chmod 644 {} +

# --- DEBIAN: sablon debden al, surumu degistir
TVER="$(dpkg-deb -f "$TEMPLATE" Version)"
TMPCTL="$(mktemp -d)"; dpkg-deb -e "$TEMPLATE" "$TMPCTL"
cp "$TMPCTL"/* "$WORK/deb/DEBIAN/"
rm -rf "$TMPCTL"
rm -f "$WORK/deb/DEBIAN/md5sums"

# surum gecen her yerde degistir (control, postinst, prerm, postrm, preinst)
for f in "$WORK/deb/DEBIAN"/*; do
    [ -f "$f" ] || continue
    sed -i "s/${TVER//./\\.}/$VER/g" "$f"
done
# Installed-Size'i yeniden hesapla
ISIZE="$(du -sk "$ROOT" | cut -f1)"
sed -i "s/^Installed-Size: .*/Installed-Size: $ISIZE/" "$WORK/deb/DEBIAN/control"

# --- birlestir + md5sums
cp -a "$ROOT"/. "$WORK/deb/"
( cd "$WORK/deb" && find . -path ./DEBIAN -prune -o -type f -print0 \
  | sed -z 's|^\./||' | xargs -0 md5sum > DEBIAN/md5sums )

mkdir -p "$OUTDIR"
OUT="$OUTDIR/eta-touchdrv_${VER}_amd64.deb"
dpkg-deb --root-owner-group -b "$WORK/deb" "$OUT" >/dev/null
echo "$OUT"
