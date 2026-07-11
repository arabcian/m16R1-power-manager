#!/usr/bin/env python3
"""
ryzenadj_wrapper - AMD Ryzen Adj için profil yöneticisi
Alienware M16 R1 AMD için optimize edilmiştir.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# O4: write_shell_script içinde sysctl -w {key}={value} ve
# echo '{value}' > {key} doğrudan string interpolasyonla yazılıyordu.
# `value`/`key` profil JSON'undan geliyor; içinde ';' veya '$()' olması
# root script'inde komut enjeksiyonuna yol açabilirdi. Bu regex'ler
# beklenen karakter setinin dışına çıkan her şeyi reddeder.
_SHELL_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9._/-]{1,256}$")
_SHELL_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9._/-]{1,256}$")


def _is_shell_safe(text) -> bool:
    """key/value'nin bash script'ine gömülmeden önce güvenli olup
    olmadığını kontrol eder (yalnızca alfanümerik + . _ / -)."""
    return isinstance(text, str) and bool(_SHELL_SAFE_VALUE_RE.match(text))

SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from tool_paths import find_tool  # noqa: E402

# FHS'e uygun, kurulum dizininden bağımsız sabit yollar. Bunlar
# root_helper.py'deki RYZENADJ_PROFILES_DIR / VAR_SCRIPTS_DIR ile
# birebir eşleşmelidir — biri değişirse diğeri de güncellenmeli.
PROFILES_DIR = Path("/etc/ryzenadj-gui/profiles")
SCRIPTS_DIR = Path("/var/lib/ryzenadj-gui/scripts")

# "Script Oluştur" butonuyla üretilen, salt önizleme/manuel-kullanım
# amaçlı .sh dosyalarının YEREL (root gerektirmeyen) kopyası. Kalıcı,
# sistem geneli kopya root_helper'ın write_activation_script op'u
# üzerinden SCRIPTS_DIR'e yazılır (bkz. ryzenadj_gui.py::_script()).
_LOCAL_CACHE_BASE = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
LOCAL_SCRIPTS_DIR = _LOCAL_CACHE_BASE / "ryzenadj-gui" / "scripts"

ROOT_HELPER_PATH = "/usr/local/lib/ryzenadj-gui/root_helper.py"


def _call_root_helper(payload: dict, timeout: int = 30):
    """GUI'nin _run_root_helper_command'ıyla aynı desen, ama Qt'siz/senkron:
    ryzenadj_wrapper.py hem GUI hem de CLI'dan (ve tray'den) kullanılabildiği
    için burada bloklayan, sade bir sürüm yeterli. root_helper.py'ye
    doğrudan (python3 değil) pkexec ile geçiliyor — bu, com.ryzenadj.gui.policy
    içindeki org.freedesktop.policykit.exec.path eşleşmesini tetikler ve
    Polkit auth cache'ini (auth_admin_keep_always) diğer çağrılarla paylaşır.
    Döner: (ok: bool, message_or_error: str)
    """
    try:
        proc = subprocess.run(
            ["pkexec", ROOT_HELPER_PATH],
            input=json.dumps(payload),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "root_helper timed out"
    except Exception as e:
        return False, str(e)

    out = (proc.stdout or "").strip()
    lines = [l for l in out.splitlines() if l.strip()]
    if not lines:
        return False, (proc.stderr or "").strip() or "root_helper produced no output"
    try:
        res = json.loads(lines[-1])
    except json.JSONDecodeError:
        return False, "Invalid root_helper response"

    if res.get("ok"):
        return True, res.get("message", "")
    return False, res.get("error", "Unknown error")


PROFILES = ["quiet", "cool", "balanced", "balanced-performance", "performance", "custom"]

SCRIPT_NAMES = {
    "quiet": "set_quiet.sh",
    "cool": "set_cool.sh",
    "balanced": "set_balanced.sh",
    "balanced-performance": "set_balanced_perf.sh",
    "performance": "set_performance.sh",
    "gmode": "set_gmode.sh",
    "custom": "set_custom.sh",
}

ALIENFX_PROFILES = {
    "quiet": "quiet",
    "cool": "cool",
    "balanced": "balanced",
    "balanced-performance": "balanced-performance",
    "performance": "performance",
    "gmode": "performance",
    "custom": "custom",
}

# ─── Boot-defaults yakalama/geri yükleme ───────────────────────────────────
# "Extra Tools" sekmesindeki THP + oyun/sistem sysctl/sysfs ayarları
# (lru_gen, sched_* dahil). Tek kaynak burası — GUI (_build_tab_extra_tools)
# artık bu sözlüğü kullanıyor, kendi kopyasını tutmuyor; böylece
# capture/restore ile UI'daki liste asla birbirinden sapmıyor.
#
# "gmode"/"custom" profillerine geçildiğinde, henüz bu boot için bir
# "boot defaults" anlık görüntüsü alınmadıysa (yani kullanıcı bu ayarları
# HENÜZ değiştirmediyse), root_helper mevcut (temiz) değerleri
# /run/ryzenadj-gui/boot_defaults.json'a kaydeder — /run tmpfs olduğundan
# bu otomatik olarak "her önyüklemede bir kez" anlamına gelir. "quiet",
# "cool", "balanced", "balanced-performance" gibi sade profillere
# dönüldüğünde, bu anlık görüntü varsa değerler ona geri yüklenir.
GAMING_TUNABLES = {
    "vm.compaction_proactiveness": {"path": "vm.compaction_proactiveness", "recommended": "0", "type": "sysctl"},
    "vm.watermark_boost_factor": {"path": "vm.watermark_boost_factor", "recommended": "1", "type": "sysctl"},
    "vm.min_free_kbytes": {"path": "vm.min_free_kbytes", "recommended": "131072", "type": "sysctl"},
    "vm.watermark_scale_factor": {"path": "vm.watermark_scale_factor", "recommended": "80", "type": "sysctl"},
    "vm.swappiness": {"path": "vm.swappiness", "recommended": "10", "type": "sysctl"},
    "vm.zone_reclaim_mode": {"path": "vm.zone_reclaim_mode", "recommended": "0", "type": "sysctl"},
    "vm.page_lock_unfairness": {"path": "vm.page_lock_unfairness", "recommended": "1", "type": "sysctl"},
    "kernel.sched_child_runs_first": {"path": "kernel.sched_child_runs_first", "recommended": "0", "type": "sysctl"},
    "kernel.sched_autogroup_enabled": {"path": "kernel.sched_autogroup_enabled", "recommended": "1", "type": "sysctl"},
    "kernel.sched_cfs_bandwidth_slice_us": {"path": "kernel.sched_cfs_bandwidth_slice_us", "recommended": "3000", "type": "sysctl"},
    "lru_gen": {"path": "/sys/kernel/mm/lru_gen/enabled", "recommended": "5", "type": "file"},
    "sched_min_base_slice": {"path": "/sys/kernel/debug/sched/min_base_slice_ns", "recommended": "3000000", "type": "file"},
    "sched_migration_cost": {"path": "/sys/kernel/debug/sched/migration_cost_ns", "recommended": "500000", "type": "file"},
    "sched_nr_migrate": {"path": "/sys/kernel/debug/sched/nr_migrate", "recommended": "8", "type": "file"},
}

# THP, gaming_settings'ten ayrı tutuluyor çünkü UI'da kendi combo box'ları
# var (checkbox grid değil); capture/restore için burada birleştiriliyor.
THP_TUNABLES = {
    "thp_enabled": {"path": "/sys/kernel/mm/transparent_hugepage/enabled", "type": "file"},
    "thp_defrag": {"path": "/sys/kernel/mm/transparent_hugepage/defrag", "type": "file"},
    "thp_shmem": {"path": "/sys/kernel/mm/transparent_hugepage/shmem_enabled", "type": "file"},
}

# capture/restore'a gönderilen tam liste (GAMING_TUNABLES + THP_TUNABLES)
BOOT_DEFAULTS_TUNABLES = {**GAMING_TUNABLES, **THP_TUNABLES}

# Bu profillere geçildiğinde boot-defaults'a dönülür (custom/gmode HARİÇ
# tüm "sade" profiller).
SIMPLE_PROFILES = {"quiet", "cool", "balanced", "balanced-performance"}
TUNING_PROFILES = {"custom", "gmode"}


def ensure_boot_defaults_captured() -> None:
    """custom/gmode'a geçmeden ÖNCE çağrılır. Bu önyükleme için henüz bir
    anlık görüntü alınmadıysa, root_helper mevcut (henüz değiştirilmemiş)
    değerleri kaydeder. Zaten alınmışsa no-op (ucuz, güvenle her seferinde
    çağrılabilir)."""
    ok, msg = _call_root_helper({
        "op": "capture_boot_defaults",
        "tunables": {k: {"path": v["path"], "type": v["type"]} for k, v in BOOT_DEFAULTS_TUNABLES.items()},
    }, timeout=10)
    if ok:
        log(f"[BOOT-DEFAULTS] {msg}")
    else:
        log(f"[BOOT-DEFAULTS] capture failed: {msg}")


def restore_boot_defaults() -> None:
    """Sade bir profile (quiet/cool/balanced/balanced-performance)
    dönüldüğünde çağrılır. Bu önyükleme için bir anlık görüntü
    alınmamışsa (yani custom/gmode hiç kullanılmadıysa) no-op —
    değerler zaten hâlâ boot-default durumda."""
    ok, msg = _call_root_helper({"op": "restore_boot_defaults"}, timeout=10)
    if ok:
        log(f"[BOOT-DEFAULTS] {msg}")
    else:
        log(f"[BOOT-DEFAULTS] restore failed: {msg}")

C = {
    "reset": "\033[0m", "bold": "\033[1m", "red": "\033[91m",
    "green": "\033[92m", "yellow": "\033[93m", "cyan": "\033[96m",
    "white": "\033[97m", "dim": "\033[2m",
}

# Sleep süresi (saniye) - 0.05 = 50ms
SLEEP_TIME = "0.05"
# Platform profile değişikliğinden sonra bekleme süresi
PLATFORM_WAIT = "2.5"

try:
    import ryzenadj_common as _common
except ImportError:
    _common = None

def log(msg: str) -> None:
    """Q1: Artık ortak, dönen (rotating) log'a yazıyor
    (ryzenadj_common.log). Modül bir nedenle bulunamazsa eski
    /tmp/ryzenadj_tray.log davranışına düşülür."""
    if _common is not None:
        _common.log(msg)
        return
    try:
        log_file = Path("/tmp/ryzenadj_tray.log")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

def clr(color: str, text: str) -> str:
    return f"{C[color]}{text}{C['reset']}"

def load_profile(name: str) -> dict:
    path = PROFILES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Profil bulunamadı: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def validate_profile(cfg: dict) -> None:
    required = ["stapm_limit_mw", "fast_limit_mw", "slow_limit_mw", "tctl_temp_c", "vrm_current_ma"]
    for key in required:
        if key not in cfg:
            raise ValueError(f"Config'de eksik alan: '{key}'")

# ─── ALIENWARE WMI HWMON BULMA ─────────────────────────────────────────

def find_alienware_wmi_hwmon() -> Path:
    """alienware_wmi hwmon dizinini dinamik olarak bulur."""
    base_path = Path("/sys/class/hwmon")
    if not base_path.exists():
        return None

    for hwmon_dir in base_path.glob("hwmon*"):
        name_file = hwmon_dir / "name"
        if name_file.exists():
            try:
                if name_file.read_text().strip() == "alienware_wmi":
                    return hwmon_dir
            except Exception:
                pass

    # Alternatif: WMI yolundan dene
    wmi_base = Path("/sys/devices/platform/PNP0C14:03/wmi_bus/wmi_bus-PNP0C14:03")
    if wmi_base.exists():
        for wmi_dir in wmi_base.glob("*"):
            hwmon_dir = wmi_dir / "hwmon"
            if hwmon_dir.exists():
                for hwmon_sub in hwmon_dir.glob("hwmon*"):
                    name_file = hwmon_sub / "name"
                    if name_file.exists():
                        try:
                            if name_file.read_text().strip() == "alienware_wmi":
                                return hwmon_sub
                        except Exception:
                            pass

    return None

def find_gpu_temp_path() -> Path:
    """GPU sıcaklık dosyasını alienware_wmi veya dell_smm'den bulur."""
    # 1. alienware_wmi'de temp2_label = "GPU" olanı bul
    hwmon = find_alienware_wmi_hwmon()
    if hwmon:
        for i in range(1, 11):
            label_file = hwmon / f"temp{i}_label"
            if label_file.exists():
                try:
                    if label_file.read_text().strip() == "GPU":
                        return hwmon / f"temp{i}_input"
                except Exception:
                    pass

        # Temp2_input genellikle GPU
        temp2 = hwmon / "temp2_input"
        if temp2.exists():
            return temp2

    # 2. dell_smm'de GPU label'lı olanı bul
    hwmon = find_hwmon_by_name("dell_smm")
    if hwmon:
        for i in range(1, 11):
            label_file = hwmon / f"temp{i}_label"
            if label_file.exists():
                try:
                    if "gpu" in label_file.read_text().strip().lower():
                        return hwmon / f"temp{i}_input"
                except Exception:
                    pass

    return None

