#!/usr/bin/env python3
"""
ryzenadj-gui root_helper.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import glob
import json
import os
import re
import subprocess
import sys
import time
import tempfile
from pathlib import Path as _Path

# Only ever add this process's own install directory to sys.path — never
# anything derived from the invoking user's environment. root_helper.py
# and tool_paths.py are installed side by side at
# /usr/local/lib/ryzenadj-gui/, both root-owned and root-writable only.
_HELPER_DIR = str(_Path(__file__).resolve().parent)
if _HELPER_DIR not in sys.path:
    sys.path.insert(0, _HELPER_DIR)

from tool_paths import find_tool, require_tool  # noqa: E402

# ─── Sabit dizin tanımlamaları (FHS'e uygun, kurulum dizininden bağımsız) ──
#   /etc/ryzenadj-gui/profiles   → güç profilleri (root_helper yazar, GUI okur)
#   /etc/nvcurve/profiles        → nvcurve GPU V/F eğri profilleri
#   /var/lib/ryzenadj-gui/scripts→ kalıcı, üretilmiş profil aktivasyon script'leri (set_*.sh)
#   /run/ryzenadj-gui/scripts    → GUI'nin ürettiği script'lerin ÇALIŞTIRILDIĞI, önyükleme
#                                  ile birlikte temizlenen (tmpfs) geçici dizin
NVCURVE_PROFILES_DIR = "/etc/nvcurve/profiles"
RYZENADJ_PROFILES_DIR = "/etc/ryzenadj-gui/profiles"
VAR_SCRIPTS_DIR = "/var/lib/ryzenadj-gui/scripts"

# custom/gmode'a ilk geçişte yakalanan "önyükleme sonrası" ayar değerleri.
# /run tmpfs olduğundan bu dosya her önyüklemede otomatik olarak sıfırlanır
# — yani "bu önyükleme için bir kez yakala" davranışı ekstra bir mekanizma
# gerektirmeden kendiliğinden sağlanıyor.
BOOT_DEFAULTS_FILE = "/run/ryzenadj-gui/boot_defaults.json"

# op_run_script_content, script içeriğini doğrudan bu (tmpfs, önyüklemede
# sıfırlanan) dizinde oluşturup çalıştırıp siliyor — hiçbir zaman kalıcı
# diske veya kullanıcı tarafından yazılabilir bir yola dokunmuyor.
ALLOWED_SCRIPTS_DIR = "/run/ryzenadj-gui/scripts"

PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_PROFILE_BYTES = 256 * 1024  # 256 KB


# ─── MEVCUT VE ÇALIŞAN ORİJİNAL FONKSİYONLARINIZ ───────────────────────────

def op_reload_alienware_wmi(params: dict) -> dict:
    """alienware-wmi modülünü kaldırıp force_gmode ayarıyla yeniden yükler."""
    force_gmode = params.get("force_gmode")
    if not isinstance(force_gmode, bool):
        return {"ok": False, "error": "force_gmode must be a boolean"}

    try:
        rmmod_path = require_tool("rmmod", root_context=True)
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}

    for i in range(5):
        result = subprocess.run([rmmod_path, "alienware_wmi"], capture_output=True, text=True)
        if result.returncode == 0:
            break
        if i == 4:
            # O7: rmmod, modül hwmon/telemetri paneli tarafından açık
            # tutuluyorsa (kullanımda) her zaman başarısız olur; sadece
            # "reboot" demek yanıltıcıydı. Kullanıcıya asıl olası nedeni
            # ve reboot gerektirmeyen bir çözümü de söylüyoruz.
            return {
                "ok": False,
                "error": (
                    "Cannot unload alienware-wmi module (still in use — likely by the "
                    "GUI's live telemetry panel or another hwmon reader). Close the "
                    "telemetry panel/tray and try again; if it still fails, a reboot "
                    "will be required."
                ),
            }
        time.sleep(1)

    conf_file = "/etc/modprobe.d/alienware-wmi.conf"
    os.makedirs("/etc/modprobe.d", exist_ok=True)
    with open(conf_file, "w") as f:
        if force_gmode:
            f.write("options alienware-wmi force_platform_profile=true force_hwmon=true force_gmode=true\n")
        else:
            f.write("options alienware-wmi force_platform_profile=true force_hwmon=true\n")

    try:
        modprobe_path = require_tool("modprobe", root_context=True)
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}

    result = subprocess.run([modprobe_path, "alienware-wmi"], capture_output=True, text=True)
    if result.returncode != 0:
        return {"ok": False, "error": f"modprobe failed: {result.stderr.strip()}"}

    return {"ok": True, "message": "Alienware-WMI reloaded successfully."}


def op_write_nvcurve_profile(params: dict) -> dict:
    """NVCurve profilini /etc/nvcurve/profiles altına yazar."""
    name = params.get("name")
    content = params.get("content")

    if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
        return {"ok": False, "error": "Invalid profile name"}
    if not isinstance(content, str):
        return {"ok": False, "error": "content must be a string"}
    if len(content.encode()) > MAX_PROFILE_BYTES:
        return {"ok": False, "error": "content too large"}

    try:
        json.loads(content)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"content is not valid JSON: {e}"}

    os.makedirs(NVCURVE_PROFILES_DIR, exist_ok=True, mode=0o755)
    target_path = os.path.join(NVCURVE_PROFILES_DIR, f"{name}.json")

    if os.path.dirname(os.path.abspath(target_path)) != os.path.abspath(NVCURVE_PROFILES_DIR):
        return {"ok": False, "error": "Path traversal detected"}

    with open(target_path, "w") as f:
        f.write(content)
    os.chmod(target_path, 0o644)

    return {"ok": True, "message": f"Profile written: {target_path}"}


def op_set_default_gpu_profile(params: dict) -> dict:
    """GPU tuning sekmesindeki 'Varsayılan Yap' düğmesinin karşılığı.

    Ayrı bir daemon/servis KURMAZ — nvcurve'un zaten var olan
    `profile default` alt komutunu çağırıp /etc/nvcurve/config.json
    içindeki auto_load_profiles map'ine tek bir satır yazar. Gerçek
    uygulama, tray açılışında bir kez tetiklenen `nvcurve autoload`
    çağrısıyla (bkz. op_run_gpu_autoload) olur.
    """
    project_dir = params.get("project_dir")
    name = params.get("name")
    clear = bool(params.get("clear", False))

    if not isinstance(project_dir, str) or not project_dir:
        return {"ok": False, "error": "project_dir required"}

    if not clear:
        if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
            return {"ok": False, "error": "Invalid profile name"}
        # Varsayılan olarak yalnızca zaten diskte var olan bir profil
        # işaretlenebilir — olmayan bir isim autoload sırasında sessizce
        # atlanır (bkz. nvcurve/profiles/apply.py: apply_with_retry
        # FileNotFoundError'da log basıp False döner), o yüzden burada
        # erken ve anlaşılır bir hata vermek daha iyi.
        profile_path = os.path.join(NVCURVE_PROFILES_DIR, f"{name}.json")
        if not os.path.isfile(profile_path):
            return {"ok": False, "error": f"Profile not found: {name}"}

    # BUG FIX: on a hardened root umask (e.g. 077), /etc/nvcurve and the
    # config.json it contains could end up created as root-only (0700/0600)
    # by nvcurve's own os.makedirs()/open() calls, which don't force a
    # mode. The GUI/tray read config.json as a normal user, so silently
    # ending up with root-only permissions here is exactly what made the
    # ★ (and the tray's autoload) look like they "weren't working" even
    # though the CLI call itself succeeded. Make the directory traversable
    # up front, regardless of umask.
    try:
        os.makedirs("/etc/nvcurve", exist_ok=True)
        os.chmod("/etc/nvcurve", 0o755)
    except OSError as e:
        return {"ok": False, "error": f"Could not prepare /etc/nvcurve: {e}"}

    env = os.environ.copy()
    env["PYTHONPATH"] = project_dir + os.pathsep + env.get("PYTHONPATH", "")

    args = [sys.executable, "-m", "nvcurve", "profile", "default"]
    args += ["--clear"] if clear else [name]

    result = subprocess.run(
        args, capture_output=True, text=True, env=env, cwd=project_dir
    )
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        return {"ok": False, "error": err or "nvcurve profile default failed"}

    # config.json root:root, mode 0644 olmalı ki root gerektirmeden
    # (tray/GUI kullanıcı olarak) okunabilsin. Sessizce yutmak yerine
    # gerçek hatayı döndürüyoruz — aksi halde "arka planda çalışıyor ama
    # GUI göstermiyor" gibi teşhisi zor bir duruma yol açar.
    try:
        os.chmod("/etc/nvcurve/config.json", 0o644)
    except OSError as e:
        return {"ok": False, "error": f"Set, but could not make config.json readable: {e}"}

    out = (result.stdout or "").strip()
    if clear:
        msg = out or "Default GPU profile cleared."
    else:
        msg = out or f"Default GPU profile set to '{name}'."
    return {"ok": True, "message": msg}


def op_run_gpu_autoload(params: dict) -> dict:
    """Tray açılışında ÇAĞRILAN tek seferlik komut.

    nvcurve'un `autoload` alt komutu, /etc/nvcurve/config.json içinde
    `profile default` ile ayarlanmış profili okuyup uygular ve çıkar.
    Bu KALICI bir daemon/servis DEĞİLDİR — her tray başlangıcında bir kez
    çalışıp biten, root_helper üzerinden pkexec ile tetiklenen sıradan
    bir alt süreçtir. Varsayılan profil ayarlanmamışsa nvcurve zaten
    no-op olarak loglayıp normal (0) çıkış koduyla döner.
    """
    project_dir = params.get("project_dir")
    if not isinstance(project_dir, str) or not project_dir:
        return {"ok": False, "error": "project_dir required"}

    env = os.environ.copy()
    env["PYTHONPATH"] = project_dir + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-m", "nvcurve", "autoload"],
        capture_output=True, text=True, env=env, cwd=project_dir
    )
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    combined = "\n".join(x for x in (out, err) if x)

    if result.returncode != 0:
        return {"ok": False, "error": combined or f"autoload failed (code {result.returncode})"}
    return {"ok": True, "message": combined or "No default GPU profile configured."}


def op_delete_nvcurve_profile(params: dict) -> dict:
    """GPU tuning sekmesindeki 'Delete' düğmesinin karşılığı.

    /etc/nvcurve/profiles altındaki profil dosyasını siler. Silinen profil
    o an varsayılan (auto-load) profil olarak işaretliyse, `nvcurve
    autoload`'ın artık var olmayan bir dosyaya işaret etmemesi için
    varsayılan kaydını da temizler.
    """
    project_dir = params.get("project_dir")
    name = params.get("name")

    if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
        return {"ok": False, "error": "Invalid profile name"}

    target_path = os.path.join(NVCURVE_PROFILES_DIR, f"{name}.json")
    if os.path.dirname(os.path.abspath(target_path)) != os.path.abspath(NVCURVE_PROFILES_DIR):
        return {"ok": False, "error": "Path traversal detected"}

    if not os.path.isfile(target_path):
        return {"ok": False, "error": f"Profile not found: {name}"}

    try:
        os.remove(target_path)
    except OSError as e:
        return {"ok": False, "error": f"Could not delete profile: {e}"}

    cleared_default = False
    try:
        config_path = "/etc/nvcurve/config.json"
        if os.path.isfile(config_path) and isinstance(project_dir, str) and project_dir:
            with open(config_path) as f:
                cfg = json.load(f)
            profiles = cfg.get("auto_load_profiles", {})
            if name in profiles.values():
                env = os.environ.copy()
                env["PYTHONPATH"] = project_dir + os.pathsep + env.get("PYTHONPATH", "")
                subprocess.run(
                    [sys.executable, "-m", "nvcurve", "profile", "default", "--clear"],
                    capture_output=True, text=True, env=env, cwd=project_dir
                )
                try:
                    os.chmod(config_path, 0o644)
                except OSError:
                    pass
                cleared_default = True
    except Exception:
        # Best-effort only: profile deletion above already succeeded and
        # is the primary outcome; failing to also clear a stale default
        # reference shouldn't turn this into an error.
        pass

    msg = f"Profile '{name}' deleted."
    if cleared_default:
        msg += " (it was the default profile — default cleared too.)"
    return {"ok": True, "message": msg}


# ─── SENİN ESKİ SCRIPT İÇERİKLERİNİN BİREBİR TAŞINMIŞ HALİ ───────────────────

def op_save_power_profile(params: dict) -> dict:
    """Güç profilini RYZENADJ_PROFILES_DIR altına yazar.

    K4 düzeltmesi: Eskiden GUI'nin gönderdiği ham `target_path` hiç
    doğrulanmadan `open(target_path, "w")` ile root olarak yazılıyordu;
    GUI ele geçirilirse (ya da bir hata sonucu) herhangi bir dosya
    (örn. /etc/passwd) üzerine yazılabilirdi. Artık yalnızca bir profil
    `name` parametresi kabul ediyoruz, `PROFILE_NAME_RE` ile doğruluyoruz
    ve hedef yolu kendimiz, whitelist edilmiş RYZENADJ_PROFILES_DIR
    içinde inşa ediyoruz — op_write_nvcurve_profile'daki path-traversal
    kontrolünün aynısıyla.
    """
    name = params.get("name")
    content = params.get("content")

    if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
        return {"ok": False, "error": "Invalid profile name"}
    if not isinstance(content, str):
        return {"ok": False, "error": "content must be a string"}
    if len(content.encode()) > MAX_PROFILE_BYTES:
        return {"ok": False, "error": "content too large"}

    try:
        json.loads(content)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"content is not valid JSON: {e}"}

    try:
        os.makedirs(RYZENADJ_PROFILES_DIR, exist_ok=True, mode=0o755)
        target_path = os.path.join(RYZENADJ_PROFILES_DIR, f"{name}.json")

        if os.path.dirname(os.path.abspath(target_path)) != os.path.abspath(RYZENADJ_PROFILES_DIR):
            return {"ok": False, "error": "Path traversal detected"}

        with open(target_path, "w") as f:
            f.write(content)
        os.chmod(target_path, 0o644)
        return {"ok": True, "message": f"Profile written: {target_path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def op_write_activation_script(params: dict) -> dict:
    """Bir profilin kalıcı bash aktivasyon script'ini (set_quiet.sh vb.)
    VAR_SCRIPTS_DIR altına yazar.

    Bu, GUI'nin `_script()` metodunun eskiden yaptığı işin (bir bash
    script'i /tmp'ye yazıp, onu root olarak kopyalayan başka bir Python
    script'i daha üretip pkexec ile çalıştırmak) yerine geçer. Artık
    içerik doğrudan bu op'a gönderiliyor; ara dosya yok, path
    doğrulaması save_power_profile ile aynı desende.
    """
    name = params.get("name")
    content = params.get("content")

    if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
        return {"ok": False, "error": "Invalid script name"}
    if not isinstance(content, str) or not content.strip():
        return {"ok": False, "error": "content must be a non-empty string"}
    if len(content.encode()) > MAX_PROFILE_BYTES:
        return {"ok": False, "error": "content too large"}

    try:
        os.makedirs(VAR_SCRIPTS_DIR, exist_ok=True, mode=0o755)
        target_path = os.path.join(VAR_SCRIPTS_DIR, f"{name}.sh")

        if os.path.dirname(os.path.abspath(target_path)) != os.path.abspath(VAR_SCRIPTS_DIR):
            return {"ok": False, "error": "Path traversal detected"}

        with open(target_path, "w") as f:
            f.write(content)
        os.chmod(target_path, 0o755)
        return {"ok": True, "message": f"Script written: {target_path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _find_alienware_hwmon():
    """HWMON9 arayan bash döngüsünün native karşılığı."""
    base = "/sys/class/hwmon"
    try:
        for entry in os.listdir(base):
            name_path = os.path.join(base, entry, "name")
            try:
                with open(name_path) as f:
                    if f.read().strip() == "alienware_wmi":
                        return os.path.join(base, entry)
            except OSError:
                continue
    except OSError:
        pass
    return None


def _run_ryzenadj(*args):
    try:
        ryzenadj_path = require_tool("ryzenadj", root_context=True)
    except FileNotFoundError as e:
        return False, str(e)
    try:
        result = subprocess.run([ryzenadj_path, *args], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        return True, (result.stdout or "").strip()
    except Exception as e:
        return False, str(e)


_TUNABLE_SAFE_RE = re.compile(r"^[A-Za-z0-9._/-]{1,256}$")


def _write_tunable(kind: str, path: str, value) -> tuple:
    """THP/gaming sysctl-sysfs ayarlarını doğrulayıp yazan ortak yardımcı.
    op_apply_power_profile (adım 6) ve op_restore_boot_defaults tarafından
    paylaşılır — aynı whitelist (O4 düzeltmesi) her ikisinde de geçerli.
    Dönüş: (ok: bool, error_or_empty: str)."""
    value = str(value)
    if not value or not _TUNABLE_SAFE_RE.match(path) or not _TUNABLE_SAFE_RE.match(value):
        return False, "unsafe path/value, skipped"
    try:
        if kind == "sysctl":
            # D11: helper already runs as root, so write the tunable straight to
            # /proc/sys/<key-with-slashes> instead of spawning a sysctl process.
            # The whitelist above still gates path/value; reject traversal.
            rel = path.replace(".", "/")
            if ".." in rel.split("/") or rel.startswith("/"):
                return False, "unsafe sysctl key, skipped"
            proc_path = "/proc/sys/" + rel
            with open(proc_path, "w") as f:
                f.write(value)
            return True, ""
        else:  # file
            with open(path, "w") as f:
                f.write(value)
            return True, ""
    except OSError as e:
        return False, str(e)


def op_apply_power_profile(params: dict) -> dict:
    """Bir güç profilini doğrudan uygular (ryzenadj + alienfx_cli + fan
    boost + curve optimizer + THP/gaming ekstra ayarları).

    Bu op, eskiden ryzenadj_wrapper.py::apply_profile()'ın yaptığı işin
    yerine geçer: eskiden bir bash script'i (write_shell_script) kullanıcı
    tarafından yazılabilir bir dizine yazıp `sudo bash script` ile
    çalıştırılıyordu — bu hem pkexec/Polkit mimarisini tamamen atlıyordu
    (GUI'den başlatıldığında TTY olmadığı için sudo genelde askıda kalır
    ya da sessizce başarısız olur) hem de root_helper'ın allowlist'ini
    devre dışı bırakan ayrı bir yetki yükseltme yolu oluşturuyordu. Artık
    her adım burada, doğrudan subprocess argüman listeleriyle (shell=True
    YOK) çalıştırılıyor; hiçbir ara script diske yazılmıyor.
    """
    name = params.get("name")
    cfg = params.get("cfg")
    if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
        return {"ok": False, "error": "Invalid profile name"}
    if not isinstance(cfg, dict):
        return {"ok": False, "error": "cfg must be an object"}

    def _int(key, default, lo, hi):
        try:
            v = int(cfg.get(key, default))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))

    stapm = _int("stapm_limit_mw", 40000, 1000, 65000)
    fast = _int("fast_limit_mw", 50000, 1000, 65000)
    slow = _int("slow_limit_mw", 40000, 1000, 65000)
    slow_time = _int("slow_time", 10, 1, 300)
    stapm_time = _int("stapm_time", 10, 1, 300)
    tctl = _int("tctl_temp_c", 75, 40, 100)
    vrm = _int("vrm_current_ma", 30000, 1000, 100000)

    fb = [max(0, min(100, int(cfg.get(f"fan_boost_{i}", 0) or 0))) for i in (1, 2, 3, 4)]

    coall = cfg.get("coall")
    cores = cfg.get("cores") or []

    alienfx_profile = params.get("alienfx_profile") or name
    if not re.match(r"^[A-Za-z0-9_-]{1,64}$", str(alienfx_profile)):
        return {"ok": False, "error": "Invalid alienfx profile name"}

    log_lines = []

    # 0. Alienware WMI hwmon'u bul
    hwmon = _find_alienware_hwmon()
    if not hwmon:
        return {"ok": False, "error": "alienware_wmi hwmon not found"}
    log_lines.append(f"Found alienware_wmi at: {hwmon}")

    # 1. RESET
    ok, msg = _run_ryzenadj("--set-coall=0")
    if not ok:
        log_lines.append(f"WARNING: reset failed: {msg}")
    time.sleep(0.05)

    # 2. AlienFX & Platform Profile
    alienfx_cli_path = find_tool("alienfx_cli", root_context=True)
    if not alienfx_cli_path:
        log_lines.append("WARNING: alienfx_cli not found, skipping power profile sync")
    else:
        try:
            subprocess.run([alienfx_cli_path, "setpowerprofile", str(alienfx_profile)],
                            capture_output=True, text=True, timeout=10)
        except Exception as e:
            log_lines.append(f"WARNING: alienfx_cli failed: {e}")
    time.sleep(2.5)

    # 3. RyzenAdj güç limitleri
    for arg in (
        f"--stapm-limit={stapm}", f"--fast-limit={fast}", f"--slow-limit={slow}",
        f"--slow-time={slow_time}", f"--stapm-time={stapm_time}",
        f"--tctl-temp={tctl}", f"--vrm-current={vrm}",
    ):
        ok, msg = _run_ryzenadj(arg)
        if not ok:
            log_lines.append(f"WARNING: {arg} failed: {msg}")
        time.sleep(0.05)

    # 4. Curve Optimizer
    if coall is not None:
        coall_v = max(-100, min(100, int(coall)))
        ok, msg = _run_ryzenadj(f"--set-coall={coall_v}")
        if not ok:
            log_lines.append(f"WARNING: set-coall failed: {msg}")
        time.sleep(0.05)
    elif cores:
        for core in cores:
            try:
                ccd = int(core.get("ccd", 0)) & 0xF
                ccx = int(core.get("ccx", 0)) & 0xF
                core_num = int(core.get("core", 0)) & 0xF
                coper = int(core.get("coper", 0)) & 0xFFFF
                encoded = (((ccd << 4 | ccx) << 4 | core_num) << 20) | coper
                ok, msg = _run_ryzenadj(f"--set-coper={encoded}")
                if not ok:
                    log_lines.append(f"WARNING: set-coper failed: {msg}")
            except (TypeError, ValueError):
                continue
            time.sleep(0.05)

    # 5. Fan boost (+ doğrulama/retry)
    if any(v > 0 for v in fb):
        for i, val in enumerate(fb, start=1):
            boost_path = os.path.join(hwmon, f"fan{i}_boost")
            try:
                with open(boost_path, "w") as f:
                    f.write(str(val))
            except OSError:
                pass
        time.sleep(0.05)
        for i, val in enumerate(fb, start=1):
            boost_path = os.path.join(hwmon, f"fan{i}_boost")
            try:
                with open(boost_path) as f:
                    actual = f.read().strip()
                if actual != str(val):
                    with open(boost_path, "w") as f:
                        f.write(str(val))
                    time.sleep(0.05)
            except OSError:
                pass

    # 6. Ekstra ayarlar (THP + gaming sysctl) — O4 ile aynı whitelist,
    # ortak _write_tunable() yardımcısı üzerinden (bkz. modül başı)
    extra = cfg.get("extra") or {}
    if extra:
        thp = extra.get("thp", {})
        for thp_key, sysfs_path in (
            ("enabled", "/sys/kernel/mm/transparent_hugepage/enabled"),
            ("defrag", "/sys/kernel/mm/transparent_hugepage/defrag"),
            ("shmem", "/sys/kernel/mm/transparent_hugepage/shmem_enabled"),
        ):
            if thp_key in thp:
                _write_tunable("file", sysfs_path, thp[thp_key])

        # Bug fix: extra.gaming sözlüğü {"lru_gen": "5", "vm.swappiness": "10", ...}
        # gibi İSİM→değer eşlemesi kullanıyor — anahtar gerçek sysfs path'i
        # DEĞİL. Eskiden burada key.startswith("vm."/"kernel."/"/") ile
        # path/sysctl ayrımı tahmin edilmeye çalışılıyordu; "lru_gen" ve
        # "sched_min_base_slice"/"sched_migration_cost"/"sched_nr_migrate"
        # gibi isimler bu üç kalıptan hiçbirine uymadığı için HİÇBİR ZAMAN
        # uygulanmıyordu (sessizce atlanıyordu — kullanıcı UI'da "recommended"
        # değeri görüyordu ama gerçek sistemde hiçbir şey değişmiyordu).
        # Artık gerçek path/type, wrapper.py'nin gönderdiği gaming_schema
        # (ryzenadj_wrapper.GAMING_TUNABLES) üzerinden isimle aranıyor.
        gaming = extra.get("gaming", {})
        gaming_schema = params.get("gaming_schema") or {}
        skipped_unknown = []
        for key, value in gaming.items():
            if not value:
                continue
            schema_entry = gaming_schema.get(key)
            if not isinstance(schema_entry, dict) or not schema_entry.get("path"):
                skipped_unknown.append(key)
                continue
            ok, err = _write_tunable(schema_entry.get("type", "file"), schema_entry["path"], value)
            if not ok:
                log_lines.append(f"WARNING: gaming setting '{key}' failed: {err}")
        if skipped_unknown:
            log_lines.append(f"WARNING: unknown gaming setting(s) skipped (no schema): {', '.join(skipped_unknown)}")

    log_lines.append(f"Profile '{name}' applied successfully.")
    return {"ok": True, "message": "\n".join(log_lines)}


def _read_tunable_value(kind: str, path: str):
    """Bir sysctl/dosya ayarının mevcut ham değerini okur. Bulunamaz/
    okunamazsa None döner (capture bu durumda o anahtarı atlar)."""
    try:
        if kind == "sysctl":
            sysctl_path = find_tool("sysctl", root_context=True)
            if not sysctl_path:
                return None
            result = subprocess.run([sysctl_path, "-n", path], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        else:
            if not os.path.exists(path):
                return None
            with open(path, "r") as f:
                content = f.read().strip()
            # THP tarzı dosyalar "always [madvise] never" formatında olabilir;
            # geri yazarken kabul edilen çıplak değeri (madvise) saklıyoruz.
            m = re.search(r"\[([^\]]+)\]", content)
            return m.group(1) if m else content
    except OSError:
        return None


def op_capture_boot_defaults(params: dict) -> dict:
    """custom/gmode'a geçmeden HEMEN ÖNCE çağrılır. Bu önyükleme için
    henüz bir anlık görüntü yoksa (BOOT_DEFAULTS_FILE /run'da tmpfs
    olduğundan her önyüklemede otomatik olarak yok olur), mevcut (henüz
    kullanıcı tarafından değiştirilmemiş) değerleri kaydeder. Zaten
    varsa dokunmaz — bu sayede GUI/tray bunu her custom/gmode
    uygulamasında güvenle, koşulsuzca çağırabilir."""
    if os.path.exists(BOOT_DEFAULTS_FILE):
        return {"ok": True, "message": "Boot defaults already captured this boot, skipping."}

    tunables = params.get("tunables")
    if not isinstance(tunables, dict):
        return {"ok": False, "error": "Missing tunables dict"}

    snapshot = {}
    for key, info in tunables.items():
        if not isinstance(info, dict):
            continue
        path = info.get("path")
        kind = info.get("type")
        if not isinstance(path, str) or kind not in ("sysctl", "file"):
            continue
        value = _read_tunable_value(kind, path)
        if value is not None:
            snapshot[key] = {"path": path, "type": kind, "value": value}

    try:
        os.makedirs(os.path.dirname(BOOT_DEFAULTS_FILE), exist_ok=True, mode=0o755)
        with open(BOOT_DEFAULTS_FILE, "w") as f:
            json.dump(snapshot, f, indent=2)
        os.chmod(BOOT_DEFAULTS_FILE, 0o644)
    except OSError as e:
        return {"ok": False, "error": f"Could not write boot defaults snapshot: {e}"}

    return {"ok": True, "message": f"Boot defaults captured ({len(snapshot)} values) → {BOOT_DEFAULTS_FILE}"}


def op_restore_boot_defaults(params: dict) -> dict:
    """quiet/cool/balanced/balanced-performance'a dönüldüğünde çağrılır.
    Bu önyükleme için bir anlık görüntü yoksa (custom/gmode hiç
    kullanılmadıysa) no-op — değerler zaten hâlâ boot-default durumda."""
    if not os.path.exists(BOOT_DEFAULTS_FILE):
        return {"ok": True, "message": "No boot-defaults snapshot for this boot, nothing to restore."}

    try:
        with open(BOOT_DEFAULTS_FILE, "r") as f:
            snapshot = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error": f"Could not read boot defaults snapshot: {e}"}

    if not isinstance(snapshot, dict):
        return {"ok": False, "error": "Corrupt boot defaults snapshot"}

    restored, failed = 0, []
    for key, entry in snapshot.items():
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        kind = entry.get("type")
        value = entry.get("value")
        if not isinstance(path, str) or kind not in ("sysctl", "file") or value is None:
            continue
        ok, err = _write_tunable(kind, path, value)
        if ok:
            restored += 1
        else:
            failed.append(f"{key}: {err}")

    message = f"Restored {restored}/{len(snapshot)} boot-default values."
    if failed:
        message += "\n" + "\n".join(failed[:10])
    return {"ok": True, "message": message}


def op_read_gpu_curve(params: dict) -> dict:
    """_read_gpu_curve içindeki orijinal nvcurve okuma kodu."""
    project_dir = params.get("project_dir")

    env = os.environ.copy()
    env["PYTHONPATH"] = project_dir + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-m", "nvcurve", "read", "--json"],
        capture_output=True, text=True,
        env=env
    )
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            with open(os.path.join(tempfile.gettempdir(), 'nvcurve_read.json'), "w") as f:
                json.dump(data, f)
            return {"ok": True, "message": "Curve read successfully."}
        except Exception as e:
            return {"ok": False, "error": f"Failed to parse JSON: {e}"}
    else:
        return {"ok": False, "error": f"nvcurve read failed with code {result.returncode}"}


def op_apply_gpu_offsets(params: dict) -> dict:
    """_apply_gpu_offsets içindeki orijinal 3 adımlı profil uygulama kodu."""
    project_dir = params.get("project_dir")
    profile_name = params.get("profile_name")
    profile_data = params.get("profile_data")

    env = os.environ.copy()
    env["PYTHONPATH"] = project_dir + os.pathsep + env.get("PYTHONPATH", "")

    # 1. Adım: Eğriyi sıfırla
    reset_result = subprocess.run(
        [sys.executable, "-m", "nvcurve", "write", "--range", "0-132", "--delta", "0"],
        capture_output=True, text=True, env=env, cwd=project_dir
    )
    if reset_result.returncode != 0:
        return {"ok": False, "error": f"Reset failed: {reset_result.stderr.strip()}"}

    # 2. Adım: Profil verisini kaydet ve uygula
    os.makedirs("/etc/nvcurve/profiles", exist_ok=True)
    profile_path = f"/etc/nvcurve/profiles/{profile_name}.json"
    with open(profile_path, "w") as f:
        json.dump(profile_data, f, indent=2)

    result = subprocess.run(
        [sys.executable, "-m", "nvcurve", "profile", "apply", profile_name],
        capture_output=True, text=True, env=env, cwd=project_dir
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() if result.stderr else "Apply failed"}

    # 3. Adım: Uygulama sonrası eğriyi tekrar oku
    result2 = subprocess.run(
        [sys.executable, "-m", "nvcurve", "read", "--json"],
        capture_output=True, text=True, env=env, cwd=project_dir
    )
    if result2.returncode == 0:
        try:
            data = json.loads(result2.stdout)
            with open(os.path.join(tempfile.gettempdir(), 'nvcurve_apply_result.json'), "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    # DÜZELTME: cli.py artık mem-offset/power-limit gibi ikincil ayarlar
    # başarısız olsa bile (returncode 0 ile) devam edip bunları stderr'e
    # "warning:" satırları olarak yazıyor. Bu uyarıları kaybetmeyip
    # başarı mesajının içine ekliyoruz, böylece kullanıcı GUI logunda
    # görebiliyor ("başarılı, ama şu ikincil ayar uygulanamadı" gibi).
    message = f"Profile '{profile_name}' applied successfully."
    if result.stderr and result.stderr.strip():
        message += "\n" + result.stderr.strip()

    return {"ok": True, "message": message}


def op_reset_gpu_curve(params: dict) -> dict:
    """_reset_gpu_curve içindeki orijinal sıfırlama ve kontrol kodu."""
    project_dir = params.get("project_dir")

    env = os.environ.copy()
    env["PYTHONPATH"] = project_dir + os.pathsep + env.get("PYTHONPATH", "")

    # 0-132 arası tüm noktaları sıfırla
    # DÜZELTME: capture_output verilmediği için bu alt sürecin kendi
    # print() çıktısı ("Target: ...", "Write OK — ...") doğrudan
    # root_helper.py'nin miras alınan stdout'una karışıyordu. Bu da
    # dosyanın en sonunda basılan tek satırlık JSON'u bozup GUI'de
    # "Geçersiz root_helper yanıtı" hatasına yol açıyordu — reset'in
    # kendisi aslında başarıyla uygulanmış olsa bile.
    reset_proc = subprocess.run(
        [sys.executable, "-m", "nvcurve", "write", "--range", "0-132", "--delta", "0"],
        capture_output=True, text=True, env=env, cwd=project_dir
    )
    if reset_proc.returncode != 0:
        return {"ok": False, "error": f"Reset write failed: {reset_proc.stderr.strip() if reset_proc.stderr else reset_proc.returncode}"}

    result = subprocess.run(
        [sys.executable, "-m", "nvcurve", "read", "--json"],
        capture_output=True, text=True, env=env, cwd=project_dir
    )
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            with open(os.path.join(tempfile.gettempdir(), 'nvcurve_reset_result.json'), "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    return {"ok": True, "message": "Reset successful."}


def op_set_vram_memlock(params: dict) -> dict:
    """VRAM'i (bellek clock) sabit bir [min, max] MHz penceresine kilitler.

    mem_offset_mhz'den (VF eğrisine delta ekleyen) farklı bir mekanizma:
    nvcurve'un `memlock set` alt komutu üzerinden NVML'in
    nvmlDeviceSetMemoryLockedClocks çağrısını tetikler (nvidia_oc'nin
    --min-mem-clock/--max-mem-clock ile aynı mekanizma).
    """
    project_dir = params.get("project_dir")
    min_mhz = params.get("min_mhz")
    max_mhz = params.get("max_mhz")

    if not isinstance(min_mhz, int) or not isinstance(max_mhz, int):
        return {"ok": False, "error": "min_mhz/max_mhz must be integers"}
    if min_mhz <= 0 or max_mhz <= 0 or min_mhz > max_mhz:
        return {"ok": False, "error": "Invalid min_mhz/max_mhz range"}

    env = os.environ.copy()
    env["PYTHONPATH"] = project_dir + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-m", "nvcurve", "memlock", "set",
         "--min", str(min_mhz), "--max", str(max_mhz)],
        capture_output=True, text=True, env=env, cwd=project_dir,
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or "nvcurve memlock set failed"}
    # `nvcurve memlock set` itself prints a warning to stdout (not stderr) when
    # the driver silently snapped the request to a different stock clock —
    # surface that in the returned message instead of a blanket "locked" claim.
    return {"ok": True, "message": result.stdout.strip() or f"VRAM locked to {min_mhz}-{max_mhz} MHz."}


def op_reset_vram_memlock(params: dict) -> dict:
    """VRAM locked-clock kilidini kaldırır (nvcurve `memlock reset`)."""
    project_dir = params.get("project_dir")

    env = os.environ.copy()
    env["PYTHONPATH"] = project_dir + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-m", "nvcurve", "memlock", "reset"],
        capture_output=True, text=True, env=env, cwd=project_dir,
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or "nvcurve memlock reset failed"}
    return {"ok": True, "message": result.stdout.strip() or "VRAM clock unlocked."}


def op_apply_cpu_isolation(params: dict) -> dict:
    """redirect-tasks.sh'nin Python karşılığı. cgroup v2 CCX izolasyonunu uygular."""
    import glob

    launcher = params.get("launcher", "lutris")
    # Basit güvenlik: sadece alfanümerik + tire/alt çizgi
    if not re.match(r'^[A-Za-z0-9_.-]{1,64}$', launcher):
        return {"ok": False, "error": "Invalid launcher name"}

    cgroup_root = "/sys/fs/cgroup"
    log_lines = []

    def write(path, value):
        with open(path, "w") as f:
            f.write(value)

    def log(msg):
        log_lines.append(msg)

    # ── L3 önbellekten CCX topolojisini oku ────────────────────
    if not os.path.isdir(f"/sys/devices/system/cpu/cpu0/cache/index3"):
        return {"ok": False, "error": "Sysfs L3 cache topology info not found."}

    shared_lists = set()
    for p in glob.glob("/sys/devices/system/cpu/cpu*/cache/index3/shared_cpu_list"):
        try:
            with open(p) as f:
                shared_lists.add(f.read().strip())
        except OSError:
            pass

    def _first_core_num(shared_cpu_list: str) -> int:
        # "0-7" ya da "0,1,2" gibi biçimleri destekle; sıralama anahtarı
        # olarak ilk çekirdek numarasını kullan. O1 düzeltmesi: string
        # sıralaması ("16-23" < "0-7" gibi durumlarda yanlış CCX seçimine
        # yol açabiliyordu), artık sayısal sıralama yapılıyor.
        try:
            first_token = shared_cpu_list.split(",")[0].split("-")[0]
            return int(first_token)
        except (ValueError, IndexError):
            return 1 << 30  # parse edilemeyenleri sona at

    ccx_lines = sorted(shared_lists, key=_first_core_num)
    if len(ccx_lines) < 2:
        return {"ok": False, "error": "Only 1 CCX found or topology could not be parsed."}

    ccx0, ccx1 = ccx_lines[0], ccx_lines[1]
    log(f"Found core P#s:")
    log(f"\tCCX0 (CCD0 - Performans): {ccx0}")
    log(f"\tCCX1 (CCD1 - Sistem): {ccx1}")

    # ── cgroup v2 cpuset controller etkinleştir ─────────────────
    subtree_ctrl = f"{cgroup_root}/cgroup.subtree_control"
    try:
        with open(subtree_ctrl) as f:
            if "cpuset" not in f.read():
                write(subtree_ctrl, "+cpuset")
    except OSError as e:
        return {"ok": False, "error": f"Cannot enable cpuset controller: {e}"}

    # ── theUgly grubunu oluştur ve yapılandır ───────────────────
    ugly_dir = f"{cgroup_root}/theUgly"
    os.makedirs(ugly_dir, exist_ok=True)
    write(f"{ugly_dir}/cpuset.cpus", ccx1)
    write(f"{ugly_dir}/cpuset.mems", "0")
    try:
        write(f"{ugly_dir}/cpuset.cpus.partition", "root")
    except OSError:
        pass  # bazı kernel versiyonlarında opsiyonel

    # ── Sistem processlerini theUgly'ye taşı ────────────────────
    CRITICAL_SERVICES = {
        "systemd", "init", "openrc-init", "dbus-daemon", "dbus-broker",
        "polkitd", "udevd", "elogind", "Xorg", "Xwayland", "wayland",
        "pipewire", "wireplumber", "sddm", "gdm", "lightdm",
        "kwin_wayland", "kwin_x11", "mutter",
    }
    success_ugly = fail_ugly = skipped_ugly = 0
    for pid_str in os.listdir("/proc"):
        if not pid_str.isdigit():
            continue
        pid = pid_str
        try:
            with open(f"/proc/{pid}/comm") as f:
                proc_name = f.read().strip()
        except OSError:
            skipped_ugly += 1
            continue

        if not proc_name or proc_name in CRITICAL_SERVICES:
            skipped_ugly += 1
            continue

        # systemd slice kontrolü (OpenRC'de bu dosya mevcut olmayabilir)
        try:
            with open(f"/proc/{pid}/cgroup") as f:
                if "system.slice" in f.read():
                    skipped_ugly += 1
                    continue
        except OSError:
            pass

        try:
            write(f"{ugly_dir}/cgroup.procs", pid)
            success_ugly += 1
        except OSError:
            fail_ugly += 1

    log(f"\t{success_ugly} processes successfully redirected to theUgly.")
    log(f"\t{skipped_ugly} protected processes intentionally skipped.")
    log(f"\t{fail_ugly} processes failed to redirect.")

    # ── theGood grubunu oluştur ve yapılandır ───────────────────
    good_dir = f"{cgroup_root}/theGood"
    os.makedirs(good_dir, exist_ok=True)
    write(f"{good_dir}/cpuset.cpus", ccx0)
    write(f"{good_dir}/cpuset.mems", "0")
    try:
        write(f"{good_dir}/cpuset.cpus.partition", "root")
    except OSError:
        pass

    # ── Launcher ve child process'lerini theGood'a taşı ─────────
    success_good = fail_good = 0
    visited_pids = set()  # O2: PID reuse / çevrim durumunda tekrar ziyareti engeller

    def _read_children(pid):
        """pgrep -P fork etmek yerine /proc/<pid>/task/*/children'ı okur.
        Derin process ağaçlarında onlarca fork'tan kaçınır; bu dosya
        doğrudan kernel tarafından sağlanır (CONFIG_PROC_CHILDREN)."""
        children = []
        try:
            task_dir = f"/proc/{pid}/task"
            for tid in os.listdir(task_dir):
                children_path = f"{task_dir}/{tid}/children"
                try:
                    with open(children_path) as f:
                        children.extend(
                            int(c) for c in f.read().split() if c.isdigit()
                        )
                except OSError:
                    continue
        except OSError:
            pass
        return children

    def move_to_good(start_pid):
        nonlocal success_good, fail_good
        stack = [start_pid]
        while stack:
            pid = stack.pop()
            if pid in visited_pids:
                continue
            visited_pids.add(pid)
            try:
                write(f"{good_dir}/cgroup.procs", str(pid))
                success_good += 1
            except OSError:
                fail_good += 1
            stack.extend(c for c in _read_children(pid) if c not in visited_pids)

    pgrep_path = find_tool("pgrep", root_context=True)
    launcher_pids = []
    if pgrep_path:
        result = subprocess.run([pgrep_path, launcher], capture_output=True, text=True)
        launcher_pids = [p.strip() for p in result.stdout.split() if p.strip().isdigit()]
    for lpid in launcher_pids:
        move_to_good(int(lpid))

    if success_good > 0:
        log(f"\t{success_good} process(es) (including games) successfully redirected to CCD0.")
    elif fail_good > 0:
        log(f"\t{fail_good} process(es) failed to redirect to CCD0.")
    else:
        log(f"\tNo {launcher} processes found. Run {launcher} first!")

    return {"ok": True, "message": "\n".join(log_lines)}


