#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# eta-112 — Birleşik parola aracı: İşletim sistemi kullanıcı parolası (kps) +
#           AMI Aptio BIOS parolası (etabios).
# Programcı: Özgür Koca <https://ozgurkoca.com>
# Copyright (C) 2026 Özgür Koca. Tamamen özgür yazılım (GNU GPL v3+); HİÇBİR GARANTİ yok.
"""
eta-112 — tek araçta iki işlev:
  * kullanici : İşletim sistemi (Linux) kullanıcı parolasını canlı/çalışan diskte sıfırla.
  * bios      : AMI Aptio BIOS yönetici/kullanıcı parolasını oku / ayarla / temizle.

Kullanım:
  eta-112.py                    -> menü
  eta-112.py kullanici [...]    -> OS kullanıcı parolası (--list, --dry-run, --help)
  eta-112.py bios <komut> [...] -> BIOS (read|set|clear <slot>|info|calibrate|--json)
  eta-112.py --help
"""


# ===================== BÖLÜM 1: OS KULLANICI PAROLASI (kps) =====================
"""
KPS — Çevrimdışı Kullanıcı Parola Sıfırlama Aracı
=================================================
Pardus ETAP / Debian tabanlı kurulumlar için CANLI (live) ortamdan çalışır.

Akış:
  1) (Gerekirse) LVM'i etkinleştirir, LUKS bölümleri için açma teklif eder.
  2) İç disklerdeki Linux kurulumlarını içerik imzasıyla bulur
     (sabit UUID yok -> bu dağıtımı kullanan tüm sistemlerde çalışır).
  3) Birden çok kurulum varsa hangisi olduğunu sorar.
  4) Hedefteki kullanıcıları 5 sütunlu, numaralı ızgarada listeler
     (root(0), etapadmin, ogretmen, ogrenci, sonra diğerleri).
  5) Seçilen hesaba yeni parolayı uygular (hedefin kendi chpasswd'i ile).
  6) Sonucu KRİPTOGRAFİK olarak doğrular, hedefi serbest bırakır.

polkit notu: Giriş parolası PAM + /etc/shadow ile korunur; polkit ayrı bir
parola tutmaz, doğrulamayı aynı Unix parolası üzerinden yapar. shadow'u
güncellemek hem giriş hem polkit istemleri için yeterlidir.
"""

import os
import sys
import json
import shutil
import tempfile
import subprocess
import atexit
import threading    # die() ana iş parçacığında mı diye bakar; aşağıda yeniden import edilir
import warnings

# crypt modülü 3.11+ DeprecationWarning üretir; kullanıcıya gürültü olmasın.
warnings.filterwarnings("ignore", category=DeprecationWarning)

# ------------------------------------------------------------------ crypt (doğrulama için)
# crypt modülü Python 3.13'te kaldırıldı; yoksa libcrypt'e ctypes ile düşeriz.
try:
    import crypt as _crypt

    def do_crypt(pw, salt):
        return _crypt.crypt(pw, salt)
except Exception:  # pragma: no cover
    def do_crypt(pw, salt):
        import ctypes
        import ctypes.util
        name = ctypes.util.find_library("crypt") or "libcrypt.so.1"
        lib = ctypes.CDLL(name, use_errno=True)
        lib.crypt.restype = ctypes.c_char_p
        lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        res = lib.crypt(pw.encode(), salt.encode())
        return res.decode() if res else None


# ------------------------------------------------------------------ UI
class C:
    R = "\033[0m"; B = "\033[1m"; DIM = "\033[2m"; INV = "\033[7m"
    CY = "\033[36m"; GR = "\033[32m"; RD = "\033[31m"; YL = "\033[33m"


if not sys.stdout.isatty():
    for _a in ("R", "B", "DIM", "INV", "CY", "GR", "RD", "YL"):
        setattr(C, _a, "")


def hr():
    print(C.DIM + "─" * 60 + C.R)


def title(t):
    print()
    print(C.CY + C.B + "  " + t + C.R)
    hr()


def ok(m):    print(f"  {C.GR}✓{C.R} {m}")
def warn(m):  print(f"  {C.YL}!{C.R} {m}")
def err(m):   print(f"  {C.RD}✗{C.R} {m}", file=sys.stderr)


class _Die(SystemExit):
    """die() bir yan iş parçacığından çağrıldığında kullanılan taşıyıcı.

    Mesajı hemen basmayız: ilerleme çubuğu aynı satırı 10 Hz'de yeniden çizdiği
    için stderr'e yazılan satır anında eziliyordu. Mesaj burada taşınır ve ana
    iş parçacığında, çubuk temizlendikten sonra basılır."""
    def __init__(self, mesaj, code=1):
        super().__init__(code)
        self.mesaj = mesaj


def die(m, code=1):
    # SystemExit bir BaseException'dir; 'except Exception' onu yakalamaz. Bir yan
    # iş parçacığında sessizce o parçacığı sonlandırır ve çağıran, işin başarılı
    # olduğunu sanıp None ile devam eder. Bu yüzden ana iş parçacığı dışında
    # mesajı taşıyan _Die fırlatılır; progress_timed onu ana parçacıkta yeniden
    # fırlatır.
    if threading.current_thread() is not threading.main_thread():
        raise _Die(m, code)
    err(m)
    sys.exit(code)


# /dev/tty üzerinden etkileşim (curl | bash ile stdin pipe olduğunda da çalışsın)
try:
    _TTY = open("/dev/tty", "r")
except OSError:
    _TTY = sys.stdin


def ask(prompt=""):
    sys.stdout.write(prompt)
    sys.stdout.flush()
    line = _TTY.readline()
    if not line:
        raise EOFError("girdi sonu")
    return line.rstrip("\n")


def ask_pw(prompt):
    import getpass
    try:
        return getpass.getpass(prompt)
    except Exception:
        return ask(prompt)


