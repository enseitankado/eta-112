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
    R = "\033[0m"; B = "\033[1m"; DIM = "\033[2m"
    CY = "\033[36m"; GR = "\033[32m"; RD = "\033[31m"; YL = "\033[33m"


if not sys.stdout.isatty():
    for _a in ("R", "B", "DIM", "CY", "GR", "RD", "YL"):
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


def die(m, code=1):
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
        try: box["r"]=fn()
        except Exception as e: box["e"]=e
    th=threading.Thread(target=w); th.start()
    W=28; t0=time.time()
    while th.is_alive():
        frac=min((time.time()-t0)/est, 1.0); fill=int(frac*W)
        bar=G("█"*fill)+"·"*(W-fill)
        extra=Y("  Bekleyiniz...") if frac>=1.0 else ""
        sys.stdout.write(f"\r  {Cy(label)} [{bar}] {int(frac*100):3d}%{extra} "); sys.stdout.flush()
        time.sleep(0.1)
    th.join()
    sys.stdout.write(f"\r  {Cy(label)} [{G('█'*W)}] 100% {OK} {D(f'{time.time()-t0:.0f}s')}            \n"); sys.stdout.flush()
    if "e" in box: raise box["e"]
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
def build_parser():
    prog="eta-112.py bios"
    desc=(B(Cy("etabios"))+" — AMI Aptio "+B("BIOS parola aracı")+" (oku / ayarla / temizle)\n"
          +D("Profil-tabanlı: yalnız önceden tanımlı modeller. UEFI'de efivarfs, Legacy'de flashrom.")+"\n"
          +Y("UYARI: ")+"ayarla/temizle flash'ı "+R("DOĞRUDAN")+" yazar (onay yok); "+R("brick riski")+".")
    ex=[B("ÖRNEKLER:"),
        D("  # Parolaları oku (parametresiz çağrı da okur):"),
        "  sudo "+prog+"            "+D("# = read"),
        "  sudo "+prog+" "+G("read"),
        D("  # Parola ayarla (Yönetici ve Kullanıcı sırayla sorulur):"),
        "  sudo "+prog+" "+G("set"),
        D("  # Parola temizle:"),
        "  sudo "+prog+" "+G("clear")+" all",
        D("  # Model/destek bilgisi:"),
        "  sudo "+prog+" "+G("info"),
        D("  # Yeni BIOS sürümü için keystream doğrula (önce BIOS'tan bilinen parola gir):"),
        "  sudo "+prog+" "+G("calibrate")+" yonetici 1234",
        "", D("  # GUI/makine için JSON çıktı ve parametreli ayarlama:"),
        "  sudo "+prog+" "+G("read")+" "+Cy("--json"),
        "  sudo "+prog+" "+G("set")+" "+Cy("--yonetici ORNEK99 --kullanici ABC123 --json"),
        "  sudo "+prog+" "+G("set")+" "+Cy("--yonetici ORNEK99 --koruma always --json")+D("  # her açılışta sorsun"),
        "  sudo "+prog+" "+G("set")+" "+Cy("--koruma setup --json")+D("  # yalnız koruma modunu değiştir (parola dokunma)"),
        "", B("DESTEKLENEN MODELLER:")]
    ex+=["  "+OK+f" {Cy(b)} / BIOS {Cy(v)}  {D('— '+PROFILES[(b,v)]['label'])}" for (b,v) in PROFILES]
    ex+=["", B("Programcı: ")+Cy("Özgür Koca")+D(" — ")+Cy("ozgurkoca.com"),
         B("Lisans: ")+G("GPL")+D(" — tamamen özgür yazılım.")]
    common=argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="makine-okur JSON çıktı (GUI için)")
    p=argparse.ArgumentParser(prog=prog, description=desc, epilog="\n".join(ex), parents=[common],
                              formatter_class=argparse.RawDescriptionHelpFormatter)
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
        return 0
    print(f"  {ERR} {R('Geçersiz MAC')}: {a.mac}")
    if m: print(D("     normalize: %s%s"%(m, "  OUI "+oui if oui else "")))
    print(f"     {Y('neden: '+reason)}")
    return 1