def op_revert_cpu_isolation(params: dict) -> dict:
    """revert-tasks.sh'nin Python karşılığı. theGood/theUgly cgroup'larını kaldırır."""
    cgroup_root = "/sys/fs/cgroup"
    ugly_dir = f"{cgroup_root}/theUgly"
    good_dir = f"{cgroup_root}/theGood"
    max_retries = 10

    def move_tasks(cg_dir):
        procs_file = f"{cg_dir}/cgroup.procs"
        if not os.path.isdir(cg_dir):
            return
        try:
            with open(procs_file) as f:
                pids = f.read().split()
        except OSError:
            return
        for pid in pids:
            pid = pid.strip()
            if pid:
                try:
                    with open(f"{cgroup_root}/cgroup.procs", "w") as f:
                        f.write(pid)
                except OSError:
                    pass

    for i in range(1, max_retries + 1):
        move_tasks(ugly_dir)
        move_tasks(good_dir)
        try:
            if os.path.isdir(ugly_dir):
                os.rmdir(ugly_dir)
        except OSError:
            pass
        try:
            if os.path.isdir(good_dir):
                os.rmdir(good_dir)
        except OSError:
            pass

        if not os.path.isdir(ugly_dir) and not os.path.isdir(good_dir):
            return {"ok": True, "message": "Successful: All tasks moved and groups are removed."}

        time.sleep(0.5)

    return {"ok": False, "error": f"After {max_retries} retries, cgroups still exist."}