def find_hwmon_by_name(name: str) -> Path:
    """İsmi verilen hwmon dizinini bulur."""
    base_path = Path("/sys/class/hwmon")
    if not base_path.exists():
        return None
    for hwmon_dir in base_path.glob("hwmon*"):
        name_file = hwmon_dir / "name"
        if name_file.exists():
            try:
                if name_file.read_text().strip() == name:
                    return hwmon_dir
            except Exception:
                pass
    return None

# Hwmon yollarını başlangıçta bul
ALIENWARE_HWMON = find_alienware_wmi_hwmon()
GPU_TEMP_PATH = find_gpu_temp_path()

if ALIENWARE_HWMON:
    log(f"Found alienware_wmi at: {ALIENWARE_HWMON}")
else:
    log("WARNING: alienware_wmi hwmon not found!")

if GPU_TEMP_PATH:
    log(f"Found GPU temp at: {GPU_TEMP_PATH}")
else:
    log("WARNING: GPU temp path not found!")

# ─── FAN BOOST ──────────────────────────────────────────────────────────

def _write_fan_boost(hwmon, idx: int, value: int) -> bool:
    """Q4: set_fan_boost_values ve set_fan_boost_manually'nin tekrarlayan
    clamp+write+verify mantığını tek bir yerde toplar. Her iki fonksiyon
    da artık bunu çağırıyor."""
    value = max(0, min(100, value))
    boost_file = hwmon / f"fan{idx}_boost"
    if not boost_file.exists():
        return False
    boost_file.write_text(str(value))
    return True