# ------------------------------------------------------------------ komut çalıştırma
def run(cmd, inp=None, env=None):
    return subprocess.run(
        cmd, input=inp, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


CHROOT_ENV_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


def target_run(target, args, inp=None, extra_env=None):
    """Komutu hedefte çalıştırır: 'running' ise doğrudan çalışan sistemde,
    değilse chroot ile. Ortam değişkenleri argv'de DEĞİL subprocess env= ile
    geçirilir; böylece parola 'ps' çıktısında görünmez."""
    env = {"PATH": CHROOT_ENV_PATH, "LANG": "C", "LC_ALL": "C"}
    if extra_env:
        env.update(extra_env)
    if target.get("running"):
        return run(list(args), inp=inp, env=env)
    return run(["chroot", target["mp"]] + list(args), inp=inp, env=env)


# ------------------------------------------------------------------ temizlik kaydı
_MOUNTS = []     # bizim bağladığımız (mp, tmpdir|None)
_BINDS = []      # chroot bind bağlamaları (dst)
_LUKS = []       # bizim açtığımız luks mapper isimleri


def _cleanup():
    for d in reversed(_BINDS):
        run(["umount", "-l", d])
    _BINDS.clear()
    for mp, tmp in reversed(_MOUNTS):
        run(["umount", mp])
        if tmp and os.path.isdir(tmp):
            try:
                os.rmdir(tmp)
            except OSError:
                pass
    _MOUNTS.clear()
    for name in reversed(_LUKS):
        run(["cryptsetup", "luksClose", name])
    _LUKS.clear()


atexit.register(_cleanup)


# ------------------------------------------------------------------ blok aygıt tarama
LINUX_FS = {"ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "reiserfs", "jfs"}
# Canlı/geçici kök dosya sistemleri (gerçek kurulum değil):
EPHEMERAL_FS = {"overlay", "overlayfs", "squashfs", "aufs", "tmpfs", "ramfs", "rootfs"}


def lsblk_tree():
    p = run(["lsblk", "-J", "-o", "NAME,PATH,TYPE,FSTYPE,MOUNTPOINT,RM,SIZE"])
    if p.returncode != 0 or not p.stdout.strip():
        die("lsblk çalıştırılamadı: " + p.stderr.strip())
    return json.loads(p.stdout).get("blockdevices", [])


def _walk(nodes):
    for n in nodes:
        yield n
        for c in n.get("children", []) or []:
            yield from _walk([c])


def leaves(nodes):
    for n in _walk(nodes):
        if not (n.get("children") or []):
            yield n


# ------------------------------------------------------------------ LVM / LUKS
def activate_lvm():
    if shutil.which("vgchange"):
        run(["vgchange", "-ay"])


def unlock_luks():
    """Kilitli LUKS bölümleri için kullanıcıya açma teklif eder."""
    if not shutil.which("cryptsetup"):
        return
    for n in _walk(lsblk_tree()):
        if n.get("fstype") == "crypto_LUKS" and not (n.get("children") or []):
            dev = n["path"]
            a = ask("  %sŞifreli bölüm:%s %s (%s) — açmak ister misiniz? [e/H] "
                    % (C.YL, C.R, dev, n.get("size", "?")))
            if not a.strip().lower().startswith("e"):
                continue
            name = "kps_" + os.path.basename(dev)
            # cryptsetup parolayı kendi /dev/tty üzerinden istesin (stdin miras alınır;
            # araç '< /dev/tty' ile başlatıldığından bu zaten tty'dir)
            r = subprocess.run(["cryptsetup", "luksOpen", dev, name])
            if r.returncode == 0:
                _LUKS.append(name)
                ok("Açıldı: /dev/mapper/" + name)
            else:
                warn("Açılamadı: " + dev)


# ------------------------------------------------------------------ bağlama
def mount_ro(dev, mp):
    for opt in ("ro", "ro,noload", "ro,norecovery"):
        if run(["mount", "-o", opt, dev, mp]).returncode == 0:
            return True
    return False


def ensure_rw(inst):
    mp = inst["mp"]
    if run(["mount", "-o", "remount,rw", mp]).returncode == 0:
        return True
    if inst["ours"]:
        run(["umount", mp])
        # kayıt güncelle: tmp aynı kalır
        if run(["mount", "-o", "rw", inst["dev"], mp]).returncode == 0:
            return True
    return False


def signature(mp):
    """Bağlı bölüm bir Linux kurulumu mu? Öyleyse PRETTY_NAME döndür."""
    if not all(os.path.isfile(os.path.join(mp, p))
               for p in ("etc/passwd", "etc/shadow", "etc/os-release")):
        return None
    pretty = "Linux"
    try:
        with open(os.path.join(mp, "etc/os-release")) as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    pretty = line.split("=", 1)[1].strip().strip('"')
                    break
    except OSError:
        return None
    return pretty


def discover():
    installs = []
    src = run(["findmnt", "-no", "SOURCE", "/"]).stdout.strip()
    fst = run(["findmnt", "-no", "FSTYPE", "/"]).stdout.strip()
    src_dev = os.path.realpath(src.split("[", 1)[0])  # btrfs '[/@]' soy + kanonik yol

    # CANLI değil de KALICI kurulu sistemin üstünde mi çalışıyoruz?
    # (Geçici kök fs'lerini ele; sadece gerçek kurulumu hedef olarak ekle.)
    if fst not in EPHEMERAL_FS:
        running_sig = signature("/")
        if running_sig:
            installs.append({"dev": src_dev or "(çalışan kök)", "mp": "/",
                             "ours": False, "os": running_sig,
                             "fstype": fst or "?", "size": "?", "running": True})

    for n in leaves(lsblk_tree()):
        if n.get("fstype") not in LINUX_FS:
            continue
        dev = n["path"]
        if os.path.realpath(dev) == src_dev:
            continue  # çalışan/canlı kök; varsa yukarıda ele alındı (kanonik karşılaştırma)
        mp = n.get("mountpoint")
        ours = False
        tmp = None
        if not mp:
            tmp = tempfile.mkdtemp(prefix="kps-")
            if not mount_ro(dev, tmp):
                os.rmdir(tmp)
                continue
            mp = tmp
            ours = True
            _MOUNTS.append((mp, tmp))
        elif mp == "/":
            continue  # çalışan kök, zaten ele alındı
        pretty = signature(mp)
        if pretty:
            installs.append({"dev": dev, "mp": mp, "ours": ours,
                             "os": pretty, "fstype": n.get("fstype"),
                             "size": n.get("size", "?"), "running": False})
        elif ours:
            run(["umount", mp])
            if (mp, tmp) in _MOUNTS:
                _MOUNTS.remove((mp, tmp))
            try:
                os.rmdir(tmp)
            except OSError:
                pass
    return installs


def choose_install(installs):
    if len(installs) == 1:
        return installs[0]
    if _menu_etkilesimli():
        ogeler = [("%s  (%s, %s)%s" % (it["os"], it["dev"], it["size"],
                                       "  ← çalışan sistem" if it.get("running") else ""), "")
                  for it in installs]
        ogeler.append(("İptal", ""))
        i = _secim("Hedef kurulum", ogeler)
        if i is None or i == len(ogeler) - 1:
            die("İptal edildi.", 0)
        return installs[i]
    title("Birden çok kurulum bulundu — hedefi seçin")
    for i, it in enumerate(installs):
        tag = "  %s← çalışan sistem%s" % (C.YL, C.R) if it.get("running") else ""
        print("  %s%2d%s) %-32s %s%s %s%s%s"
              % (C.CY, i, C.R, it["os"], C.DIM, it["dev"], it["size"], C.R, tag))
    hr()
    while True:
        s = ask("  Hedef numarası: ").strip()
        if s.isdigit() and 0 <= int(s) < len(installs):
            return installs[int(s)]
        warn("Geçersiz seçim.")


# ------------------------------------------------------------------ kullanıcılar
PRIORITY = ["root", "etapadmin", "ogretmen", "ogrenci"]


def parse_passwd(mp):
    users = []
    with open(os.path.join(mp, "etc/passwd"), encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            p = line.split(":")
            if len(p) < 7:
                continue
            try:
                uid = int(p[2])
            except ValueError:
                continue
            users.append({"name": p[0], "uid": uid, "gecos": p[4], "shell": p[6]})
    return users


def is_login(u):
    if u["uid"] == 0:
        return True
    if 1000 <= u["uid"] < 65000 and u["name"] != "nobody":
        return True
    return False


def order_users(users):
    by_name = {u["name"]: u for u in users}
    ordered, seen = [], set()
    for p in PRIORITY:
        if p in by_name:
            ordered.append(by_name[p])
            seen.add(p)
    rest = [u for u in users if u["name"] not in seen]
    rest.sort(key=lambda u: (u["uid"], u["name"]))
    ordered += rest
    for i, u in enumerate(ordered):
        u["idx"] = i
    return ordered


def print_grid(users):
    if not users:
        warn("Gösterilecek hesap yok.")
        return
    cols = 5
    cellw = max(len("%2d) %s" % (u["idx"], u["name"])) for u in users) + 2
    for row in range(0, len(users), cols):
        line = ""
        for u in users[row:row + cols]:
            plain = "%2d) %s" % (u["idx"], u["name"])
            padded = plain.ljust(cellw)
            colored = padded.replace("%2d)" % u["idx"],
                                     "%s%2d%s)" % (C.CY, u["idx"], C.R), 1)
            line += colored
        print("  " + line.rstrip())


def select_user(mp):
    all_users = parse_passwd(mp)
    show_all = False
    while True:
        users = order_users(all_users if show_all else
                            [u for u in all_users if is_login(u)])
        if not users:
            if not show_all:        # login görünümü boşsa tüm hesaplara geç
                show_all = True
                continue
            die("Hedefte kullanıcı hesabı bulunamadı.")
        if _menu_etkilesimli():
            ogeler = [("%-16s UID %-6d %s" % (u["name"], u["uid"], u["gecos"]), "")
                      for u in users]
            gecis = "Yalnız girişli hesapları göster" if show_all \
                else "Tüm hesapları göster (sistem hesapları dahil)"
            ogeler.append((gecis, ""))
            ogeler.append(("İptal", ""))
            i = _secim("Hesap seç", ogeler)
            if i is None or i == len(ogeler) - 1:
                die("İptal edildi.", 0)
            if i == len(ogeler) - 2:
                show_all = not show_all
                continue
            return users[i]
        title("Kullanıcı hesapları")
        print_grid(users)
        hr()
        extra = "tum=tüm hesaplar, " if not show_all else "az=sadece girişliler, "
        s = ask("  Sıfırlanacak hesabın numarası (%sq=çık): " % extra).strip().lower()
        if s == "q":
            die("İptal edildi.", 0)
        if s == "tum":
            show_all = True
            continue
        if s == "az":
            show_all = False
            continue
        if s.isdigit() and 0 <= int(s) < len(users):
            return users[int(s)]
        warn("Geçersiz numara.")


# ------------------------------------------------------------------ parola uygula
def bind_chroot(mp):
    for sub in ("dev", "proc", "sys", "run"):
        dst = os.path.join(mp, sub)
        # Güvenilmeyen hedefte mp/dev vb. bir sembolik bağ olabilir; bind onu
        # ana sistemde rastgele bir yere bağlardı -> reddet.
        if os.path.islink(dst):
            die("Güvenlik: hedefte '%s' sembolik bağ; bind reddedildi." % dst)
        os.makedirs(dst, mode=0o700, exist_ok=True)
        if run(["mount", "--bind", "/" + sub, dst]).returncode == 0:
            _BINDS.append(dst)
        else:
            warn("chroot için bağlanamadı: %s" % dst)


def unbind_chroot():
    for d in reversed(_BINDS):
        run(["umount", "-l", d])
    _BINDS.clear()


def apply_password(target, user, pw):
    data = "%s:%s\n" % (user, pw)
    r = target_run(target, ["chpasswd", "-c", "YESCRYPT"], inp=data)
    method = "yescrypt"
    # Eski shadow '-c' seçeneğini tanımıyorsa (rc!=0 veya stderr'de uyarı) düş
    if r.returncode != 0 or "nrecognized" in r.stderr or "nvalid" in r.stderr:
        r = target_run(target, ["chpasswd"], inp=data)
        method = "varsayılan (login.defs)"
        if r.returncode != 0:
            raise RuntimeError("chpasswd başarısız: " + r.stderr.strip())
    # chpasswd 2. alanı tümüyle yeni hash ile değiştirir -> olası '!' kilidi de
    # kalkar. passwd -u/chage yine de güvence için (yoksa zararsızca atlanır).
    target_run(target, ["passwd", "-u", user])          # kilidi aç
    target_run(target, ["chage", "-M", "-1", "-E", "-1", user])  # sona erme temizle
    run(["sync"])  # umount/çıkış öncesi değişikliği diske yaz
    return method


# ------------------------------------------------------------------ doğrulama
def read_shadow(mp, user):
    with open(os.path.join(mp, "etc/shadow"), encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(":")
            if p and p[0] == user:
                return p
    return None


def hash_fmt(h):
    if h[:2] in ("$2",) or h[:3] in ("$2a", "$2b", "$2y"):
        return "bcrypt"
    return {"$y$": "yescrypt", "$7$": "scrypt", "$6$": "SHA512",
            "$5$": "SHA256", "$1$": "MD5"}.get(h[:3], h[:3] + "…")


def chroot_crypt_check(target, user, pw):
    if target_run(target, ["sh", "-c", "command -v python3 >/dev/null"]).returncode != 0:
        return None
    script = (
        "import os,sys\n"
        "try:\n import crypt;cc=crypt.crypt\n"
        "except Exception:\n"
        " import ctypes,ctypes.util\n"
        " l=ctypes.CDLL(ctypes.util.find_library('crypt') or 'libcrypt.so.1')\n"
        " l.crypt.restype=ctypes.c_char_p\n"
        " cc=lambda p,s:(l.crypt(p.encode(),s.encode()) or b'').decode()\n"
        # Parola argv/env'de DEĞİL STDIN ile gelir (/proc/PID/environ sızıntısı yok)
        "u=os.environ['U'];p=sys.stdin.readline().rstrip('\\n');h=None\n"
        "for ln in open('/etc/shadow'):\n"
        " f=ln.split(':')\n"
        " if f[0]==u:h=f[1];break\n"
        "sys.exit(0 if h and cc(p,h)==h else 1)\n"
    )
    r = target_run(target, ["python3", "-W", "ignore", "-c", script],
                   inp=pw + "\n", extra_env={"U": user})
    return r.returncode == 0


def verify(target, user, pw):
    fields = read_shadow(target["mp"], user)
    if not fields:
        return {"ok": False, "msg": "shadow kaydı bulunamadı"}
    h = fields[1]
    res = {"hash_prefix": h[:3] if h else "", "lastchg": fields[2] if len(fields) > 2 else "?"}
    if not h or h[0] in "!*":
        res["ok"] = False
        res["msg"] = "hesap kilitli/parolasız (%r)" % h
        return res
    res["fmt"] = hash_fmt(h)
    # Kriptografik doğrulama: önce canlı libcrypt, olmazsa hedef python3
    cr = None
    try:
        cr = (do_crypt(pw, h) == h)
    except Exception:
        cr = None
    if cr is not True:
        alt = chroot_crypt_check(target, user, pw)
        if alt is not None:
            cr = alt
    res["crypto"] = cr
    s = target_run(target, ["passwd", "-S", user])
    parts = s.stdout.split()
    res["status"] = parts[1] if len(parts) > 1 else "?"
    res["ok"] = (cr is True) or (cr is None and res["status"] == "P")
    return res


# ------------------------------------------------------------------ ana akış
USAGE = """KPS — Kullanıcı Parola Sıfırlama
Kullanım:
  kps.py                 parolayı sıfırla (root gerekir)
  kps.py --dry-run       seç ve planı göster; HİÇBİR ŞEY YAZMA
  kps.py --list          kurulumları ve hesapları listele (salt-okunur)
  kps.py --help          bu yardım
"""


def kps_main(argv):
    args = list(argv)
    if "--help" in args or "-h" in args:
        print(USAGE)
        return
    mode = "apply"
    if "--list" in args or "--liste" in args:
        mode = "list"
    elif "--dry-run" in args or "--kuru" in args:
        mode = "dry"

    if mode == "apply" and os.geteuid() != 0:
        die("Bu araç root olmalı. Canlı ortamda:  curl … | sudo bash")
    if mode != "apply" and os.geteuid() != 0:
        warn("root değilsiniz — bazı diskler bağlanamayabilir, shadow okunamayabilir.")

    print()
    print(C.B + "  KPS — Kullanıcı Parola Sıfırlama" + C.R)
    sub = {"apply": "disklerdeki hesaplar için.", "dry": "KURU ÇALIŞMA (yazma yok).",
           "list": "salt-okunur listeleme."}[mode]
    print(C.DIM + "  Canlı ortamdan VEYA çalışan sistemden; " + sub + C.R)

    activate_lvm()
    if mode == "apply":
        unlock_luks()

    title("Kurulu sistemler aranıyor")
    installs = discover()
    if not installs:
        die("Disklerde Linux kurulumu bulunamadı.")
    for it in installs:
        tag = "  ← çalışan sistem" if it.get("running") else ""
        ok("%s  %s(%s, %s)%s%s" % (it["os"], C.DIM, it["dev"], it["size"], C.R, tag))

    if mode == "list":
        for it in installs:
            title("%s  (%s)" % (it["os"], it["dev"]))
            print_grid(order_users([u for u in parse_passwd(it["mp"]) if is_login(u)]))
        return

    target = choose_install(installs)
    user = select_user(target["mp"])

    if mode == "dry":
        title("Kuru çalışma — yazma YOK")
        print("  Sistem : %s" % target["os"])
        print("  Aygıt  : %s" % target["dev"])
        print("  Hesap  : %s%s%s  (UID %d)" % (C.B, user["name"], C.R, user["uid"]))
        try:
            fields = read_shadow(target["mp"], user["name"])
        except Exception:
            fields = None
        if fields:
            h = fields[1]
            durum = "kilitli/parolasız" if (not h or h[0] in "!*") else \
                    "parolalı (%s)" % hash_fmt(h)
            print("  Mevcut : %s" % durum)
        else:
            print("  Mevcut : (shadow okunamadı — root değil veya erişim yok)")
        hr()
        ok("Kuru çalışma tamam: hiçbir değişiklik yapılmadı.")
        return

    title("Onay")
    print("  Sistem : %s" % target["os"])
    print("  Aygıt  : %s" % target["dev"])
    print("  Hesap  : %s%s%s  (UID %d%s)"
          % (C.B, user["name"], C.R, user["uid"],
             (", " + user["gecos"]) if user["gecos"] else ""))
    hr()
    if ask("  '%s' hesabının parolasını sıfırlamak için EVET yazın: " % user["name"]).strip() != "EVET":
        die("İptal edildi.", 0)

    while True:
        p1 = ask_pw("  Yeni parola: ")
        p2 = ask_pw("  Yeni parola (tekrar): ")
        if not p1:
            warn("Boş olamaz.")
            continue
        if "\n" in p1 or "\r" in p1:
            warn("Parola satır sonu karakteri içeremez.")
            continue
        if p1 != p2:
            warn("Parolalar eşleşmedi.")
            continue
        break

    if not target.get("running"):
        if not ensure_rw(target):
            die("Hedef yazılabilir bağlanamadı (dosya sistemi hatalı olabilir).")
        bind_chroot(target["mp"])
    try:
        method = apply_password(target, user["name"], p1)
        res = verify(target, user["name"], p1)
    finally:
        if not target.get("running"):
            unbind_chroot()

    title("Sonuç")
    print("  Yazım yöntemi : %s" % method)
    if "fmt" in res:
        print("  Hash biçimi   : %s (%s)" % (res.get("fmt"), res.get("hash_prefix")))
    print("  Hesap durumu  : %s" % res.get("status", "?"))
    if res.get("crypto") is True:
        ok("KRİPTOGRAFİK DOĞRULAMA BAŞARILI — yeni parola eşleşiyor.")
    elif res.get("crypto") is False:
        die("KRİPTOGRAFİK DOĞRULAMA BAŞARISIZ — parola hash ile eşleşmiyor!")
    else:
        warn("Kriptografik doğrulama atlandı; hash güncellendi, durum=%s." % res.get("status"))
    if not res.get("ok"):
        die("Doğrulama başarısız: %s" % res.get("msg", "bilinmiyor"))

    # Hedefi serbest bırak (biz bağladıysak)
    _cleanup()
    hr()
    if target.get("running"):
        ok("Tamamlandı. Parola güncellendi; oturumu kapatıp yeni parolayla girin.")
    else:
        ok("Tamamlandı. Hedef sistem serbest bırakıldı; diski çıkarıp normal başlatın.")
    print(C.DIM + "  Not: gnome-keyring eski parolaya bağlıysa ilk girişte ayrıca "
                  "sorulabilir; bu girişi engellemez." + C.R)


# ===================== BÖLÜM 2: BIOS PAROLASI (etabios, GPL-3) =====================
import os, sys, struct, glob, subprocess, argparse, tempfile, threading, time, json
from shutil import which

_JSON = False   # GUI/makine modu: ciktilar JSON, ilerleme/etkilesim kapali

def validate_pw(s, pmin, pmax):
    """GUI/parametre girisi: BUYUK harfe cevirir; yalniz A-Z 0-9; uzunluk. (deger, hata)."""
    u=(s or "").upper()
    if any(not ("A"<=c<="Z" or "0"<=c<="9") for c in u):
        return None, "yalnız A-Z ve 0-9 kullanılabilir"
    if not (pmin<=len(u)<=pmax):
        return None, f"uzunluk {pmin}-{pmax} olmalı"
    return u, None

def emit(obj, code=0):
    """JSON modunda makine-okur cikti yazar."""
    print(json.dumps(obj, ensure_ascii=False))
    return code

# ===================== RENK =====================
_EN = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
def _c(code, s): return f"\033[{code}m{s}\033[0m" if _EN else s
def B(s):  return _c("1", s)
def R(s):  return _c("1;31", s)
def G(s):  return _c("1;32", s)
def Y(s):  return _c("1;33", s)
def Cy(s): return _c("1;36", s)
def D(s):  return _c("2", s)
OK=G("✓"); WARN=Y("⚠"); ERR=R("✗")

# ===================== MODEL PROFILLERI =====================
# Yeni model eklemek: (kart, bios_surum) -> profil. keystream sikistirilmis AMITSE
# modulundedir, dump'tan otomatik cikmaz; surume KILITLIDIR. Once 'calibrate'.
PROFILES = {
    ("14MB24A", "4.6.5"): {
        "model_name": "Faz 1 Vestel Intel (Siyah)",
        "label": "VESTEL 14MB24A / Intel Core i3-2310M, AMI Aptio",
        # Intel Sandy Bridge (HM65): ME bolgesi kilitli -> flashrom --ifd ile yalniz BIOS bolgesi.
        # SPI cip Macronix MX25L320x (4MB); flashrom coklu-eslesir -> -c sart.
        # keystream AMI Aptio AMITSE sabiti (AMD 30 bayt = Intel ilk 30 bayt); 40 baytlik tam dizi.
        "keystream": bytes.fromhex("5b93b62611ba6c4dc7e022747d07d89a332e8ec1e95444e89f7bfa0e55a2b0350bc9665cc1ef1c83"),
        "slot_user": 0x00, "slot_super": 0x28, "slot_len": 40,   # AMITSESetup: user[40]+super[40]+bayrak
        "store_len": 81,                                          # canli dump'tan dogrulandi (datalen=81)
        "store_name": b"AMITSESetup",                            # 81-bayt NVAR'lar coklu -> adla ayikla

        "pw_min": 3, "pw_max": 20,                                # slot 40 bayt = 20 karakter (UTF-16-LE)
        "chip": "MX25L3206E/MX25L3208E", "flash_mode": "ifd",
        "amitse_glob": "/sys/firmware/efi/efivars/AMITSESetup-*",  # legacy: yok -> flashrom
        "setup_glob":  "/sys/firmware/efi/efivars/Setup-ec87d643-*",
        # pwcheck (parola ne zaman sorulsun) BIOS toggle-diff ile bulunur; fiziksel BIOS
        # erisimi gerektigi icin henuz kalibre edilmedi -> 'koruma' destegi kapali.
        "pwcheck_off": None, "pwcheck_opts": {1: "Setup", 2: "Always"},
        "setup_len": (545, 555),                                 # ana Setup NVAR datalen=549
        "active_store_end": 0x220000,                            # iki NVRAM bankasinin sonu
        "banks": [(0x200000, 0x210000), (0x210000, 0x220000)],   # iki NVRAM bankasi (64KB ping-pong)
        # --- MAC adresi (onboard NIC: Realtek RTL8168) ---
        # OUI beyaz listesi: YALNIZ Vestel 00:09:DF (kullanici karari). etapi/all_boards.json'a
        # gore Faz1'in (motherboard_id=7, 60.176 cihaz) %95.87'si 00:09:DF; kalan Elitegroup
        # (F4:4D:30/B8:AE:ED/C0:3F:D5/C8:9C:DC ~%4) + tekil/rastgele kayitlar liste DISI birakildi.
        # MAC, BIOS SPI flash NVRAM'inde (~0x3daee7) tutulur.
        "mac_ouis": {"00:09:DF": "Vestel Elektronik"},
        "verified": "2026-06-24 canli UCTAN UCA dogrulandi (flashrom --ifd + otomatik PNP0C02 unbind): "
                    "read 2357236797B/2357236797C dogru cozdu; set ADMINTEST/USERTEST yazildi+geri-oku "
                    "dogrulandi; clear all temizledi+dogrulandi. store_len=81, slot_len=40, banklar "
                    "0x200000/0x210000, MX25L320x (4MB) -c sart. Erisim: IO_STRICT_DEVMEM RCBA'yi "
                    "(PNP0C02) kapatir -> arac flashrom oncesi system aygitini unbind/rebind eder "
                    "(iomem=relaxed/reboot GEREKMEZ). pwcheck (koruma) bu modelde YOK; davranis ortuk "
                    "(yalniz Yonetici=setup, Kullanici varsa her acilis).",
    },
    ("14MB37C1", "L0.30"): {
        "model_name": "Faz 2 Vestel AMD (Gri)",
        "label": "VESTEL 14MB37C1 / AMD A10-5750M, AMI Aptio",
        "chip": "W25Q64BV/W25Q64CV/W25Q64FV",
        "keystream": bytes.fromhex("5b93b62611ba6c4dc7e022747d07d89a332e8ec1e95444e89f7bfa0e55a2"),
        "slot_user": 0x00, "slot_super": 0x1E, "slot_len": 30,
        "pw_min": 3, "pw_max": 15,   # BIOS IFR: MinSize=0x3, MaxSize=0xF
        "amitse_glob": "/sys/firmware/efi/efivars/AMITSESetup-*",
        "setup_glob":  "/sys/firmware/efi/efivars/Setup-ec87d643-*",
        "pwcheck_off": 0x14D, "pwcheck_opts": {1: "Setup", 2: "Always"},
        "store_len": 61, "setup_len": (330, 345), "flash_mode": "region",
        "active_store_end": 0x20000,
        "banks": [(0x0, 0x20000), (0x30000, 0x50000)],  # iki NVRAM bankası (reclaim ping-pong)
        # MAC OUI beyaz listesi (hazirda): yalniz Vestel 00:09:DF. etapi/all_boards.json'a gore
        # Faz2 AMD (motherboard_id=9, 53.720 cihaz) %99.83 00:09:DF; kalan <%0.2 degisim/gurultu.
        "mac_ouis": {"00:09:DF": "Vestel Elektronik"},
        "verified": "2026-06-19 canli flashrom testleriyle dogrulandi",
    },
    ("14MB57", "4.6.5"): {
        "model_name": "Faz 2 Vestel Intel (Gri)",
        "label": "VESTEL 14MB57 / Intel Core i3-4000M, AMI Aptio",
        # Intel: ME bolgesi kilitli -> flashrom --ifd ile yalniz BIOS bolgesi (opaque, -c yok)
        "chip": None, "flash_mode": "ifd",
        # 40-baytlik keystream: AMD'nin 30 bayti + 10 bayt uzanti (USER3/ADMIN12/2357236797B ile dogrulandi)
        "keystream": bytes.fromhex("5b93b62611ba6c4dc7e022747d07d89a332e8ec1e95444e89f7bfa0e55a2b0350bc9665cc1ef1c83"),
        "slot_user": 0x00, "slot_super": 0x28, "slot_len": 40,
        "store_len": 81,             # AMITSESetup parola blobu 81 bayt (user[40]+super[40]+bayrak[1])
        "pw_min": 3, "pw_max": 20,   # slot 40 bayt = 20 karakter (UTF-16-LE)
        "amitse_glob": "/sys/firmware/efi/efivars/AMITSESetup-*",  # bu makinede yok -> flashrom
        "setup_glob":  "/sys/firmware/efi/efivars/Setup-ec87d643-*",
        "pwcheck_off": 0x49F, "pwcheck_opts": {1: "Setup", 2: "Always"},  # 2026-06-22 toggle-diff
        "setup_len": (1330, 1340),   # Setup NVAR blobu ~1336 bayt
        "active_store_end": 0x440000,
        "banks": [(0x400000, 0x420000), (0x420000, 0x440000)],  # iki NVRAM bankası (bitisik)
        # MAC OUI beyaz listesi (hazirda): yalniz Vestel 00:09:DF. etapi/all_boards.json'a gore
        # Faz2 Intel (motherboard_id=5, 205.399 cihaz) %99.74 00:09:DF; kalan <%0.3 degisim/gurultu.
        "mac_ouis": {"00:09:DF": "Vestel Elektronik"},
        "verified": "2026-06-22 USER3/ADMIN12/2357236797B uc parola ile dogrulandi",
    },
}

# ===================== BAGIMLILIK =====================
def _have(tool):
    return bool(which(tool)) or os.path.exists(f"/usr/sbin/{tool}") or os.path.exists(f"/sbin/{tool}")

def ensure_deps():
    """Gerekli araclar (flashrom, dmidecode) yoksa otomatik kurar."""
    pkgs={"flashrom":"flashrom","dmidecode":"dmidecode"}
    missing=[p for t,p in pkgs.items() if not _have(t)]
    if not missing: return True
    if not _JSON: print(Y(f"  Eksik bagimlilik: {', '.join(missing)} -> kuruluyor..."))
    if os.geteuid()!=0:
        if not _JSON: print(R("  Kurulum icin 'sudo' gerekli."))
        return False
    run_msg("Bağımlılıklar kuruluyor...", lambda: (
        subprocess.run(["apt-get","update"], capture_output=True),
        subprocess.run(["apt-get","install","-y"]+missing, capture_output=True)))
    ok=all(_have(t) for t in pkgs)
    if not _JSON: print(G("  Bagimliliklar hazir.") if ok else R("  Kurulum basarisiz."))
    return ok

# ===================== ILERLEME =====================
def progress_timed(label, fn, est=30.0):
    """Soldan saga determinist ilerleme (est saniye tahminine gore). Islem est'ten
    uzun surerse cubuk %100'de kalir ve saginda 'Bekleyiniz...' gosterilir."""
    if _JSON: return fn()
    if not _EN:
        print(f"  {label}...", flush=True); return fn()
    box={}
    def w():
        # BaseException — 'except Exception' SystemExit'i (die()) ve
        # KeyboardInterrupt'i yakalamaz; yakalanmazsa bu parçacık sessizce ölür,
        # box boş kalır ve çağıran None alıp 'NoneType is not subscriptable' ile
        # çöker. Ne gelirse gelsin kutuya koyup ana parçacıkta yeniden fırlatıyoruz.
        try: box["r"]=fn()
        except BaseException as e: box["e"]=e
    th=threading.Thread(target=w); th.start()
    W=28; t0=time.time()
    while th.is_alive():
        frac=min((time.time()-t0)/est, 1.0); fill=int(frac*W)
        bar=G("█"*fill)+"·"*(W-fill)
        extra=Y("  Bekleyiniz...") if frac>=1.0 else ""
        sys.stdout.write(f"\r  {Cy(label)} [{bar}] {int(frac*100):3d}%{extra} "); sys.stdout.flush()
        time.sleep(0.1)
    th.join()
    if "e" in box:
        # Başarısız işe '100% ✓' basma; satırı temizle ki hata mesajı okunabilsin.
        sys.stdout.write("\r\033[K"); sys.stdout.flush()
        e=box["e"]
        if isinstance(e, _Die): err(e.mesaj)      # die()'nin mesajı burada basılır
        raise e
    sys.stdout.write(f"\r  {Cy(label)} [{G('█'*W)}] 100% {OK} {D(f'{time.time()-t0:.0f}s')}            \n"); sys.stdout.flush()
    return box.get("r")

def run_msg(msg, fn):
    """Ilerleme cubugu olmadan mesaj gosterir, islemi calistirir, sonucu doner."""
    if _JSON: return fn()
    print(f"  {D(msg)}", flush=True)
    return fn()

def read_pw_keys(prompt, pmin, pmax):
    """Parolayi TUS TUS okur; yalniz BUYUK Ingiliz harf/rakam (A-Z 0-9) kabul eder.
    Kucuk harf basilirsa Caps Lock uyarisi verir. Ham metni doner."""
    hint=f"  {prompt} ({pmin}-{pmax}, BÜYÜK harf A-Z 0-9): "
    sys.stdout.write(hint); sys.stdout.flush()
    if not sys.stdin.isatty():
        raw=sys.stdin.readline().rstrip("\n").upper()
        s="".join(c for c in raw if ("A"<=c<="Z" or "0"<=c<="9"))[:pmax]
        print(s); return s
    import termios, tty
    fd=sys.stdin.fileno(); old=termios.tcgetattr(fd); buf=[]; warned=False
    def redraw(): sys.stdout.write("\r\033[K"+hint+"".join(buf)); sys.stdout.flush()
    try:
        tty.setraw(fd)
        while True:
            b=os.read(fd,1)
            if not b: break
            x=b[0]
            if x in (10,13): break
            if x==3: raise KeyboardInterrupt
            if x in (127,8):
                if buf: buf.pop()
                redraw(); continue
            if x<128:
                c=chr(x)
                if ("A"<=c<="Z" or "0"<=c<="9") and len(buf)<pmax:
                    buf.append(c); sys.stdout.write(c); sys.stdout.flush()
                elif "a"<=c<="z":
                    if not warned:
                        sys.stdout.write("\r\n  "+Y("⚠ Küçük harf algılandı — BÜYÜK harf moduna geçin (Caps Lock).")+"\r\n")
                        warned=True; redraw()
                    else:
                        sys.stdout.write("\a"); sys.stdout.flush()
                # diger (Turkce harf, noktalama, kontrol) sessizce yoksayilir
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old); sys.stdout.write("\r\n")
    return "".join(buf)

# ===================== DMI / MODEL =====================
def dmi():
    out={}
    try:
        r=subprocess.run(["dmidecode","-t","bios","-t","baseboard"], capture_output=True, text=True)
        for ln in r.stdout.splitlines():
            s=ln.strip()
            if s.startswith("Version:") and "bios_version" not in out: out["bios_version"]=s.split(":",1)[1].strip()
            elif s.startswith("Vendor:"): out.setdefault("bios_vendor", s.split(":",1)[1].strip())
            elif s.startswith("Product Name:"): out["board"]=s.split(":",1)[1].strip()
            elif s.startswith("Manufacturer:"): out.setdefault("board_mfr", s.split(":",1)[1].strip())
    except FileNotFoundError:
        pass
    return out

def match_profile(d): return PROFILES.get((d.get("board",""), d.get("bios_version","")))

# ===================== flashrom =====================
def _flashbin(): return "/usr/sbin/flashrom" if os.path.exists("/usr/sbin/flashrom") else "flashrom"

def _layout(end):
    """Yalniz 0x0-end bolgesini hedefleyen gecici flashrom layout dosyasi."""
    f=tempfile.NamedTemporaryFile(prefix="etabios_lay_", suffix=".txt", delete=False, mode="w")
    f.write(f"00000000:{end-1:08x} nvram\n"); f.close(); return f.name

# --- Intel /dev/mem kilidi (reboot'suz) ---
# Intel cipsetlerinde STRICT_DEVMEM, SPI denetleyici MMIO'sunu (RCRB) bir cekirdek
# surucusu claim'ledigi icin /dev/mem'i engeller. Asagidaki moduller SPI bolgesini
# claim'ler; gecici kaldirip flashrom'u calistirir ve geri yukleriz (reboot gerekmez).
_INTEL_SPI_MODS = ("iTCO_wdt", "iTCO_vendor_support", "lpc_ich")
def _devmem_blocked(r):
    txt = ((r.stderr or "") + (r.stdout or "")) if r else ""
    return ("mmap failed" in txt) or ("Operation not permitted" in txt) or ("ICH RCRB" in txt)
def _intel_spi_unlock():
    removed=[]
    for m in _INTEL_SPI_MODS:
        if subprocess.run(["modprobe","-r",m], capture_output=True).returncode==0:
            removed.append(m)
    return removed
def _intel_spi_restore(removed):
    for m in reversed(removed):
        subprocess.run(["modprobe", m], capture_output=True)

# --- PNP0C02 (anakart kaynak aygiti) unbind ---
# Bazi BIOS'lar (or. 14MB24A) RCBA/SPI MMIO'sunu bir PNP0C02 'system' aygitina kaynak
# olarak bildirir. IO_STRICT_DEVMEM bu "busy" bolgeyi /dev/mem'e kapatir; modul kaldirmak
# YETMEZ -> PNP aygitini gecici unbind edip flashrom sonrasi geri bind ederiz (reboot yok).
_PNP_SYSDRV = "/sys/bus/pnp/drivers/system"
def _rcrb_addr(r):
    """flashrom hata metninden engellenen MMIO adresini cikar (yoksa tipik RCBA 0xfed1c000)."""
    import re
    txt = ((r.stderr or "") + (r.stdout or "")) if r else ""
    m = re.search(r"RCRB[^0]*0x0*([0-9a-fA-F]+)", txt) or re.search(r"at 0x0*([0-9a-fA-F]+)", txt)
    try: return int(m.group(1), 16) if m else 0xfed1c000
    except (ValueError, AttributeError): return 0xfed1c000
def _pnp_unbind_holding(addr):
    """addr'i mem kaynagi olarak tutan PNP 'system' aygitini unbind eder; dev adini doner."""
    import re
    if not os.path.isdir(_PNP_SYSDRV): return None
    for d in glob.glob("/sys/bus/pnp/devices/*/"):
        try: res = open(os.path.join(d, "resources")).read()
        except OSError: continue
        holds = any((int(m.group(1),16) <= addr <= int(m.group(2),16))
                    for m in re.finditer(r"mem\s+0x([0-9a-fA-F]+)-0x([0-9a-fA-F]+)", res))
        if holds:
            dev = os.path.basename(d.rstrip("/"))
            try:
                with open(os.path.join(_PNP_SYSDRV, "unbind"), "w") as f: f.write(dev)
                return dev
            except OSError: return None
    return None
def _pnp_rebind(dev):
    if not dev: return
    try:
        with open(os.path.join(_PNP_SYSDRV, "bind"), "w") as f: f.write(dev)
    except OSError: pass

def _run_flashrom(cmd, label, show, est):
    """flashrom calistir; /dev/mem engeli (Intel) varsa modulleri kaldir + RCBA'yi tutan
    PNP0C02 aygitini gecici unbind edip yeniden dene; sonra hepsini geri yukle (reboot yok)."""
    runit=lambda: subprocess.run(cmd, capture_output=True, text=True)
    r = progress_timed(label, runit, est) if show else runit()
    if _devmem_blocked(r):
        removed=_intel_spi_unlock()
        pnp=_pnp_unbind_holding(_rcrb_addr(r))   # PNP0C02 RCBA'yi tutuyorsa serbest birak
        if removed or pnp:
            try: r = progress_timed(label, runit, est) if show else runit()
            finally:
                _pnp_rebind(pnp); _intel_spi_restore(removed)
    return r

def flashrom_read(chip, label="Okunuyor", show=True, region_end=None, ifd=False):
    tmp=tempfile.NamedTemporaryFile(prefix="etabios_", suffix=".bin", delete=False).name
    lay=None
    try:
        cmd=[_flashbin(),"-p","internal"]
        if ifd:
            cmd+=(["-c",chip] if chip else [])          # coklu-cip eslesmesinde -c sart ( or. MX25L320x)
            cmd+=["--ifd","-i","bios"]                  # Intel: ME bolgesi kilitli -> yalniz BIOS
        else:
            cmd+=(["-c",chip] if chip else [])
            if region_end: lay=_layout(region_end); cmd+=["--layout",lay,"--image","nvram"]
        cmd+=["-r",tmp]
        est = 25 if ifd else (3 if region_end else 30)
        r=_run_flashrom(cmd, label, show, est)
        if not os.path.exists(tmp) or os.path.getsize(tmp)==0:
            return None, "\n".join((r.stderr or r.stdout).strip().splitlines()[-3:])
        return open(tmp,"rb").read(), None
    finally:
        for f in (tmp,lay):
            if f:
                try: os.remove(f)
                except OSError: pass

def flashrom_write(chip, image_path, show=True, region_end=None, contents_path=None, ifd=False):
    cmd=[_flashbin(),"-p","internal"]
    lay=None
    if ifd:
        # Intel: yalniz BIOS bolgesini yaz; ME/descriptor kilitli oldugundan tum-cip
        # dogrulamasini atla (yazilan bolge yine de dogrulanir).
        cmd+=(["-c",chip] if chip else [])             # coklu-cip eslesmesinde -c sart
        cmd+=["--ifd","-i","bios","--noverify-all"]
    else:
        cmd+=(["-c",chip] if chip else [])
        if region_end:
            # yalniz bolgeyi yaz/dogrula; --flash-contents ile 8MB on-okumayi atla
            lay=_layout(region_end); cmd+=["--layout",lay,"-i","nvram","-N"]
            if contents_path: cmd+=["--flash-contents",contents_path]
    cmd+=["-w",image_path]
    try:
        r=_run_flashrom(cmd, "Yazılıyor", show, 30 if ifd else (6 if region_end else 45))
    finally:
        if lay:
            try: os.remove(lay)
            except OSError: pass
    out=(r.stdout+r.stderr)
    ok=(r.returncode==0) and "FAILED" not in out
    tail="\n".join(l for l in out.splitlines() if any(k in l for k in ("Erase","Writ","Verif","Error","FAILED")))
    return ok, tail

# ===================== NVAR / efivars =====================
def nvar_scan(data, store_len=61):
    # store_len: parola store blob boyutu (AMD=61, Intel 14MB57=81)
    out=[]; n=len(data); NV=b"NVAR"; i=data.find(NV)
    while i!=-1 and i<n-10:
        size=struct.unpack("<H",data[i+4:i+6])[0]
        if 8<size<0x800 and i+size<=n:
            nxt=data[i+6]|(data[i+7]<<8)|(data[i+8]<<16); flags=data[i+9]; name=b""
            if flags&0x02:
                end=data.find(b"\x00",i+11); doff=(end+1)-i if end!=-1 else 10
                name=data[i+11:end] if end!=-1 else b""
            else: doff=10
            if size-doff==store_len:
                out.append({"off":i,"data_off":i+doff,"flags":flags,"next":nxt,"name":name,"blob":data[i+doff:i+doff+store_len]})
            i=data.find(NV, i+size)
        else:
            i=data.find(NV, i+1)
    return out

def nvar_setup_payload(data, active_end, setup_len=(330, 345)):
    lo,hi=setup_len; best=None; n=len(data); NV=b"NVAR"; i=data.find(NV)
    while i!=-1 and i<n-10:
        size=struct.unpack("<H",data[i+4:i+6])[0]
        if 8<size<0x800 and i+size<=n:
            flags=data[i+9]
            if flags&0x02:
                end=data.find(b"\x00",i+11); doff=(end+1)-i if end!=-1 else 10
            else: doff=10
            blob=data[i+doff:i+size]
            if lo<=len(blob)<=hi and i<active_end: best=blob
            i=data.find(NV, i+size)
        else:
            i=data.find(NV, i+1)
    return best

def efivars_amitse(p):
    fs=glob.glob(p["amitse_glob"])
    if not fs: return None
    raw=open(fs[0],"rb").read()[4:]
    sl=p.get("store_len",61)
    return [{"off":0,"data_off":0,"flags":0x88,"next":0xFFFFFF,"name":b"","blob":(raw+b"\x00"*sl)[:sl]}]

def efivars_setup(p):
    fs=glob.glob(p["setup_glob"]); return open(fs[0],"rb").read()[4:] if fs else None

# ===================== KARAKTER / TURKCE-Q =====================
# BIOS parolayi US scancode'una gore saklar; bu cihazlarda Turkce-Q klavye
# kullanildigi icin GORUNTU normallestirilir (US -> Turkce-Q ayni fiziksel tus).
# Görüntü hep BÜYÜK harf (BIOS parolayı büyük harfe çevirerek saklar).
TURKCE_Q = {"'":"İ", '"':"İ", ";":"Ş", ":":"Ş", "[":"Ğ", "{":"Ğ", "]":"Ü", "}":"Ü",
            ",":"Ö", "<":"Ö", ".":"Ç", ">":"Ç", "/":".", "?":":", "\\":",", "|":";"}
# GIRIS icin ters: kullanicinin Turkce-Q karakteri -> BIOS'un US karsiligi.
INV_TURKCE_Q = {"i":"'", "İ":"'", "ı":"i", "ş":";", "Ş":";", "ğ":"[", "Ğ":"[",
                "ü":"]", "Ü":"]", "ö":",", "Ö":",", "ç":".", "Ç":"."}
def trq(s):  return "".join(TURKCE_Q.get(c,c) for c in s) if s else s
def to_bios(pw, maxlen):
    """Kullanici girisini BIOS'un saklayacagi bicime cevirir:
    Turkce-Q -> US fiziksel tus, ardindan BUYUK HARF, sonra uzunluk siniri."""
    s="".join(INV_TURKCE_Q.get(c,c) for c in pw).upper()
    return s[:maxlen]

# ===================== sifre cozme / kodlama =====================
def decode(slot, ks):
    if slot==b"\x00"*len(ks): return None
    return bytes(a^b for a,b in zip(slot,ks)).decode("utf-16-le","replace").rstrip("\x00")
def obf(pw, ks):
    b=pw.encode("utf-16-le"); b=(b+b"\x00"*len(ks))[:len(ks)]
    return bytes(a^c for a,c in zip(b,ks))
def is_clean(s): return s is not None and len(s)>0 and all(32<=ord(c)<127 for c in s)
def _bank_tail(es):
    """Bir bankanın zincir sonu (next=FFFFFF, en yüksek ofset)."""
    if not es: return None
    ts=[e for e in es if e["next"]==0xFFFFFF]
    return max(ts,key=lambda e:e["off"]) if ts else max(es,key=lambda e:e["off"])

def _chain_named(es):
    """Zinciri başlatan isimli AMITSESetup girişi (reclaim anındaki değer)."""
    nm=[e for e in es if e["name"]==b"AMITSESetup" and e["next"]!=0xFFFFFF]
    return min(nm,key=lambda e:e["off"]) if nm else None

def resolve_current(entries, banks):
    """Çift-banka NVRAM'de GERÇEK güncel parolayı bulur. Aktif banka = diğerinin
    zincir-sonundan devam eden (daha yeni reclaim edilmiş) banka."""
    if not banks or len(banks)<2:
        t=_bank_tail(entries); return t["blob"] if t else None
    (a0,a1),(b0,b1)=banks[0],banks[1]
    A=[e for e in entries if a0<=e["off"]<a1]; B=[e for e in entries if b0<=e["off"]<b1]
    tA=_bank_tail(A); tB=_bank_tail(B)
    if tA is None: return tB["blob"] if tB else None
    if tB is None: return tA["blob"]
    nA=_chain_named(A); nB=_chain_named(B)
    if nB and nB["blob"][:60]==tA["blob"][:60]: return tB["blob"]   # B, A'nın son halinden devam -> B güncel
    if nA and nA["blob"][:60]==tB["blob"][:60]: return tA["blob"]   # tersi
    return (tB if tB["off"]>tA["off"] else tA)["blob"]              # yedek: yüksek ofset

# ===================== KAYNAK =====================
def _scan(data, prof):
    return (nvar_scan(data, prof.get("store_len",61)),
            nvar_setup_payload(data, prof["active_store_end"], prof.get("setup_len",(330,345))))

def load_source(prof, dumppath):
    if dumppath:
        data=open(dumppath,"rb").read()
        return (*_scan(data, prof), "dump")
    if os.path.isdir("/sys/firmware/efi"):
        ev=efivars_amitse(prof)
        if ev is not None: return ev, efivars_setup(prof), "efivarfs"
    if os.geteuid()!=0:
        return None, None, "Bu makine icin 'sudo' gerekli."
    if not _JSON: print(f"  {D('Okunuyor...')}", flush=True)
    data,err=flashrom_read(prof["chip"], show=False, region_end=max(hi for lo,hi in prof["banks"]),
                           ifd=(prof.get("flash_mode")=="ifd"))
    if data is None: return None, None, err
    return (*_scan(data, prof), "flashrom")

# ===================== KOMUTLAR =====================
def need_profile():
    ensure_deps()
    d=dmi(); prof=match_profile(d)
    if _JSON: return prof, d
    if prof and prof.get("model_name"):
        print(f"  Model: {G(prof['model_name'])}")
    print(f"  Kart : {Cy(d.get('board_mfr','?'))} {Cy(d.get('board','?'))}")
    print(f"  BIOS : {d.get('bios_vendor','?')} sürüm {Cy(d.get('bios_version','?'))}")
    if prof:
        print(f"  Durum: {OK} {G('DESTEKLENİYOR')}  {D('('+prof['label']+')')}")
        if prof.get("calib_pending"):
            print(f"         {WARN} {Y('YAZMA KALİBRASYON BEKLİYOR')} — okuma açık; set/clear kilitli "
                  + D("(offsetler canlı dump ile doğrulanmalı)."))
    else:
        same=[k for k in PROFILES if k[0]==d.get("board")]
        if same:
            print(f"  Durum: {WARN} {Y('AYNI KART, FARKLI BIOS SÜRÜMÜ')} (destekli: {[k[1] for k in same]})")
            print(D("         keystream/offset farklı olabilir -> 'calibrate' ile doğrula."))
        else:
            print(f"  Durum: {ERR} {R('DESTEKLENMİYOR')} -> işlem yapılmaz.")
    return prof, d

def cmd_info(a):
    prof,d=need_profile()
    if _JSON:
        if not prof:
            return emit({"ok":False,"supported":False,"board":d.get("board"),
                         "bios":d.get("bios_version"),"error":"desteklenmeyen model/sürüm"},1)
        return emit({"ok":True,"supported":True,"model":prof.get("model_name"),
                     "board":d.get("board"),"bios":d.get("bios_version"),"chip":prof["chip"],
                     "pw_min":prof["pw_min"],"pw_max":prof["pw_max"]})
    if prof:
        print(f"  Parola: {prof['pw_min']}-{prof['pw_max']} karakter, BÜYÜK harf")
        print(f"  Çip   : {prof['chip']}")
    return 0 if prof else 1

def cmd_read(a):
    prof,d=need_profile()
    if not prof:
        return emit({"ok":False,"supported":False,"error":"desteklenmeyen model/sürüm"},1) if _JSON else 1
    ents,setup,src=load_source(prof, a.dump)
    if ents is None:
        if _JSON: return emit({"ok":False,"error":str(src)},1)
        print(R("  "+str(src))); return 1
    ents=_pw_filter(ents, prof)
    ks=prof["keystream"]; su,sp,sl=prof["slot_user"],prof["slot_super"],prof["slot_len"]
    cur=resolve_current(ents, prof["banks"])
    du=decode(cur[su:su+sl],ks) if cur else None
    ds=decode(cur[sp:sp+sl],ks) if cur else None
    prev=[]; seen=set()
    for e in sorted(ents, key=lambda e:e["off"]):
        if e["blob"]==cur: continue
        u=decode(e["blob"][su:su+sl],ks); s=decode(e["blob"][sp:sp+sl],ks)
        if not (u or s): continue
        yon=trq(s) if s else None; kul=trq(u) if u else None
        key=(yon or "-", kul or "-")
        if key in seen: continue
        seen.add(key); prev.append((yon,kul))
    prot=None
    pco=prof.get("pwcheck_off")
    if pco is not None and setup and len(setup)>pco and setup[pco] in (1,2):
        prot="always" if setup[pco]==2 else "setup"
    elif pco is None:
        # Bu modelde ayri 'parola ne zaman sorulsun' bayti YOK; davranis hangi parolanin
        # ayarli oldguna gore ortuk: Kullanici varsa her acilis; yalniz Yonetici varsa setup.
        prot="always" if du else ("setup" if ds else None)
    if _JSON:
        return emit({"ok":True,"supported":True,"model":prof.get("model_name"),
                     "board":d.get("board"),"bios":d.get("bios_version"),
                     "supervisor": trq(ds) if ds else None, "user": trq(du) if du else None,
                     "previous":[{"supervisor":y,"user":k} for (y,k) in prev], "protection":prot})
    print(B("\nGüncel parolalar"))
    def show(lbl,raw):
        if raw is None: print(f"  {lbl:<10}: {D('(parola yok)')}")
        elif is_clean(raw): print(f"  {lbl:<10}: {G(trq(raw))}")
        else: print(f"  {lbl:<10}: {R(trq(raw))}  {WARN} {Y('okunamadı (model/sürüm uyumsuz)')}")
    show("Yönetici", ds); show("Kullanıcı", du)
    if prev:
        print(B("\nÖnceki parolalar"))
        for i,(yon,kul) in enumerate(prev,1):
            print(f"  {D(str(i)+'.'):<3} Yönetici: {(yon or '-'):<14} Kullanıcı: {(kul or '-')}")
    if prof.get("pwcheck_off") is None:
        # ortuk davranis (ayar bayti yok): hangi parola ayarliysa ona gore
        kor=(Y("her açılışta sorulur") + D("  (Kullanıcı parolası ayarlı)")) if prot=="always" \
            else (G("yalnızca BIOS ayarlarına girerken sorulur") + D("  (yalnız Yönetici ayarlı)")) if prot=="setup" \
            else D("parola ayarlı değil")
        kor += D("  — bu modelde ayrı 'ne zaman sorulsun' ayarı yoktur")
    else:
        kor=(Y("her açılışta sorulur") if prot=="always" else G("yalnızca BIOS ayarlarına girerken sorulur")) if prot else D("okunamadı")
    print(f"\n  {D('Koruma:')} {kor}")
    return 0

def cmd_calibrate(a):
    prof,d=need_profile()
    pp=prof or PROFILES[next(iter(PROFILES))]
    ents,_,src=load_source(pp, a.dump)
    if ents is None:
        return emit({"ok":False,"error":str(src)},1) if _JSON else (print(R("  "+str(src))) or 1)
    ents=_pw_filter(ents, pp)
    off=pp["slot_user"] if a.slot=="user" else pp["slot_super"]; sl=pp["slot_len"]
    blob=resolve_current(ents, pp["banks"]); cur=blob[off:off+sl] if blob else None
    if not cur or cur==b"\x00"*sl:
        if _JSON: return emit({"ok":False,"error":"slot boş; önce BIOS'tan parola ayarlayın"},1)
        print(R(f"  {a.slot} slotu boş. Önce BIOS'tan bu parolayı ayarlayın.")); return 1
    b=to_bios(a.password, pp["pw_max"]); b=(b.encode("utf-16-le")+b"\x00"*sl)[:sl]
    derived=bytes(x^y for x,y in zip(cur,b))
    matches=bool(prof and derived==prof["keystream"])
    if _JSON: return emit({"ok":True,"keystream":derived.hex(),"matches":matches})
    print(B("\n=== KALİBRASYON ==="))
    print(f"  türetilen keystream: {G(derived.hex())}")
    if matches: print(f"  {OK} {G('Profil ile AYNI')} -> doğru.")
    elif prof:  print(f"  {WARN} {Y('Profil keystreaminden FARKLI')} -> yeni profil olarak ekleyin.")
    return 0

# ----- yazma -----
def _pw_filter(entries, prof):
    """Parola store'unu adiyla ayikla. Bazi kartlarda (or. 14MB24A) store_len=81
    AMITSESetup'a OZGU degil; baska NVAR'lar da ayni boyutta. store_name verilirse
    yalniz o ada sahip girisler donulur (yanlis blob okuma / yanlis yere yazma onlenir).
    store_name yoksa davranis degismez (AMD/Intel profilleri etkilenmez)."""
    nm=prof.get("store_name")
    return [e for e in entries if e["name"]==nm] if nm else entries

def _edit_image(data, prof, edits):
    data=bytearray(data); changes=[]
    for e in _pw_filter(nvar_scan(data, prof.get("store_len",61)), prof):
        if not any(lo<=e["off"]<hi for (lo,hi) in prof["banks"]): continue  # her iki banka
        for off,val in edits:
            a=e["data_off"]+off; old=bytes(data[a:a+prof["slot_len"]])
            if old!=val:
                data[a:a+prof["slot_len"]]=val; changes.append((a,old,val))
    return bytes(data), changes

def _edit_setup_pwcheck(data, prof, value):
    """Setup değişkeninin 'Password Check' baytını HER İKİ bankadaki tüm Setup
    girişlerinde (adlandırılmış 'Setup' + zincir devamı) ayarlar. 1=Setup, 2=Always.
    Bu profilde 330-345 baytlık tüm bloblar Setup'a aittir (boyut çakışması yok)."""
    data=bytearray(data); changes=[]; off=prof["pwcheck_off"]; n=len(data); NV=b"NVAR"
    slo,shi=prof.get("setup_len",(330,345)); i=data.find(NV)
    while i!=-1 and i<n-10:
        size=struct.unpack("<H",data[i+4:i+6])[0]
        if 8<size<0x800 and i+size<=n:
            flags=data[i+9]
            if flags&0x02:
                end=data.find(b"\x00",i+11); doff=(end+1)-i if end!=-1 else 10
            else: doff=10
            if slo<=size-doff<=shi and any(lo<=i<hi for (lo,hi) in prof["banks"]):
                a=i+doff+off
                if i<a<i+size and data[a]!=value:
                    changes.append((a,data[a],value)); data[a]=value
            i=data.find(NV,i+size)
        else:
            i=data.find(NV,i+1)
    return bytes(data), changes

def _write_flow(prof, edits, outpath, pwcheck=None):
    """Yaz (oku+düzenle+yaz) -> geri-oku doğrula. Doner: {ok,changed,error,verified}."""
    if os.geteuid()!=0: return {"ok":False,"changed":False,"error":"sudo gerekli"}
    RE=max(hi for lo,hi in prof["banks"]); sl=prof["slot_len"]
    ifd=(prof.get("flash_mode")=="ifd")
    if not _JSON: print(f"  {D('Yazılıyor...')}", flush=True)
    cur,err=flashrom_read(prof["chip"], show=False, region_end=RE, ifd=ifd)
    if cur is None: return {"ok":False,"changed":False,"error":"okunamadı"}
    if prof.get("store_name"):
        # store_name'li kartlarda yazma hedefi CANLI AMITSESetup NVAR'idir. Parola hic
        # kurulmadiysa bu degisken yoktur (yalniz StdDefaults icinde varsayilan kopya).
        # Bos slota uydurma yazmak yerine net yonlendirme don.
        live=[e for e in _pw_filter(nvar_scan(cur, prof.get("store_len",61)), prof)
              if any(lo<=e["off"]<hi for (lo,hi) in prof["banks"])]
        if not live: return {"ok":False,"changed":False,"error":"no_live_store"}
    new,changes=_edit_image(cur, prof, edits)
    if pwcheck is not None:
        new,pchanges=_edit_setup_pwcheck(new, prof, pwcheck); changes=changes+pchanges
    if not changes: return {"ok":True,"changed":False,"error":None,"verified":True}
    diffs=[i for i in range(len(cur)) if cur[i]!=new[i]]
    if any(not any(lo<=x<hi for (lo,hi) in prof["banks"]) for x in diffs):
        return {"ok":False,"changed":False,"error":"korumalı bölge"}
    # mevcut bolge icerigini referans dosyaya yaz -> flashrom 8MB on-okumayi atlar
    cf=tempfile.NamedTemporaryFile(prefix="etabios_c_",suffix=".bin",delete=False).name
    open(cf,"wb").write(cur)
    if outpath: open(outpath,"wb").write(new); img=outpath; keep=True
    else:
        img=tempfile.NamedTemporaryFile(prefix="etabios_w_",suffix=".bin",delete=False).name
        open(img,"wb").write(new); keep=False
    try: ok,_=flashrom_write(prof["chip"], img, show=False, region_end=RE, contents_path=cf, ifd=ifd)
    finally:
        for f in ([cf] if keep else [cf,img]):
            try: os.remove(f)
            except OSError: pass
    if not ok: return {"ok":False,"changed":False,"error":"yazma başarısız"}
    # geri-oku doğrula
    if not _JSON: print(f"  {D('Doğrulanıyor...')}", flush=True)
    verified=False
    rb,_=flashrom_read(prof["chip"], show=False, region_end=RE, ifd=ifd)
    if rb:
        edok=True
        if edits:
            b=resolve_current(_pw_filter(nvar_scan(rb, prof.get("store_len",61)), prof), prof["banks"])
            edok=bool(b) and all(b[off:off+sl]==val for off,val in edits)
        pwok=True
        if pwcheck is not None:
            sp=nvar_setup_payload(rb, prof["active_store_end"], prof.get("setup_len",(330,345)))
            pwok=bool(sp) and prof.get("pwcheck_off") is not None and len(sp)>prof["pwcheck_off"] and sp[prof["pwcheck_off"]]==pwcheck
        verified=edok and pwok
    return {"ok":True,"changed":True,"error":None,"verified":verified}

def _write_result_print(res):
    if res["error"]=="sudo gerekli": print(R("  Bunun için 'sudo' gerekli."))
    elif res["error"]=="no_live_store":
        print(Y("  BIOS'ta henüz hiç parola kurulmamış (AMITSESetup değişkeni oluşmamış)."))
        print(D("  Önce BIOS setup'a girip herhangi bir parola ayarlayıp kaydedin (F10);"))
        print(D("  değişken oluştuktan sonra bu araçla oku/ayarla/temizle tam çalışır."))
    elif res["error"]:               print(R(f"  İşlem başarısız: {res['error']}."))
    elif not res["changed"]:         print(Y("  Parolalar zaten istenen durumda."))
    elif not res.get("verified", True): print(f"  {WARN} {Y('Yazıldı ama doğrulama tutmadı.')}")
    else: print(f"  {OK} {G('Tamam.')}")

def _calib_guard(prof):
    """calib_pending profillerde YAZMA'yi engeller: offsetler bu kartin canli
    dump'undan dogrulanmadan flash'a yazilmaz (brick riski). Okuma serbesttir."""
    if not prof.get("calib_pending"): return None
    msg=("bu model icin yazma kalibrasyon bekliyor; offsetler canli dump ile "
         "dogrulanmadan flash'a yazilmaz. Once: sudo flashrom ... -r dump.bin "
         "ve 'calibrate' ile profili kesinlestirin")
    if _JSON: return emit({"ok":False,"calib_pending":True,"error":msg},1)
    print(f"  {WARN} {Y(msg)}."); return 1

def cmd_set(a):
    prof,_=need_profile()
    if not prof:
        return emit({"ok":False,"error":"desteklenmeyen model"},1) if _JSON else 1
    g=_calib_guard(prof)
    if g is not None: return g
    ks=prof["keystream"]; pmin,pmax=prof["pw_min"],prof["pw_max"]
    edits=[]; shown={}; pwcheck=None
    yon_arg=getattr(a,"yonetici",None); kul_arg=getattr(a,"kullanici",None)
    kor_arg=getattr(a,"koruma",None)
    if kor_arg: pwcheck={"always":2,"acilis":2,"setup":1}[kor_arg]   # 2=her açılışta, 1=yalnız setup
    if pwcheck is not None and prof.get("pwcheck_off") is None:
        msg="bu modelde 'koruma' (parola ne zaman sorulsun) henüz desteklenmiyor"
        return emit({"ok":False,"error":msg},1) if _JSON else (print(Y("  "+msg)) or 1)
    if _JSON or yon_arg is not None or kul_arg is not None or kor_arg is not None:
        # parametreli (GUI/makine): degerleri dogrula
        for val,key,slot in ((yon_arg,"supervisor",prof["slot_super"]),(kul_arg,"user",prof["slot_user"])):
            if val is None: continue
            v,err=validate_pw(val, pmin, pmax)
            if err:
                if _JSON: return emit({"ok":False,"error":f"{key}: {err}"},1)
                print(R(f"  {key}: {err}")); return 1
            edits.append((slot, obf(v, ks))); shown[key]=v
        if not edits and pwcheck is None:
            if _JSON: return emit({"ok":False,"error":"parola/koruma verilmedi"},1)
            print(Y("  Parola/koruma verilmedi.")); return 0
    else:
        # etkilesimli (tus tus, BUYUK harf)
        print(B("\nParola ayarla ")+D("(boş bırakırsan o parola değişmez)"))
        if prof.get("pwcheck_off") is None:
            # Bu modelde "ne zaman sorulsun" (setup/always) BIOS secenegi YOK; davranis
            # hangi parolayi ayarladigina gore belirlenir. Kullaniciya kisaca hatirlat.
            print(D("  Hangi parolayı ayarladığın, parolanın ne zaman sorulacağını belirler:"))
            print(D("    • ")+Cy("Yönetici")+D(" — yalnız BIOS ayarlarına girişi korur (sistem normal açılır)"))
            print(D("    • ")+Cy("Kullanıcı")+D(" — her açılışta sorulur (sistemi açılışta kilitler)"))
        for lbl,key,slot in (("Yönetici","supervisor",prof["slot_super"]),("Kullanıcı","user",prof["slot_user"])):
            raw=read_pw_keys(f"{lbl} parolası", pmin, pmax)
            if not raw: continue
            if len(raw)<pmin: print(R(f"    En az {pmin} karakter olmalı; atlandı.")); continue
            v=to_bios(raw, pmax); edits.append((slot, obf(v, ks))); shown[key]=trq(v)
        if "supervisor" in shown and prof.get("pwcheck_off") is not None:
            print(D("\n  Yönetici parolası ne zaman sorulsun? ")+D("(boş = değiştirme)"))
            print("    1) "+G("Her açılışta"))
            print("    2) "+G("Yalnız BIOS ayarlarına girerken"))
            try: kk=input("  Seçim [1/2]: ").strip()
            except EOFError: kk=""
            pwcheck={"1":2,"2":1}.get(kk)
        if not edits and pwcheck is None: print(Y("  Parola girilmedi.")); return 0
        print()
        if "supervisor" in shown: print(f"  Yönetici : {G(shown['supervisor'])}")
        if "user" in shown:       print(f"  Kullanıcı: {G(shown['user'])}")
        if pwcheck is not None:
            print(f"  Sorulma  : {G('her açılışta' if pwcheck==2 else 'yalnız BIOS ayarlarına girerken')}")
        try: ans=input(f"\n  {Y('Yazmak istiyor musunuz?')} (e/h): ").strip().lower()
        except EOFError: ans=""
        if ans not in ("e","evet"):
            print(Y("  İptal edildi.")); return 0
    res=_write_flow(prof, edits, a.out, pwcheck=pwcheck)
    prot=None if pwcheck is None else ("always" if pwcheck==2 else "setup")  # read --json ile aynı sözleşme
    if _JSON:
        return emit({"ok":res["ok"],"changed":res["changed"],"error":res["error"],
                     "verified":res.get("verified"),
                     "supervisor":shown.get("supervisor"),"user":shown.get("user"),
                     "protection":prot},
                    0 if res["ok"] else 1)
    _write_result_print(res); return 0 if res["ok"] else 1

def cmd_clear(a):
    prof,_=need_profile()
    if not prof:
        return emit({"ok":False,"error":"desteklenmeyen model"},1) if _JSON else 1
    g=_calib_guard(prof)
    if g is not None: return g
    z=b"\x00"*prof["slot_len"]
    edits={"all":[(prof["slot_user"],z),(prof["slot_super"],z)],
           "kullanici":[(prof["slot_user"],z)], "yonetici":[(prof["slot_super"],z)]}[a.slot]
    res=_write_flow(prof, edits, a.out)
    if _JSON:
        return emit({"ok":res["ok"],"changed":res["changed"],"error":res["error"],"verified":res.get("verified")}, 0 if res["ok"] else 1)
    _write_result_print(res); return 0 if res["ok"] else 1

# ===================== CLI =====================
class _BiosHelp(argparse.RawDescriptionHelpFormatter):
    """Yardım: komut kolonunu genişletir (calibrate alt satıra kaymaz);
    açıklama/epilog ham (renkli) kalır."""
    def __init__(self, prog):
        super().__init__(prog, max_help_position=30)
    def add_argument(self, action):
        super().add_argument(action)
        # argparse alt komutlari (info/read/calibrate...) kolon uzunluguna katmaz ->
        # 'calibrate' yardimi alt satira kayar. Bunlari da hesaba kat.
        try:
            for sub in self._iter_indented_subactions(action):
                inv=self._format_action_invocation(sub)
                self._action_max_length=max(self._action_max_length, len(inv)+self._current_indent)
        except Exception:
            pass

def build_parser():
    prog="eta-112.py bios"
    rule=D("─"*60)
    desc=("\n"+B(Cy("  etabios"))+" — AMI Aptio "+B("BIOS parola aracı")+"   "+D("oku · ayarla · temizle")+"\n"
          +D("  Profil tabanlı; yalnız tanımlı modeller.  UEFI → efivarfs,  Legacy → flashrom.")+"\n\n"
          +"  "+WARN+"  "+Y("set/clear flash'a DOĞRUDAN yazar (onaysız) — ")+R("brick riski")+".")
    pre="sudo "+prog+" "
    def E(args_plain, args_colored, note=None):
        """Hizalı örnek satırı: önek soluk, komut renkli, açıklama sağda."""
        s="    "+D(pre)+args_colored
        if note is not None:
            vis=len("    "+pre+args_plain)
            s+=" "*max(2, 56-vis)+D("→ "+note)
        return s
    ex=["", rule, B("ÖRNEKLER"),
        Cy("  Okuma"),
        E("", "", "parametresiz çağrı da okur (= read)"),
        E("read", G("read"), "yönetici + kullanıcı parolasını göster"),
        "",
        Cy("  Ayarla / temizle")+"  "+R("(flash'a yazar)"),
        E("set", G("set"), "iki parola sırayla sorulur"),
        E("clear all", G("clear")+" "+Cy("all"), "tüm parolaları temizle"),
        "",
        Cy("  Bilgi / gelişmiş"),
        E("info", G("info"), "model ve destek durumu"),
        E("calibrate yonetici 1234", G("calibrate")+" "+Cy("yonetici 1234"), "keystream doğrula"),
        "",
        Cy("  GUI / makine (JSON çıktı)"),
        E("read --json", G("read")+" "+Cy("--json")),
        E("set --yonetici ORNEK99 --kullanici ABC123 --json",
          G("set")+" "+Cy("--yonetici ORNEK99 --kullanici ABC123 --json")),
        E("set --yonetici ORNEK99 --koruma always --json",
          G("set")+" "+Cy("--yonetici ORNEK99 --koruma always --json"), "her açılışta sor"),
        E("set --koruma setup --json",
          G("set")+" "+Cy("--koruma setup --json"), "yalnız koruma modunu değiştir"),
        "", rule, B("DESTEKLENEN MODELLER")]
    ex+=["  "+OK+"  "+Cy(b.ljust(9))+D("/")+" BIOS "+Cy(v.ljust(6))+"  "+D(PROFILES[(b,v)]['label'])
         for (b,v) in PROFILES]
    ex+=["", rule,
         "  "+D("Geliştirici ")+Cy("Özgür Koca")+D(" · ")+Cy("ozgurkoca.com")
         +D("      Lisans ")+G("GPL")+D(" — özgür yazılım")]
    common=argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="makine-okur JSON çıktı (GUI için)")
    p=argparse.ArgumentParser(prog=prog, description=desc, epilog="\n".join(ex), parents=[common],
                              formatter_class=_BiosHelp)
    sub=p.add_subparsers(dest="cmd", metavar="KOMUT")
    sub.add_parser("info", help="model/destek bilgisi", parents=[common])
    pr=sub.add_parser("read", help="parolaları oku (varsayılan komut)", parents=[common])
    pr.add_argument("--dump", metavar="DOSYA", help="canlı yerine bu ROM dump'tan oku")
    pc=sub.add_parser("calibrate", help="bilinen parolayla keystream doğrula", parents=[common])
    pc.add_argument("slot", choices=["yonetici","kullanici","user","supervisor"]); pc.add_argument("password")
    pc.add_argument("--dump", metavar="DOSYA")
    ps=sub.add_parser("set", help="parola ayarla, flash'a YAZAR", parents=[common])
    ps.add_argument("--yonetici", metavar="PAROLA", help="Yönetici parolası (parametreli/GUI; A-Z 0-9)")
    ps.add_argument("--kullanici", metavar="PAROLA", help="Kullanıcı parolası (parametreli/GUI; A-Z 0-9)")
    ps.add_argument("--koruma", choices=["always","setup","acilis"], metavar="{always,setup}",
                    help="Parola ne zaman sorulsun (GUI/makine sözleşmesi, read --json ile aynı): "
                         "always=her açılışta, setup=yalnız BIOS setup (acilis=always eşanlamlı)")
    ps.add_argument("--out", metavar="DOSYA", help="yazılan imajı ayrıca kaydet")
    pcl=sub.add_parser("clear", help="parola temizle (flash'a YAZAR)", parents=[common])
    pcl.add_argument("slot", choices=["yonetici","kullanici","all"])
    pcl.add_argument("--out", metavar="DOSYA")
    return p

def etabios_main(argv):
    global _JSON
    p=build_parser(); a=p.parse_args(argv)
    _JSON=getattr(a,"json",False)
    if not a.cmd:
        a.dump=None; return cmd_read(a) or 0
    if a.cmd=="calibrate":   # eski terimleri esle
        a.slot={"yonetici":"supervisor","kullanici":"user"}.get(a.slot,a.slot)
    return {"info":cmd_info,"read":cmd_read,"calibrate":cmd_calibrate,
            "set":cmd_set,"clear":cmd_clear}[a.cmd](a) or 0


# ===================== BÖLÜM 3: MAC ADRESİ (etamac) =====================
# Onboard ethernet MAC'ini OKUR ve onerilen bir MAC'i Faz profili OUI beyaz
# listesine (profil['mac_ouis']) gore DOGRULAR. Amac: kullanicinin Faz'a ait
# OLMAYAN bir MAC tanimlamasini engellemek. YAZMA henuz etkin degil (MAC SPI
# flash NVRAM'inde bulundu fakat yazmanin NIC'e gectigi reboot testiyle
# dogrulanmadi) -> 'set' net bir erteleme mesaji doner.

def _dmi_sysfs():
    """dmidecode (root) olmadan da model saptamak icin /sys/class/dmi/id."""
    g=lambda f: (open("/sys/class/dmi/id/"+f).read().strip()
                 if os.path.exists("/sys/class/dmi/id/"+f) else "")
    return {"board":g("board_name"),"bios_version":g("bios_version"),
            "board_mfr":g("board_vendor"),"bios_vendor":g("bios_vendor")}

def _mac_profile():
    """MAC komutlari icin profil: once dmidecode, board bossa sysfs'e dus (root'suz)."""
    d=dmi()
    if not d.get("board"): d=_dmi_sysfs()
    return match_profile(d), d

def _norm_mac(s):
    """Girisi 'AA:BB:CC:DD:EE:FF' (BUYUK) bicimine getirir; gecersizse None.
    Ayirici : - . veya bitisik kabul eder."""
    if not s: return None
    h="".join(c for c in s if c in "0123456789abcdefABCDEF")
    if len(h)!=12: return None
    return ":".join(h[i:i+2] for i in range(0,12,2)).upper()

def _mac_oui(mac): return mac[:8] if mac else None      # 'AA:BB:CC'

def validate_mac(mac_in, prof):
    """Onerilen MAC'i dogrular. Doner: (ok, normalized, oui, vendor, reason)."""
    ouis=(prof or {}).get("mac_ouis")
    m=_norm_mac(mac_in)
    if not m: return (False, None, None, None, "biçim geçersiz (12 onaltılık hane gerekir)")
    first=int(m[:2],16)
    if m=="00:00:00:00:00:00": return (False,m,None,None,"hepsi-sıfır MAC geçersiz")
    if m=="FF:FF:FF:FF:FF:FF": return (False,m,_mac_oui(m),None,"broadcast MAC geçersiz")
    if first&1: return (False,m,_mac_oui(m),None,"çok-noktalı (multicast) adres — NIC MAC'i olamaz")
    oui=_mac_oui(m)
    if ouis is None:
        return (False,m,oui,None,"bu model için Faz OUI doğrulaması tanımlı değil")
    if first&2:
        return (False,m,oui,None,"yerel-yönetimli (rastgele) adres — Faz cihazları global OUI kullanır")
    if oui not in ouis:
        return (False,m,oui,None,"OUI %s Faz'a ait değil (izinli: %s)"%(oui, ", ".join(ouis)))
    return (True,m,oui,ouis[oui],None)

def _eth_ifaces():
    """Kablolu ethernet arayuzleri: [(ifc, mac, driver)]; wifi/sanal haric."""
    out=[]; base="/sys/class/net"
    try: names=sorted(os.listdir(base))
    except OSError: return out
    for ifc in names:
        if ifc=="lo": continue
        d=os.path.join(base,ifc)
        if os.path.exists(os.path.join(d,"wireless")) or os.path.exists(os.path.join(d,"phy80211")):
            continue
        try:
            if open(os.path.join(d,"type")).read().strip()!="1": continue   # ARPHRD_ETHER
        except OSError: continue
        if not os.path.exists(os.path.join(d,"device")): continue           # sanal arayuzleri ele
        try: mac=open(os.path.join(d,"address")).read().strip().upper()
        except OSError: mac=""
        try: drv=os.path.basename(os.path.realpath(os.path.join(d,"device","driver")))
        except OSError: drv=""
        out.append((ifc, mac, drv))
    return out

def cmd_mac_read(a):
    prof,d=_mac_profile(); ouis=(prof or {}).get("mac_ouis")
    ifs=_eth_ifaces()
    if _JSON:
        items=[{"iface":i,"mac":m,"driver":v,"oui":_mac_oui(m),
                "vendor":(ouis or {}).get(_mac_oui(m)),
                "faz_uyumlu":bool(ouis and _mac_oui(m) in ouis)} for i,m,v in ifs]
        return emit({"ok":True,"supported":bool(prof),
                     "model":(prof or {}).get("model_name"),"board":d.get("board"),
                     "interfaces":items,"allowed_ouis":ouis})
    print(f"  Model: {G(prof['model_name']) if prof else Y('(tanınmadı)')}  "
          f"{D('Kart '+str(d.get('board','?')))}")
    if not ifs:
        print(R("  Kablolu ethernet arayüzü bulunamadı.")); return 1
    print(B("\nEthernet MAC adresleri"))
    for ifc,mac,drv in ifs:
        oui=_mac_oui(mac)
        if ouis and oui in ouis:   tag=f"{OK} {G('Faz OUI')} {D('('+ouis[oui]+')')}"
        elif ouis:                 tag=f"{WARN} {Y('Faz OUI değil')}"
        else:                      tag=D("(model profili yok)")
        print(f"  {ifc:<10} {Cy(mac)}  {D('['+drv+']')}  {tag}")
    if ouis:
        print(D("\n  İzinli Faz OUI: ")+", ".join("%s (%s)"%(o,v) for o,v in ouis.items()))
    else:
        print(D("\n  (Bu model için OUI beyaz listesi tanımlı değil.)"))
    return 0

def cmd_mac_check(a):
    prof,_=_mac_profile()
    ok,m,oui,vendor,reason=validate_mac(a.mac, prof)
    if _JSON:
        return emit({"ok":ok,"mac":m,"oui":oui,"vendor":vendor,"reason":reason}, 0 if ok else 1)
    if ok:
        print(f"  {OK} {G('Geçerli Faz MAC')}: {Cy(m)}  {D('OUI '+oui+' — '+vendor)}")
        print(D("     Bu adres 'mac set' tarafından kabul edilir. Doğrulama yalnız"))
        print(D("     adresin kendisini sınar; tahtaya henüz hiçbir şey yazılmadı."))
        return 0
    print(f"  {ERR} {R('Geçersiz MAC')}: {a.mac}")
    if m: print(D("     normalize: %s%s"%(m, "  OUI "+oui if oui else "")))
    print(f"     {Y('neden: '+reason)}")
    print(D("     Bu adres 'mac set' tarafından reddedilir; eFuse'a yazılmaz."))
    return 1

# ----- MAC YAZMA: Realtek eFuse (rtnicpg) -----
# MAC, RTL8168F'in dahili eFuse'unda (OTP) tutulur; OS-bagimsiz/kalici degisiklik
# yalniz Realtek'in PG araci (rtnicpg) ile eFuse'a yazilarak yapilir. Arac+modul
# otomatik indirilip derlenir. eFuse OTP: her yazim ~7 bayt tuketir, GERI ALINAMAZ.
PG_REPO = "https://github.com/redchenjs/rtnicpg.git"
PG_DIR  = "/var/tmp/eta-112-rtnicpg"
PG_BYTES_PER_WRITE = 7   # bir NODEID override'inin tukettigi yaklasik eFuse bayti

def _pg_binname():
    m = os.uname().machine
    return {"x86_64":"rtnicpg-x86_64","i686":"rtnicpg-i686","i386":"rtnicpg-i686",
            "aarch64":"rtnicpg-aarch64-linux-gnu","armv7l":"rtnicpg-armv8",
            "armv8l":"rtnicpg-armv8"}.get(m, "rtnicpg-x86_64")

def _hx2mac(h):
    h=(h or "").replace(":","").upper()
    return ":".join(h[i:i+2] for i in range(0,12,2)) if len(h)>=12 else (h or "")

def _parse_nodeid(out):
    import re
    m=re.search(r"NODE\s*ID\s*[:=]\s*([0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2}){5})", out or "")
    return re.sub(r"\s+","",m.group(1)).upper() if m else None

def _parse_remain(out):
    import re
    m=re.search(r"Remain\s+(\d+)\s+Bytes", out or "")
    return int(m.group(1)) if m else None

def _parse_writecount(out):
    import re
    m=re.search(r"Write Count\s*=\s*(\d+)", out or "")
    return int(m.group(1)) if m else None

def _efuse_dump_bytes(out):
    """rtnicpg /r /efuse ham hex dokum satirlarini bayt dizisine cevirir."""
    import re; bs=[]
    for ln in (out or "").splitlines():
        s=ln.strip()
        if s and re.fullmatch(r"(?:[0-9A-Fa-f]{2}\s+)*[0-9A-Fa-f]{2}", s) and len(s.split())>=8:
            try: bs+=[int(x,16) for x in s.split()]
            except ValueError: pass
    return bs

def _parse_efuse_history(out, current_hex):
    """eFuse'daki MAC-bayt override komutlarini (18 0X VV, X<=5) sirayla parse edip
    yazilan MAC dizisini (yaklasik) kurar. current_hex: mevcut NODE ID (12 hex)."""
    bs=_efuse_dump_bytes(out)
    if not current_hex or len(current_hex)<12: return []
    cur=[int(current_hex[i:i+2],16) for i in range(0,12,2)]
    macs=[]; i=0
    while i+2 < len(bs):
        if bs[i]==0x18 and bs[i+1]<=0x05:
            b=list(cur); b[bs[i+1]]=bs[i+2]
            macs.append(":".join("%02X"%x for x in b)); i+=3
        else: i+=1
    return macs

def _nm_eth_cons():
    if not which("nmcli"): return []
    r=subprocess.run(["nmcli","-t","-f","NAME,TYPE","connection","show"],capture_output=True,text=True)
    out=[]
    for ln in (r.stdout or "").splitlines():
        p=ln.rsplit(":",1)
        if len(p)==2 and "ethernet" in p[1]: out.append(p[0])
    return out

def ensure_rtnicpg():
    """rtnicpg ikilisi + pgdrv.ko hazirla (indir/derle). Doner (pgdir, binpath, err)."""
    binname=_pg_binname(); bpath=os.path.join(PG_DIR,binname); ko=os.path.join(PG_DIR,"pgdrv.ko")
    if os.path.exists(bpath) and os.path.exists(ko):
        try: os.chmod(bpath,0o755)
        except OSError: pass
        return PG_DIR,bpath,None
    if os.geteuid()!=0: return None,None,"sudo gerekli"
    rel=os.uname().release; hdr="/lib/modules/%s/build"%rel
    need=[t for t in ("git","gcc","make") if not which(t)]
    if not os.path.isdir(hdr): need.append("linux-headers-"+rel)
    if need:
        if not _JSON: print(f"  {D('Derleme bağımlılıkları kuruluyor: '+', '.join(need))}",flush=True)
        subprocess.run(["apt-get","install","-y"]+need,capture_output=True)
        if any(not which(t) for t in ("git","gcc","make")) or not os.path.isdir(hdr):
            return None,None,"derleme araçları/başlıkları kurulamadı (git build-essential linux-headers-%s)"%rel
    if not os.path.isdir(os.path.join(PG_DIR,".git")):
        if not _JSON: print(f"  {D('rtnicpg indiriliyor...')}",flush=True)
        r=subprocess.run(["git","clone","--depth","1",PG_REPO,PG_DIR],capture_output=True,text=True)
        if not os.path.isdir(os.path.join(PG_DIR,".git")):
            return None,None,"rtnicpg indirilemedi: "+((r.stderr or "")[-160:])
    if not os.path.exists(bpath):
        return None,None,"bu mimari için rtnicpg ikilisi yok: "+binname
    # pgdrv.c: kernel>=6.3 vm_flags salt-okunur -> vm_flags_set
    pgc=os.path.join(PG_DIR,"pgdrv.c")
    try:
        s=open(pgc).read()
        if "vma->vm_flags |= VM_IO;" in s:
            open(pgc,"w").write(s.replace("vma->vm_flags |= VM_IO;","vm_flags_set(vma, VM_IO);"))
    except OSError: pass
    if not _JSON: print(f"  {D('pgdrv.ko derleniyor...')}",flush=True)
    subprocess.run(["make","clean"],cwd=PG_DIR,capture_output=True)
    r=subprocess.run(["make"],cwd=PG_DIR,capture_output=True,text=True)
    if not os.path.exists(ko):
        return None,None,"pgdrv.ko derlenemedi: "+((r.stderr or "")[-240:])
    try: os.chmod(bpath,0o755)
    except OSError: pass
    return PG_DIR,bpath,None

def _pg_session(pgdir, pgbin, write_hex=None):
    """Atomik rtnicpg oturumu (AG-GUVENLI): r8169 unbind -> pgdrv -> oku[/yaz] ->
    pgdrv kaldir -> r8169 geri yukle -> ag geri gelene kadar bekle. Hep geri yukler."""
    ko=os.path.join(pgdir,"pgdrv.ko")
    def sh(*a, t=60):
        try: return subprocess.run(list(a),capture_output=True,text=True,cwd=pgdir,timeout=t)
        except Exception:
            class _R: returncode=124; stdout=""; stderr="timeout"
            return _R()
    if write_hex:
        for con in _nm_eth_cons():   # MAC degisince NM baglanabilsin
            sh("nmcli","connection","modify",con,"802-3-ethernet.mac-address","")
    res={"ok":False}; removed=False
    try:
        if sh("rmmod","r8169").returncode==0: removed=True
        sh("insmod",ko)
        if write_hex:
            w=sh(pgbin,"/efuse","/nodeid",write_hex,"/#","1"); wo=(w.stdout or "")+(w.stderr or "")
            res["wrote_ok"]=("Successfully" in wo)
        rd=sh(pgbin,"/r","/efuse","/#","1"); ro=(rd.stdout or "")+(rd.stderr or "")
        res["rdump"]=ro
        res["nodeid"]=_parse_nodeid(ro); res["remain"]=_parse_remain(ro)
        res["writecount"]=_parse_writecount(ro)
        if not res["nodeid"]:        # /r vermezse /v ile tamamla
            v=sh(pgbin,"/v","/efuse","/#","1"); vo=(v.stdout or "")+(v.stderr or "")
            res["nodeid"]=_parse_nodeid(vo); res["remain"]=res["remain"] or _parse_remain(vo)
        if write_hex: res["verified"]=(res["nodeid"]==write_hex.upper())
        res["ok"]=True
    finally:
        sh("rmmod","pgdrv")
        if removed: subprocess.run(["modprobe","r8169"],capture_output=True)
        for _ in range(30):   # ag geri gelene kadar bekle (max 30sn)
            if subprocess.run(["ip","route","get","8.8.8.8"],capture_output=True).returncode==0: break
            time.sleep(1)
    return res

def cmd_mac_set(a):
    if os.geteuid()!=0:
        return emit({"ok":False,"error":"sudo gerekli"},1) if _JSON else (print(R("  Bunun için 'sudo' gerekli.")) or 1)
    prof,d=_mac_profile()
    if not prof:
        return emit({"ok":False,"error":"desteklenmeyen model"},1) if _JSON else (print(R("  Desteklenmeyen model.")) or 1)
    ok,m,oui,vendor,reason=validate_mac(a.mac, prof)
    if not ok:
        if _JSON: return emit({"ok":False,"error":reason,"mac":m},1)
        print(f"  {ERR} {R('Geçersiz MAC')}: {Y(reason)}"); return 1
    hexmac=m.replace(":","")
    pgdir,pgbin,err=ensure_rtnicpg()
    if err:
        if _JSON: return emit({"ok":False,"error":err},1)
        print(R("  rtnicpg hazırlanamadı: "+err)); return 1
    pre=_pg_session(pgdir,pgbin)            # on-okuma: mevcut MAC + gecmis + sayaclar
    if not pre.get("ok"):
        if _JSON: return emit({"ok":False,"error":"NIC eFuse okunamadı"},1)
        print(R("  NIC eFuse okunamadı (rtnicpg).")); return 1
    cur=pre.get("nodeid") or ""; remain=pre.get("remain"); wcount=pre.get("writecount")
    history=_parse_efuse_history(pre.get("rdump") or "", cur)
    uniq=[]
    for mc in history:
        if mc not in uniq: uniq.append(mc)
    maxw=(remain//PG_BYTES_PER_WRITE) if isinstance(remain,int) else None
    if cur==hexmac.upper() and not getattr(a,"yes",False):
        if _JSON: return emit({"ok":True,"changed":False,"mac":m,"remain_bytes":remain,
                               "max_changes_left":maxw,"write_count":wcount,"history":uniq,"note":"zaten bu MAC"})
        print(Y("  eFuse NODE ID zaten bu değerde; değişiklik yok (yine de yazmak için -y).")); return 0
    if not _JSON:
        print(B("\n  MAC değiştirme — eFuse durumu"))
        print(f"  Mevcut MAC          : {Cy(_hx2mac(cur)) if cur else D('(okunamadı)')}")
        if uniq:
            print(D("  Daha önce yazılan MAC'ler (eskiden yeniye):"))
            for i,mc in enumerate(uniq,1):
                tag=D("  (mevcut)") if mc.replace(":","")==cur.upper() else ""
                print(f"     {D('%d.'%i)} {Cy(mc)}{tag}")
        else:
            print(D("  Daha önce yazılmış MAC override kaydı yok (fabrika değerinde)."))
        if isinstance(wcount,int):
            print(f"  Toplam eFuse yazma  : {wcount}")
        if isinstance(remain,int):
            print(f"  Kalan yazma hakkı   : ~{maxw}   {D('(boş alan %d bayt; her değişiklik ~%d bayt)'%(remain,PG_BYTES_PER_WRITE))}")
        print(f"  Yeni MAC            : {G(m)}  {D('('+(vendor or oui)+')')}")
        print(f"  {WARN} {Y('eFuse = OTP (tek-yönlü kalıcı): yeni MAC eskisini SİLMEZ, boş alana EKLENİR; GERİ ALINAMAZ. Alan bitince MAC bir daha değiştirilemez.')}")
        if isinstance(maxw,int) and maxw<=1:
            print(f"  {WARN} {R('DİKKAT: yazma hakkı tükenmek üzere — bu işlemden sonra MAC bir daha değiştirilemeyebilir!')}")
        if not getattr(a,"yes",False):
            try: ans=input(f"\n  Onaylıyorsanız {B('EVET')} yazın: ").strip()
            except EOFError: ans=""
            if ans!="EVET": print(Y("  İptal edildi.")); return 0
        print(f"  {D('eFuse yazılıyor (ağ kısa süre düşebilir)...')}",flush=True)
    res=_pg_session(pgdir,pgbin,write_hex=hexmac)
    rb=res.get("remain"); wleft=(rb//PG_BYTES_PER_WRITE) if isinstance(rb,int) else None
    if _JSON:
        return emit({"ok":bool(res.get("wrote_ok")),"changed":True,"verified":res.get("verified"),
                     "mac":m,"nodeid":res.get("nodeid"),"write_count":res.get("writecount"),
                     "remain_bytes":rb,"max_changes_left":wleft,
                     "history":_parse_efuse_history(res.get("rdump") or "", res.get("nodeid") or "")},
                    0 if (res.get("wrote_ok") and res.get("verified")) else 1)
    if not res.get("wrote_ok"):
        print(f"  {ERR} {R('Yazma başarısız (rtnicpg).')}"); return 1
    if res.get("verified"):
        print(f"  {OK} {G('eFuse yazıldı ve DOĞRULANDI')} — NODE ID: {Cy(m)}")
    else:
        print(f"  {WARN} {Y('Yazıldı ama geri-oku doğrulaması tutmadı (NODE ID: %s).'%_hx2mac(res.get('nodeid') or ''))}")
    if isinstance(rb,int):
        print(D("  Toplam eFuse yazma: %s   Kalan yazma hakkı: ~%d  (boş alan %d bayt)."
                %(res.get('writecount') if res.get('writecount') is not None else '?', wleft, rb)))
    print(f"  {Y('Yeni MAC OS-bağımsız ve kalıcıdır.')} Doğrulamak için yeniden başlatıp 'mac read' çalıştırın.")
    return 0

def etamac_main(argv):
    global _JSON
    args=list(argv)
    if "--json" in args: _JSON=True; args=[x for x in args if x!="--json"]
    if args and args[0] in ("-h","--help","yardim"):
        print(B("eta-112.py mac")+" — onboard ethernet MAC oku / doğrula / yaz")
        print("  eta-112.py mac read            # MAC(ler) + Faz OUI durumu (varsayılan)")
        print("  eta-112.py mac check <MAC>     # yazmadan önce sına: biçim, tür ve Faz OUI")
        print("  eta-112.py mac set <MAC> [-y]  # MAC'i Realtek eFuse'a YAZ (kalıcı, OS-bağımsız)")
        print("  eta-112.py mac [--json]        # makine-okur çıktı")
        print(D("  check: salt-okunur ön kontrol — donanıma dokunmaz, root istemez, hiçbir şey"))
        print(D("       yazmaz. 'set' aynı kontrolü zaten uygular; 'check' onu eFuse'a yazmadan"))
        print(D("       önce görmenizi sağlar. Sınadığı şeyler: 12 hane biçimi; hepsi-sıfır /"))
        print(D("       broadcast / multicast olmaması; yerel-yönetimli (rastgele) olmaması;"))
        print(D("       OUI'nin bu modelin Faz beyaz listesinde bulunması."))
        print(D("  set: Faz OUI zorunlu; yazma geri-oku ile DOĞRULANIR; rtnicpg+pgdrv otomatik"))
        print(D("       indirilip derlenir. eFuse = OTP (tek-yönlü kalıcı): her değişiklik ~7 bayt"))
        print(D("       tüketir, GERİ ALINAMAZ; araç kaç değişiklik kaldığını gösterir. -y onaysız."))
        return 0
    cmd=args[0] if args else "read"
    class _A: pass
    if cmd in ("read","oku"):
        return cmd_mac_read(_A()) or 0
    if cmd in ("check","dogrula","validate","kontrol"):
        if len(args)<2:
            if _JSON: return emit({"ok":False,"error":"mac argümanı gerekli"},1)
            print(R("  Kullanım: mac check <MAC>")); return 1
        a=_A(); a.mac=args[1]; return cmd_mac_check(a) or 0
    if cmd in ("set","write","yaz"):
        if len(args)<2:
            if _JSON: return emit({"ok":False,"error":"mac argümanı gerekli"},1)
            print(R("  Kullanım: mac set <MAC> [-y]")); return 1
        a=_A(); a.mac=args[1]; a.yes=("--yes" in args or "-y" in args)
        return cmd_mac_set(a) or 0
    die("Bilinmeyen mac komutu: %s   (read|check|set)" % cmd)


# ===================== BÖLÜM 4: WINDOWS ÜRÜN ANAHTARI (MSDM) =====================
# OEM Windows ürün anahtarı BIOS firmware'inde ACPI MSDM tablosunda saklanir (Win8+).
# OS bunu /sys/firmware/acpi/tables/MSDM'de gosterir. Degistirmek icin flash'taki MSDM
# tablosu duzenlenir + ACPI checksum guncellenir (BIOS parolasiyla ayni flashrom yolu).
# Not: Win7/Vista cihazlarda MSDM degil SLIC bulunur ve SLIC okunabilir anahtar icermez.
_MSDM_SYS = "/sys/firmware/acpi/tables/MSDM"
_WKEY_RE  = r"[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}"

def validate_wkey(s):
    import re
    k=(s or "").strip().upper()
    if not re.fullmatch(_WKEY_RE, k):
        return False, None, "biçim XXXXX-XXXXX-XXXXX-XXXXX-XXXXX olmalı (5×5, A-Z 0-9)"
    return True, k, None

def _read_msdm_sys():
    if not os.path.exists(_MSDM_SYS): return None
    try: return open(_MSDM_SYS,"rb").read()
    except OSError: return "DENIED"

def _msdm_find_key(tbl):
    """MSDM tablo baytlarinda 29-karakter anahtari bulur. Doner (key, offset) | (None,None)."""
    import re
    m=re.search(_WKEY_RE.encode(), tbl or b"")
    return (m.group().decode(), m.start()) if m else (None, None)

def _acpi_checksum_fix(tbl):
    """ACPI tablo checksum baytini (offset 9) yeniden hesaplar -> sum%256==0."""
    b=bytearray(tbl); b[9]=0; b[9]=(256-(sum(b)%256))%256; return bytes(b)

def cmd_wkey_read(a):
    raw=_read_msdm_sys()
    slic=os.path.exists("/sys/firmware/acpi/tables/SLIC")
    if raw is None:
        if _JSON: return emit({"ok":True,"present":False,"slic":slic,"key":None})
        print(Y("  MSDM tablosu yok — bu makinede okunabilir Windows ürün anahtarı saklanmıyor."))
        if slic: print(D("  (SLIC var: Windows 7/Vista OEM aktivasyonu — okunabilir anahtar içermez.)"))
        return 1
    if raw=="DENIED":
        if _JSON: return emit({"ok":False,"error":"sudo gerekli"},1)
        print(R("  MSDM okunamadı — 'sudo' gerekli.")); return 1
    key,_=_msdm_find_key(raw)
    if _JSON: return emit({"ok":bool(key),"present":True,"key":key})
    if key: print(f"  {OK} {G('Windows ürün anahtarı (MSDM)')}: {Cy(key)}")
    else:   print(Y("  MSDM tablosu var ama anahtar çözülemedi.")); return 1
    return 0

def cmd_wkey_set(a):
    import struct as _st, tempfile
    if os.geteuid()!=0:
        return emit({"ok":False,"error":"sudo gerekli"},1) if _JSON else (print(R("  Bunun için 'sudo' gerekli.")) or 1)
    prof,d=need_profile()
    if not prof:
        return emit({"ok":False,"error":"desteklenmeyen model"},1) if _JSON else (print(R("  Desteklenmeyen model (flash erişimi için profil gerekli).")) or 1)
    ok,newkey,reason=validate_wkey(a.key)
    if not ok:
        if _JSON: return emit({"ok":False,"error":reason},1)
        print(f"  {ERR} {R('Geçersiz anahtar')}: {Y(reason)}"); return 1
    ifd=(prof.get("flash_mode")=="ifd")
    if not _JSON: print(f"  {D('Flash okunuyor...')}",flush=True)
    data,err=flashrom_read(prof.get("chip"), show=False, ifd=ifd)
    if data is None:
        if _JSON: return emit({"ok":False,"error":"flash okunamadı"},1)
        print(R("  Flash okunamadı.")); return 1
    i=data.find(b"MSDM")
    if i==-1:
        msg="MSDM flash'ta bulunamadı (bu cihazda Windows anahtarı yok ya da sıkıştırılmış)"
        return emit({"ok":False,"error":"no_msdm"},1) if _JSON else (print(Y("  "+msg+".")) or 1)
    length=_st.unpack("<I",bytes(data[i+4:i+8]))[0]
    if not (36<length<0x2000) or i+length>len(data):
        return emit({"ok":False,"error":"MSDM uzunluğu geçersiz"},1) if _JSON else (print(R("  Flash'taki MSDM tablosu geçersiz.")) or 1)
    tbl=bytes(data[i:i+length]); fk,koff=_msdm_find_key(tbl)
    if not fk or len(fk)!=len(newkey):
        return emit({"ok":False,"error":"flash MSDM'de anahtar bulunamadı"},1) if _JSON else (print(R("  Flash MSDM'de anahtar bulunamadı.")) or 1)
    if fk==newkey:
        return emit({"ok":True,"changed":False,"key":newkey,"note":"zaten bu anahtar"}) if _JSON else (print(Y("  Anahtar zaten bu değerde.")) or 0)
    nt=bytearray(tbl); nt[koff:koff+len(newkey)]=newkey.encode("ascii")
    nt=_acpi_checksum_fix(bytes(nt))
    new=bytearray(data); new[i:i+length]=nt
    if not _JSON and not getattr(a,"yes",False):
        print(f"\n  Mevcut anahtar : {Cy(fk)}")
        print(f"  Yeni anahtar   : {G(newkey)}")
        print(f"  {WARN} {Y('BIOS flash MSDM tablosu değiştirilir (ACPI checksum güncellenir). Brick riski; etki için yeniden başlatma gerekir.')}")
        try: ans=input(f"  Onaylıyorsanız {B('EVET')} yazın: ").strip()
        except EOFError: ans=""
        if ans!="EVET": print(Y("  İptal edildi.")); return 0
        print(f"  {D('Yazılıyor...')}",flush=True)
    img=tempfile.NamedTemporaryFile(prefix="etawkey_",suffix=".bin",delete=False).name
    open(img,"wb").write(bytes(new))
    try:
        wok,_=flashrom_write(prof.get("chip"), img, show=False, ifd=ifd)
    finally:
        try: os.remove(img)
        except OSError: pass
    verified=False
    if wok:
        rb,_=flashrom_read(prof.get("chip"), show=False, ifd=ifd)
        if rb:
            j=rb.find(b"MSDM")
            if j!=-1:
                l2=_st.unpack("<I",bytes(rb[j+4:j+8]))[0]; t2=bytes(rb[j:j+l2])
                k2,_=_msdm_find_key(t2)
                verified=(k2==newkey) and (sum(t2)%256==0)
    if _JSON:
        return emit({"ok":bool(wok),"changed":True,"verified":verified,"key":newkey,"old":fk},
                    0 if (wok and verified) else 1)
    if not wok: print(f"  {ERR} {R('Yazma başarısız (flashrom).')}"); return 1
    if verified: print(f"  {OK} {G('MSDM yazıldı ve DOĞRULANDI')} — anahtar: {Cy(newkey)}")
    else:        print(f"  {WARN} {Y('Yazıldı ama geri-oku doğrulaması tutmadı.')}")
    print(f"  {Y('Yeniden başlatın')} — Windows yeni anahtarı MSDM'den okur. 'wkey read' ile doğrulayın.")
    return 0

def etawkey_main(argv):
    global _JSON
    args=list(argv)
    if "--json" in args: _JSON=True; args=[x for x in args if x!="--json"]
    if args and args[0] in ("-h","--help","yardim"):
        print(B("eta-112.py wkey")+" — BIOS'taki Windows ürün anahtarı (ACPI MSDM) oku / değiştir")
        print("  eta-112.py wkey read           # MSDM'deki Windows ürün anahtarını göster")
        print("  eta-112.py wkey set <ANAHTAR>  # flash'taki MSDM anahtarını değiştir (checksum'la)")
        print("  eta-112.py wkey [--json]       # makine-okur çıktı")
        print(D("  Anahtar biçimi: XXXXX-XXXXX-XXXXX-XXXXX-XXXXX. set: yazma geri-oku ile doğrulanır,"))
        print(D("  reboot gerekir. Win7/Vista cihazlarda MSDM yerine SLIC vardır (okunabilir anahtar yok)."))
        return 0
    cmd=args[0] if args else "read"
    class _A: pass
    if cmd in ("read","oku"):
        return cmd_wkey_read(_A()) or 0
    if cmd in ("set","write","yaz","degistir"):
        if len(args)<2:
            if _JSON: return emit({"ok":False,"error":"anahtar gerekli"},1)
            print(R("  Kullanım: wkey set <ANAHTAR>")); return 1
        a=_A(); a.key=args[1]; a.yes=("--yes" in args or "-y" in args)
        return cmd_wkey_set(a) or 0
    die("Bilinmeyen wkey komutu: %s   (read|set)" % cmd)


# ===================== BÖLÜM 5: DOKUNMATİK SÜRÜCÜ (etatouch) =====================
# Pardus ETAP akıllı tahtalarda dokunmatik (eta-touchdrv) sürüm denemesi.
#
# Mimari (ayrıntı: dokunmatik/belgeler/mimari.md):
#     USB → OtdDrv.ko → /dev/OtdUsbRaw000 → OtdTouchServer → /dev/input/eventX → X11
# Kernel modülü DURUMSUZDUR: ham USB paketlerini taşır, koordinat yorumlamaz.
# Kalibrasyon polinomunu kullanıcı uzayındaki sunucu uygular. Bu yüzden deneme
# iki kademelidir:
#     Kademe 1 — yalnız sunucu ikilisini değiştir (saniyeler, DKMS yok)
#     Kademe 2 — .deb'i tam kur (modül + sunucu + servis + udev, DKMS derler)
#
# Paket sistemde /etc altında hiçbir şey yönetmez (conffiles yok); dokunmatiğe
# ait tek müdahale yüzeyi .deb'in kendisidir.

import re
import hashlib
import platform
import urllib.parse
import urllib.request

TOUCH_PKG = "eta-touchdrv"
TOUCH_DEPO = "https://raw.githubusercontent.com/enseitankado/eta-112/main/dokunmatik"
TOUCH_YEDEK = "/var/backups/eta-112-dokunmatik"
TOUCH_PIN = "/etc/apt/preferences.d/99-eta-112-dokunmatik"

# lsusb'de aranacak kimlikler -> (tip, 0.5.x servis örneği)
TOUCH_AYGITLAR = (
    ("2621:2201", "otd"), ("2621:4501", "otd"),
    ("6615:0084", "optical"), ("6615:0085", "optical"), ("6615:0086", "optical"),
    ("6615:0087", "optical"), ("6615:0088", "optical"), ("6615:0c20", "optical"),
)


def _t_kok():
    return 0 if os.geteuid() == 0 else None


def _t_run(cmd, inp=None):
    """run() gibi, ama komut kurulu değilse patlamaz (canlı ortamda eksik olabilir)."""
    try:
        return run(cmd, inp=inp)
    except (FileNotFoundError, PermissionError):
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: bulunamadı")


def _t_aygit():
    """Takılı dokunmatik paneli bul. -> (tip, 'VVVV:PPPP') | (None, None)"""
    r = _t_run(["lsusb"])
    for kimlik, tip in TOUCH_AYGITLAR:
        if kimlik in r.stdout:
            return tip, kimlik
    return None, None


def _t_tip_ayikla(secim):
    """Kullanıcıdan gelen panel tipi girdisini normalleştir. -> tip | None"""
    s = (secim or "").strip().lower().lstrip("-")
    if s in ("otd", "4", "4k", "4kamera", "4-kamera", "otd/4"):
        return "otd"
    if s in ("optical", "optik", "2", "2k", "2kamera", "2-kamera", "optical/2"):
        return "optical"
    return None


def _t_tip_coz(a, islem):
    """Bu işlemin uygulanacağı panel tipini kesinleştir. -> (tip, kimlik|None)

    Panel tipi yalnız bir etiket değil: hangi servis örneğinin (eta-touchdrv@otd
    ↔ @optical) yeniden başlatılacağını, hangi sunucu ikilisinin değiştirileceğini
    ve hangi aygıt düğümünün açılacağını belirler. Yanlış tip sessizce yanlış
    donanıma müdahale demektir; bu yüzden asla varsayılmaz.

    Sıra:  --tip  >  lsusb  >  kullanıcıya sor  >  hata."""
    bulunan, kimlik = _t_aygit()
    istenen = getattr(a, "tip", None)
    if istenen:
        if bulunan and bulunan != istenen:
            warn(f"lsusb {bulunan} paneli gösteriyor ({kimlik}); "
                 f"--tip {istenen} ile ezildi.")
            kimlik = None
        return istenen, kimlik
    if bulunan:
        return bulunan, kimlik

    warn(f"Panel lsusb'de tanınmadı; {islem} hangi panel tipine uygulanacağı belirsiz.")
    print(f"  {D('Yanlış tip yanlış servis örneğini başlatır ve yanlış sunucu')}")
    print(f"  {D('ikilisini değiştirir; bu yüzden tip varsayılmıyor.')}")
    if _TTY.isatty():
        for _ in range(3):
            try:
                c = ask("  Panel tipi [1=OTD/4 kamera, 2=Optical/2 kamera, v=vazgeç]: ")
            except EOFError:
                break
            if c.strip().lower() in ("v", "q", "vazgec", "vazgeç"):
                die("Vazgeçildi.")
            t = _t_tip_ayikla(c)
            if t:
                return t, None
            print(f"  {Y('Geçersiz seçim.')}")
    die("Panel tipi belirlenemedi. Açıkça verin:\n"
        "    --tip otd       (2621 — 4 kameralı)\n"
        "    --tip optical   (6615 — 2 kameralı)")


def _t_kurulu():
    """Kurulu eta-touchdrv sürümü. -> str | None"""
    r = _t_run(["dpkg-query", "-W", "-f=${Version}", TOUCH_PKG])
    v = r.stdout.strip()
    return v if r.returncode == 0 and v else None


def _t_servis(tip):
    """Bu sistemdeki dokunmatik servis biriminin adı.

    0.5.0+ şablon birim kullanır (eta-touchdrv@otd / @optical); daha eskiler tek
    birim. Hangisinin kurulu olduğuna dosya sisteminden karar veririz.

    tip=None yalnız salt-okunur yollarda (durum ekranı) geçerlidir ve @otd
    varsayar; sisteme dokunan her çağrı tipi önce _t_tip_coz ile kesinleştirmeli."""
    for kok in ("/lib/systemd/system", "/usr/lib/systemd/system"):
        if os.path.exists(f"{kok}/{TOUCH_PKG}@.service"):
            return f"{TOUCH_PKG}@{tip or 'otd'}.service"
    return f"{TOUCH_PKG}.service"


def _t_sunucu_yolu(tip):
    """Çalışan sunucu ikilisinin /usr/bin altındaki yolu."""
    if tip == "optical":
        adaylar = ["OpticalService", "OpticalTouchServer.x86_64", "opticServer"]
    else:
        adaylar = [f"OtdTouchServer.{platform.machine()}", "OtdTouchServer",
                   "OpticalTouchServer.x86_64"]
    for ad in adaylar:
        y = f"/usr/bin/{ad}"
        if os.path.exists(y):
            return y
    return None


def _t_deb_sunucu(cikar, tip):
    """Açılmış paket ağacında bu panele ait sunucu ikilisini bul.

    Üç yerleşim var:
      resmi  : usr/bin/OtdTouchServer[.arch]  (otd) · usr/bin/OpticalService|opticServer (optical)
      vrdons : usr/bin/touch4/OpticalTouchServer.arch (otd) · usr/bin/touch2/... (optical)
    """
    kok = os.path.join(cikar, "usr", "bin")
    arch = platform.machine()
    bulunan = []
    for dizin, _alt, dosyalar in os.walk(kok):
        gorece = os.path.relpath(dizin, kok)
        for ad in dosyalar:
            yol = os.path.join(dizin, ad)
            if gorece in ("touch2", "touch4"):          # vrdons yerleşimi
                if ((gorece == "touch2") == (tip == "optical")
                        and ad.endswith(arch) and "Server" in ad):
                    bulunan.append(yol)
            elif tip == "optical":
                if ad in ("OpticalService", "opticServer"):
                    bulunan.append(yol)
            else:
                if ad in (f"OtdTouchServer.{arch}", "OtdTouchServer"):
                    bulunan.append(yol)
    return bulunan[0] if bulunan else None


def _t_servis_durum(birim):
    r = _t_run(["systemctl", "is-active", birim])
    return r.stdout.strip() or "bilinmiyor"


def _t_event_aygitlari():
    """Dokunmatik panelin ürettiği /dev/input/eventX düğümleri."""
    bulunan = []
    try:
        with open("/proc/bus/input/devices") as f:
            blok = []
            for satir in f:
                if satir.strip():
                    blok.append(satir.strip())
                    continue
                metin = " ".join(blok)
                if re.search(r"(?i)(otd|optical|touch|irtouch)", metin):
                    m = re.search(r"(event\d+)", metin)
                    ad = re.search(r'N: Name="([^"]*)"', metin)
                    if m:
                        bulunan.append((m.group(1), ad.group(1) if ad else "?"))
                blok = []
    except OSError:
        pass
    return bulunan


def _t_indir(gorece_yol, yerel_kok=None):
    """dokunmatik/ altındaki bir dosyayı getir. -> bytes"""
    if yerel_kok:
        with open(os.path.join(yerel_kok, gorece_yol), "rb") as f:
            return f.read()
    import urllib.request
    url = f"{TOUCH_DEPO}/{urllib.parse.quote(gorece_yol)}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def _t_manifest(yerel_kok=None, sessiz=False):
    try:
        ham = _t_indir("surumler.json", yerel_kok)
    except Exception as e:
        if sessiz:
            return None
        die(f"Sürüm listesi alınamadı: {e}\n"
            f"    İnternet yoksa depo klonunu gösterin:  --yerel /yol/eta-112/dokunmatik")
    return json.loads(ham.decode("utf-8"))


def _t_nesil_bul(man, oz):
    """Çalışan sunucu ikilisinin sha256'sından hangi sürümlerden geldiğini bul.

    0.5.x ikilileri dh_strip'ten geçmediği için depodaki ham blob özetiyle birebir
    tutar; eski sürümlerde tutmayabilir. Bulunamazsa None döner."""
    if not man:
        return None
    esler = [k["surum"] for k in man["surumler"]
             if (k.get("upstream_blob") or {}).get("otd_sunucu", "").startswith(oz[:16])
             or (k.get("upstream_blob") or {}).get("optik_sunucu", "").startswith(oz[:16])]
    return esler or None


def _t_paket_getir(kayit, yerel_kok=None):
    """Paketi indirip sha256'sını doğrula. -> geçici .deb yolu"""
    ham = _t_indir(kayit["dosya"], yerel_kok)
    if hashlib.sha256(ham).hexdigest() != kayit["sha256"]:
        die(f"{kayit['surum']}: sha256 tutmadı — indirme bozuk, işlem durduruldu.")
    hedef = os.path.join(tempfile.mkdtemp(prefix="eta112-touch-"),
                         os.path.basename(kayit["dosya"]))
    with open(hedef, "wb") as f:
        f.write(ham)
    return hedef


def _t_kayit(man, surum):
    for k in man["surumler"]:
        if k["surum"] == surum and k["sinif"] == "resmi":
            return k
    for k in man["surumler"]:
        if k["surum"] == surum:
            return k
    return None


# --------------------------------------------------------------- yedek / geri alma
def _t_yedek_al(tip):
    """Başlangıç durumunu sakla: kurulu sürüm + çalışan sunucu ikilisinin kopyası."""
    os.makedirs(TOUCH_YEDEK, exist_ok=True)
    durum = {"surum": _t_kurulu(), "tip": tip, "sunucu": None}
    yol = _t_sunucu_yolu(tip)
    if yol:
        hedef = os.path.join(TOUCH_YEDEK, os.path.basename(yol))
        if not os.path.exists(hedef):          # ilk yedeği koru, üzerine yazma
            shutil.copy2(yol, hedef)
        durum["sunucu"] = {"yol": yol, "yedek": hedef}
    with open(os.path.join(TOUCH_YEDEK, "baslangic.json"), "w") as f:
        json.dump(durum, f, ensure_ascii=False, indent=2)
    return durum


def _t_yedek_oku():
    try:
        with open(os.path.join(TOUCH_YEDEK, "baslangic.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _t_sunucu_yaz(kaynak, hedef, tip):
    """Sunucu ikilisini yerine koy ve servisi yeniden başlat."""
    shutil.copy2(kaynak, hedef)
    os.chmod(hedef, 0o755)
    _t_run(["systemctl", "restart", _t_servis(tip)])
    time.sleep(2.0)


def _t_geri_al(durum, tip, sessiz=False):
    """Başlangıç durumuna dön: önce paketi, sonra sunucu ikilisini geri koy."""
    if not durum:
        return False
    hedef = durum.get("surum")
    if hedef and _t_kurulu() != hedef:
        man = _t_manifest(durum.get("yerel_kok"))
        kayit = _t_kayit(man, hedef) if man else None
        if kayit:
            _t_deb_kur(_t_paket_getir(kayit, durum.get("yerel_kok")), tip)
    # Paket kurulumu ikiliyi tazeler; Kademe 1 yedeğini ondan SONRA geri yaz.
    s = durum.get("sunucu")
    if s and os.path.exists(s["yedek"]):
        _t_sunucu_yaz(s["yedek"], s["yol"], tip)
    if not sessiz:
        ok(f"Başlangıç durumuna dönüldü (sürüm {durum.get('surum') or '?'}).")
    return True


# --------------------------------------------------------------- kurulum
def _t_deb_kur(deb, tip):
    """dpkg -i ile kur. Düşürme (downgrade) da yapabilmeli.

    Sürüm aileleri farklı servis/udev düzeni kullanır (0.5.0+ eta-touchdrv@.service
    şablonu + SYSTEMD_WANTS, öncesi tek birim + RUN+=touchdrv_restart). Bu yüzden
    birim adı kurulumdan SONRA yeniden hesaplanır ve udev yeniden tetiklenir."""
    r = _t_run(["dpkg", "-i", "--force-downgrade", "--force-confnew", deb])
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()
    _t_run(["systemctl", "daemon-reload"])
    _t_run(["udevadm", "control", "--reload-rules"])
    _t_run(["udevadm", "trigger", "--subsystem-match=usb", "--action=add"])
    time.sleep(1.0)
    _t_run(["systemctl", "restart", _t_servis(tip)])
    time.sleep(2.0)
    return True, ""


def _t_dkms_derlendi(surum):
    """Bu sürümün kernel modülü gerçekten derlenmiş mi?"""
    r = _t_run(["dkms", "status", f"{TOUCH_PKG}/{surum}"])
    return "installed" in (r.stdout or "").lower()


# --------------------------------------------------------------- komutlar
def cmd_touch_durum(a):
    tip, kimlik = _t_aygit()
    varsayildi = False
    if not tip and getattr(a, "tip", None):
        tip = a.tip
    elif not tip:
        # Salt-okunur ekran: hangi birime bakacağımızı bilmiyoruz, @otd varsayıp
        # bunu açıkça söylüyoruz. Sisteme dokunan komutlar bu varsayımı yapmaz.
        varsayildi = True
    surum = _t_kurulu()
    birim = _t_servis(tip)
    title("Dokunmatik — durum")
    if tip:
        print(f"  Panel          : {G(kimlik or '?')}  {D('(' + ('OTD / 4 kamera' if tip == 'otd' else 'Optical / 2 kamera') + ')')}"
              + ("" if kimlik else D("  ← --tip ile verildi")))
    else:
        print(f"  Panel          : {Y('bulunamadı')}  {D('(lsusb bilinen kimlik göstermiyor)')}")
    print(f"  Kurulu sürüm   : {G(surum) if surum else Y('kurulu değil')}")
    print(f"  Servis         : {Cy(birim)}  → {_t_servis_durum(birim)}"
          + (f"  {Y('← panel tanınmadı, @otd varsayıldı')}" if varsayildi else ""))
    yol = _t_sunucu_yolu(tip)
    if yol:
        with open(yol, "rb") as f:
            oz = hashlib.sha256(f.read()).hexdigest()[:16]
        print(f"  Sunucu ikilisi : {Cy(yol)}  {D('sha256:' + oz)}")
        # Kademe 1 denemesinden sonra dpkg hâlâ eski sürümü gösterir; asıl
        # çalışan ikiliyi özetinden tanımaya çalış.
        esler = _t_nesil_bul(_t_manifest(a.yerel, sessiz=True), oz)
        if esler:
            uyum = surum in esler
            print(f"  Çalışan sunucu : {(G if uyum else Y)(' / '.join(esler))}"
                  + ("" if uyum else f"  {Y('← paket kaydıyla uyuşmuyor (Kademe 1 denemesi etkin)')}"))
    evs = _t_event_aygitlari()
    if evs:
        for ev, ad in evs:
            print(f"  Girdi aygıtı   : {G('/dev/input/' + ev)}  {D(ad)}")
    else:
        print(f"  Girdi aygıtı   : {Y('yok')}  {D('sunucu event düğümü üretmemiş')}")
    print(f"  Çekirdek       : {Cy(platform.release())}")
    yd = _t_yedek_oku()
    if yd:
        print(f"  Yedek          : {D('başlangıç sürümü ' + str(yd.get('surum')) + ' — geri almak için: dokunmatik geri')}")
    tutulu = TOUCH_PKG in _t_run(["apt-mark", "showhold"]).stdout.split()
    if tutulu:
        print(f"  apt            : {G('tutuluyor (hold)')} — otomatik güncelleme sürümü değiştirmez")
    hr()
    return 0


def cmd_touch_liste(a):
    man = _t_manifest(a.yerel)
    kurulu = _t_kurulu()
    yeni_cekirdek = tuple(int(x) for x in platform.release().split(".")[:2]) >= (6, 8)
    aylar = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
             "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"}

    def _tarih(s):
        m = re.search(r"(\d{1,2}) (\w{3}) (\d{4})", s or "")
        return f"{m.group(3)}-{aylar.get(m.group(2), '??')}-{m.group(1):0>2}" if m else ""

    def _lej(etiket, *satirlar):
        """Tablo altı lejant satırı: solda sütun adı, sağda açıklaması.

        Sütun adı 15 karakteri aşarsa (örn. 'bu çekirdekte derlenir mi?') kendi
        satırında durur; açıklama her hâlde aynı kolondan hizalanır."""
        girinti = " " * 4
        bosluk = " " * 16
        if len(etiket) <= 15:
            print(f"{girinti}{Cy(f'{etiket:<16}')}{D(satirlar[0])}")
            satirlar = satirlar[1:]
        else:
            print(f"{girinti}{Cy(etiket)}")
        for x in satirlar:
            print(f"{girinti}{bosluk}{D(x)}")

    tip, _ = _t_aygit()
    if getattr(a, "tip", None):
        tip = a.tip
    title("Dokunmatik — arşivdeki sürümler")
    print(f"  {D('sunucu/modül nesli aynı olan sürümler aynı sonucu verir; deneme sırası bunları atlar')}")
    print()
    # İki satırlık başlık: üst satır sütunun neye ait olduğunu, alt satır neyi
    # gösterdiğini söyler. sunucu/modül çiftleri panel tipine göre gruplanmıştır.
    print("  " + D(f"{'':<13}{'':<12}{'OTD paneli':<16}{'Optical paneli':<16}"
                   f"{'bu çekirdekte':<14}{'paket'}"))
    print("  " + D(f"{'sürüm':<13}{'tarih':<12}{'sunucu':<8}{'modül':<8}"
                   f"{'sunucu':<8}{'modül':<8}{'derlenir mi?':<14}{'sınıfı'}"))
    hr()
    for k in man["surumler"]:
        isaret = G("●") if k["surum"] == kurulu and k["sinif"] == "resmi" else " "
        if k["guncel_cekirdekte_derlenir"]:
            dkms, boya = "evet", G
        elif yeni_cekirdek:
            dkms, boya = "şüpheli", Y
        else:
            dkms, boya = "ilgisiz", D
        # 2k sütunları eski manifestlerde yok; uzaktan inen sürüm eskiyse boş geç.
        s4, m4 = k["sunucu_nesli"], k["modul_nesli"]
        s2 = k.get("optik_sunucu_nesli", "?")
        m2 = k.get("optik_modul_nesli", "?")
        # Panel tipi biliniyorsa ilgisiz sütun çifti soluk gösterilir.
        b4 = D if tip == "optical" else (lambda x: x)
        b2 = D if tip == "otd" else (lambda x: x)
        print(f"{isaret} {k['surum']:<13}{_tarih(k.get('tarih')):<12}"
              f"{b4(f'{s4:<8}{m4:<8}')}{b2(f'{s2:<8}{m2:<8}')}"
              f"{boya(f'{dkms:<14}')}{D(k['sinif'])}")
    hr()
    print(f"  {D('Deneme sırası:')} {' → '.join(man['deneme_sirasi'])}")
    print()
    print(f"  {B('Sütunların anlamı')}")
    _lej("sürüm",
         "Arşivdeki paketin sürüm numarası. Satır başındaki ● şu an kurulu olan sürümdür.")
    _lej("tarih",
         "Sürümün upstream'de yayımlanma tarihi — bizim paketleme tarihimiz değil.")
    _lej("OTD paneli",
         "USB kimliği 2621 olan, 4 kameralı panele ait sunucu/modül sütun çifti.")
    _lej("Optical paneli",
         "USB kimliği 6615 olan, 2 kameralı panele ait sunucu/modül sütun çifti.",
         "Panel tipiniz saptanmışsa ilgisiz olan çift soluk gösterilir; kendi",
         "panelinizin çiftine bakın, diğerinin bu makinede hiçbir etkisi yoktur.")
    _lej("  · sunucu",
         "Kullanıcı alanında çalışan sunucu ikilisinin nesli: A, B, C …",
         "Aynı harf = bayt bayt aynı program. Sürüm numaraları farklı ama harf",
         "aynıysa dokunmatik davranışı birebir aynıdır; denemeye değmez.")
    _lej("  · modül",
         "Kernel modülünün nesli: OTD tarafında M1, M2 … / Optical tarafında o1, o2 …",
         "Aynı etiket = aynı sürücü kaynağı, dolayısıyla aynı davranış.")
    _lej("  · tekil",
         "Bu paket hiçbir resmi nesle eşlenmiyor (varyant ya da üçüncü taraf).",
         "Tek başına değerlendirilir; deneme sırası onu eşdeğer sayıp atlayamaz.")
    _lej("bu çekirdekte derlenir mi?",
         f"DKMS'in kernel modülünü şu an çalışan çekirdekte ({platform.release()})",
         "derleyebilmesi bekleniyor mu:",
         "evet     → derlenmesi bekleniyor; Kademe 2 (tam kurulum) denenebilir.",
         "şüpheli  → çekirdeğiniz 6.8 veya üzeri, bu sürümün modül kaynağı ise daha",
         "           eski; modül büyük olasılıkla derlenmez, Kademe 2 bu sürümü atlar.",
         "           Kademe 1 (yalnız sunucu ikilisi) bundan etkilenmez, denenebilir.",
         "ilgisiz  → çekirdeğiniz 6.8'den eski; bu ayrım sizin sisteminizde anlamsız.")
    _lej("paket sınıfı",
         "resmi        → ETAP/upstream kaynaklı, desteklenen paket.",
         "varyant      → resmi bir sürümün değiştirilmiş kopyası.",
         "ucuncu-taraf → dışarıdan derlenmiş, desteklenmeyen paket.")
    return 0


def _t_sunucu_imzasi(kayit, tip):
    """Bu paketin, ilgili panel tipine ait sunucu ikilisinin kimliği.

    Paket içindeki baytlar kullanılamaz: resmi paketler dh_strip'ten geçmiş,
    git'ten yeniden paketlediklerimiz geçmemiştir; aynı program farklı özet verir.
    Upstream ham blob özeti tek karşılaştırılabilir ölçüdür."""
    u = kayit.get("upstream_blob") or {}
    oz = u.get("optik_sunucu" if tip == "optical" else "otd_sunucu")
    return oz if oz and oz != "-" else None


def _t_adaylar(man, tip, kademe):
    """Deneme sırasını bu sisteme göre süz ve önceliklendir."""
    kurulu = _t_kurulu()
    kurulu_kayit = _t_kayit(man, kurulu) if kurulu else None
    kurulu_sunucu = _t_sunucu_imzasi(kurulu_kayit, tip) if kurulu_kayit else None
    yeni_cekirdek = tuple(int(x) for x in platform.release().split(".")[:2]) >= (6, 8)
    liste, gorulen = [], set()
    for surum in man["deneme_sirasi"]:
        k = _t_kayit(man, surum)
        if not k:
            continue
        if surum == kurulu:
            continue                    # zaten çalışan sürüm; temel durum
        if kademe == 2 and yeni_cekirdek and not k["guncel_cekirdekte_derlenir"]:
            continue                    # modülü bu çekirdekte derlenmez
        if kademe == 1:
            # Kademe 1 yalnız sunucuyu değiştirir. Bu panel tipi için sunucusu
            # kurulu olanla aynı olan sürüm hiçbir şey değiştirmez -- örneğin
            # Optical (6615) sunucusu 0.2.0'dan beri hiç değişmemiştir.
            imza = _t_sunucu_imzasi(k, tip)
            if imza is not None and (imza == kurulu_sunucu or imza in gorulen):
                continue
            if imza is not None:
                gorulen.add(imza)
        liste.append(k)
    if kademe == 1:
        # Kurulu modülle aynı nesilden yayınlanmış sunucular önce denensin;
        # farklı nesildekiler ioctl uyuşmazlığı yüzünden boşa adım olabilir.
        liste.sort(key=lambda k: _t_uyumlu_mu(k, kurulu_kayit) is not True)
    return liste


def _t_uyumlu_mu(kayit, kurulu_kayit):
    """Kademe 1'de sunucu değiş-tokuşu bu modülle güvenli mi?

    OtdDrv.ko ↔ OtdTouchServer arasındaki ioctl protokolü sürüme bağlı; aynı
    modül nesliyle yayınlanmış sunucular birbirinin yerine konabilir."""
    if not kurulu_kayit:
        return None
    a = kayit.get("kademe1_uyumlu_modul")
    b = kurulu_kayit.get("kademe1_uyumlu_modul")
    if a is None or b is None:
        return None
    return a == b


def cmd_touch_dene(a):
    if _t_kok() is None:
        die("Bunun için 'sudo' gerekli.")
    if not _t_aygit()[0] and not a.zorla and not a.tip:
        die("Bilinen bir dokunmatik panel bulunamadı (lsusb).\n"
            "    Panelin bağlı olduğundan eminseniz:  dokunmatik dene --zorla\n"
            "    veya tipi doğrudan verin:            dokunmatik dene --tip otd|optical")
    tip, kimlik = _t_tip_coz(a, "Sürüm denemesi")
    man = _t_manifest(a.yerel)
    kurulu = _t_kurulu()
    kurulu_kayit = _t_kayit(man, kurulu) if kurulu else None

    adaylar = _t_adaylar(man, tip, a.kademe)
    if a.surum:
        k = _t_kayit(man, a.surum)
        if not k:
            die(f"Arşivde böyle bir sürüm yok: {a.surum}   ('dokunmatik liste')")
        adaylar = [k]
    if not adaylar:
        if a.kademe == 1 and tip == "optical":
            die("Bu panel tipi (Optical / 6615) için denenecek farklı sunucu yok.\n"
                "    Arşivdeki tüm sürümler 0.2.0'dan beri aynı OpticalService ikilisini\n"
                "    taşıyor; sunucu değiş-tokuşu hiçbir şeyi değiştirmez.\n"
                "    Kernel modülü sürümler arasında değişiyor:  dokunmatik dene --kademe 2")
        die("Denenecek sürüm kalmadı.")

    title(f"Dokunmatik — sürüm denemesi (Kademe {a.kademe})")
    print(f"  Panel          : {G(kimlik or '?')}  {D(tip)}")
    print(f"  Kurulu sürüm   : {G(kurulu) if kurulu else Y('yok')}")
    print(f"  Servis         : {Cy(_t_servis(tip))}")
    if a.kademe == 1:
        print(f"  {D('Yalnız sunucu ikilisi değişecek; kernel modülü ve DKMS katmanı korunacak.')}")
    else:
        print(f"  {D('Paket tam kurulacak; DKMS her adımda modülü yeniden derleyecek (yavaş).')}")
    print(f"  {D('Denenecek:')} {' → '.join(k['surum'] for k in adaylar)}")
    print()
    print(f"  {WARN}  {Y('Deneme sırasında dokunmatik geçici olarak çalışmayabilir.')}")
    print(f"     {D('Onay vermeden çıkarsanız başlangıç durumuna otomatik dönülür.')}")
    hr()
    if ask("  Başlansın mı? [E/h]: ").strip().lower() in ("h", "hayır", "hayir", "n"):
        return 0

    durum = _t_yedek_al(tip)
    durum["yerel_kok"] = a.yerel
    onaylanan = None
    try:
        for i, k in enumerate(adaylar, 1):
            print()
            print(f"  {C.B}[{i}/{len(adaylar)}] {k['surum']}{C.R}  "
                  f"{D('sunucu ' + k['sunucu_nesli'] + ' · modül ' + k['modul_nesli'])}")
            if k.get("not"):
                print(f"      {D(k['not'])}")
            if k["sinif"] != "resmi":
                print(f"      {Y('resmi değil — ' + k['sinif'])}")

            uyum = _t_uyumlu_mu(k, kurulu_kayit) if a.kademe == 1 else None
            if uyum is False:
                print(f"      {WARN} {Y('Farklı modül nesliyle yayınlanmış sunucu; bu modülle çalışmayabilir.')}")

            if a.kademe == 1 and k["sinif"] == "ucuncu-taraf":
                warn("Üçüncü taraf çatal kendi kernel modülünü kullanır; yalnız sunucu "
                     "değiş-tokuşu anlamsız. Bunu Kademe 2 ile deneyin.")
                continue

            deb = progress_timed(f"{k['surum']} indiriliyor",
                                 lambda k=k: _t_paket_getir(k, a.yerel), est=8.0)

            if a.kademe == 1:
                cikar = tempfile.mkdtemp(prefix="eta112-deb-")
                _t_run(["dpkg-deb", "-x", deb, cikar])
                yeni = _t_deb_sunucu(cikar, tip)
                if not yeni:
                    warn("Bu pakette bu panele uygun sunucu ikilisi yok — atlanıyor.")
                    continue
                hedef = _t_sunucu_yolu(tip) or f"/usr/bin/{os.path.basename(yeni)}"
                run_msg(f"{os.path.basename(hedef)} değiştiriliyor ve servis yeniden başlatılıyor...",
                        lambda: _t_sunucu_yaz(yeni, hedef, tip))
            else:
                basarili, hata = progress_timed(
                    f"{k['surum']} kuruluyor (DKMS derliyor)",
                    lambda: _t_deb_kur(deb, tip), est=90.0)
                if not basarili:
                    warn(f"Kurulum başarısız — atlanıyor.  {D(hata.splitlines()[-1] if hata else '')}")
                    continue
                if not _t_dkms_derlendi(k["surum"]):
                    warn("DKMS modülü derlenemedi (bu çekirdekte beklenen olabilir) — atlanıyor.")
                    continue

            dur = _t_servis_durum(_t_servis(tip))   # birim adı sürümle değişebilir
            evs = _t_event_aygitlari()
            print(f"      servis: {G(dur) if dur == 'active' else Y(dur)}   "
                  f"girdi aygıtı: {G('/dev/input/' + evs[0][0]) if evs else Y('yok')}")
            if dur != "active" or not evs:
                warn("Sunucu event düğümü üretmedi; bu sürüm bu sistemde çalışmıyor.")
                if ask("      Yine de ekrana dokunup denemek ister misiniz? [e/H]: "
                       ).strip().lower() not in ("e", "evet", "y"):
                    continue

            print()
            print(f"      {C.B}Şimdi ekrana dokunun ve kalibrasyonu kontrol edin.{C.R}")
            c = ask("      Sorun düzeldi mi? [e = evet · h = hayır, sıradakine geç · "
                    "d = dur]: ").strip().lower()
            if c in ("e", "evet", "y"):
                onaylanan = k
                break
            if c in ("d", "dur", "q"):
                break
    except KeyboardInterrupt:
        print()
        warn("İptal edildi.")

    print()
    if not onaylanan:
        hr()
        warn("Düzelten sürüm bulunamadı.")
        run_msg("Başlangıç durumuna dönülüyor...",
                lambda: _t_geri_al(durum, tip, sessiz=True))
        ok(f"Başlangıç durumu geri yüklendi (sürüm {durum.get('surum') or '?'}).")
        return 1

    hr()
    ok(f"Düzelten sürüm: {G(onaylanan['surum'])}")
    if ask("  Kalıcı hale getirilsin mi? [E/h]: ").strip().lower() in ("h", "hayır", "hayir", "n"):
        warn("Kalıcılaştırılmadı. Bir sonraki apt güncellemesi bu sürümü geri alabilir.")
        print(f"  {D('Sonra kalıcılaştırmak için:  eta-112.py dokunmatik kalici ' + onaylanan['surum'])}")
        return 0
    return _t_kalicilastir(onaylanan, tip, a.yerel, a.kademe)


def _t_kalicilastir(kayit, tip, yerel, kademe):
    """Sürümü tam kur, apt'yi bu sürüme sabitle.

    Kademe 1 yalnız /usr/bin'deki dosyayı değiştirir; paket veritabanı hâlâ eski
    sürümü gösterir ve ilk apt güncellemesinde dosya geri gelir. Bu yüzden
    kalıcılaştırma her durumda tam kurulumdan geçer."""
    surum = kayit["surum"]
    if kademe == 1 or _t_kurulu() != surum:
        deb = _t_paket_getir(kayit, yerel)
        basarili, hata = progress_timed(f"{surum} tam kuruluyor",
                                        lambda: _t_deb_kur(deb, tip), est=90.0)
        if not basarili:
            err(f"Tam kurulum başarısız: {hata.splitlines()[-1] if hata else ''}")
            warn("Sunucu ikilisi yerinde ama paket kaydı güncellenmedi; "
                 "kalıcı değil.")
            return 1
        if not _t_dkms_derlendi(surum):
            warn("DKMS modülü derlenemedi. Sunucu çalışıyor olabilir ama modül "
                 "bir sonraki çekirdek güncellemesinde kaybolur.")

    _t_run(["apt-mark", "hold", TOUCH_PKG])
    try:
        with open(TOUCH_PIN, "w") as f:
            f.write(
                "# eta-112 tarafından yazıldı — dokunmatik sürümü sabitlendi.\n"
                "# Kaldırmak için:  eta-112.py dokunmatik serbest\n"
                f"Package: {TOUCH_PKG}\n"
                f"Pin: version {surum}\n"
                "Pin-Priority: 1001\n")
    except OSError as e:
        warn(f"apt pin yazılamadı: {e}")

    hr()
    ok(f"{G(surum)} kuruldu ve sabitlendi.")
    print(f"  {D('apt-mark hold + ' + TOUCH_PIN)}")
    print(f"  {D('Sabitlemeyi kaldırmak için:  eta-112.py dokunmatik serbest')}")
    return 0


def cmd_touch_kalici(a):
    if _t_kok() is None:
        die("Bunun için 'sudo' gerekli.")
    man = _t_manifest(a.yerel)
    kayit = _t_kayit(man, a.surum)
    if not kayit:
        die(f"Arşivde böyle bir sürüm yok: {a.surum}   ('dokunmatik liste')")
    tip, _ = _t_tip_coz(a, f"{a.surum} sürümünü kalıcılaştırmak")
    title(f"Dokunmatik — {a.surum} kalıcı hale getiriliyor")
    return _t_kalicilastir(kayit, tip, a.yerel, kademe=2)


def cmd_touch_serbest(a):
    if _t_kok() is None:
        die("Bunun için 'sudo' gerekli.")
    _t_run(["apt-mark", "unhold", TOUCH_PKG])
    if os.path.exists(TOUCH_PIN):
        os.remove(TOUCH_PIN)
    ok("Sabitleme kaldırıldı; apt yeniden güncelleyebilir.")
    return 0


def cmd_touch_geri(a):
    if _t_kok() is None:
        die("Bunun için 'sudo' gerekli.")
    durum = _t_yedek_oku()
    if not durum:
        die(f"Yedek bulunamadı ({TOUCH_YEDEK}). Geri alınacak bir şey yok.")
    # Yedek kendi panel tipini taşır; geri yazılacak ikili o panele ait olduğu
    # için kaynak odur. Kart değişmişse sessizce devam etmek yerine uyarıyoruz.
    tip = getattr(a, "tip", None) or durum.get("tip")
    if tip:
        bulunan, kimlik = _t_aygit()
        if bulunan and bulunan != tip:
            warn(f"Yedek {tip} panelden alınmış, şu an takılı panel {bulunan} ({kimlik}).\n"
                 f"    Geri alma {tip} için yapılacak; istediğiniz bu değilse:  --tip {bulunan}")
    else:
        tip, _ = _t_tip_coz(a, "Geri alma")
    title("Dokunmatik — başlangıç durumuna dönülüyor")
    print(f"  Hedef sürüm    : {G(str(durum.get('surum')))}")
    durum["yerel_kok"] = a.yerel
    _t_geri_al(durum, tip)
    return 0


# ------------------------------------------------------------------ kalibrasyon (EEPROM)
# Dokunmatik panelin kalibrasyon bloklarini cihazdan dogrudan okur/yazar.
#
# TASIMA KATMANI — kesin. Kaynagi GPL kernel modulu (OtdDrv.h / OtdDrv.c,
# OpticalDrv.h / OpticalDrv.c; paketin /usr/src agacinda):
#     ioctl(fd, 0x0010_LLLL, buf)  -> SET_REPORT
#         usb_control_msg(sndctrlpipe, bRequest=0, bmRequestType=0x40, 0, 0, buf, LLLL)
#     ioctl(fd, 0x0011_LLLL, buf)  -> GET_REPORT
#         usb_control_msg(rcvctrlpipe, bRequest=0, bmRequestType=0xc0, 0, 0, buf, LLLL)
# Yani saticiya ozel (vendor) kontrol transferi; tum protokol 64 baytlik yukun icinde.
#
# PAKET BICIMI — Optical (6615) icin kesin. OpticalService ikilisinin sembollu
# surumunden (0.3.6~tbt1, `packageBuild` @0x401606) sokulerek cikarildi:
#     [0]        0xAA
#     [1]        tur   (1 = set, 2 = get)
#     [2]        komut
#     [3]        yuk_uzunlugu + 1
#     [4]        indeks
#     [5..]      yuk
#     [5+n]      saglama = (0x55 + toplam(paket[0 .. n+4])) & 0xFF
# Cevap (deviceGetFeature @0x4016d1): paket[0]==0xAA veya paket[1]==0x12 veya
# paket[2]==komut ise gecerli; yuk yine ofset 5'ten baslar. Cevapta saglama
# DOGRULANMIYOR (ikili de dogrulamiyor).
#
# KOMUT HARITASI — Optical icin kesin; OpticalService `main` (@0x405d74) acilista
# tam olarak bu diziyi calistirir.
#
# OTD (2621) tarafinda tasima katmani ayni, ancak komut kimlikleri DOGRULANMADI:
# OtdTouchServer statik derlenmis ve sembolsuz. Bu yuzden OTD icin okuma yalnizca
# --dene bayragiyla, deneysel olarak yapilir.

import array
import fcntl

KALIB_SET_REPORT = 0x00100000
KALIB_GET_REPORT = 0x00110000
KALIB_PAKET = 0x40          # tum komutlar 64 baytlik rapor kullanir
KALIB_BEKLE = 0.010         # ikilinin her ioctl arasinda bekledigi sure (10 ms)
KALIB_YUK_OFSET = 5
KALIB_OKU_ENCOK = KALIB_PAKET - KALIB_YUK_OFSET       # 59: ofset 5..63
KALIB_YAZ_ENCOK = KALIB_PAKET - KALIB_YUK_OFSET - 1   # 58: son bayt saglama

# (komut, indeks, cevap_uzunlugu, ad)
#
# NOT — uretici ikilisinde bir yigin tasmasi var: main, (0x30, 1) blogunu 0x48=72
# bayt olarak istiyor, ama deviceGetFeature 64 baytlik yigin tamponunun 5.
# ofsetinden 72 bayt kopyaliyor; son 13 bayt bitisik yigin cop verisi. Bunu
# taklit etmiyoruz: okuma 59 bayta (tamponun gercek sonu) kirpiliyor.
KALIB_BLOKLAR = (
    (0x10, 0, 0x3a, "kamera-0"),
    (0x10, 1, 0x3a, "kamera-1"),
    (0x30, 0, 0x3a, "yapilandirma-0"),
    (0x30, 1, 0x48, "yapilandirma-1"),
    (0x14, 0, 0x3a, "ekran-0"),
    (0x14, 1, 0x3a, "ekran-1"),
)
KALIB_MOD_KOMUTU = 0x71     # yuk 1 = yapilandirma moduna gir, 0 = cik


def _k_aygit_yolu(tip):
    """Sürücünün oluşturduğu karakter aygıtı."""
    kalip = "/dev/IRTouchOptical%03d" if tip == "optical" else "/dev/OtdUsbRaw%03d"
    for i in range(4):
        y = kalip % i
        if os.path.exists(y):
            return y
    return None


def _k_paket_kur(tur, komut, indeks, yuk=b""):
    """OpticalService `packageBuild` (@0x401606) ile birebir aynı."""
    n = len(yuk)
    if n > KALIB_YAZ_ENCOK:
        raise ValueError(f"yük {KALIB_YAZ_ENCOK} bayttan uzun olamaz ({n} verildi)")
    p = bytearray(KALIB_PAKET)
    p[0] = 0xAA
    p[1] = tur & 0xFF
    p[2] = komut & 0xFF
    p[3] = (n + 1) & 0xFF
    p[4] = indeks & 0xFF
    p[5:5 + n] = yuk
    p[5 + n] = (0x55 + sum(p[0:n + 5])) & 0xFF
    return bytes(p)


def _k_ioctl(fd, taban, veri, uzunluk=None):
    """ioctl kodu uzunlugu tasir: OTD_IOCTL_CODE(tip, uzunluk).

    Optical sabit 64 bayt gonderir; OTD ise paketin gercek boyunu (n+6)
    gonderir. Uzunlugu 64'e sabitlemek OTD panelde kontrol transferinin
    STALL etmesine (EPIPE) yol aciyordu."""
    if uzunluk is None:
        uzunluk = KALIB_PAKET
    tampon = array.array("B", veri)
    fcntl.ioctl(fd, taban | (uzunluk & 0xFFFF), tampon, True)
    return bytes(tampon)


# ---- OTD (2621) paket bicimi ------------------------------------------------
# Kaynak: /usr/bin/OtdTouchServer.x86_64 (0.4.0) sokumu, paketleyici @0x3351.
# Optical'dan FARKLI; eski kod Optical bicimini OTD'ye gonderdigi icin panel
# her istegi STALL ediyordu.
#
#     [0]        0x55                      (Optical'da 0xAA)
#     [1]        b1   - cagri basina sabit: 0x1e / 0x2d / 0x3c
#     [2]        indeks / parametre
#     [3]        komut
#     [4]        n    - yuk uzunlugu
#     [5..5+n-1] yuk
#     [n+5]      saglama = toplam(paket[2 .. n+4]) & 0xFF
#     gonderilen uzunluk = n+6                (Optical'da sabit 64)
#
# Sunucu SET'ten sonra 50 ms bekler (Optical 10 ms), sonra 64 baytlik GET yapar
# ve cevap[1] == 0x87 ise basarili sayar.
KALIB_OTD_BASLIK = 0x55
KALIB_OTD_BEKLE = 0.050
# Cevap durumu cevap[1]'de. Sunucu (@0x34f3) iki degeri taniyor:
#   0x87 -> ACK, veri yok
#   0x4b -> VERI VAR: uzunluk cevap[2], veri cevap[3]'ten baslar (5'ten DEGIL;
#           ofset 5 Optical'a ait). Sunucu cevap[3 .. 3+cevap[2]-1] kopyalar.
# Baska bir deger gelirse sunucu basarisiz sayar; biz yine de ham cevabi
# sakliyoruz - kesif icin en degerli veri o.
KALIB_OTD_TAMAM = 0x87
KALIB_OTD_VERI = 0x4b
KALIB_OTD_VERI_OFSET = 3

# Sunucunun gercekte kullandigi bes cagri (ecx=komut, edx=indeks, r9=n):
#   0x3c/degisken/0xb1/n=2    yazma
#   0x1e/degisken/0xb2/n=4    yazma
#   0x2d/degisken/0xb2/n=36   yazma (9 x float32)
#   0x1e/degisken/0xb0/n=0    SAF OKUMA
#   0x1e/0x80    /0xae/n=0    SAF OKUMA
# Yalniz yuk tasimayan iki okuma komutunu kullaniyoruz; yazanlara dokunmuyoruz.
# (b1, indeks, komut, ad)
# Cihazda olculdu: indeks 0x00-0x03 yanit veriyor, 0x04+ STALL ediyor.
# Dort indeks = panelin dort kamerasi (OTD 4 kamerali).
KALIB_OTD_BLOKLAR = tuple(
    [(0x1e, i, 0xb0, "b0-idx%02x" % i) for i in range(4)] +
    [(0x1e, i, 0xae, "ae-idx%02x" % i) for i in range(4)] +
    [(0x1e, 0x80, 0xae, "ae-idx80")]
)


# ---- OTD depolama (kalibrasyonun gercek yeri) -------------------------------
# Sembol adlari ikilinin dinamik tablosundan; adresler oradan, govdeler sokumden.
#   0xb0 n=0  OpticalTouchDeviceGetStoragePartitionInformation  @0x3da2
#   0xb2 n=4  OpticalTouchDeviceGetStorageBlock                 @0x3b9f   OKUMA
#   0xb2 n=36 OpticalTouchDeviceSetStorageBlock                 @0x3cda   yazma
#   0xb1 n=2  OpticalTouchDeviceEraseStorage                    @0x3ac8   SILME
#   0xae n=0  OtdGetAllFcb (indeks 0x80)                        @0x3e36
#
# Bolum bilgisi 8 bayt, uint16 LE dort alan:
#   [0:2] tip/bayrak   [2:4] toplam blok   [4:6] bolen   [6:8] blok boyu (32)
# GetStorageBlock 4 baytlik yuk gonderir:  uint16(blok_no // bolen) +
# uint16(blok_no % bolen)  -- sokumdeki 'divw 0x4(%r8)' tam olarak bu.
KALIB_OTD_BOLUM_BILGI = 0xb0
KALIB_OTD_BLOK_OKU = 0xb2
KALIB_OTD_BLOK_YAZ = 0xb2       # n=36 ile; yalniz 'yazma-testi' / 'depo-yaz'
KALIB_OTD_SIL = 0xb1            # ASLA gonderilmez
KALIB_OTD_TEHLIKELI = (0xb1, 0xb2)

# ---- OTD blok yazma --------------------------------------------------------
# Yuk duzeni VARSAYIM: 4 bayt blok adresi + 32 bayt veri = 36.
# Dayanagi, okuma yolunun simetrisi -- ayni komut kimligi (0xb2) okumada n=4
# ile YALNIZ adres tasiyor; yazmada n=36 ile ayni adres + bir blok (blok boyu
# bolum bilgisinde 32 olarak bildiriliyor). Ikilinin 0x2d/0xb2/n=36 cagrisi
# icin yorum satirinda "9 x float32" yaziyordu; 4+32 bolunmesi adresleme
# simetrisiyle daha tutarli, ama DOGRULANMASI gerekiyor -- 'yazma-testi'
# komutu tam bunu yapar.
#
# NEDEN NO-OP YAZMA BRICK YAPMAZ: bir blogu KENDI okunan degeriyle yazmak,
# hucre NOR flash olup silme gerektirse bile icerigi degistirmez (x & x = x).
# Geriye tek risk kalir: adres duzeni yanlissa yazma BASKA bir bloga gider.
# 'yazma-testi' bunu, yazmadan once ve sonra bolumun TAMAMINI dokup
# karsilastirarak yakalar.
KALIB_OTD_YAZ_B1 = 0x2d         # sunucuda 0xb2/n=36 cagrisinin [1] bayti
KALIB_OTD_YAZ_YUK = 36          # 4 bayt adres + 32 bayt veri
KALIB_OTD_BLOK_BOYU = 32
KALIB_OTD_SERI_BLOK = 0         # her bolumde blok 0 = ASCII seri / ProductKey
# FCB kayit tablosunda kullanilan bloklar: 0 (seri) + 1..3 (kamera-parametre,
# 82 bayt -> 31+32+32). Ilk kullanilmayan blok 4; 'yazma-testi' varsayilan
# olarak onu secer, boylece bir hata bile gercek kalibrasyon verisine dokunmaz.
# Cihazda olculdu (kalibrasyon-protokolu.md 6.6): blok 4+ tamamen 0xFF, yani
# silinmis flash -- test icin en zararsiz hedef.
KALIB_OTD_TEST_BLOK = 4         # yalniz kucuk/bilinmeyen bolumlerde geri dusus


def _k_otd_test_blok(bilgi):
    """Adres duzeni yanlissa STALL uretecek bir test blogu sec. -> int

    Yazma yuku uint16(blok//bolen) + uint16(blok%bolen) varsayiliyor. En
    sinsi hata bicimi, alanlarin TERS sirada olmasi: o zaman cihaz
    (alcak*bolen + yuksek) adresine yazar ve bu gecerli bir blok olabilir --
    yani yanlis varsayim sessizce BASKA bir blogu bozar.

    Test blogunu oyle seceriz ki ters okuma bolumun DISINA dussun; panel o
    istegi STALL eder ve hata gorunur olur, veri kaybi olmaz:

        blok = bolen + k   (yuksek=1, alcak=k),  k*bolen + 1 >= toplam_blok

    Cihazda olculen degerlerle (toplam 4096, bolen 128): k=33 -> blok 161,
    ters okuma 33*128+1 = 4225 > 4096 -> STALL. Blok 161 ayrica kayit
    bolgesinin (ilk ~48 blok) disinda ve 0xFF (silinmis flash)."""
    bolen = bilgi.get("bolen") or 0
    toplam = bilgi.get("toplam_blok") or 0
    if bolen < 2 or toplam < 2:
        return KALIB_OTD_TEST_BLOK
    k = -(-toplam // bolen) + 1                  # ceil(toplam/bolen) + 1
    blok = bolen + k
    if blok >= toplam:                           # bolum bunu tasimiyor
        return min(KALIB_OTD_TEST_BLOK, toplam - 1)
    return blok

# Bolum 4096 blok bildiriyor (olculdu) ve her blok bir SET+GET+2x50 ms demek:
# tam dokum ~7 dakika. Yan etki kontrolu icin bu gereksiz -- kayitlar ilk
# ~48 blokta yasiyor ve yanlis adresleme yakin bir bloga duser. Bu yuzden
# kiyas penceresi varsayilan olarak bu kadar; --blok-sayisi ile buyutulebilir.
KALIB_OTD_KIYAS_PENCERE = 64


def _k_otd_bolum_coz(yuk):
    """0xb0 cevabini alanlarina ayir. -> dict | None"""
    if not yuk or len(yuk) < 8:
        return None
    tip, toplam, bolen, blok_boyu = struct.unpack("<4H", yuk[:8])
    return {"tip": tip, "toplam_blok": toplam,
            "bolen": bolen, "blok_boyu": blok_boyu}


# Kayit tablolari - LoadCcbParameters @0x13ee2 / LoadFcbParameters @0x143e5
# sokumunden cikarildi. (bas_blok, uzunluk, ad)
#
# Kayit birlestirme (readRecord @0x131cd): blok sayisi = (uzunluk+32)//32;
# ILK blogun [0] bayti 0x01 olmali ve o bloktan yalniz [1..31] (31 bayt)
# alinir; sonraki bloklarin 32 bayti da alinir; sonuc 'uzunluk'a kirpilir.
KALIB_OTD_CCB_BOLUM = 0x80          # StaticLoadCcbParameters @0x14c0e: esi=0x80
KALIB_OTD_KAMERA_BOLUM = (0, 1, 2, 3)

KALIB_OTD_FCB_KAYIT = (
    (0, 25, "seri"),                # ASCII urun kimligi (mimari.md'deki ProductKey)
    (1, 82, "kamera-parametre"),
)
KALIB_OTD_CCB_KAYIT = (
    (0, 25, "ccb-seri"),
    (12, 34, "ccb-b12-34"),
    (1, 150, "ccb-b01-150"),
    (16, 169, "ccb-b16-169"),
    (6, 186, "ccb-b06-186"),
    (22, 186, "ccb-b22-186"),
    (30, 25, "ccb-b30-25"),
    (31, 25, "ccb-b31-25"),
    (32, 512, "ccb-b32-512"),
)


def _k_otd_kayit_oku(fd, bolum, bas_blok, uzunluk, bolen):
    """readRecord (@0x131cd) ile birebir. -> bytes"""
    adet = (uzunluk + 32) // 32
    veri = bytearray()
    for i in range(adet):
        v, ham, durum = _k_otd_blok_oku(fd, bolum, bas_blok + i, bolen)
        if v is None:
            raise OSError(f"blok {bas_blok + i}: durum 0x{durum:02x}")
        if i == 0:
            if not v or v[0] != 0x01:
                raise OSError(
                    f"blok {bas_blok}: geçerlilik baytı "
                    f"0x{(v[0] if v else 0):02x} (0x01 bekleniyordu) — kayıt yok/silinmiş")
            veri += v[1:32]
        else:
            veri += v[:32]
        if len(veri) >= uzunluk:
            break
    if len(veri) < uzunluk:
        raise OSError(f"kayıt eksik: {len(veri)}/{uzunluk} bayt")
    return bytes(veri[:uzunluk])


def _k_otd_blok_oku(fd, bolum, blok_no, bolen):
    """Tek 32 baytlik depolama blogu. -> (veri|None, ham, durum)"""
    if not bolen:
        raise OSError("bölüm bilgisi bölen alanı 0; blok adresi hesaplanamaz")
    yuk = struct.pack("<HH", blok_no // bolen, blok_no % bolen)
    return _k_oku_blok_otd(fd, 0x1e, bolum, KALIB_OTD_BLOK_OKU, yuk)


def _k_otd_blok_yaz(fd, bolum, blok_no, veri, bolen):
    """SetStorageBlock (0xb2, n=36) ile tek blok yaz. -> (veri|None, ham, durum)

    Yuk duzeni varsayim (bkz. yukaridaki not): okuma ile ayni 4 baytlik adres,
    ardindan 32 baytlik blok. Cagiran yazma sonrasi MUTLAKA geri okuyup
    dogrulamali; bu fonksiyon yalnizca paketi gonderir."""
    if not bolen:
        raise OSError("bölüm bilgisi bölen alanı 0; blok adresi hesaplanamaz")
    if len(veri) != KALIB_OTD_BLOK_BOYU:
        raise ValueError(f"blok verisi tam {KALIB_OTD_BLOK_BOYU} bayt olmalı "
                         f"({len(veri)} verildi)")
    yuk = struct.pack("<HH", blok_no // bolen, blok_no % bolen) + bytes(veri)
    if len(yuk) != KALIB_OTD_YAZ_YUK:                       # duzen bozulduysa gonderme
        raise ValueError(f"yazma yükü {KALIB_OTD_YAZ_YUK} bayt olmalı ({len(yuk)})")
    return _k_oku_blok_otd(fd, KALIB_OTD_YAZ_B1, bolum, KALIB_OTD_BLOK_YAZ, yuk)


def _k_otd_bolum_dok(fd, bolum, bilgi, bas=0, adet=None):
    """Bolumun ham bloklarini sirayla oku. -> {blok_no: bytes}

    Yazma oncesi/sonrasi kiyas icin kullanilir: eksik okunan blok atlanir,
    boylece tek bir STALL tum dokumu dusurmez."""
    son = bilgi["toplam_blok"] if adet is None else min(
        bilgi["toplam_blok"], bas + adet)
    goruntu = {}
    for n in range(bas, son):
        try:
            v, _ham, _d = _k_otd_blok_oku(fd, bolum, n, bilgi["bolen"])
        except OSError:
            continue
        if v is not None and len(v) == KALIB_OTD_BLOK_BOYU:
            goruntu[n] = v
    return goruntu


def _k_otd_kiyas_goruntu(fd, bolum, bilgi, blok, pencere):
    """Yan etki kontrolu icin iki parcali goruntu. -> {blok_no: bytes}

    Her blok bir SET+GET+2x50 ms demek, yani okuma pahali. Tam bolumu (4096
    blok, ~7 dakika) dokmek gereksiz: bilgi tasiyan yerler kayit bolgesi
    (bastan ~64 blok) ve test blogunun kendi cevresi. Aradaki bloklar 0xFF."""
    goruntu = _k_otd_bolum_dok(fd, bolum, bilgi, 0, pencere)
    bas = max(0, blok - 8)
    if bas >= pencere:
        goruntu.update(_k_otd_bolum_dok(fd, bolum, bilgi, bas, 17))
    return goruntu


def _k_paket_kur_otd(b1, indeks, komut, yuk=b""):
    """OtdTouchServer paketleyicisi (@0x3351) ile birebir. -> (paket, uzunluk)"""
    n = len(yuk)
    if n > KALIB_PAKET - 6:
        raise ValueError(f"yuk en cok {KALIB_PAKET - 6} bayt olabilir ({n} verildi)")
    p = bytearray(KALIB_PAKET)
    p[0] = KALIB_OTD_BASLIK
    p[1] = b1 & 0xFF
    p[2] = indeks & 0xFF
    p[3] = komut & 0xFF
    p[4] = n & 0xFF
    p[5:5 + n] = yuk
    p[n + 5] = sum(p[2:n + 5]) & 0xFF
    return bytes(p[:n + 6]), n + 6


def _k_oku_blok_otd(fd, b1, indeks, komut, yuk=b""):
    """OTD tarafinda tek komut: SET (n+6 bayt) -> 50 ms -> GET (64 bayt).

    -> (veri|None, ham_cevap, durum). Durum baytini yorumlamak cagirana ait;
    burada yalniz ioctl hatalari (EPIPE vb.) istisna olur."""
    paket, uzunluk = _k_paket_kur_otd(b1, indeks, komut, yuk)
    _k_ioctl(fd, KALIB_SET_REPORT, paket, uzunluk)
    time.sleep(KALIB_OTD_BEKLE)
    istek = bytearray(KALIB_PAKET)
    istek[0] = KALIB_OTD_BASLIK          # sunucu da GET oncesi bunu yaziyor
    cevap = _k_ioctl(fd, KALIB_GET_REPORT, bytes(istek), KALIB_PAKET)
    time.sleep(KALIB_OTD_BEKLE)
    durum = cevap[1]
    if durum == KALIB_OTD_VERI:
        n = cevap[2]
        o = KALIB_OTD_VERI_OFSET
        return cevap[o:o + n], cevap, durum
    if durum == KALIB_OTD_TAMAM:
        return b"", cevap, durum           # ACK - veri tasimiyor
    return None, cevap, durum              # bilinmeyen: ham cevabi cagirana birak


def _k_oku_blok(fd, komut, indeks, uzunluk):
    """getCommand (@0x40178b): istek paketini yaz, 10 ms bekle, cevabı oku."""
    _k_ioctl(fd, KALIB_SET_REPORT, _k_paket_kur(2, komut, indeks))
    time.sleep(KALIB_BEKLE)
    cevap = _k_ioctl(fd, KALIB_GET_REPORT, bytes(KALIB_PAKET))
    time.sleep(KALIB_BEKLE)
    if not (cevap[0] == 0xAA or cevap[1] == 0x12 or cevap[2] == komut):
        raise OSError(f"geçersiz cevap başlığı: {cevap[:5].hex(' ')}")
    n = min(uzunluk, KALIB_OKU_ENCOK)
    return cevap[KALIB_YUK_OFSET:KALIB_YUK_OFSET + n], cevap


def _k_yaz_blok(fd, komut, indeks, yuk):
    """setCommand (@0x40183d): yük taşıyan set paketini yaz, sonucu oku."""
    _k_ioctl(fd, KALIB_SET_REPORT, _k_paket_kur(1, komut, indeks, yuk))
    time.sleep(KALIB_BEKLE)
    _k_ioctl(fd, KALIB_GET_REPORT, bytes(KALIB_PAKET))
    time.sleep(KALIB_BEKLE)


def _k_mod(fd, ac):
    """Yapılandırma moduna gir/çık — main açılışta bunu iki indeks için yapar."""
    for indeks in (0, 1):
        _k_yaz_blok(fd, KALIB_MOD_KOMUTU, indeks, b"\x01" if ac else b"\x00")


def _k_servis_durdur(tip):
    """Sunucu cihazı sürekli okuyor; kontrol transferlerinden önce durdurulmalı."""
    birim = _t_servis(tip)
    calisiyordu = _t_servis_durum(birim) == "active"
    if calisiyordu:
        _t_run(["systemctl", "stop", birim])
        time.sleep(0.5)
    return birim, calisiyordu


def _k_double_avi(ham, en_az=1e-6, en_cok=1e6):
    """Ham blokta makul float64 degerleri ara. -> [(ofset, deger)]

    CCB kayitlari float64 tutuyor ve alanlar 8'e hizali DEGIL (blok
    birlestirmesi 31+32 oldugu icin hiza kayiyor). Bu yuzden her ofseti
    deniyoruz; sadece makul buyuklukteki ve tam-sifir olmayan degerleri
    raporluyoruz. Kesif icin; kesin bir cozumleme degil."""
    bulunan = []
    for i in range(0, max(0, len(ham) - 7)):
        (d,) = struct.unpack_from("<d", ham, i)
        if d != d or d in (float("inf"), float("-inf")) or d == 0.0:
            continue
        if en_az <= abs(d) <= en_cok:
            bulunan.append((i, d))
    # Ust uste binen pencereleri seyrelt: bir deger bulununca 8 bayt atla.
    seyrek, son = [], -8
    for i, d in bulunan:
        if i - son >= 8:
            seyrek.append((i, d))
            son = i
    return seyrek


def _k_floatlar(ham):
    n = len(ham) // 4
    return list(struct.unpack("<%df" % n, ham[:n * 4]))


def _k_otd_kayitlari(fd):
    """OTD'de sunucunun okudugu her kaydi oku. -> [blok dict]

    Bolum 0x80 = CCB (kalibrasyon), bolum 0-3 = kameralarin FCB'si.
    Bolum bilgisi (0xb0) 'bolen' alanini verir; blok adresi onunla hesaplanir."""
    cikti = []
    islenecek = [(KALIB_OTD_CCB_BOLUM, KALIB_OTD_CCB_KAYIT, "ccb")] + [
        (b, KALIB_OTD_FCB_KAYIT, "fcb%d" % b) for b in KALIB_OTD_KAMERA_BOLUM]
    for bolum, tablo, onek in islenecek:
        try:
            yuk, ham, durum = _k_oku_blok_otd(fd, 0x1e, bolum,
                                              KALIB_OTD_BOLUM_BILGI)
            bilgi = _k_otd_bolum_coz(yuk)
        except OSError as e:
            cikti.append({"ad": f"{onek}-bolum", "komut": KALIB_OTD_BOLUM_BILGI,
                          "indeks": bolum, "uzunluk": 0, "hata": str(e)})
            continue
        if not bilgi:
            cikti.append({"ad": f"{onek}-bolum", "komut": KALIB_OTD_BOLUM_BILGI,
                          "indeks": bolum, "uzunluk": 0,
                          "hata": f"bölüm bilgisi çözülemedi (durum 0x{durum:02x})"})
            continue
        cikti.append({"ad": f"{onek}-bolum", "komut": KALIB_OTD_BOLUM_BILGI,
                      "indeks": bolum, "uzunluk": len(yuk), "veri": yuk.hex(),
                      "ham_cevap": ham.hex(), "durum": durum, "bolum_bilgisi": bilgi})
        for bas, uz, ad in tablo:
            kayit = {"ad": f"{onek}/{ad}", "komut": KALIB_OTD_BLOK_OKU,
                     "indeks": bolum, "bas_blok": bas, "uzunluk": uz}
            try:
                veri = _k_otd_kayit_oku(fd, bolum, bas, uz, bilgi["bolen"])
            except OSError as e:
                kayit["hata"] = str(e)
            else:
                kayit["veri"] = veri.hex()
                kayit["uzunluk"] = len(veri)
            cikti.append(kayit)
    return cikti


def _k_snapshot(tip, deneysel=False):
    """Tüm blokları oku. -> dict"""
    yol = _k_aygit_yolu(tip)
    if not yol:
        die("Sürücünün aygıt düğümü yok "
            f"({'IRTouchOptical' if tip == 'optical' else 'OtdUsbRaw'}).\n"
            "    Panel bağlı ve modül yüklü mü?  'dokunmatik durum' ile bakın.")
    if tip != "optical" and not deneysel:
        die("OTD (2621) komut kümesi OtdTouchServer 0.4.0 ikilisinden türetildi,\n"
            "    cihazda henüz doğrulanmadı. Yalnızca yük taşımayan okuma\n"
            "    komutları (0xb0, 0xae) denenir. Devam etmek için:  --dene")

    birim, calisiyordu = _k_servis_durdur(tip)
    bloklar, hata, mod_hatasi = [], None, None
    try:
        fd = os.open(yol, os.O_RDWR)
        try:
            # Yapilandirma moduna giris (0x71) Optical'a ozgu bir komut. OTD
            # panelde STALL edebilir - ama bu, okuma komutlarini hic denememek
            # icin gerekce degil. Eskiden bu cagri dongunun disindaydi ve ilk
            # EPIPE tum kesfi olduruyordu; kullanici hangi komutun yanit
            # verdigini goremiyordu. Artik hatayi kaydedip devam ediyoruz.
            if tip != "optical":
                # OTD: yapilandirma modu komutu YOK (sunucu da gondermiyor) ve
                # veri ham blok degil, KAYIT halinde duruyor. Sunucunun
                # LoadCcb/LoadFcbParameters yolunu birebir izliyoruz.
                bloklar.extend(_k_otd_kayitlari(fd))
                sira = []
            else:
                try:
                    _k_mod(fd, True)
                except OSError as e:
                    mod_hatasi = str(e)
                sira = [(k, i, u, ad, None) for k, i, u, ad in KALIB_BLOKLAR]
            for komut, indeks, uzunluk, ad, b1 in sira:
                try:
                    durum = None
                    if b1 is None:
                        yuk, tam = _k_oku_blok(fd, komut, indeks, uzunluk)
                    else:
                        yuk, tam, durum = _k_oku_blok_otd(fd, b1, indeks, komut)
                    kayit = {"ad": ad, "komut": komut, "indeks": indeks,
                             "ham_cevap": tam.hex()}
                    if durum is not None:
                        kayit["durum"] = durum
                    if yuk is None:
                        # Bilinmeyen durum bayti: veriyi yorumlayamiyoruz ama
                        # ham cevap duruyor - kesfin devami buna bakacak.
                        kayit["uzunluk"] = 0
                        kayit["veri"] = ""
                        kayit["not"] = f"bilinmeyen durum 0x{durum:02x}"
                    else:
                        kayit["uzunluk"] = len(yuk)
                        kayit["veri"] = yuk.hex()
                    bloklar.append(kayit)
                except OSError as e:
                    bloklar.append({"ad": ad, "komut": komut, "indeks": indeks,
                                    "uzunluk": uzunluk, "hata": str(e)})
            if tip == "optical":
                try:
                    _k_mod(fd, False)
                except OSError:
                    pass                  # moda girilemediyse cikis da anlamsiz
        finally:
            os.close(fd)
    except OSError as e:
        hata = str(e)
    finally:
        if calisiyordu:
            _t_run(["systemctl", "start", birim])

    if hata and not bloklar:
        die(f"Cihaza erişilemedi: {hata}")

    tip_kod, kimlik = _t_aygit()
    return {
        "bicim": "eta-112-dokunmatik-kalibrasyon/1",
        "tarih": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "panel": {"tip": tip, "usb": kimlik, "aygit": yol},
        "surucu": _t_kurulu(),
        "makine": platform.node(),
        "deneysel": tip != "optical",
        "mod_hatasi": mod_hatasi,
        "bloklar": bloklar,
    }


def _k_yaz_dosya(anlik, yol):
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(anlik, f, ensure_ascii=False, indent=2)
    os.chmod(yol, 0o600)


def _k_oku_dosya(yol):
    try:
        with open(yol, encoding="utf-8") as f:
            a = json.load(f)
    except (OSError, ValueError) as e:
        die(f"{yol}: okunamadı ({e})")
    if a.get("bicim", "").split("/")[0] != "eta-112-dokunmatik-kalibrasyon":
        die(f"{yol}: bu bir kalibrasyon anlık görüntüsü değil.")
    return a


def _k_oku_dosya_depo(yol):
    """'depo' ciktisini oku ve bicimini dogrula. -> dict

    'yazma-testi' ciktisi da kabul edilir: onun 'referans' alani ayni bolumun
    tam dokumunu tasir, yani bir yan etkiyi geri almak icin dogrudan
    kullanilabilir."""
    try:
        with open(yol, encoding="utf-8") as f:
            a = json.load(f)
    except (OSError, ValueError) as e:
        die(f"{yol}: okunamadı ({e})")
    aile = a.get("bicim", "").split("/")[0]
    if aile == "eta-112-dokunmatik-yazma-testi":
        ref = a.get("referans") or {}
        if not ref:
            die(f"{yol}: yazma testi çıktısında referans döküm yok.")
        a = {"bicim": "eta-112-dokunmatik-depo/1", "tarih": a.get("tarih", ""),
             "panel": a.get("panel", {}), "surucu": a.get("surucu"),
             "makine": a.get("makine"),
             "bolumler": [{"bolum": a.get("bolum", 0),
                           "bilgi": a.get("bolum_bilgisi") or {},
                           "bloklar": [{"no": int(n), "veri": v}
                                       for n, v in sorted(ref.items(),
                                                          key=lambda kv: int(kv[0]))]}]}
        aile = "eta-112-dokunmatik-depo"
    if aile != "eta-112-dokunmatik-depo":
        die(f"{yol}: bu bir 'depo' dökümü değil.\n"
            "    Blok geri yükleme ham blok dökümü ister:  "
            "dokunmatik kalibrasyon depo --tam --cikti <dosya>")
    if not a.get("bolumler"):
        die(f"{yol}: dökümde hiç bölüm yok.")
    return a


def _k_stall_mi(b):
    """Blok kaydi USB STALL ile mi bitti? (EPIPE -> 'Broken pipe')"""
    return "hata" in b and ("Errno 32" in b["hata"] or "Broken pipe" in b["hata"])


def _k_teshis(anlik):
    """Hepsi STALL ise komut kumesi bu panele ait degil demektir.

    usb_control_msg STALL'da -EPIPE doner (OtdDrv.c: set_report/get_report
    donus degerini oldugu gibi aktarir). Yani panel istegi anlamadi; tasima
    katmani calisiyor, komut kimlikleri yanlis."""
    bloklar = anlik.get("bloklar") or []
    if not bloklar or not all(_k_stall_mi(b) for b in bloklar):
        return
    print()
    warn("Panel tum komutlari STALL etti (EPIPE) - hicbiri taninmadi.")
    print(f"  {D('Tasima katmani saglam: ioctl cekirdege, oradan USB kontrol')}")
    print(f"  {D('transferine ulasti. STALL, panelin BU komut kumesini')}")
    print(f"  {D('tanimadigi anlamina gelir.')}")
    if anlik.get("panel", {}).get("tip") != "optical":
        print()
        print(f"  {D('OTD komut kumesi OtdTouchServer 0.4.0 sokumunden turetildi')}")
        print(f"  {D('(paketleyici @0x3351). Hepsi STALL ettiyse ya bu surumun')}")
        print(f"  {D('bicimi panelinizle uyusmuyor ya da komut/indeks farkli.')}")
        print(f"  {D('Tarama icin:  dokunmatik kalibrasyon tara')}")


def _k_yazdir(anlik):
    p = anlik["panel"]
    print(f"  Panel          : {G(p.get('usb') or '?')}  {D(p['tip'])}  {D(p['aygit'])}")
    print(f"  Sürücü         : {G(str(anlik.get('surucu')))}")
    print(f"  Alındığı zaman : {D(anlik['tarih'])}  {D(anlik.get('makine', ''))}")
    if anlik.get("deneysel"):
        print(f"  {WARN} {Y('Deneysel: bu panel tipinde komut kimlikleri doğrulanmadı.')}")
    if anlik.get("mod_hatasi"):
        print(f"  {WARN} {Y('Yapılandırma moduna girilemedi (0x71): ' + anlik['mod_hatasi'])}")
        print(f"  {D('Bloklar yine de tek tek denendi; sonuçlar aşağıda.')}")
    print()
    for b in anlik["bloklar"]:
        basl = f"  {b['ad']:<16} {D('cmd 0x%02x idx %d' % (b['komut'], b['indeks']))}"
        if "hata" in b:
            print(f"{basl}  {Y('okunamadı: ' + b['hata'])}")
            continue
        ek = ""
        if "durum" in b:
            etiket = {KALIB_OTD_VERI: "veri", KALIB_OTD_TAMAM: "ACK"}.get(
                b["durum"], "bilinmeyen")
            ek = f"  {D('durum 0x%02x %s' % (b['durum'], etiket))}"
        if b.get("not"):
            print(f"{basl}{ek}  {Y(b['not'])}")
            print(f"      {D('ham cevap:')}  {bytes.fromhex(b['ham_cevap'])[:16].hex(' ')}")
            continue
        ham = bytes.fromhex(b["veri"])
        print(f"{basl}{ek}  {D('%d bayt' % len(ham))}")
        if b.get("komut") == KALIB_OTD_BOLUM_BILGI:
            bilgi = _k_otd_bolum_coz(ham)
            if bilgi:
                print(f"      {D('bölüm bilgisi:')} tip {bilgi['tip']}  "
                      f"toplam blok {bilgi['toplam_blok']}  "
                      f"bölen {bilgi['bolen']}  blok boyu {bilgi['blok_boyu']}")
                print(f"      {D('blokları okumak için:  dokunmatik kalibrasyon depo')}")
                continue
        for i in range(0, len(ham), 16):
            print(f"      {D('%02x' % i)}  {ham[i:i+16].hex(' ')}")
        yazdirilabilir = sum(1 for c in ham if 0x20 <= c < 0x7f)
        if ham and yazdirilabilir >= len(ham) - 1:
            metin = "".join(chr(c) if 0x20 <= c < 0x7f else "." for c in ham)
            print(f"      {D('ascii:')} {Cy(metin.strip())}")
            continue
        f = _k_floatlar(ham)
        if f and any(1e-6 < abs(x) < 1e6 for x in f):
            print(f"      {D('float32:')} " + " ".join(f"{x:.6g}" for x in f[:8])
                  + (D("  …") if len(f) > 8 else ""))
        ciftler = _k_double_avi(ham)
        if ciftler:
            print(f"      {D('float64 (makul olanlar, ofset: değer):')}")
            for i in range(0, min(len(ciftler), 12), 4):
                print("        " + "  ".join(
                    f"{D('%3d:' % o)} {Cy('%.8g' % d)}" for o, d in ciftler[i:i + 4]))
            if len(ciftler) > 12:
                print(f"        {D('… %d tane daha' % (len(ciftler) - 12))}")


def cmd_touch_kalib_oku(a):
    if _t_kok() is None:
        die("Bunun için 'sudo' gerekli.")
    tip, _ = _t_tip_coz(a, "Kalibrasyon okuma")
    title("Dokunmatik — kalibrasyon okunuyor")
    anlik = progress_timed("Cihazdan bloklar okunuyor",
                           lambda: _k_snapshot(tip, a.dene), est=3.0)
    _k_yazdir(anlik)
    _k_teshis(anlik)
    yol = a.cikti or os.path.join(
        TOUCH_YEDEK, time.strftime("kalibrasyon-%Y%m%d-%H%M%S.json"))
    os.makedirs(os.path.dirname(yol) or ".", exist_ok=True)
    _k_yaz_dosya(anlik, yol)
    hr()
    ok(f"Kaydedildi: {Cy(yol)}")
    print(f"  {D('Sağlam bir tahtadan da alıp karşılaştırın:')}")
    print(f"  {D('eta-112.py dokunmatik kalibrasyon karsilastir saglam.json ' + os.path.basename(yol))}")
    return 0


def cmd_touch_kalib_karsilastir(a):
    if len(a.dosyalar) != 2:
        die("Kullanım: dokunmatik kalibrasyon karsilastir <A.json> <B.json>")
    A, B = (_k_oku_dosya(y) for y in a.dosyalar)
    title("Dokunmatik — kalibrasyon karşılaştırması")
    for etiket, x, yol in (("A", A, a.dosyalar[0]), ("B", B, a.dosyalar[1])):
        print(f"  {C.B}{etiket}{C.R}  {Cy(os.path.basename(yol))}  "
              f"{D(x['panel'].get('usb') or '?')}  {D('sürücü ' + str(x.get('surucu')))}  "
              f"{D(x['tarih'])}")
    if A["panel"]["tip"] != B["panel"]["tip"]:
        warn("Panel tipleri farklı; karşılaştırma anlamlı olmayabilir.")
    print()
    ab = {b["ad"]: b for b in B["bloklar"]}
    farkli = 0
    for ba in A["bloklar"]:
        bb = ab.get(ba["ad"])
        if not bb:
            print(f"  {ba['ad']:<16} {Y('B tarafında yok')}")
            continue
        if "hata" in ba or "hata" in bb:
            print(f"  {ba['ad']:<16} {Y('bir tarafta okunamamış')}")
            continue
        x, y = bytes.fromhex(ba["veri"]), bytes.fromhex(bb["veri"])
        if x == y:
            print(f"  {ba['ad']:<16} {G('aynı')}  {D('%d bayt' % len(x))}")
            continue
        farkli += 1
        ofsetler = [i for i in range(min(len(x), len(y))) if x[i] != y[i]]
        print(f"  {ba['ad']:<16} {R('%d bayt farklı' % len(ofsetler))}")
        for i in ofsetler[:24]:
            print(f"      {D('+0x%02x' % i)}  A={Cy('%02x' % x[i])}  B={Y('%02x' % y[i])}")
        if len(ofsetler) > 24:
            print(f"      {D('… %d fark daha' % (len(ofsetler) - 24))}")
        fa, fb = _k_floatlar(x), _k_floatlar(y)
        for i, (u, v) in enumerate(zip(fa, fb)):
            if u != v and not (u != u and v != v):     # NaN != NaN'i ele
                print(f"      {D('float[%d]' % i)}  A={Cy('%.6g' % u)}  B={Y('%.6g' % v)}")
    hr()
    if farkli:
        warn(f"{farkli} blok farklı. Kalibrasyon verisi iki panelde aynı değil.")
    else:
        ok("Tüm bloklar aynı. Fark kalibrasyon verisinde değil.")
    return 0


def cmd_touch_kalib_yaz(a):
    if _t_kok() is None:
        die("Bunun için 'sudo' gerekli.")
    if not a.dosyalar:
        die("Kullanım: dokunmatik kalibrasyon yaz <anlik.json> --onayliyorum")
    anlik = _k_oku_dosya(a.dosyalar[0])
    tip, kimlik = _t_tip_coz(a, "Kalibrasyon yazma")
    yol = _k_aygit_yolu(tip)
    if not yol:
        die("Sürücünün aygıt düğümü yok; yazılamaz.  'dokunmatik durum' ile bakın.")
    if anlik["panel"]["tip"] != tip:
        die(f"Anlık görüntü {anlik['panel']['tip']} panelden alınmış, hedef {tip}. "
            "Farklı panel tipine yazılamaz.")
    if tip != "optical" and not a.dene:
        die("OTD (2621) panellerde komut kimlikleri doğrulanmadı; yazma varsayılan "
            "olarak kapalı.\n    Ne yaptığınızı biliyorsanız:  --dene --onayliyorum")

    title("Dokunmatik — kalibrasyon cihaza yazılacak")
    print(f"  Kaynak dosya   : {Cy(a.dosyalar[0])}")
    print(f"  Alındığı panel : {D(anlik['panel'].get('usb') or '?')}  {D(anlik['tarih'])}")
    print(f"  Hedef panel    : {G(kimlik)}  {D(tip)}")
    print()
    print(f"  {ERR} {R('YAZMA KOMUTU DOĞRULANMADI.')}")
    print(f"     {Y('Okuma protokolü ikiliden birebir sökülerek çıkarıldı ve kesindir.')}")
    print(f"     {Y('Yazma için aynı komut/indeks çiftlerinin set paketiyle kullanıldığı')}")
    print(f"     {Y('VARSAYILIYOR — üretici aracının bunu yaptığı gözlemlenmedi. Yanlış')}")
    print(f"     {Y('komut panelin kalibrasyonunu kalıcı olarak bozabilir.')}")
    print()
    if anlik["panel"].get("usb") != kimlik:
        print(f"  {WARN} {Y('Bu anlık görüntü başka bir panelden alınmış.')}")
    if not a.onayliyorum:
        die("Devam etmek için açıkça onaylayın:  --onayliyorum")

    print(f"  {D('Önce mevcut durum yedekleniyor...')}")
    yedek = os.path.join(TOUCH_YEDEK, time.strftime("kalibrasyon-yazmadan-once-%Y%m%d-%H%M%S.json"))
    os.makedirs(TOUCH_YEDEK, exist_ok=True)
    _k_yaz_dosya(_k_snapshot(tip, deneysel=True), yedek)
    ok(f"Yedek: {Cy(yedek)}")
    print()
    if ask(f"  {R('Yazılsın mı?')} Geri almak için yukarıdaki yedeği kullanın "
           "[yaz/iptal]: ").strip().lower() != "yaz":
        warn("İptal edildi; hiçbir şey yazılmadı.")
        return 1

    birim, calisiyordu = _k_servis_durdur(tip)
    yazilan = 0
    try:
        fd = os.open(yol, os.O_RDWR)
        try:
            _k_mod(fd, True)
            for b in anlik["bloklar"]:
                if "hata" in b:
                    warn(f"{b['ad']}: kaynak dosyada eksik — atlandı.")
                    continue
                try:
                    _k_yaz_blok(fd, b["komut"], b["indeks"], bytes.fromhex(b["veri"]))
                    yazilan += 1
                    ok(f"{b['ad']} yazıldı  {D('cmd 0x%02x idx %d' % (b['komut'], b['indeks']))}")
                except (OSError, ValueError) as e:
                    err(f"{b['ad']}: yazılamadı — {e}")
            _k_mod(fd, False)
        finally:
            os.close(fd)
    finally:
        if calisiyordu:
            _t_run(["systemctl", "start", birim])

    hr()
    if not yazilan:
        err("Hiçbir blok yazılamadı.")
        return 1
    ok(f"{yazilan} blok yazıldı.")
    print(f"  {D('Doğrulamak için:  eta-112.py dokunmatik kalibrasyon oku')}")
    print(f"  {D('Geri almak için:  eta-112.py dokunmatik kalibrasyon yaz ' + yedek + ' --onayliyorum')}")
    return 0


def cmd_touch_kalib_depo(a):
    """OTD depolama bolumlerini listele ve bloklari oku.

    Kalibrasyon (CCB/FCB parametreleri) bu bolumlerde duruyor. Yalniz OKUMA
    yapilir: 0xb0 (bolum bilgisi) ve 0xb2/n=4 (blok oku). Yazan (0xb2/n=36) ve
    silen (0xb1) komutlar bu araca hic konulmadi."""
    if _t_kok() is None:
        die("Bunun için 'sudo' gerekli.")
    tip, kimlik = _t_tip_coz(a, "Depolama dökümü")
    if tip == "optical":
        die("Depolama dökümü OTD (2621) panellere özgüdür.")
    yol = _k_aygit_yolu(tip)
    if not yol:
        die("Sürücünün aygıt düğümü yok.  'dokunmatik durum' ile bakın.")

    bolumler = [a.bolum] if a.bolum is not None else list(range(4))
    title("Dokunmatik — OTD depolama")
    print(f"  Panel          : {G(kimlik or '?')}  {D(tip)}  {D(yol)}")
    print(f"  Bölümler       : {Cy(', '.join(str(b) for b in bolumler))}")
    print(f"  Blok           : "
          + (Cy("%d..son" % a.blok) if a.tam
             else Cy("%d..%d" % (a.blok, a.blok + a.blok_sayisi - 1))))
    print(f"  {D('Yalnızca okuma komutları gönderilir (0xb0, 0xb2/n=4).')}")
    if a.tam:
        print(f"  {WARN} {Y('--tam: bölüm 4096 blok bildiriyor; her blok ~100 ms')}")
        print(f"     {Y('→ bölüm başına ~7 dakika sürebilir.')}")
    hr()

    birim, calisiyordu = _k_servis_durdur(tip)
    sonuc = []
    try:
        fd = os.open(yol, os.O_RDWR)
        try:
            for bolum in bolumler:
                try:
                    yuk, ham, durum = _k_oku_blok_otd(fd, 0x1e, bolum,
                                                      KALIB_OTD_BOLUM_BILGI)
                except OSError as e:
                    warn(f"bölüm {bolum}: bilgi okunamadı ({e})")
                    continue
                bilgi = _k_otd_bolum_coz(yuk)
                if not bilgi:
                    warn(f"bölüm {bolum}: durum 0x{durum:02x}, bilgi çözülemedi "
                         f"({ham[:12].hex(' ')})")
                    continue
                print(f"  {C.B}bölüm {bolum}{C.R}  "
                      f"{D('tip')} {bilgi['tip']}  "
                      f"{D('toplam blok')} {bilgi['toplam_blok']}  "
                      f"{D('bölen')} {bilgi['bolen']}  "
                      f"{D('blok boyu')} {bilgi['blok_boyu']}")
                kayit = {"bolum": bolum, "bilgi": bilgi, "bloklar": []}
                # --tam: bolumun bildirdigi son bloga kadar (geri yukleme icin
                # gereken tam goruntu; varsayilan kisa pencere degismedi).
                adet = (bilgi["toplam_blok"] - a.blok) if a.tam else a.blok_sayisi
                for n in range(a.blok, a.blok + max(0, adet)):
                    if n >= bilgi["toplam_blok"]:
                        break
                    try:
                        v, h, d = _k_otd_blok_oku(fd, bolum, n, bilgi["bolen"])
                    except OSError as e:
                        print(f"      blok {n:5d}  {Y('okunamadı: %s' % e)}")
                        continue
                    if v is None:
                        print(f"      blok {n:5d}  {Y('durum 0x%02x' % d)}  "
                              f"{D(h[:12].hex(' '))}")
                        continue
                    kayit["bloklar"].append({"no": n, "veri": v.hex()})
                    print(f"      blok {n:5d}  {D('%d bayt' % len(v))}  {v.hex(' ')}")
                    f = _k_floatlar(v)
                    if f and any(1e-6 < abs(x) < 1e6 for x in f):
                        print(f"             {D('float32:')} "
                              + " ".join(f"{x:.6g}" for x in f[:8]))
                sonuc.append(kayit)
        finally:
            os.close(fd)
    except OSError as e:
        if calisiyordu:
            _t_run(["systemctl", "start", birim])
        die(f"Cihaza erişilemedi: {e}")
    finally:
        if calisiyordu:
            _t_run(["systemctl", "start", birim])

    hr()
    if not sonuc:
        warn("Hiçbir bölüm okunamadı.")
        return 1
    anlik = {
        "bicim": "eta-112-dokunmatik-depo/1",
        "tarih": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "panel": {"tip": tip, "usb": kimlik, "aygit": yol},
        "surucu": _t_kurulu(), "makine": platform.node(),
        "bolumler": sonuc,
    }
    hedef = a.cikti or os.path.join(
        TOUCH_YEDEK, time.strftime("depo-%Y%m%d-%H%M%S.json"))
    os.makedirs(os.path.dirname(hedef) or ".", exist_ok=True)
    _k_yaz_dosya(anlik, hedef)
    ok(f"Kaydedildi: {Cy(hedef)}")
    return 0


def cmd_touch_kalib_tara(a):
    """OTD komut/indeks taramasi - hangi cift yanit veriyor?

    Varsayilan kume GUVENLI: yalniz OtdTouchServer ikilisinde YUK TASIMAYAN
    okuma olarak gorulen komutlar (0xb0, 0xae) indeks boyunca supurulur.
    Rastgele komut kimligi denemek saglam bir panelde yazma/sifirlama
    tetikleyebilir; bu yuzden genis tarama --aralik ile ve ayrica onayla."""
    if _t_kok() is None:
        die("Bunun için 'sudo' gerekli.")
    tip, kimlik = _t_tip_coz(a, "Komut taraması")
    if tip == "optical":
        die("Tarama şimdilik yalnız OTD (2621) panelleri içindir.\n"
            "    Optical komut haritası zaten çözülmüş durumda.")
    yol = _k_aygit_yolu(tip)
    if not yol:
        die("Sürücünün aygıt düğümü yok.  'dokunmatik durum' ile bakın.")

    komutlar = [0xb0, 0xae]
    if a.aralik:
        try:
            bas, son = (int(x, 0) for x in a.aralik.replace("-", " ").split())
        except ValueError:
            die("--aralik biçimi:  --aralik 0xa0-0xbf")
        if not 0 <= bas <= son <= 0xff:
            die("--aralik 0x00 ile 0xff arasında olmalı.")
        komutlar = [k for k in range(bas, son + 1) if k not in KALIB_OTD_TEHLIKELI]
        atlanan = [k for k in range(bas, son + 1) if k in KALIB_OTD_TEHLIKELI]
        if atlanan:
            # 0xb1 = EraseStorage, 0xb2 = Set/GetStorageBlock. Yuksuz bir paketle
            # bile tetiklenmelerini goze alamayiz: saglam bir panelin
            # kalibrasyonu silinebilir.
            warn("Taramadan çıkarıldı: "
                 + ", ".join("0x%02x" % k for k in atlanan)
                 + "  (silme/yazma komutları)")
        if not komutlar:
            die("Verilen aralıkta taranabilecek güvenli komut kalmadı.")

    title("Dokunmatik — OTD komut taraması")
    print(f"  Panel          : {G(kimlik or '?')}  {D(tip)}  {D(yol)}")
    print(f"  Komutlar       : {Cy(', '.join('0x%02x' % k for k in komutlar))}")
    print(f"  İndeks         : {Cy('0x%02x-0x%02x' % (0, a.encok_indeks))}")
    print(f"  {D('Gönderilen paketlerin yükü yok (n=0); panele veri yazılmaz.')}")
    if a.aralik:
        print()
        print(f"  {WARN}  {Y('GENİŞ TARAMA — bu komutların ne yaptığı bilinmiyor.')}")
        print(f"     {Y('Bilinmeyen bir komut paneli sıfırlayabilir veya kalibrasyonu bozabilir.')}")
        if ask("  Devam edilsin mi? [e/H]: ").strip().lower() not in ("e", "evet", "y"):
            return 0
    hr()

    birim, calisiyordu = _k_servis_durdur(tip)
    yanit = []
    try:
        fd = os.open(yol, os.O_RDWR)
        try:
            for komut in komutlar:
                for indeks in range(a.encok_indeks + 1):
                    try:
                        yuk, tam, durum = _k_oku_blok_otd(fd, a.b1, indeks, komut)
                    except OSError as e:
                        im = D("stall" if ("Errno 32" in str(e)
                                           or "Broken pipe" in str(e)) else str(e)[:30])
                    else:
                        yanit.append({"komut": komut, "indeks": indeks,
                                      "durum": durum,
                                      "veri": (yuk or b"").hex(),
                                      "ham_cevap": tam.hex()})
                        etiket = {KALIB_OTD_VERI: "VERİ", KALIB_OTD_TAMAM: "ACK"}.get(
                            durum, "durum")
                        im = (G(f"{etiket} 0x{durum:02x}") if yuk
                              else Y(f"{etiket} 0x{durum:02x}"))
                        if yuk:
                            im += D(f"  {len(yuk)} bayt")
                    print(f"  cmd 0x{komut:02x}  idx 0x{indeks:02x}   {im}")
        finally:
            os.close(fd)
    except OSError as e:
        if calisiyordu:
            _t_run(["systemctl", "start", birim])
        die(f"Cihaza erişilemedi: {e}")
    finally:
        if calisiyordu:
            _t_run(["systemctl", "start", birim])

    hr()
    if not yanit:
        warn("Hiçbir komut/indeks çifti yanıt vermedi.")
        return 1
    ok(f"{len(yanit)} çift yanıt verdi.")
    for r in yanit:
        ham = bytes.fromhex(r["veri"])
        tam = bytes.fromhex(r["ham_cevap"])
        print(f"  cmd 0x{r['komut']:02x} idx 0x{r['indeks']:02x}  "
              f"{D('durum 0x%02x' % r['durum'])}  {D('%d bayt veri' % len(ham))}")
        print(f"      {D('ham cevap (ilk 32):')}")
        for i in range(0, 32, 16):
            print(f"      {D('%02x' % i)}  {tam[i:i+16].hex(' ')}")
        if ham:
            f = _k_floatlar(ham)
            if f:
                print(f"      {D('float32:')} " + " ".join(f"{x:.6g}" for x in f[:8]))
    return 0


def cmd_touch_kalib_ham(a):
    """Tek bir komut/indeks çiftini elle sorgula — protokol keşfi için."""
    if _t_kok() is None:
        die("Bunun için 'sudo' gerekli.")
    tip, _ = _t_tip_coz(a, "Ham blok sorgusu")
    yol = _k_aygit_yolu(tip)
    if not yol:
        die("Sürücünün aygıt düğümü yok.")
    birim, calisiyordu = _k_servis_durdur(tip)
    mod_hatasi = None
    otd_durum = None
    try:
        fd = os.open(yol, os.O_RDWR)
        try:
            # Mod komutu (0x71) Optical'a ozgu. Kesif araci OTD panelde de
            # kullanilabilmeli, bu yuzden STALL burada olumcul degil: uyarilir
            # ve asil sorgu yine de denenir.
            if tip == "optical":
                try:
                    _k_mod(fd, True)
                except OSError as e:
                    mod_hatasi = str(e)
                yuk, tam = _k_oku_blok(fd, a.komut, a.indeks, a.uzunluk)
                try:
                    _k_mod(fd, False)
                except OSError:
                    pass
            else:
                yuk, tam, otd_durum = _k_oku_blok_otd(fd, a.b1, a.indeks, a.komut)
                yuk = yuk if yuk is not None else b""
        finally:
            os.close(fd)
    except OSError as e:
        if calisiyordu:
            _t_run(["systemctl", "start", birim])
        if mod_hatasi:
            warn(f"Yapılandırma moduna da girilemedi (0x71): {mod_hatasi}")
        die(f"Sorgu başarısız: {e}")
    if calisiyordu:
        _t_run(["systemctl", "start", birim])
    title(f"Ham sorgu — cmd 0x{a.komut:02x} indeks {a.indeks}")
    if otd_durum is not None:
        etiket = {KALIB_OTD_VERI: "veri taşıyor", KALIB_OTD_TAMAM: "ACK, veri yok"}.get(
            otd_durum, "bilinmeyen")
        print(f"  {D('cevap durumu:')} {Cy('0x%02x' % otd_durum)}  {D(etiket)}")
    if mod_hatasi:
        print(f"  {WARN} {Y('Yapılandırma moduna girilemedi (0x71): ' + mod_hatasi)}")
        print(f"  {D('Sorgu yine de yanıt verdi; aşağıdaki veri o moda girmeden alındı.')}")
    print(f"  {D('tam 64 baytlık cevap:')}")
    for i in range(0, 64, 16):
        print(f"      {D('%02x' % i)}  {tam[i:i+16].hex(' ')}")
    print(f"  {D('yük (ofset 5, %d bayt):' % a.uzunluk)}  {Cy(yuk.hex(' '))}")
    f = _k_floatlar(yuk)
    if f:
        print(f"  {D('float32:')} " + " ".join(f"{x:.6g}" for x in f))
    return 0


def _k_otd_yaz_hazirla(a, islem):
    """OTD yazma komutlarinin ortak on kosullari. -> (tip, kimlik, yol)"""
    if _t_kok() is None:
        die("Bunun için 'sudo' gerekli.")
    tip, kimlik = _t_tip_coz(a, islem)
    if tip != "otd":
        die("Bu komut OTD (2621) panellere özgüdür.\n"
            "    Optical (6615) tarafında blok yazma yolu yok; "
            "orada 'kalibrasyon yaz' kullanılır.")
    yol = _k_aygit_yolu(tip)
    if not yol:
        die("Sürücünün aygıt düğümü yok.  'dokunmatik durum' ile bakın.")
    return tip, kimlik, yol


def _k_otd_bolum_bilgi_al(fd, bolum):
    """0xb0 ile bolum bilgisi; cozulemezse die. -> dict"""
    yuk, ham, durum = _k_oku_blok_otd(fd, 0x1e, bolum, KALIB_OTD_BOLUM_BILGI)
    bilgi = _k_otd_bolum_coz(yuk)
    if not bilgi:
        die(f"bölüm {bolum}: bilgi okunamadı (durum 0x{durum:02x}, "
            f"{ham[:12].hex(' ')})")
    if bilgi["blok_boyu"] != KALIB_OTD_BLOK_BOYU:
        die(f"bölüm {bolum}: blok boyu {bilgi['blok_boyu']} bayt, "
            f"{KALIB_OTD_BLOK_BOYU} bekleniyordu — yazma yükü düzeni geçersiz.")
    return bilgi


def cmd_touch_kalib_yazma_testi(a):
    """No-op yazma dogrulamasi -- icerigi DEGISTIRMEDEN yazma yolunu olcer.

    Yazma komutu (0xb2/n=36, b1=0x2d) ve varsayilan yuk duzeni (4 bayt adres +
    32 bayt veri) cihazda gecerli mi? Bunu olcmenin brick'siz yolu, bir blogu
    KENDI okunan degeriyle yazmak: NOR flash olup silme gerekse bile icerik
    degismez (x & x = x). Geriye kalan tek risk yanlis adreslemedir; onu da
    yazmadan once/sonra bolumun tamamini dokup karsilastirarak yakalariz.

    Adimlar:  bolum bilgisi -> tam dokum (referans) -> blogu oku -> ayni
    baytlari yaz -> blogu geri oku -> tam dokumu yenile -> kiyasla."""
    tip, kimlik, yol = _k_otd_yaz_hazirla(a, "Yazma testi")
    bolum = a.bolum if a.bolum is not None else 0

    title("Dokunmatik — OTD yazma yolu testi (no-op)")
    print(f"  Panel          : {G(kimlik or '?')}  {D(tip)}  {D(yol)}")
    print(f"  Bölüm          : {Cy(str(bolum))}")
    print(f"  Blok           : "
          + (Cy(str(a.blok)) + D("  (--blok ile verildi)") if a.blok
             else D("bölüm bilgisine göre seçilecek (ters adres → STALL)")))
    print(f"  {D('Blok kendi değeriyle yazılır; hiçbir bayt değişmez.')}")
    print(f"  {D('Kalibrasyon kayıtları bölüm 0x80; bölüm 0 blok 0-3 kamera kaydı.')}")
    if 0 < a.blok <= 3:
        warn(f"Blok {a.blok} kayıt tablosunda kullanılıyor (0=seri, 1-3=kamera "
             "parametresi). Test için kullanılmayan bir blok daha güvenlidir.")
    if not a.onayliyorum:
        die("Yazma komutu gönderilecek (içerik değişmese de). Onaylayın:  --onayliyorum")
    hr()

    birim, calisiyordu = _k_servis_durdur(tip)
    sonuc = {"bicim": "eta-112-dokunmatik-yazma-testi/1",
             "tarih": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
             "panel": {"tip": tip, "usb": kimlik, "aygit": yol},
             "surucu": _t_kurulu(), "makine": platform.node(),
             "bolum": bolum}   # 'blok' bolum bilgisi okunduktan sonra eklenir
    try:
        fd = os.open(yol, os.O_RDWR)
        try:
            bilgi = _k_otd_bolum_bilgi_al(fd, bolum)
            sonuc["bolum_bilgisi"] = bilgi
            print(f"  {D('bölüm bilgisi:')} toplam blok {bilgi['toplam_blok']}  "
                  f"bölen {bilgi['bolen']}  blok boyu {bilgi['blok_boyu']}")
            # Blok, bolum bilgisi olmadan secilemez: ters adres okumasinin
            # bolum disina dusmesi icin bolen/toplam gerekiyor.
            blok = a.blok if a.blok else _k_otd_test_blok(bilgi)
            sonuc["blok"] = blok
            if blok >= bilgi["toplam_blok"]:
                die(f"blok {blok}, bölümün {bilgi['toplam_blok']} bloğu dışında.")
            if not a.blok:
                ters = (blok % bilgi["bolen"]) * bilgi["bolen"] + blok // bilgi["bolen"]
                print(f"  {D('seçilen test bloğu:')} {Cy(str(blok))}  "
                      + (D("(adres alanları ters okunursa %d → bölüm dışı → STALL)" % ters)
                         if ters >= bilgi["toplam_blok"]
                         else Y("(ters okuma %d → bölüm içi; dikkat)" % ters)))

            # Pencere yalniz kayit bolgesini kapsar; test blogunun cevresi
            # _k_otd_kiyas_goruntu icinde ikinci bir parca olarak okunur.
            pencere = (a.blok_sayisi if a.blok_sayisi != 8
                       else KALIB_OTD_KIYAS_PENCERE)
            pencere = min(pencere, bilgi["toplam_blok"])
            sonuc["kiyas_penceresi"] = pencere
            _p1 = "1/5 blok 0-%d + test bloğu çevresi dökülüyor (referans)..." % (
                pencere - 1)
            print(f"  {D(_p1)}")
            once = _k_otd_kiyas_goruntu(fd, bolum, bilgi, blok, pencere)
            sonuc["referans_blok_sayisi"] = len(once)
            if blok not in once:
                die(f"blok {blok} okunamadı; test edilemez. Başka bir blok deneyin "
                    "(--blok N).")
            ozgun = once[blok]
            print(f"      {D('%d blok okundu' % len(once))}")
            print(f"  {D('2/5 test bloğu:')}  {ozgun.hex(' ')}")

            print(f"  {D('3/5 aynı baytlar geri yazılıyor...')}")
            try:
                _v, ham, durum = _k_otd_blok_yaz(fd, bolum, blok, ozgun,
                                                 bilgi["bolen"])
            except OSError as e:
                sonuc["yazma"] = {"kabul": False, "hata": str(e)}
                durum = None
                stall = "Errno 32" in str(e) or "Broken pipe" in str(e)
                print(f"      {Y('yazma reddedildi: %s' % e)}")
                if stall:
                    print(f"      {D('STALL = panel bu komutu tanımadı. Veri değişmedi.')}")
            else:
                kabul = durum in (KALIB_OTD_TAMAM, KALIB_OTD_VERI)
                sonuc["yazma"] = {"kabul": kabul, "durum": durum,
                                  "ham_cevap": ham.hex()}
                etiket = {KALIB_OTD_TAMAM: "ACK", KALIB_OTD_VERI: "veri"}.get(
                    durum, "bilinmeyen")
                im = G if kabul else Y
                print(f"      {im('cevap durumu 0x%02x (%s)' % (durum, etiket))}")

            print(f"  {D('4/5 blok geri okunuyor...')}")
            simdi, _h, _d = _k_otd_blok_oku(fd, bolum, blok, bilgi["bolen"])
            korundu = simdi == ozgun
            sonuc["blok_korundu"] = korundu
            if korundu:
                ok("Test bloğu birebir aynı — içerik korundu.")
            else:
                err(f"Test bloğu DEĞİŞTİ: {(simdi or b'').hex(' ')}")
                sonuc["blok_yeni"] = (simdi or b"").hex()
                sonuc["blok_ozgun"] = ozgun.hex()

            print(f"  {D('5/5 aynı bloklar yeniden dökülüyor (yan etki kontrolü)...')}")
            sonra = _k_otd_kiyas_goruntu(fd, bolum, bilgi, blok, pencere)
            degisen = sorted(n for n in once if n in sonra and once[n] != sonra[n])
            kaybolan = sorted(n for n in once if n not in sonra)
            sonuc["degisen_bloklar"] = degisen
            sonuc["okunamayan_bloklar"] = kaybolan
            sonuc["referans"] = {str(n): v.hex() for n, v in once.items()}
        finally:
            os.close(fd)
    except OSError as e:
        if calisiyordu:
            _t_run(["systemctl", "start", birim])
        die(f"Cihaza erişilemedi: {e}")
    finally:
        if calisiyordu:
            _t_run(["systemctl", "start", birim])

    hedef = a.cikti or os.path.join(
        TOUCH_YEDEK, time.strftime("yazma-testi-%Y%m%d-%H%M%S.json"))
    os.makedirs(os.path.dirname(hedef) or ".", exist_ok=True)
    _k_yaz_dosya(sonuc, hedef)

    hr()
    yz = sonuc.get("yazma") or {}
    degisen = sonuc.get("degisen_bloklar") or []
    kaybolan = sonuc.get("okunamayan_bloklar") or []
    if degisen:
        err("YAN ETKİ: şu bloklar değişti: "
            + ", ".join(str(n) for n in degisen))
        print(f"  {D('Adres düzeni varsayımı yanlış olabilir — yazma BAŞKA bloğa gitti.')}")
        print(f"  {D('Referans döküm dosyada; geri yükleme için:')}")
        print(f"  {D('  eta-112.py dokunmatik kalibrasyon depo-yaz ' + hedef)}")
        ok(f"Kaydedildi: {Cy(hedef)}")
        return 1
    if kaybolan:
        warn("Şu bloklar artık okunamıyor: " + ", ".join(str(n) for n in kaybolan))
    if not yz.get("kabul"):
        warn("Yazma yolu ÇALIŞMIYOR: panel komutu kabul etmedi.")
        print(f"  {D('İyi haber: hiçbir bayt değişmedi, panel sağlam.')}")
        print(f"  {D('b1 baytı farklı olabilir:  --b1 0x1e / 0x3c ile tekrar deneyin.')}")
        ok(f"Kaydedildi: {Cy(hedef)}")
        return 1
    if not sonuc.get("blok_korundu"):
        err("Yazma kabul edildi ama blok içeriği bozuldu.")
        print(f"  {D('Silme (erase) gerekiyor olabilir; 0xb1 bu araca konulmadı.')}")
        ok(f"Kaydedildi: {Cy(hedef)}")
        return 1
    ok("Yazma yolu DOĞRULANDI: komut kabul edildi, içerik korundu, yan etki yok.")
    print(f"  {D('Adres düzeni (4 bayt adres + 32 bayt veri) cihazda geçerli.')}")
    print(f"  {D('Artık ham yedek geri yüklenebilir:  kalibrasyon depo-yaz <depo.json>')}")
    ok(f"Kaydedildi: {Cy(hedef)}")
    return 0


def cmd_touch_kalib_depo_yaz(a):
    """Ham blok dokumunu ('depo' cikti dosyasi) cihaza geri yaz.

    Icerigin ANLAMINI bilmeye gerek yok: 32 baytlik bloklar okundugu gibi
    geri konur. Guvenlik siniri:
      * yalniz OTD; panel tipi ve dosya bicimi dogrulanir,
      * blok 0 (ASCII seri / ProductKey) VARSAYILAN OLARAK atlanir,
      * yazmadan once hedefin tam dokumu yedeklenir,
      * hedefte zaten ayni olan blok yazilmaz,
      * her blok yazildiktan sonra geri okunup dogrulanir; ilk uyusmazlikta
        durulur (kalan bloklara dokunulmaz)."""
    if not a.dosyalar:
        die("Kullanım: dokunmatik kalibrasyon depo-yaz <depo.json> --onayliyorum")
    kaynak = _k_oku_dosya_depo(a.dosyalar[0])
    tip, kimlik, yol = _k_otd_yaz_hazirla(a, "Blok geri yükleme")
    if kaynak["panel"]["tip"] != tip:
        die(f"Döküm {kaynak['panel']['tip']} panelden alınmış, hedef {tip}. "
            "Farklı panel tipine yazılamaz.")

    istenen = [b for b in kaynak["bolumler"]
               if a.bolum is None or b["bolum"] == a.bolum]
    if not istenen:
        die(f"Dökümde bölüm {a.bolum} yok.")

    title("Dokunmatik — ham blok geri yükleme")
    print(f"  Kaynak döküm   : {Cy(a.dosyalar[0])}")
    print(f"  Alındığı panel : {D(kaynak['panel'].get('usb') or '?')}  {D(kaynak['tarih'])}")
    print(f"  Hedef panel    : {G(kimlik or '?')}  {D(tip)}  {D(yol)}")
    print(f"  Bölümler       : {Cy(', '.join(str(b['bolum']) for b in istenen))}")
    print(f"  Seri bloğu (0) : "
          + (Y("YAZILACAK (--seri-dahil)") if a.seri_dahil else D("atlanacak")))
    print()
    print(f"  {ERR} {R('YAZMA YÜKÜ DÜZENİ VARSAYIMA DAYANIR.')}")
    print(f"     {Y('Okuma protokolü kesindir; yazmada 4 bayt adres + 32 bayt veri')}")
    print(f"     {Y('düzeni varsayılır. Önce doğrulayın:')}")
    print(f"     {Y('  eta-112.py dokunmatik kalibrasyon yazma-testi --onayliyorum')}")
    print()
    if kaynak["panel"].get("usb") != kimlik:
        warn("Bu döküm başka bir panelden alınmış.")
        print(f"  {D('Kalibrasyon panele özgüdür (kamera konumu, cam, montaj')}")
        print(f"  {D('toleransı). Sonuç çalışan ama hizası kaymış bir ekran olabilir;')}")
        print(f"  {D('ardından panelin kendi kalibrasyon aracıyla yeniden kalibre edin.')}")
    if not a.onayliyorum:
        die("Devam etmek için açıkça onaylayın:  --onayliyorum")

    birim, calisiyordu = _k_servis_durdur(tip)
    yedek = os.path.join(
        TOUCH_YEDEK, time.strftime("depo-yazmadan-once-%Y%m%d-%H%M%S.json"))
    yazilan = atlanan = 0
    basarisiz = None
    try:
        fd = os.open(yol, os.O_RDWR)
        try:
            # --- yazmadan once: hedefin tam dokumu -----------------------
            print(f"  {D('Hedefin mevcut durumu yedekleniyor...')}")
            yedek_bolumler = []
            plan = []
            for kb in istenen:
                bolum = kb["bolum"]
                bilgi = _k_otd_bolum_bilgi_al(fd, bolum)
                # Yedek penceresi: dokumun dokundugu en yuksek blok + marj.
                # Bolum 4096 blok bildiriyor; tamamini dokmek ~7 dakika ve
                # yazmayacagimiz bloklar icin gereksiz.
                enbuyuk = max((b.get("no", 0) for b in kb.get("bloklar") or []),
                              default=0)
                pencere = min(bilgi["toplam_blok"],
                              max(KALIB_OTD_KIYAS_PENCERE, enbuyuk + 8))
                mevcut = _k_otd_bolum_dok(fd, bolum, bilgi, 0, pencere)
                yedek_bolumler.append({
                    "bolum": bolum, "bilgi": bilgi,
                    "bloklar": [{"no": n, "veri": v.hex()}
                                for n, v in sorted(mevcut.items())]})
                for blok in kb.get("bloklar") or []:
                    n = blok["no"]
                    try:
                        veri = bytes.fromhex(blok["veri"])
                    except ValueError:
                        warn(f"bölüm {bolum} blok {n}: geçersiz hex — atlandı.")
                        continue
                    if len(veri) != KALIB_OTD_BLOK_BOYU:
                        warn(f"bölüm {bolum} blok {n}: {len(veri)} bayt "
                             f"({KALIB_OTD_BLOK_BOYU} olmalı) — atlandı.")
                        continue
                    if n == KALIB_OTD_SERI_BLOK and not a.seri_dahil:
                        atlanan += 1
                        continue
                    if n >= bilgi["toplam_blok"]:
                        warn(f"bölüm {bolum} blok {n}: bölüm dışında — atlandı.")
                        continue
                    if mevcut.get(n) == veri:
                        atlanan += 1
                        continue
                    plan.append((bolum, n, veri, bilgi["bolen"]))
            os.makedirs(TOUCH_YEDEK, exist_ok=True)
            _k_yaz_dosya({"bicim": "eta-112-dokunmatik-depo/1",
                          "tarih": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                          "panel": {"tip": tip, "usb": kimlik, "aygit": yol},
                          "surucu": _t_kurulu(), "makine": platform.node(),
                          "bolumler": yedek_bolumler}, yedek)
            ok(f"Yedek: {Cy(yedek)}")
            print(f"  {D('Yazılacak blok: %d   ·   atlanan: %d' % (len(plan), atlanan))}")
            if not plan:
                hr()
                ok("Yazılacak blok yok; hedef zaten dökümle aynı.")
                return 0
            print()
            if ask(f"  {R('Yazılsın mı?')} Geri almak için yukarıdaki yedeği kullanın "
                   "[yaz/iptal]: ").strip().lower() != "yaz":
                warn("İptal edildi; hiçbir şey yazılmadı.")
                return 1

            # --- yaz + her blogu geri okuyup dogrula ---------------------
            for bolum, n, veri, bolen in plan:
                etiket = f"bölüm {bolum} blok {n}"
                try:
                    _v, _ham, durum = _k_otd_blok_yaz(fd, bolum, n, veri, bolen)
                except (OSError, ValueError) as e:
                    basarisiz = f"{etiket}: yazılamadı — {e}"
                    break
                if durum not in (KALIB_OTD_TAMAM, KALIB_OTD_VERI):
                    basarisiz = f"{etiket}: panel kabul etmedi (durum 0x{durum:02x})"
                    break
                try:
                    geri, _h, _d = _k_otd_blok_oku(fd, bolum, n, bolen)
                except OSError as e:
                    basarisiz = f"{etiket}: yazıldı ama geri okunamadı — {e}"
                    break
                if geri != veri:
                    basarisiz = (f"{etiket}: geri okuma uyuşmadı\n"
                                 f"    beklenen: {veri.hex(' ')}\n"
                                 f"    okunan  : {(geri or b'').hex(' ')}")
                    break
                yazilan += 1
                ok(f"{etiket} yazıldı ve doğrulandı")
        finally:
            os.close(fd)
    except OSError as e:
        if calisiyordu:
            _t_run(["systemctl", "start", birim])
        die(f"Cihaza erişilemedi: {e}")
    finally:
        if calisiyordu:
            _t_run(["systemctl", "start", birim])

    hr()
    if basarisiz:
        err(basarisiz)
        print(f"  {D('%d blok yazıldıktan sonra durduldu; kalanlara dokunulmadı.' % yazilan)}")
        print(f"  {D('Geri almak için:')}")
        print(f"  {D('  eta-112.py dokunmatik kalibrasyon depo-yaz ' + yedek + ' --onayliyorum')}")
        return 1
    ok(f"{yazilan} blok yazıldı ve geri okumayla doğrulandı  "
       f"{D('(atlanan: %d)' % atlanan)}")
    print(f"  {D('Kontrol için:  eta-112.py dokunmatik kalibrasyon depo --tam')}")
    print(f"  {D('Geri almak için:  eta-112.py dokunmatik kalibrasyon depo-yaz ' + yedek + ' --onayliyorum')}")
    print(f"  {D('Kalibrasyon başka panelden geldiyse panelin kendi aracıyla yeniden kalibre edin.')}")
    return 0


# ------------------------------------------------------------------ ekrana dokunarak kalibrasyon
# Üreticinin kendi X11 kalibrasyon araçları her eta-touchdrv paketinde gelir:
# ekranda artı işaretleri çizer, kullanıcı dokunur, katsayılar panele gönderilir.
#
# UYUMLULUK — sökülerek doğrulandı. Araçlar sürücüye özel ioctl'lerle konuşur ve
# sabit bir aygıt düğümü açar:
#     OtdCalibrationTool   /dev/OtdOpticTouch   (OtdTouchDriver.c — TOUCH_DEVICE_NAME)
#     calibrationTools     /dev/optictouch      (optictouch.c)
# Bu düğümleri yalnız 0.1.x kernel modülleri oluşturur. 0.2.0'dan beri modüller
# /dev/OtdUsbRaw%03d ve /dev/IRTouchOptical%03d üretiyor; araçlar paketlerde hâlâ
# duruyor ama eski düğümü aradığı için güncel sürücüyle çalışmaz. Bu yüzden
# uyumluluk sürüm numarasından değil, aracın açtığı düğümün varlığından anlaşılır.
#
# Bayraklar (argc == 2 ile strcmp — sökümden):
#     OtdCalibrationTool   --reset
#     calibrationTools     -reset · -a (16 nokta)
# Araçlar yalnız system("clear") çağırır; servise dokunmaz.

KALIB_ARACLARI = {
    "otd": ("OtdCalibrationTool", "touch4/OpticalTouchCalibrationTool.{arch}"),
    "optical": ("calibrationTools", "touch2/OpticalTouchCalibrationTool.{arch}"),
}
KALIB_ARAC_MODULU = {"OtdOpticTouch": "OtdTouchDriver.c", "optictouch": "optictouch.c"}
KALIB_ARAC_SIFIRLA = {"otd": "--reset", "optical": "-reset"}
KALIB_ARAC_GELISMIS = {"optical": "-a"}          # OTD aracında 16 nokta kipi yok
KALIB_ARAC_ZAMAN_ASIMI = 300                     # araç takılırsa ekranı kilitli bırakmasın


def _k_arac_yolu(tip):
    """Bu panel tipine ait üretici kalibrasyon aracı. -> yol | None"""
    for ad in KALIB_ARACLARI[tip]:
        yol = "/usr/bin/" + ad.format(arch=platform.machine())
        if os.path.exists(yol):
            return yol
    return None


def _k_arac_dugumu(yol):
    """Aracın açtığı aygıt düğümünü ikilinin dizgilerinden oku. -> '/dev/..' | None

    /dev/bus/usb (gömülü libusb) ve /dev/null gibi genel düğümler elenir; kalan
    tek düğüm aracın sürücüyle konuştuğu yerdir."""
    try:
        with open(yol, "rb") as f:
            veri = f.read()
    except OSError:
        return None
    for m in re.finditer(rb"/dev/([A-Za-z][A-Za-z0-9_]*)\x00", veri):
        ad = m.group(1).decode()
        if ad not in ("null", "full", "zero", "tty", "console"):
            return "/dev/" + ad
    return None


def _k_karakter_aygiti_mi(yol):
    import stat
    try:
        return stat.S_ISCHR(os.stat(yol).st_mode)
    except OSError:
        return False


def _k_x_ekrani():
    """Aracın açılacağı X ekranı ve yetki dosyası. -> (DISPLAY, XAUTHORITY|None) | None

    Önce çalışan Xorg'un komut satırına bakılır (lightdm: -auth /var/run/lightdm/root/:0);
    root o dosyayla her oturuma bağlanabilir. Bulunamazsa sudo'nun koruduğu
    DISPLAY/XAUTHORITY, o da yoksa çağıran kullanıcının ~/.Xauthority'si denenir."""
    for pid in sorted(glob.glob("/proc/[0-9]*/cmdline")):
        try:
            with open(pid, "rb") as f:
                argv = f.read().decode(errors="replace").split("\0")
        except OSError:
            continue
        if os.path.basename(argv[0]) not in ("Xorg", "X"):
            continue
        ekran = next((x for x in argv[1:] if re.fullmatch(r":\d+", x)), ":0")
        yetki = argv[argv.index("-auth") + 1] if "-auth" in argv[:-1] else None
        return ekran, yetki
    ekran = os.environ.get("DISPLAY")
    if not ekran:
        return None
    yetki = os.environ.get("XAUTHORITY")
    if not yetki and os.environ.get("SUDO_USER"):
        aday = os.path.expanduser(f"~{os.environ['SUDO_USER']}/.Xauthority")
        yetki = aday if os.path.exists(aday) else None
    return ekran, yetki


def _k_arac_uyumsuz(tip, arac, dugum):
    """Araç bu sürücüyle çalışmıyor: nedenini ve yapılabilecekleri anlat."""
    modul = KALIB_ARAC_MODULU.get(os.path.basename(dugum))
    suruculer = sorted(glob.glob("/dev/OtdUsbRaw*" if tip == "otd" else "/dev/IRTouchOptical*"))
    print(f"  Sürücü aygıtı  : {G(', '.join(suruculer)) if suruculer else Y('yok')}")
    hr()
    warn("Kalibrasyon aracı bu sürücüyle uyumlu değil.")
    print(f"  {D(os.path.basename(arac) + ' ' + dugum + ' aygıtını açar; o düğümü yalnız')}")
    print(f"  {D('0.1.x kernel modülü (' + (modul or '?') + ') oluşturur. Kurulu sürücü')}")
    print(f"  {D('başka bir düğüm kullandığı için araç panele ulaşamaz.')}")
    man = _t_manifest(None, sessiz=True) if modul else None
    if not man:
        return 1
    uygun = [k for k in man["surumler"]
             if k["sinif"] == "resmi" and modul in (k.get("modul_kaynagi") or [])]
    if not uygun:
        return 1
    yeni_cekirdek = tuple(int(x) for x in platform.release().split(".")[:2]) >= (6, 8)
    derlenir = [k["surum"] for k in uygun if k["guncel_cekirdekte_derlenir"] or not yeni_cekirdek]
    print()
    print(f"  Uyumlu modülü taşıyan sürümler: {Cy(', '.join(k['surum'] for k in uygun))}")
    if derlenir:
        print(f"  {D('Önce bu sürümü tam kurun, sonra bu seçeneği yeniden çalıştırın:')}")
        print(f"  {D('  Sürüm dene — tam   (eta-112.py dokunmatik dene --kademe 2 --surum ' + derlenir[-1] + ')')}")
    else:
        print(f"  {D('Bu sürümlerin modülü çekirdek ' + platform.release() + ' üzerinde derlenmiyor;')}")
        print(f"  {D('bu tahtada üretici aracıyla kalibrasyon yapılamaz. Kalibrasyon sorunu için')}")
        print("  " + D('"Sürüm dene" ve "Kalibrasyonu oku / karşılaştır" adımlarını kullanın.'))
    return 1


def _k_arac_kipi(a, tip):
    """Kalibre et / 16 nokta / sıfırla. -> araç argümanları | None (vazgeçildi)"""
    if a.sifirla:
        return [KALIB_ARAC_SIFIRLA[tip]]
    if a.gelismis:
        return [KALIB_ARAC_GELISMIS[tip]]
    if not _TTY.isatty():
        return []
    kipler = [("kalibre et (4 nokta)", [])]
    if tip in KALIB_ARAC_GELISMIS:
        kipler.append(("gelişmiş kalibrasyon (16 nokta)", [KALIB_ARAC_GELISMIS[tip]]))
    kipler.append(("kalibrasyonu sıfırla", [KALIB_ARAC_SIFIRLA[tip]]))
    print()
    for i, (ad, _) in enumerate(kipler, 1):
        print(f"    {Cy(str(i))} {ad}")
    for _ in range(3):
        c = ask(f"  Kip [1-{len(kipler)}, v=vazgeç]: ").strip().lower()
        if c in ("v", "q", "vazgec", "vazgeç"):
            return None
        if c.isdigit() and 1 <= int(c) <= len(kipler):
            return kipler[int(c) - 1][1]
        if c == "":
            return kipler[0][1]
        print(f"  {Y('Geçersiz seçim.')}")
    return None


def cmd_touch_kalib_ekran(a):
    if _t_kok() is None:
        die("Bunun için 'sudo' gerekli.")
    tip, kimlik = _t_tip_coz(a, "Ekran kalibrasyonu")
    if a.gelismis and tip not in KALIB_ARAC_GELISMIS:
        die("OTD (4 kamera) kalibrasyon aracında 16 noktalı kip yok.")
    arac = _k_arac_yolu(tip)
    title("Dokunmatik — ekrana dokunarak kalibrasyon")
    print(f"  Panel          : {G(kimlik or '?')}  {D('(' + ('OTD / 4 kamera' if tip == 'otd' else 'Optical / 2 kamera') + ')')}")
    print(f"  Kurulu sürüm   : {G(_t_kurulu() or '?')}")
    if not arac:
        hr()
        adlar = ", ".join(x.format(arch=platform.machine()) for x in KALIB_ARACLARI[tip])
        die(f"Kalibrasyon aracı bulunamadı (/usr/bin: {adlar}).\n"
            f"    Araç eta-touchdrv paketiyle gelir; paket kurulu mu?  eta-112.py dokunmatik durum")
    dugum = _k_arac_dugumu(arac)
    print(f"  Araç           : {Cy(arac)}")
    if not dugum:
        hr()
        die("Aracın hangi aygıtı açtığı ikiliden okunamadı; uyumluluk sınanamıyor.")
    uyumlu = _k_karakter_aygiti_mi(dugum)
    print(f"  Aracın aygıtı  : {G(dugum) if uyumlu else Y(dugum + '  yok')}")
    if not uyumlu:
        return _k_arac_uyumsuz(tip, arac, dugum)

    x = _k_x_ekrani()
    print(f"  X ekranı       : {G(x[0]) + '  ' + D(x[1] or 'yetki dosyası yok') if x else Y('bulunamadı')}")
    hr()
    if not x:
        die("Çalışan bir X oturumu bulunamadı. Araç ekranda çizim yapar;\n"
            "    masaüstü açıkken, tahtanın kendi terminalinden çalıştırın.")
    ok("Araç bu sürücüyle uyumlu.")

    arg = _k_arac_kipi(a, tip)
    if arg is None:
        warn("Vazgeçildi.")
        return 0
    sifirla = arg == [KALIB_ARAC_SIFIRLA[tip]]
    print()
    if sifirla:
        print(f"  {D('Panel kalibrasyonsuz (ham koordinat) duruma döndürülecek. Bu önceki')}")
        print(f"  {D('kalibrasyona dönüş değildir.')}")
    else:
        print(f"  {D('Ekran tam ekran bir kalibrasyon penceresiyle kaplanacak ve sırayla artı')}")
        print(f"  {D('işaretleri çıkacak. Her birinin tam merkezine parmağınızla dokunun.')}")
        print(f"  {D('Araç hesapladığı katsayıları panele yazar; mevcut değerler yedeklenemez')}")
        print(f"  {D('(yalnız sıfırlanabilir). Araç ' + str(KALIB_ARAC_ZAMAN_ASIMI // 60) + ' dakika içinde bitmezse kapatılır.')}")
    if ask("  Başlansın mı? [E/h]: ").strip().lower() in ("h", "hayır", "hayir", "n"):
        return 0

    ortam = dict(os.environ, DISPLAY=x[0])
    if x[1]:
        ortam["XAUTHORITY"] = x[1]
    # stdout yakalanır: araçların system("clear") çağrısı terminali silmesin.
    p = subprocess.Popen([arac] + arg, env=ortam, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    zaman_asimi = False
    try:
        cikti, _ = p.communicate(timeout=KALIB_ARAC_ZAMAN_ASIMI)
    except subprocess.TimeoutExpired:
        p.kill()
        cikti, _ = p.communicate()
        zaman_asimi = True
    except KeyboardInterrupt:
        p.kill()
        p.communicate()
        raise

    satirlar = []
    for s in re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", cikti.decode(errors="replace")).splitlines():
        s = s.strip()
        if s and not s.startswith("*"):          # açılış afişi
            satirlar.append(s)
    for s in satirlar[-8:]:
        print(f"    {D(s)}")
    hr()
    metin = "\n".join(satirlar)
    if zaman_asimi:
        err(f"Araç {KALIB_ARAC_ZAMAN_ASIMI} saniyede bitmedi ve kapatıldı.")
        return 1
    if "cannot connect to X server" in metin:
        err(f"Araç X ekranına ({x[0]}) bağlanamadı.")
        print(f"  {D('Masaüstü oturumu açık mı? Gerekirse:  sudo DISPLAY=:0 XAUTHORITY=... eta-112.py ...')}")
        return 1
    if p.returncode != 0 or re.search(r"(?i)failed|no device found", metin):
        err(f"Kalibrasyon aracı hata bildirdi (çıkış kodu {p.returncode}).")
        return 1
    if sifirla:
        ok("Kalibrasyon sıfırlandı.")
    else:
        ok("Kalibrasyon aracı tamamlandı.")
        print(f"  {D('Ekranın köşelerine ve ortasına dokunarak sonucu kontrol edin. Beğenmezseniz')}")
        print("  " + D('bu adımı yeniden çalıştırın ya da "kalibrasyonu sıfırla" kipini seçin.'))
    return 0


def etatouch_main(argv):
    cmd = (argv[0] if argv else "durum").lower()
    if cmd in ("--help", "-h", "help", "yardim"):
        print(B("eta-112.py dokunmatik") + " — ETAP akıllı tahta dokunmatik sürücü aracı")
        print()
        print("  dokunmatik durum                 panel, kurulu sürüm, servis ve girdi aygıtı")
        print("  dokunmatik liste                 arşivdeki sürümler ve deneme sırası")
        print("  dokunmatik dene                  sürümleri sırayla dene, düzeleni kalıcılaştır")
        print("  dokunmatik kalici <SÜRÜM>        bir sürümü kur ve apt'ye sabitle")
        print("  dokunmatik serbest               sabitlemeyi kaldır")
        print("  dokunmatik geri                  başlangıç durumuna dön")
        print()
        print("  Kalibrasyon — cihazın EEPROM'undaki blokları doğrudan okur/yazar:")
        print("  dokunmatik kalibrasyon oku            blokları oku ve dosyaya kaydet")
        print("  dokunmatik kalibrasyon karsilastir A B   iki anlık görüntüyü karşılaştır")
        print("  dokunmatik kalibrasyon yaz A.json     anlık görüntüyü cihaza geri yaz")
        print("  dokunmatik kalibrasyon ham            tek komut/indeks sorgula")
        print("  dokunmatik kalibrasyon tara           OTD: hangi komut yanıt veriyor")
        print("  dokunmatik kalibrasyon depo           OTD: depolama bölümlerini oku")
        print("  dokunmatik kalibrasyon yazma-testi    OTD: yazma yolunu no-op ile sına")
        print("  dokunmatik kalibrasyon depo-yaz A.json  OTD: ham blok dökümünü geri yaz")
        print("  dokunmatik kalibrasyon ekran          üretici aracıyla ekrana dokunarak kalibre et")
        print()
        print("  Seçenekler:")
        print("    --kademe 1   yalnız sunucu ikilisini değiştir (varsayılan, hızlı)")
        print("    --kademe 2   .deb'i tam kur (modül + sunucu; DKMS derler, yavaş)")
        print("    --surum X    sıradan bağımsız tek bir sürümü dene")
        print("    --yerel YOL  internet yerine yerel dokunmatik/ dizininden oku")
        print("    --tip X      panel tipi: otd (2621, 4 kamera) | optical (6615, 2 kamera)")
        print("                 lsusb panelinizi tanımıyorsa gerekli; algılamayı ezer")
        print("    --zorla      panel algılanamasa da devam et")
        print("    --cikti YOL  kalibrasyon anlık görüntüsünü buraya yaz")
        print("    --dene       OTD (2621) panelde doğrulanmamış komut kümesini dene")
        print("    --onayliyorum   kalibrasyon yazmayı açıkça onayla (gerekli)")
        print("    --cmd/--indeks/--uzunluk   'kalibrasyon ham' için sorgu alanları")
        print("    --b1 X       OTD paketinin [1] baytı (varsayılan 0x1e; 0x2d/0x3c de görüldü)")
        print("    --aralik A-B 'tara' için geniş komut aralığı — riskli, ayrıca onay ister")
        print("    --encok-indeks N  'tara' indeks üst sınırı (varsayılan 0x07)")
        print("    --bolum N / --blok N / --blok-sayisi N   'depo' için bölüm ve blok aralığı")
        print("    --tam        'depo': bölümün tüm bloklarını oku (geri yükleme için)")
        print("    --seri-dahil 'depo-yaz': blok 0'ı (seri/ProductKey) da yaz — normalde atlanır")
        print("    --gelismis   'ekran': 16 noktalı kalibrasyon (yalnız Optical aracında)")
        print("    --sifirla    'ekran': panel kalibrasyonunu sıfırla")
        return 0

    class _A:
        kademe = 1
        surum = None
        yerel = None
        tip = None
        zorla = False
        cikti = None
        dene = False
        onayliyorum = False
        komut = 0x30
        indeks = 0
        uzunluk = 0x3a
        b1 = 0x1e          # OTD paketinin [1] bayti; ikilide 0x1e/0x2d/0x3c
        aralik = None
        encok_indeks = 0x07
        bolum = None
        blok = 0
        blok_sayisi = 8
        tam = False            # depo: bolumun tum bloklarini oku
        seri_dahil = False     # depo-yaz: blok 0 (seri/ProductKey) da yazilsin
        gelismis = False       # ekran: 16 nokta
        sifirla = False        # ekran: kalibrasyonu sifirla
        dosyalar = ()
    a = _A()
    kalan = []
    it = iter(argv[1:])
    for p in it:
        if p == "--kademe":
            a.kademe = int(next(it, "1"))
        elif p == "--surum":
            a.surum = next(it, None)
        elif p == "--yerel":
            a.yerel = next(it, None)
        elif p == "--tip":
            ham = next(it, None)
            a.tip = _t_tip_ayikla(ham)
            if not a.tip:
                die(f"--tip yalnız 'otd' (2621 / 4 kamera) veya 'optical' "
                    f"(6615 / 2 kamera) olabilir; verilen: {ham!r}")
        elif p == "--zorla":
            a.zorla = True
        elif p == "--cikti":
            a.cikti = next(it, None)
        elif p == "--dene":
            a.dene = True
        elif p == "--onayliyorum":
            a.onayliyorum = True
        elif p == "--cmd":
            a.komut = int(next(it, "0x30"), 0)
        elif p == "--indeks":
            a.indeks = int(next(it, "0"), 0)
        elif p == "--uzunluk":
            a.uzunluk = int(next(it, "0x3a"), 0)
        elif p == "--b1":
            a.b1 = int(next(it, "0x1e"), 0) & 0xFF
        elif p == "--aralik":
            a.aralik = next(it, None)
        elif p == "--bolum":
            a.bolum = int(next(it, "0"), 0)
        elif p == "--blok":
            a.blok = max(0, int(next(it, "0"), 0))
        elif p == "--blok-sayisi":
            a.blok_sayisi = max(1, min(4096, int(next(it, "8"), 0)))
        elif p == "--tam":
            a.tam = True
        elif p in ("--seri-dahil", "--seri"):
            a.seri_dahil = True
        elif p in ("--gelismis", "--gelişmiş"):
            a.gelismis = True
        elif p in ("--sifirla", "--sıfırla"):
            a.sifirla = True
        elif p == "--encok-indeks":
            a.encok_indeks = max(0, min(0xff, int(next(it, "0x0f"), 0)))
        else:
            kalan.append(p)
    if a.kademe not in (1, 2):
        die("--kademe yalnız 1 veya 2 olabilir.")
    if not 0 <= a.uzunluk <= 0x3b:
        die("--uzunluk 0 ile 59 arasında olmalı (64 baytlık rapor, yük ofset 5'ten başlar).")

    if cmd in ("durum", "status", "bilgi"):
        return cmd_touch_durum(a) or 0
    if cmd in ("liste", "list", "surumler"):
        return cmd_touch_liste(a) or 0
    if cmd in ("dene", "try", "sihirbaz"):
        return cmd_touch_dene(a) or 0
    if cmd in ("kalici", "kalıcı", "pin", "sabitle"):
        a.surum = a.surum or (kalan[0] if kalan else None)
        if not a.surum:
            die("Kullanım: dokunmatik kalici <SÜRÜM>")
        return cmd_touch_kalici(a) or 0
    if cmd in ("serbest", "unhold", "cozs", "coz"):
        return cmd_touch_serbest(a) or 0
    if cmd in ("geri", "restore", "geridon"):
        return cmd_touch_geri(a) or 0
    if cmd in ("kalibrasyon", "kalib", "eeprom"):
        alt = (kalan[0] if kalan else "oku").lower()
        a.dosyalar = tuple(kalan[1:])
        if alt in ("oku", "dump", "kaydet"):
            return cmd_touch_kalib_oku(a) or 0
        if alt in ("karsilastir", "karşılaştır", "diff", "fark"):
            return cmd_touch_kalib_karsilastir(a) or 0
        if alt in ("yaz", "geriyukle", "restore"):
            return cmd_touch_kalib_yaz(a) or 0
        if alt in ("ham", "raw", "sorgu"):
            return cmd_touch_kalib_ham(a) or 0
        if alt in ("tara", "scan", "kesif"):
            return cmd_touch_kalib_tara(a) or 0
        if alt in ("depo", "storage", "bolum"):
            return cmd_touch_kalib_depo(a) or 0
        if alt in ("yazma-testi", "yazmatesti", "noop", "no-op"):
            return cmd_touch_kalib_yazma_testi(a) or 0
        if alt in ("depo-yaz", "depoyaz", "blok-yaz", "geri-yukle"):
            return cmd_touch_kalib_depo_yaz(a) or 0
        if alt in ("ekran", "dokunarak", "arac", "araç"):
            if a.gelismis and a.sifirla:
                die("--gelismis ve --sifirla birlikte kullanılamaz.")
            return cmd_touch_kalib_ekran(a) or 0
        die("Bilinmeyen kalibrasyon komutu: %s   "
            "(oku|karsilastir|yaz|ham|tara|depo|yazma-testi|depo-yaz|ekran)" % alt)
    die("Bilinmeyen dokunmatik komutu: %s   "
        "(durum|liste|dene|kalici|serbest|geri|kalibrasyon)" % cmd)


# ===================== BİRLEŞİK DAĞITICI =====================
def _unified_usage():
    rule=D("─"*60)
    PRE="eta-112.py "
    def row(rest_plain, rest_colored, desc=None):
        s="  "+D(PRE)+rest_colored
        if desc is not None:
            vis=len("  "+PRE+rest_plain)
            s+=" "*max(2, 40-vis)+D("→ "+desc)
        return s
    sub=lambda s: print("        "+D(s))   # komut altı seçenek/alt-komut satırı
    print()
    print("  "+B(Cy("ETA-112"))+" — "+B("Birleşik Parola Aracı")+"   "+D("OS · BIOS · MAC · Windows"))
    print()
    print(rule)
    print(B("  KULLANIM"))
    print(row("", "", "etkileşimli menü (kullanıcı / BIOS / MAC / wkey / dokunmatik)"))
    print()
    print(Cy("  Komutlar")+"   "+D("ayrıntılı yardım: ")+G("eta-112.py <komut> -h"))
    print(row("kullanici [...]", G("kullanici")+" "+D("[...]"), "OS kullanıcı parolasını sıfırla"))
    sub("--list · --dry-run · --help")
    print(row("bios <komut>", G("bios")+" "+D("<komut>"), "BIOS yönetici / kullanıcı parolası"))
    sub("read · set · clear <slot> · info · calibrate · --json")
    print(row("mac <komut>", G("mac")+" "+D("<komut>"), "Ethernet MAC adresi oku / doğrula / değiştir"))
    sub("read · check <MAC> · set <MAC> · --json")
    print(row("wkey <komut>", G("wkey")+" "+D("<komut>"), "Windows ürün anahtarı (MSDM) oku / değiştir"))
    sub("read · set <ANAHTAR> · --json")
    print()
    print(row("dokunmatik <komut>", G("dokunmatik")+" "+D("<komut>"), "dokunmatik sürücü sürümünü dene / sabitle"))
    sub("durum · liste · dene · kalici <SÜRÜM> · serbest · geri")
    print()
    print(row("--help", Cy("--help"), "bu yardım"))
    print(rule)


# ------------------------------------------------------------------ pull-down menü
# Ok tuslariyla gezilen renkli menu. Yalnizca ETKILESIMLI oturumda devreye girer;
# borulanmis/yakalanmis cikti ya da tty yoksa eski numarali menuye duser. Komut
# satiri arayuzu (bios/kullanici/mac/wkey/dokunmatik + --json) bundan etkilenmez:
# menu yalnizca hic argumansiz calistirildiginda gosterilir.
#
# Tasarim kurali: kullanici acikca istemeden menuden cikilmaz.
#   * Alt menulerde Esc/q/0 -> ust menuye doner (cikis degil).
#   * Ana menude Esc/q/0/Ctrl-C -> imleci "Cikis" uzerine tasir, cikmaz.
#   * Bir eylem hata verip die() cagirsa bile menu kapanmaz; hata gosterilir,
#     tusa basilinca menuye donulur.

import contextlib

_MENU_IPUCU = "↑/↓ gez · Enter seç · 1-9 doğrudan · Esc geri"
_MENU_YOL = []   # bulunulan menü patikası (breadcrumb)


def _tty_fd():
    try:
        fd = _TTY.fileno()
    except (AttributeError, ValueError, OSError):
        return -1
    return fd if os.isatty(fd) else -1


def _menu_etkilesimli():
    """Pull-down menü çizilebilir mi?"""
    if os.environ.get("ETA112_BASIT_MENU"):
        return False
    if not sys.stdout.isatty():
        return False
    if os.environ.get("TERM", "") in ("", "dumb"):
        return False
    return _tty_fd() >= 0


def _tus_oku(fd):
    """Tek tuş veya kaçış dizisi oku. -> 'yukari'|'asagi'|'bas'|'son'|'giris'|
    'esc'|'ctrl-c'|tek karakter|None"""
    import select
    try:
        b = os.read(fd, 1)
    except OSError:
        return None
    if not b:
        return None
    if b == b"\x03":
        return "ctrl-c"
    if b in (b"\r", b"\n"):
        return "giris"
    if b == b"\x1b":
        r, _, _ = select.select([fd], [], [], 0.05)
        if not r:
            return "esc"
        b2 = os.read(fd, 1)
        if b2 not in (b"[", b"O"):
            return "esc"
        b3 = os.read(fd, 1)
        if b3 == b"":
            return "esc"
        if b3 in b"0123456789":          # ör. ESC [ 5 ~  (page up)
            while True:
                b4 = os.read(fd, 1)
                if b4 in (b"~", b""):
                    break
            return {b"5": "bas", b"6": "son"}.get(b3, "esc")
        return {b"A": "yukari", b"B": "asagi", b"H": "bas", b"F": "son",
                b"C": "giris", b"D": "esc"}.get(b3, "esc")
    try:
        return b.decode("utf-8", "replace")
    except UnicodeDecodeError:
        return None


def _ham_tty(fd):
    """Terminali tuş-tuş okuma kipine alır; çıkarken eski haline döndürür."""
    import contextlib
    import termios
    import tty

    @contextlib.contextmanager
    def _kip():
        eski = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            sys.stdout.write("\033[?25l")      # imleci gizle
            sys.stdout.flush()
            yield
        finally:
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()
            termios.tcsetattr(fd, termios.TCSADRAIN, eski)
    return _kip()


def _menu_yolu(baslik):
    """Bulunulan konumun tam patikası:  ETA-112 > BIOS EEPROM > MAC adresi"""
    return " > ".join(_MENU_YOL + [baslik])


@contextlib.contextmanager
def _yol_ekle(*adlar):
    """Alt menü/işlem süresince patikaya basamak ekler."""
    _MENU_YOL.extend(a for a in adlar if a)
    try:
        yield
    finally:
        del _MENU_YOL[len(_MENU_YOL) - len([a for a in adlar if a]):]


def _menu_kapasite():
    """Ekrana kaç öğe sığar (başlık/ayraç/ipucu payı düşülmüş).

    Pencere boyutu bildirmeyen terminaller 0 döndürebiliyor; o durumda listeyi
    boş yere kırpmamak için 24 satır varsayılır."""
    satir = 0
    for fd in (_tty_fd(), 1):
        try:
            satir = os.get_terminal_size(fd).lines
        except (OSError, ValueError):
            satir = 0
        if satir >= 10:
            break
    if satir < 10:
        satir = 24
    return max(3, satir - 8)


def _menu_ciz(baslik, ogeler, secili, ana):
    """Menü bloğunu satır listesi olarak üretir."""
    n = len(ogeler)
    kap = _menu_kapasite()
    if n <= kap:
        bas, bit = 0, n
    else:                               # uzun liste: seçili öğenin çevresini göster
        bas = max(0, min(secili - kap // 2, n - kap))
        bit = bas + kap
    # Açıklaması olan öğe yoksa hizalama sütunu da yok — etiketler yalın kalsın.
    aciklamali = any(a for _, a in ogeler)
    genislik = max((len(e) for e, _ in ogeler), default=0) if aciklamali else 0
    nog = 2 if n > 9 else 1
    satirlar = ["", f"  {C.CY}{C.B}{_menu_yolu(baslik)}{C.R}", C.DIM + "─" * 60 + C.R]
    if bas > 0:
        satirlar.append(f"    {C.DIM}↑ {bas} öğe daha{C.R}")
    for i in range(bas, bit):
        etiket, aciklama = ogeler[i]
        no = " " * nog if i == n - 1 else str(i + 1).rjust(nog)
        govde = f"{no} {etiket.ljust(genislik)}  {aciklama}".rstrip()
        if i == secili:
            satirlar.append(f"  {C.CY}▸{C.R}{C.INV} {govde} {C.R}")
        else:
            satirlar.append(f"    {C.DIM}{govde}{C.R}")
    if bit < n:
        satirlar.append(f"    {C.DIM}↓ {n - bit} öğe daha{C.R}")
    satirlar.append(C.DIM + "─" * 60 + C.R)
    ipucu = _MENU_IPUCU if not ana else _MENU_IPUCU.replace("Esc geri", "Esc → Çıkış")
    satirlar.append(f"  {C.DIM}{ipucu}{C.R}")
    return satirlar


def _secim_basit(baslik, ogeler, ana):
    """Etkileşimli olmayan ortam için eski numaralı menü."""
    print()
    print(C.B + "  " + _menu_yolu(baslik) + C.R)
    for i, (etiket, aciklama) in enumerate(ogeler):
        no = 0 if i == len(ogeler) - 1 else i + 1
        ek = f" ({aciklama})" if aciklama else ""
        print(C.DIM + f"  {no}) {etiket}{ek}" + C.R)
    hr()
    s = ask("  Seçim: ").strip()
    if s in ("", "0"):
        return len(ogeler) - 1
    if s.isdigit() and 1 <= int(s) <= len(ogeler) - 1:
        return int(s) - 1
    return None


def _secim(baslik, ogeler, secili=0, ana=False):
    """Menüyü göster, seçilen öğenin indeksini döndür.

    Son öğe her zaman 'Geri'/'Çıkış' sayılır. Esc/q/0:
      * alt menüde  -> son öğe seçilir (üst menüye dön)
      * ana menüde  -> imleç son öğeye taşınır, çıkmak için Enter gerekir
    """
    if not _menu_etkilesimli():
        s = _secim_basit(baslik, ogeler, ana)
        return len(ogeler) - 1 if s is None and not ana else s
    fd = _tty_fd()
    son = len(ogeler) - 1
    secili = max(0, min(secili, son))
    cizilen = 0
    sonuc = None
    with _ham_tty(fd):
        while sonuc is None:
            satirlar = _menu_ciz(baslik, ogeler, secili, ana)
            if cizilen:
                sys.stdout.write(f"\033[{cizilen}A")
            for s in satirlar:
                sys.stdout.write("\r\033[2K" + s + "\n")
            cizilen = len(satirlar)
            sys.stdout.write("\033[J")      # blok kısaldıysa altta artık kalmasın
            sys.stdout.flush()

            t = _tus_oku(fd)
            if t is None:
                sonuc = son
            elif t == "yukari":
                secili = son if secili == 0 else secili - 1
            elif t == "asagi":
                secili = 0 if secili == son else secili + 1
            elif t == "bas":
                secili = 0
            elif t == "son":
                secili = son
            elif t == "giris":
                sonuc = secili
            elif t in ("esc", "ctrl-c", "q", "Q", "0"):
                if not ana:
                    sonuc = son
                else:
                    secili = son      # ana menüde çıkmak için ayrıca Enter gerekir
            elif t.isdigit() and 1 <= int(t) <= son:
                sonuc = int(t) - 1
        # Seçim yapıldı: menü bloğunu ekrandan sil. Alt menü ya da işlem çıktısı
        # üstte asılı kalan bir menünün altına değil, tam onun yerine çizilsin.
        sys.stdout.write(f"\033[{cizilen}A\033[J")
        sys.stdout.flush()
    return sonuc


def _devam_bekle():
    print()
    if not _menu_etkilesimli():
        ask(C.DIM + "  Menüye dönmek için Enter'a basın... " + C.R)
        return
    sys.stdout.write(C.DIM + "  Menüye dönmek için bir tuşa basın... " + C.R)
    sys.stdout.flush()
    fd = _tty_fd()
    with _ham_tty(fd):
        _tus_oku(fd)
    sys.stdout.write("\r\033[2K")      # istem satırını temizle
    sys.stdout.flush()


def _eylem_calistir(fn):
    """Menüden çağrılan işi yürütür; hiçbir hata menüyü kapatmaz.

    die() normalde süreci bitirir ve bağlamaları atexit temizler. Menüde SystemExit
    yakalandığı için süreç yaşamaya devam eder; bu yüzden temizliği burada açıkça
    yapıyoruz, yoksa yarıda kalan bir 'kullanıcı parolası' akışından kalan
    bağlamalar bir sonraki denemeyi bozar."""
    print()
    kod = 0
    try:
        kod = fn() or 0
    except SystemExit as e:
        kod = e.code if isinstance(e.code, int) else 1
    except KeyboardInterrupt:
        print()
        warn("İşlem iptal edildi.")
        kod = 130
    except EOFError:
        print()
        warn("Girdi alınamadı.")
        kod = 1
    except Exception as e:          # menü hiçbir koşulda çökmesin
        err(f"Beklenmeyen hata: {type(e).__name__}: {e}")
        kod = 1
    finally:
        try:
            _cleanup()
        except Exception:
            pass
    if _menu_etkilesimli():
        _devam_bekle()
    return kod


def _menu_dongusu(baslik, ogeler, ana=False):
    """Menüyü seçim yapılana dek döndürür.

    ogeler: (etiket, açıklama, eylem|None[, alt_menu_mu]) dizileri.
    Son öğe Geri/Çıkış sayılır (eylem None). alt_menu_mu=True olan öğelerden
    dönüşte 'bir tuşa basın' beklemesi yapılmaz.

    Döngü YALNIZCA etkileşimli kipte kurulur. Etkileşimsiz kipte (borulanmış
    girdi, tty yok) menü eskisi gibi tek seferliktir; boru ile numara besleyen
    mevcut script'ler aynı şekilde çalışmaya devam etsin diye."""
    secili = 0
    dongu = _menu_etkilesimli()
    goster = [(o[0], o[1]) for o in ogeler]
    while True:
        try:
            secim = _secim(baslik, goster, secili, ana=ana)
        except KeyboardInterrupt:
            continue                # ana menüde Ctrl-C çıkarmaz
        if secim is None:
            if not dongu:
                return 0            # eski davranış: geçersiz girdi menüyü kapatır
            continue
        secili = secim
        oge = ogeler[secim]
        eylem = oge[2]
        if eylem is None:
            return 0
        if len(oge) > 3 and oge[3]:
            with _yol_ekle(baslik):          # alt menü kendi adını leaf olarak yazar
                sonuc = eylem()
        else:
            with _yol_ekle(baslik, oge[0]):  # işlem: patikaya öğe adı da eklenir
                sonuc = _eylem_calistir(eylem)
        if not dongu:
            return sonuc or 0


def _bios_menu():
    return _menu_dongusu("BIOS parolası", [
        ("Parolaları oku", "", lambda: etabios_main(["read"])),
        ("Parola ayarla", "", lambda: etabios_main(["set"])),
        ("Parola temizle", "", _bios_temizle),
        ("Model / destek bilgisi", "", lambda: etabios_main(["info"])),
        ("Geri", "", None),
    ])


def _bios_temizle():
    slot = ask("  Hangi parola silinsin? [all/yonetici/kullanici]: ").strip() or "all"
    if slot not in ("all", "yonetici", "kullanici"):
        slot = "all"
    return etabios_main(["clear", slot])


def _mac_dogrula():
    """Menüdeki 'MAC doğrula' adımı: önce ne işe yaradığını anlatır, sonra sorar.

    Açıklama buraya konuldu çünkü adımın kendisi zararsız; asıl risk atlanmasında.
    'MAC değiştir' adresi Realtek eFuse'una (OTP) yazar ve o yazım geri alınamaz;
    bu adım o yazımdan önceki kuru denemedir."""
    prof, _d = _mac_profile()
    ouis = (prof or {}).get("mac_ouis")
    title("MAC doğrula — yazmadan önce ön kontrol")
    print("  " + D("Bu adım hiçbir şeyi değiştirmez. Girdiğiniz adresi donanıma"))
    print("  " + D("dokunmadan, yalnızca kurallara göre sınar."))
    print()
    print("  " + D("Neye bakar:"))
    for ad, ne in (
        ("biçim",    "12 onaltılık hane mi — AA:BB:CC:DD:EE:FF"),
        ("tür",      "hepsi-sıfır, broadcast ya da multicast değil mi; bu üçü"),
        ("",         "bir ethernet kartına MAC olarak verilemez"),
        ("köken",    "yerel-yönetimli (rastgele) bir adres değil, gerçek bir"),
        ("",         "üretici OUI'si mi"),
        ("sahiplik", "adresin ilk üç baytı (OUI) bu tahtanın Faz profilinde"),
        ("",         "izin verilen üreticiye ait mi"),
    ):
        print("    " + (Cy(f"{ad:<11}") if ad else " " * 11) + D(ne))
    print()
    print("  " + D("Bu tahta: ") + (G(prof["model_name"]) if prof else Y("tanınmadı")))
    if ouis:
        print("  " + D("İzinli Faz OUI: ")
              + ", ".join(f"{Cy(o)} {D('(' + v + ')')}" for o, v in ouis.items()))
    else:
        print("  " + Y("Bu model için OUI beyaz listesi tanımlı değil — sahiplik"))
        print("  " + Y("kontrolü yapılamaz, her adres geçersiz sayılır."))
    print()
    print("  " + D("Neden gerekli: 'MAC değiştir' adımı adresi Realtek NIC'inin eFuse'una"))
    print("  " + D("yazar. eFuse tek-yönlüdür (OTP): yazılan geri alınamaz ve her değişiklik"))
    print("  " + D("yongadaki sınırlı alandan ~7 bayt tüketir. Yanlış bir adresi fark etmenin"))
    print("  " + D("yeri yazdıktan sonrası değil, burasıdır."))
    print()
    mac = ask("  Doğrulanacak MAC: ").strip()
    if not mac:
        warn("MAC girilmedi — doğrulama yapılmadı.")
        return 1
    print()
    return etamac_main(["check", mac])


def _mac_menu():
    return _menu_dongusu("MAC adresi", [
        ("MAC oku", "güncel adresler ve Faz OUI durumu",
         lambda: etamac_main(["read"])),
        ("MAC değiştir", "eFuse'a kalıcı yazar — geri alınamaz",
         lambda: etamac_main(["set", ask("  Yeni MAC: ").strip()])),
        ("MAC doğrula", "yazmadan önce sına — donanıma dokunmaz",
         _mac_dogrula),
        ("Geri", "", None),
    ])


def _wkey_menu():
    return _menu_dongusu("Windows ürün anahtarı", [
        ("Anahtarı oku", "", lambda: etawkey_main(["read"])),
        ("Anahtarı değiştir", "",
         lambda: etawkey_main(["set", ask(
             "  Yeni anahtar (XXXXX-XXXXX-XXXXX-XXXXX-XXXXX): ").strip()])),
        ("Geri", "", None),
    ])


def _touch_kalib_oku():
    """Menüden kalibrasyon okuma.

    OTD (2621) panellerde komut kimlikleri doğrulanmadığı için komut satırı
    --dene ister. Menüde bayrak yazılamaz; aynı onayı soruyla alıyoruz. Okuma
    salt GET kontrol transferidir — panele yazılmaz; risk veri kaybı değil,
    çıktının anlamsız olmasıdır."""
    argv = ["kalibrasyon", "oku"]
    tip, kimlik = _t_aygit()
    if tip == "otd":
        warn(f"OTD paneli ({kimlik}) — komut kümesi OtdTouchServer'dan türetildi,")
        print(f"  {D('cihazda henüz doğrulanmadı. Yalnızca yük taşımayan okuma')}")
        print(f"  {D('komutları (0xb0, 0xae) gönderilir; panele yazılmaz.')}")
        if ask("  Yine de denensin mi? [e/H]: ").strip().lower() not in ("e", "evet", "y"):
            return 0
        argv.append("--dene")
    return etatouch_main(argv)


def _touch_kalib_karsilastir():
    x = ask("  Birinci kayıt (sağlam tahta): ").strip()
    y = ask("  İkinci kayıt (sorunlu tahta): ").strip()
    return etatouch_main(["kalibrasyon", "karsilastir", x, y])


def _touch_menu():
    return _menu_dongusu("Dokunmatik sürücü", [
        ("Durum", "", lambda: etatouch_main(["durum"])),
        ("Sürümleri listele", "", lambda: etatouch_main(["liste"])),
        ("Sürüm dene — hızlı", "", lambda: etatouch_main(["dene", "--kademe", "1"])),
        ("Sürüm dene — tam", "", lambda: etatouch_main(["dene", "--kademe", "2"])),
        ("Kalibrasyonu oku", "", _touch_kalib_oku),
        ("Kalibrasyon karşılaştır", "", _touch_kalib_karsilastir),
        ("Ekrana dokunarak kalibre et", "",
         lambda: etatouch_main(["kalibrasyon", "ekran"])),
        ("Başlangıç durumuna dön", "", lambda: etatouch_main(["geri"])),
        ("Sabitlemeyi kaldır", "", lambda: etatouch_main(["serbest"])),
        ("Geri", "", None),
    ])


def _bios_eeprom_menu():
    return _menu_dongusu("BIOS EEPROM", [
        ("BIOS parolası", "", _bios_menu, True),
        ("MAC adresi", "", _mac_menu, True),
        ("Windows ürün anahtarı", "", _wkey_menu, True),
        ("Geri", "", None),
    ])


def _menu():
    return _menu_dongusu("ETA-112", [
        ("Kullanıcı hesapları", "", lambda: kps_main([])),
        ("BIOS EEPROM", "", _bios_eeprom_menu, True),
        ("Dokunmatik sürücü", "", _touch_menu, True),
        ("Çıkış", "", None),
    ], ana=True)


def main():
    argv = sys.argv[1:]
    if argv and argv[0] in ("--help", "-h", "help", "yardim"):
        _unified_usage(); return 0
    if argv and argv[0] in ("bios", "firmware", "uefi"):
        return etabios_main(argv[1:]) or 0
    if argv and argv[0] in ("kullanici", "user", "os", "kps"):
        return kps_main(argv[1:]) or 0
    if argv and argv[0] in ("mac", "ethernet", "eth"):
        return etamac_main(argv[1:]) or 0
    if argv and argv[0] in ("wkey", "windows", "winkey", "seri"):
        return etawkey_main(argv[1:]) or 0
    if argv and argv[0] in ("dokunmatik", "touch", "tahta", "ekran"):
        return etatouch_main(argv[1:]) or 0
    if argv:
        if argv[0].startswith("-"):     # çıplak bayraklar -> kullanıcı modu (geriye uyum)
            return kps_main(argv) or 0
        die("Bilinmeyen komut: %s   ('eta-112.py --help')" % argv[0])
    return _menu()


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        print()
        die("Kullanıcı iptali.", 130)
    except EOFError:
        die("Girdi alınamadı (tty yok).", 1)