def op_read_gaming_status(params: dict) -> dict:
    """Reads the current value of each Gaming Optimizations setting as root.

    Several of these settings (sched_min_base_slice, sched_migration_cost,
    sched_nr_migrate) live under /sys/kernel/debug, which typically
    requires root just to traverse the directory. Reading them from the
    unprivileged GUI process always showed "(missing)" even though the
    files exist. This op does the same read that _refresh_gaming_status
    used to do directly, but as root.

    Teşhis düzeltmesi: Eskiden herhangi bir okuma hatası tek tip "?" ile
    gösteriliyordu — bu, örn. lru_gen gibi bir ayarın neden okunamadığını
    (dosya bu kernelde hiç yok mu? path yanlış mı? sysctl ismi değişmiş
    mi?) anlamayı imkansız kılıyordu. Artık "?" yerine kısa, ayırt edici
    bir sebep gösteriliyor: "(no file)", "(no sysctl)", "(perm denied)",
    ya da beklenmedik bir hata için "(err: ...)".
    """
    settings = params.get("settings")
    if not isinstance(settings, dict):
        return {"ok": False, "error": "Missing settings dict"}

    values = {}
    for key, info in settings.items():
        if not isinstance(info, dict):
            continue
        path = info.get("path")
        kind = info.get("type")
        if not isinstance(path, str):
            values[key] = "(no path)"
            continue
        try:
            if kind == "sysctl":
                sysctl_path = find_tool("sysctl", root_context=True)
                if not sysctl_path:
                    values[key] = "(no sysctl)"
                    continue
                result = subprocess.run([sysctl_path, "-n", path], capture_output=True, text=True)
                if result.returncode == 0:
                    values[key] = result.stdout.strip()
                else:
                    err = (result.stderr or "").strip()
                    values[key] = "(no sysctl)" if "unknown key" in err.lower() else f"(err: {err[:40] or 'sysctl failed'})"
            else:  # file
                if not os.path.exists(path):
                    values[key] = "(no file)"
                    continue
                with open(path, "r") as f:
                    content = f.read().strip()
                m = re.search(r"\[([^\]]+)\]", content)
                values[key] = m.group(1) if m else (content or "(empty)")
        except PermissionError:
            values[key] = "(perm denied)"
        except Exception as e:
            values[key] = f"(err: {e})"[:60]

    return {"ok": True, "values": values}