def set_fan_boost_values(fb1: int, fb2: int, fb3: int, fb4: int):
    """Fan boost değerlerini doğrudan sysfs'e yazar ve doğrular.
    W4: cached ALIENWARE_HWMON kullanır; glob taraması yok.
    """
    try:
        hwmon = ALIENWARE_HWMON
        if not hwmon:
            log("ERROR: alienware_wmi hwmon not found, cannot set fan boost")
            return

        for idx, value in enumerate((fb1, fb2, fb3, fb4), start=1):
            value = max(0, min(100, value))
            if _write_fan_boost(hwmon, idx, value):
                log(f"Set fan{idx}_boost = {value}")

        time.sleep(0.3)

        for boost_file, expected in boost_files:
            if boost_file.exists():
                try:
                    actual = int(boost_file.read_text().strip())
                    if actual != expected:
                        log(f"WARNING: {boost_file.name} is {actual}, expected {expected}. Retrying...")
                        boost_file.write_text(str(expected))
                        time.sleep(0.1)
                except Exception:
                    pass

    except Exception as e:
        log(f"Fan boost set error: {e}")

def _build_shell_script_content(name: str, cfg: dict) -> str:
    """Bir profilin (yalnızca önizleme/manuel-kullanım amaçlı) bash
    script içeriğini üretir. GUI'nin _script() metodu bu içeriği
    root_helper'ın write_activation_script op'una gönderir; gerçek
    profil uygulaması artık bu script ÜZERİNDEN değil, doğrudan
    op_apply_power_profile üzerinden yapılır (bkz. apply_profile())."""
    # Temel ryzenadj ayarları
    stapm = cfg.get("stapm_limit_mw", 40000)
    fast = cfg.get("fast_limit_mw", 50000)
    slow = cfg.get("slow_limit_mw", 40000)
    slow_time = cfg.get("slow_time", 10)
    stapm_time = cfg.get("stapm_time", 10)
    tctl = cfg.get("tctl_temp_c", 75)
    vrm = cfg.get("vrm_current_ma", 30000)

    # Fan boost ayarları (0-100)
    fb1 = max(0, min(100, cfg.get("fan_boost_1", 0)))
    fb2 = max(0, min(100, cfg.get("fan_boost_2", 0)))
    fb3 = max(0, min(100, cfg.get("fan_boost_3", 0)))
    fb4 = max(0, min(100, cfg.get("fan_boost_4", 0)))

    # Curve Optimizer
    coall = cfg.get("coall")
    cores = cfg.get("cores", [])

    alienfx_profile = ALIENFX_PROFILES.get(name, name)

    # Resolve tool paths once, before generating any script lines. This is
    # a preview/manual-use script (see docstring above), so a missing tool
    # shouldn't raise — ryzenadj is required for the script to do anything
    # useful, so fall back to the bare name with a warning comment if truly
    # not found; alienfx_cli is optional and its section is simply omitted.
    ryzenadj_path = find_tool("ryzenadj") or "ryzenadj"
    alienfx_cli_path = find_tool("alienfx_cli")

    lines = []
    w = lines.append
    w("#!/bin/bash\n# Profile: {}\n".format(name))
    if ryzenadj_path == "ryzenadj":
        w("# WARNING: ryzenadj was not found at generation time; this script")
        w("# assumes it is on PATH. Install ryzenadj or edit the path below.\n")

    # 0. Alienware WMI hwmon'u dinamik olarak bul
    w("# Find alienware_wmi hwmon path dynamically")
    w('HWMON9=""')
    w("for i in /sys/class/hwmon/hwmon*; do")
    w('    if [ -f "$i/name" ] && [ "$(cat $i/name 2>/dev/null)" = "alienware_wmi" ]; then')
    w('        HWMON9="$i"')
    w("        break")
    w("    fi")
    w("done")
    w('if [ -z "$HWMON9" ]; then')
    w('    echo "ERROR: alienware_wmi hwmon not found!"')
    w("    exit 1")
    w("fi")
    w('echo "Found alienware_wmi at: $HWMON9"\n')

    # 1. RESET
    w("# 1. RESET: Clear all Curve Optimizer settings")
    w(f"{ryzenadj_path} --set-coall=0")
    w(f"sleep {SLEEP_TIME}\n")

    # 2. AlienFX & Platform Profile
    if alienfx_cli_path:
        w("# 2. AlienFX & Platform Profile")
        w(f"{alienfx_cli_path} setpowerprofile {alienfx_profile} 2>/dev/null")
        w(f"sleep {PLATFORM_WAIT}\n")
    else:
        w("# 2. AlienFX & Platform Profile — SKIPPED (alienfx_cli not found at generation time)")
        w("")

    # 3. RyzenAdj power limits
    w("# 3. RyzenAdj Power Limits (after platform profile settled)")
    for arg in (f"--stapm-limit={stapm}", f"--fast-limit={fast}", f"--slow-limit={slow}",
                f"--slow-time={slow_time}", f"--stapm-time={stapm_time}",
                f"--tctl-temp={tctl}", f"--vrm-current={vrm}"):
        w(f"{ryzenadj_path} {arg}")
        w(f"sleep {SLEEP_TIME}")
    w("")

    # 4. Curve Optimizer
    if coall is not None:
        w("# 4. Global Curve Optimizer")
        w(f"{ryzenadj_path} --set-coall={coall}")
        w(f"sleep {SLEEP_TIME}\n")
    elif cores:
        w("# 4. Per-Core Curve Optimizer")
        for core in cores:
            ccd = core.get("ccd", 0)
            ccx = core.get("ccx", 0)
            core_num = core.get("core", 0)
            coper = core.get("coper", 0)
            encoded = (((ccd << 4 | ccx & 0xF) << 4 | core_num & 0xF) << 20) | (coper & 0xFFFF)
            w(f"{ryzenadj_path} --set-coper={encoded}")
            w(f"sleep {SLEEP_TIME}")
        w("")

    # 5. FAN BOOST
    if fb1 > 0 or fb2 > 0 or fb3 > 0 or fb4 > 0:
        w("# 5. Fan Boost Settings (0-100%)")
        w(f"FB1={fb1}")
        w(f"FB2={fb2}")
        w(f"FB3={fb3}")
        w(f"FB4={fb4}\n")
        w("# Set fan boost values")
        w("echo $FB1 > $HWMON9/fan1_boost 2>/dev/null")
        w("echo $FB2 > $HWMON9/fan2_boost 2>/dev/null")
        w("echo $FB3 > $HWMON9/fan3_boost 2>/dev/null")
        w("echo $FB4 > $HWMON9/fan4_boost 2>/dev/null")
        w(f"sleep {SLEEP_TIME}\n")
        w("# Verify and retry using defined variables")
        w("for i in 1 2 3 4; do")
        w('    EXPECTED=$(eval echo \\$FB$i)')
        w('    ACTUAL=$(cat $HWMON9/fan${i}_boost 2>/dev/null)')
        w('    if [ "$ACTUAL" != "$EXPECTED" ]; then')
        w('        echo "Retrying fan${i}_boost: expected $EXPECTED, actual $ACTUAL"')
        w("        echo $EXPECTED > $HWMON9/fan${i}_boost 2>/dev/null")
        w(f"        sleep {SLEEP_TIME}")
        w("    fi")
        w("done\n")

    # 6. EXTRA SETTINGS (THP + Gaming Optimizations)
    extra = cfg.get("extra", {})
    if extra:
        w("# 6. Extra Settings (THP + Gaming Optimizations)")
        thp = extra.get("thp", {})
        for thp_key, sysfs_path in (
            ("enabled", "/sys/kernel/mm/transparent_hugepage/enabled"),
            ("defrag", "/sys/kernel/mm/transparent_hugepage/defrag"),
            ("shmem", "/sys/kernel/mm/transparent_hugepage/shmem_enabled"),
        ):
            if thp_key in thp:
                val = thp[thp_key]
                if _is_shell_safe(val):
                    w(f"echo '{val}' > {sysfs_path} 2>/dev/null")
                else:
                    w(f"# SKIPPED unsafe THP value for {thp_key}: {val!r}")
        w(f"sleep {SLEEP_TIME}")

        # Bug fix: extra.gaming anahtarları "lru_gen" gibi İSİM'lerdir,
        # gerçek sysfs path'i değil — key.startswith("vm."/"kernel."/"/")
        # tahmini "lru_gen"/"sched_*" için hiçbir zaman eşleşmiyordu ve bu
        # satırlar script'e hiç yazılmıyordu. Artık GAMING_TUNABLES
        # şemasından gerçek path/type isimle aranıyor.
        gaming = extra.get("gaming", {})
        for key, value in gaming.items():
            if not value:
                continue
            schema_entry = GAMING_TUNABLES.get(key)
            if not schema_entry:
                w(f"# SKIPPED unknown gaming setting (no schema): {key!r}")
                continue
            path = schema_entry["path"]
            kind = schema_entry["type"]
            if not (_is_shell_safe(path) and _is_shell_safe(str(value))):
                w(f"# SKIPPED unsafe gaming setting: {key!r}={value!r}")
                continue
            if kind == "sysctl":
                w(f"sysctl -w {path}={value} 2>/dev/null")
            else:
                w(f"echo '{value}' > {path} 2>/dev/null")
        w(f"sleep {SLEEP_TIME}\n")

    w('echo "Profile {} applied successfully."'.format(name))
    return "\n".join(lines) + "\n"