def etamac_main(argv):
    global _JSON
    args=list(argv)
    if "--json" in args: _JSON=True; args=[x for x in args if x!="--json"]
    if args and args[0] in ("-h","--help","yardim"):
        print(B("eta-112.py mac")+" — onboard ethernet MAC oku / doğrula")
        print("  eta-112.py mac read            # MAC(ler) + Faz OUI durumu (varsayılan)")
        print("  eta-112.py mac check <MAC>     # önerilen MAC Faz'a ait mi? (biçim+OUI)")
        print("  eta-112.py mac [--json]        # makine-okur çıktı")
        print(D("  Not: 'set' (flash-yazma) bu NIC'te ETKİSİZ — MAC Realtek eFuse'unda; devre dışı."))
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
    if cmd in ("set","write","yaz","clear","temizle"):
        msg=("MAC flash-yazma bu donanımda ETKİSİZ (2026-06-24 canlı test): MAC, BIOS SPI "
             "flash NVRAM'inde (0x3daee7) yalnız PASİF bir kopyadır; Realtek RTL8168 gerçek "
             "MAC'ini kendi eFuse/EEPROM'undan okur — flash'ı değiştirmek reboot sonrası MAC'i "
             "DEĞİŞTİRMEDİ. Kalıcı donanım değişikliği NIC eFuse programlama gerektirir (ayrı/riskli)")
        if _JSON: return emit({"ok":False,"error":"flash_write_ineffective","detail":msg},1)
        print(f"  {WARN} {Y(msg)}."); return 1
    die("Bilinmeyen mac komutu: %s   (read|check)" % cmd)


# ===================== BİRLEŞİK DAĞITICI =====================
def _unified_usage():
    print(C.B + "ETA-112 — Birleşik Parola Aracı" + C.R)
    print("""Kullanım:
  eta-112.py                      menü (kullanıcı / BIOS)
  eta-112.py kullanici [...]      İşletim sistemi kullanıcı parolası
       seçenekler: --list, --dry-run, --help
  eta-112.py bios <komut> [...]   BIOS parolası (etabios)
       komutlar: read | set | clear <slot> | info | calibrate | --json
       set seçenekleri: --yonetici --kullanici --koruma {always,setup} --json
  eta-112.py mac <komut> [...]    Ethernet MAC (oku / doğrula)
       komutlar: read | check <MAC> | --json
  eta-112.py --help               bu yardım""")


def _bios_menu():
    print()
    print(C.B + "  BIOS parolası" + C.R)
    print(C.DIM + "  1) Parolaları oku" + C.R)
    print(C.DIM + "  2) Parola ayarla" + C.R)
    print(C.DIM + "  3) Parola temizle" + C.R)
    print(C.DIM + "  4) Model / destek bilgisi" + C.R)
    print(C.DIM + "  0) Geri" + C.R)
    hr()
    s = ask("  Seçim: ").strip()
    if s == "1":
        return etabios_main(["read"]) or 0
    if s == "2":
        return etabios_main(["set"]) or 0
    if s == "3":
        slot = ask("  Hangi parola silinsin? [all/yonetici/kullanici]: ").strip() or "all"
        if slot not in ("all", "yonetici", "kullanici"):
            slot = "all"
        return etabios_main(["clear", slot]) or 0
    if s == "4":
        return etabios_main(["info"]) or 0
    return 0


def _mac_menu():
    print()
    print(C.B + "  MAC adresi" + C.R)
    print(C.DIM + "  1) MAC oku (+ Faz OUI durumu)" + C.R)
    print(C.DIM + "  2) Bir MAC'i doğrula (Faz'a ait mi?)" + C.R)
    print(C.DIM + "  0) Geri" + C.R)
    hr()
    s = ask("  Seçim: ").strip()
    if s == "1":
        return etamac_main(["read"]) or 0
    if s == "2":
        m = ask("  Doğrulanacak MAC: ").strip()
        return etamac_main(["check", m]) or 0
    return 0


def _menu():
    print()
    print(C.B + "  ETA-112 — Birleşik Parola Aracı" + C.R)
    print(C.DIM + "  1) İşletim sistemi kullanıcı parolası (canlı/çalışan disk)" + C.R)
    print(C.DIM + "  2) BIOS parolası (oku / ayarla / temizle)" + C.R)
    print(C.DIM + "  3) MAC adresi (oku / doğrula)" + C.R)
    print(C.DIM + "  0) Çıkış" + C.R)
    hr()
    s = ask("  Seçim [1/2/3/0]: ").strip()
    if s == "1":
        return kps_main([]) or 0
    if s == "2":
        return _bios_menu()
    if s == "3":
        return _mac_menu()
    return 0


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