def op_run_script_content(params: dict) -> dict:
    """K1 düzeltmesi: GUI dinamik olarak ürettiği (gaming ayarları, profil
    script'i vb.) Python kaynak kodunu artık kendi başına /tmp içine
    0o755 (dünya-okunur/öngörülebilir yol) bir wrapper olarak yazıp
    pkexec ile çalıştırmıyor (bu, TOCTOU + yerel yetki yükseltme riski
    taşıyordu ve ayrıca apply_cpu_isolation/revert_cpu_isolation gibi
    güvenli op'larla çift bir yol oluşturuyordu).

    Bunun yerine GUI, çalıştırılacak Python kaynak kodunu doğrudan bu
    op'a 'content' olarak yollar. Dosya YALNIZCA burada, zaten root
    olan bu süreç tarafından, ALLOWED_SCRIPTS_DIR altında rastgele adlı,
    0o700 izinli ve root sahipli olarak oluşturulur; çalıştırıldıktan
    hemen sonra silinir. Böylece hiçbir zaman kullanıcı tarafından
    yazılabilir/tahmin edilebilir bir yol root ile çalıştırılmaz.
    """
    content = params.get("content")
    if not isinstance(content, str) or not content.strip():
        return {"ok": False, "error": "Missing script content"}
    if len(content.encode()) > MAX_PROFILE_BYTES:
        return {"ok": False, "error": "Script content too large"}

    os.makedirs(ALLOWED_SCRIPTS_DIR, exist_ok=True, mode=0o700)
    os.chmod(ALLOWED_SCRIPTS_DIR, 0o700)

    fd, script_path = tempfile.mkstemp(prefix="gui_op_", suffix=".py", dir=ALLOWED_SCRIPTS_DIR)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(script_path, 0o700)  # root:root, sadece root çalıştırabilir

        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True, text=True, timeout=120,
        )
        output = (result.stdout or "").strip()
        warnings = (result.stderr or "").strip()

        if result.returncode != 0:
            err = warnings or output or f"Script exited with code {result.returncode}"
            return {"ok": False, "error": err}

        message = output
        if warnings:
            message = (message + "\n" + warnings) if message else warnings
        return {"ok": True, "message": message or "Script executed successfully."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Script timed out after 120 seconds."}
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass


# NOT: Eskiden burada path-tabanlı bir `op_run_script` da vardı. GUI'nin
# tüm çağrıları artık `op_run_script_content`'e taşındığı için (script
# içeriği doğrudan gönderiliyor, ara dosya yok) path-tabanlı sürüm
# kullanılmayan/ölü kod haline geldi ve saldırı yüzeyini gereksiz yere
# büyüttüğü için tamamen kaldırıldı.

# ─────────────────────────────────────────────────────────────────
# İzin Verilenler Listesi (Allowlist)
# ─────────────────────────────────────────────────────────────────
OPERATIONS = {
    "reload_alienware_wmi": op_reload_alienware_wmi,
    "write_nvcurve_profile": op_write_nvcurve_profile,
    "set_default_gpu_profile": op_set_default_gpu_profile,
    "run_gpu_autoload": op_run_gpu_autoload,
    "delete_nvcurve_profile": op_delete_nvcurve_profile,

    # Yeni eklenen güvenli operasyon köprüleri:
    "save_power_profile": op_save_power_profile,
    "apply_power_profile": op_apply_power_profile,
    "capture_boot_defaults": op_capture_boot_defaults,
    "restore_boot_defaults": op_restore_boot_defaults,
    "write_activation_script": op_write_activation_script,
    "read_gpu_curve": op_read_gpu_curve,
    "apply_gpu_offsets": op_apply_gpu_offsets,
    "reset_gpu_curve": op_reset_gpu_curve,
    "set_vram_memlock": op_set_vram_memlock,
    "reset_vram_memlock": op_reset_vram_memlock,
    "run_script_content": op_run_script_content,
    "read_gaming_status": op_read_gaming_status,

    # CPU izolasyonu (redirect-tasks.sh / revert-tasks.sh yerine)
    "apply_cpu_isolation": op_apply_cpu_isolation,
    "revert_cpu_isolation": op_revert_cpu_isolation,
}


def main() -> int:
    if os.geteuid() != 0:
        print(json.dumps({"ok": False, "error": "root_helper must run as root"}))
        return 1

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"Invalid JSON on stdin: {e}"}))
        return 1

    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "error": "Payload must be a JSON object"}))
        return 1

    op_name = payload.get("op")
    handler = OPERATIONS.get(op_name)
    if handler is None:
        print(json.dumps({"ok": False, "error": f"Unknown or disallowed op: {op_name!r}"}))
        return 1

    try:
        result = handler(payload)
    except Exception as e:
        result = {"ok": False, "error": f"Internal error: {e}"}

    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