def write_shell_script(name: str, cfg: dict) -> Path:
    """Script içeriğini YEREL (root gerektirmeyen) önizleme dizinine
    yazar. Sistem geneli kalıcı kopya için bkz. ryzenadj_gui.py::_script()
    (root_helper'ın write_activation_script op'unu kullanır)."""
    LOCAL_SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LOCAL_SCRIPTS_DIR / SCRIPT_NAMES[name]
    out_path.write_text(_build_shell_script_content(name, cfg), encoding="utf-8")
    out_path.chmod(0o755)
    return out_path

def apply_profile(name: str) -> bool:
    """Bir profili uygular.

    ÖNEMLİ MİMARİ DEĞİŞİKLİK: Eskiden bu fonksiyon bir bash script'i
    (write_shell_script) diske yazıp `sudo /usr/bin/bash script` ile
    çalıştırıyordu. Bu, uygulamanın geri kalanının kullandığı
    pkexec/Polkit/root_helper.py mimarisini tamamen atlıyordu — GUI'den
    başlatıldığında (TTY olmadan) `sudo` genelde ya askıda kalır ya da
    parola önbelleği yoksa sessizce başarısız olur; ayrıca script'in
    yazılacağı dizin artık root'a ait olduğu için (bkz. SCRIPTS_DIR/FHS
    merkezileştirmesi) buraya kullanıcı olarak yazmak zaten mümkün
    değildi. Artık tüm adımlar tek bir pkexec çağrısıyla, root_helper'ın
    apply_power_profile op'u üzerinden native olarak uygulanıyor.
    """
    try:
        cfg = load_profile(name)
        validate_profile(cfg)
        log(f"[APPLY] Applying profile '{name}' via root_helper...")

        # Bug fix: tray.py'nin ProfileWatcher'ı, alienfx_cli'nin bu profili
        # uygularken ACPI platform_profile'da yol açtığı yan etkiyi "harici
        # bir değişiklik" sanıp aktif profil durumunu ezmesin diye, apply
        # başlamadan bir zaman damgası bırakıyoruz (bkz. ryzenadj_common.
        # mark_local_apply / recent_local_apply).
        if _common is not None:
            _common.mark_local_apply()

        ok, msg = _call_root_helper({
            "op": "apply_power_profile",
            "name": name,
            "cfg": cfg,
            "alienfx_profile": ALIENFX_PROFILES.get(name, name),
            # Bug fix: extra.gaming sözlüğü anahtar olarak "lru_gen",
            # "sched_min_base_slice" gibi İSİMLER kullanıyor (gerçek sysfs
            # path'leri değil). root_helper eskiden bu anahtarın "/" ile
            # başlayıp başlamadığına bakıp path/sysctl ayrımı yapmaya
            # çalışıyordu — "lru_gen" ne "vm."/"kernel." ile başlıyor ne de
            # "/" ile, bu yüzden HİÇBİR ZAMAN uygulanmıyordu (sessizce
            # atlanıyordu). Artık gerçek path/type bilgisini bu şemadan
            # (GAMING_TUNABLES) doğrudan gönderiyoruz.
            "gaming_schema": {k: {"path": v["path"], "type": v["type"]} for k, v in GAMING_TUNABLES.items()},
        }, timeout=30)

        if ok:
            for line in (msg or "").splitlines():
                log(f"[APPLY] {line}")
            log(f"Profile '{name}' applied successfully.")
            return True
        else:
            log(f"Profile '{name}' failed: {msg}")
            return False
    except Exception as e:
        log(f"Profile '{name}' error: {e}")
        return False

