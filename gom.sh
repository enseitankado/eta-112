#!/usr/bin/env bash
# eta-112.py'yi baslat.sh icindeki heredoc blogunun yerine gomer.
#
# baslat.sh, Python kaynagini ETA112_PY_EOF_4F7A isaretcileri arasinda tasir ve
# curl-pipe ile dagitilir. eta-112.py her degistiginde bu script calistirilmali;
# aksi halde depodaki iki kopya birbirinden ayrisir.
#
#   ./gom.sh            gomer
#   ./gom.sh --kontrol  gomulu kopya guncel mi, sadece bildirir (CI icin)
set -euo pipefail

KOK="$(cd "$(dirname "$0")" && pwd)"
PY="$KOK/eta-112.py"
SH="$KOK/baslat.sh"
ISARET="ETA112_PY_EOF_4F7A"

python3 -c "import ast,sys; ast.parse(open('$PY',encoding='utf-8').read())" \
    || { echo "eta-112.py sozdizimi hatali — gomme iptal." >&2; exit 1; }

bas=$(grep -n "<<'$ISARET'\$" "$SH" | cut -d: -f1)
son=$(grep -n "^$ISARET\$" "$SH" | cut -d: -f1)
[ -n "$bas" ] && [ -n "$son" ] || { echo "baslat.sh icinde $ISARET blogu bulunamadi." >&2; exit 1; }

if [ "${1:-}" = "--kontrol" ]; then
    if diff -q <(sed -n "$((bas+1)),$((son-1))p" "$SH") "$PY" >/dev/null; then
        echo "baslat.sh guncel."
        exit 0
    fi
    echo "baslat.sh ESKI — './gom.sh' calistirin." >&2
    exit 1
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
sed -n "1,${bas}p" "$SH" > "$tmp"
cat "$PY"                >> "$tmp"
sed -n "${son},\$p" "$SH" >> "$tmp"
mv "$tmp" "$SH"
chmod +x "$SH"
trap - EXIT

echo "gomuldu: $(wc -l < "$PY") satir Python -> baslat.sh ($(wc -l < "$SH") satir)"