def get_current_profile() -> str:
    """Q3: Bu okuma zinciri (platform_profile → state dosyası → 'balanced')
    gui/tray/wrapper'da üç kez kopyalanmıştı; artık ryzenadj_common'daki
    tek ortak fonksiyona devrediliyor."""
    if _common is not None:
        return _common.read_active_profile(default="balanced")
    # ryzenadj_common bulunamazsa eski davranış:
    try:
        return Path("/sys/firmware/acpi/platform_profile").read_text().strip()
    except (FileNotFoundError, OSError):
        pass
    try:
        return Path("/tmp/ryzenadj_active_profile.state").read_text().strip()
    except (FileNotFoundError, OSError):
        pass
    return "balanced"

def set_active_profile_state(name: str) -> bool:
    """Durum dosyasını yazar. Dönüş: tray bildirimi ALDIYSA True (yani
    çağıran kendi fallback bildirim popup'ını GÖSTERMEMELİ); tray
    çalışmıyorsa False (çağıran isterse kendi bildirimini gösterebilir)."""
    if _common is not None:
        return bool(_common.write_active_profile(name))
    try:
        Path("/tmp/ryzenadj_active_profile.state").write_text(name.strip())
    except Exception as e:
        log(f"Failed to write state: {e}")
    return False

# ─── FAN BOOST ──────────────────────────────────────────────────────────

def get_fan_boost_value(fan_number: int) -> int:
    # W4: cached ALIENWARE_HWMON; glob taraması yok
    hwmon = ALIENWARE_HWMON
    if not hwmon:
        return 0
    val = _read_sysfs(hwmon / f"fan{fan_number}_boost")
    if val is not None:
        try: return int(val)
        except Exception: pass
    return 0

def get_all_fan_boost_values() -> dict:
    # W4: tek hwmon lookup; get_fan_boost_value 4 kez çağırılmaz
    hwmon = ALIENWARE_HWMON
    result = {"CPU": 0, "GPU": 0, "Mid": 0, "Side": 0}
    if not hwmon:
        return result
    for i, label in enumerate(["CPU", "GPU", "Mid", "Side"], start=1):
        val = _read_sysfs(hwmon / f"fan{i}_boost")
        if val is not None:
            try: result[label] = int(val)
            except Exception: pass
    return result

def set_fan_boost_manually(fan: int, value: int) -> bool:
    # W4: cached ALIENWARE_HWMON
    hwmon = ALIENWARE_HWMON
    if not hwmon:
        log("ERROR: alienware_wmi hwmon not found")
        return False
    try:
        value = max(0, min(100, value))
        if _write_fan_boost(hwmon, fan, value):
            log(f"Manual set fan{fan}_boost = {value}")
            return True
    except Exception as e:
        log(f"Manual set failed: {e}")
    return False

# ─── TELEMETRY ──────────────────────────────────────────────────────────
# W5: find_hwmon_by_name iki kez tanımlıydı (L144 + L478); ikincisi kaldırıldı.
#
# W1-W4: Telemetri fonksiyonları (get_cpu_temperature_live vb.) her çağrıda
# hwmon dizinini glob ile tarıyordu → her 2 sn'de 4+ OS dizin taraması.
# Çözüm: çözümlenen yollar modül-global olarak cache'lenir, sysfs dosyaları
# kalıcı açık tutulur (seek(0) deseni; TelemetryWorker ile aynı yaklaşım).

# Çözümlenen yollar (modül yüklendiğinde bir kez doldurulur)
_CPU_TEMP_PATH: Path = None

def _resolve_cpu_temp_path() -> Path:
    hwmon = find_hwmon_by_name("k10temp")
    if hwmon:
        p = hwmon / "temp1_input"
        if p.exists():
            return p
    return None

_CPU_TEMP_PATH = _resolve_cpu_temp_path()
if _CPU_TEMP_PATH:
    log(f"Found CPU temp at: {_CPU_TEMP_PATH}")
else:
    log("WARNING: k10temp hwmon not found!")

# Kalıcı sysfs dosya handle'ları
# key: Path nesnesinin string hali, value: açık file nesnesi
_SYSFS_HANDLES: dict = {}

def _read_sysfs(path: Path, default=None):
    """Sysfs dosyasını kalıcı handle ile oku (seek(0) deseni).
    W1-W4: hwmon yolu zaten çözümlü, açma/kapatma overhead'i yok.
    """
    if path is None:
        return default
    key = str(path)
    f = _SYSFS_HANDLES.get(key)
    try:
        if f is None:
            f = open(path, "r")
            _SYSFS_HANDLES[key] = f
        f.seek(0)
        return f.read().strip()
    except Exception:
        # Handle bozulduysa kapat ve bir sonraki çağrıda yeniden aç
        if f:
            try: f.close()
            except Exception: pass
        _SYSFS_HANDLES.pop(key, None)
        return default

def get_cpu_temperature_live() -> float:
    # W1: her çağrıda hwmon glob taraması yerine module-level cache + persistent handle
    val = _read_sysfs(_CPU_TEMP_PATH)
    if val is not None:
        try:
            return float(val) / 1000.0
        except Exception:
            pass
    return 0.0

def get_gpu_temperature_live() -> float:
    """GPU sıcaklığını module-level cache + persistent handle ile okur.
    W2: Her çağrıda find_gpu_temp_path() → find_alienware_wmi_hwmon() glob
    taraması yerine modül başlangıcında çözümlenen GPU_TEMP_PATH kullanılır.
    """
    try:
        # 1. Öncelikli: modül başlangıcında çözümlenen path (persistent handle)
        if GPU_TEMP_PATH is not None:
            val = _read_sysfs(GPU_TEMP_PATH)
            if val is not None:
                try:
                    v = float(val) / 1000.0
                    if 0 < v < 120:
                        return v
                except Exception:
                    pass

        # 2. Fallback: module-level ALIENWARE_HWMON (glob taraması yok)
        if ALIENWARE_HWMON:
            temp_file = ALIENWARE_HWMON / "temp2_input"
            val = _read_sysfs(temp_file)
            if val is not None:
                try:
                    v = float(val) / 1000.0
                    if 0 < v < 120:
                        return v
                except Exception:
                    pass

        # 3. Son fallback: dell_smm (nadiren kullanılır; yeniden tarama kabul edilebilir)
        hwmon = find_hwmon_by_name("dell_smm")
        if hwmon:
            temp_file = hwmon / "temp5_input"
            val = _read_sysfs(temp_file)
            if val is not None:
                try:
                    v = float(val) / 1000.0
                    if 0 < v < 120:
                        return v
                except Exception:
                    pass
    except Exception:
        pass
    return 0.0

def get_fan_speeds_live() -> dict:
    fans = {"CPU": 0, "GPU": 0, "Mid": 0, "Side": 0}
    mapping = {"fan1_input": "CPU", "fan2_input": "GPU",
               "fan3_input": "Mid", "fan4_input": "Side"}

    # W3: her çağrıda glob taraması yerine module-level ALIENWARE_HWMON
    hwmon = ALIENWARE_HWMON
    if hwmon:
        for filename, label in mapping.items():
            val = _read_sysfs(hwmon / filename)
            if val is not None:
                try: fans[label] = int(val)
                except Exception: pass
        if any(fans.values()):
            return fans

    # Fallback: dell_smm (yeniden tarama kabul edilebilir; nadiren)
    hwmon = find_hwmon_by_name("dell_smm")
    if hwmon:
        labels = ["CPU", "GPU", "Mid", "Side"]
        for i in range(1, 5):
            val = _read_sysfs(hwmon / f"fan{i}_input")
            if val is not None:
                try: fans[labels[i-1]] = int(val)
                except Exception: pass
        if any(fans.values()):
            return fans

    return fans

def start_tray_background():
    try:
        pgrep_path = find_tool("pgrep")
        if not pgrep_path:
            log("WARNING: pgrep not found, cannot check for an already-running tray; skipping duplicate-launch guard")
            res = None
        else:
            res = subprocess.run([pgrep_path, "-f", "ryzenadj_tray.py"], stdout=subprocess.PIPE)
        if res is None or not res.stdout:
            log_path = Path("/tmp/ryzenadj_tray.log")
            with open(log_path, "a", encoding="utf-8") as logf:
                subprocess.Popen(
                    [sys.executable, str(SCRIPT_DIR / "ryzenadj_tray.py")],
                    cwd=str(SCRIPT_DIR),
                    stdout=logf,
                    stderr=logf,
                    start_new_session=True,
                )
    except Exception:
        pass

def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: python ryzenadj_wrapper.py apply <profile>")
        print("       python ryzenadj_wrapper.py fan <1-4> <0-100>")
        return
    if args[0].lower() == "apply" and len(args) >= 2:
        apply_profile(args[1])
    elif args[0].lower() == "fan" and len(args) >= 3:
        set_fan_boost_manually(int(args[1]), int(args[2]))

if __name__ == "__main__":
    main()
