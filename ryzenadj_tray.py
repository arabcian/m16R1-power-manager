#!/usr/bin/env python3
"""
RyzenAdj System Tray - KDE Plasma için
"""
import os
import sys
import subprocess
import fcntl
import time
import select
import socket
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from tool_paths import find_tool

LOG_FILE         = Path("/tmp/ryzenadj_tray.log")
LOCK_PATH        = Path("/tmp/ryzenadj_tray.lock")
STATE_FILE       = Path("/tmp/ryzenadj_active_profile.state")
SYS_PROFILE_PATH = Path("/sys/firmware/acpi/platform_profile")

# Watcher retry parametreleri
_WATCHER_RETRY_DELAY = 3.0   # saniye; exception sonrası bekleme
_WATCHER_POLL_MS     = 500   # poll timeout (ms)

try:
    import ryzenadj_common as _common
except ImportError:
    _common = None

def log(msg: str) -> None:
    """Q1: Artık ortak, dönen (rotating) log'a yazıyor."""
    if _common is not None:
        _common.log(msg)
        return
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

log("=" * 60)
log("Tray starting...")

try:
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
    from PySide6.QtGui import QIcon, QAction
    from PySide6.QtCore import QThread, Signal, QTimer
except ImportError:
    log("FATAL: PySide6 not installed")
    sys.exit(1)

try:
    import ryzenadj_wrapper as wrapper
except ImportError:
    log("FATAL: ryzenadj_wrapper not found")
    sys.exit(1)

# Single instance lock
try:
    lock = open(LOCK_PATH, "w")
    fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    log("Another instance running. Exiting.")
    sys.exit(0)

# ─── YARDIMCI FONKSİYONLAR ──────────────────────────────────────────────

def get_current_profile() -> str:
    """Bug fix: Bu fonksiyon eskiden önce SYS_PROFILE_PATH (ACPI
    platform_profile sysfs) sonra STATE_FILE'a bakıyordu. Sorun: "gmode"
    ve "custom" gibi uygulamaya özgü profil isimlerinin ACPI
    platform_profile sözlüğünde (low-power/balanced/performance) hiçbir
    karşılığı yok — alienfx_cli setpowerprofile bunları "performance"
    gibi bir ACPI değerine haritalıyor (bkz. wrapper.ALIENFX_PROFILES).
    Yani GUI'den "gmode" uygulandığında tray, ACPI sysfs'i okuyup
    "performance" gösteriyordu — kullanıcının seçtiği profil değil.
    Artık GUI'nin yazdığı STATE_FILE (uygulamanın kendi, yetkili durumu)
    ACPI sysfs'ten ÖNCE kontrol ediliyor; bu, ryzenadj_common.
    read_active_profile()'ın zaten yaptığı sırayla aynı."""
    if _common is not None:
        return _common.read_active_profile(default="balanced")
    try:
        return STATE_FILE.read_text().strip()
    except (FileNotFoundError, OSError):
        pass
    try:
        return SYS_PROFILE_PATH.read_text().strip()
    except (FileNotFoundError, OSError):
        pass
    return "balanced"

def set_current_profile(name: str) -> None:
    if _common is not None:
        _common.write_active_profile(name, path=str(STATE_FILE))
        return
    try:
        STATE_FILE.write_text(name.strip())
    except Exception:
        pass

def is_gui_running() -> bool:
    """/proc tarama ile GUI kontrolü — fork yok, ~0.1 ms."""
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    if b"ryzenadj_gui.py" in f.read():
                        return True
            except (FileNotFoundError, PermissionError, OSError):
                pass
    except Exception:
        pass
    return False

def get_gui_pids() -> list:
    """/proc üzerinden GUI süreç ID'lerini döner."""
    pids = []
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    if b"ryzenadj_gui.py" in f.read():
                        pids.append(int(pid))
            except (FileNotFoundError, PermissionError, OSError):
                pass
    except Exception:
        pass
    return pids

def launch_gui():
    if is_gui_running():
        return
    try:
        subprocess.Popen(
            [sys.executable, str(SCRIPT_DIR / "ryzenadj_gui.py")],
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log("GUI launched")
    except Exception as e:
        log(f"Failed to launch GUI: {e}")

def send_notification(title: str, message: str, icon: str = "preferences-system-power"):
    """KDE/freedesktop bildirimi gönder.
    notify-send başarısız olursa QSystemTrayIcon.showMessage() fallback'i
    tray nesnesine bırakılır (send_notification_with_fallback kullanılır).
    """
    try:
        subprocess.run(
            ["notify-send", "--app-name=RyzenAdj", "--icon", icon, title, message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except Exception:
        pass

# ─── PROFILE WATCHER ────────────────────────────────────────────────────

class ProfileWatcher(QThread):
    """platform_profile sysfs dosyasını izler ve değişince sinyal verir.

    Önceki sürümde iki kritik bug vardı:
    1. POLLERR ve POLLPRI ayırt edilmiyordu; POLLERR sonrası fd bozulunca
       thread sessizce ölüyordu (bir daha restart edilmiyordu).
    2. Herhangi bir exception → thread çıkıyor, bildirimler tamamen duruyordu.

    Düzeltmeler:
    - poll() sonucunda event mask incelenir; POLLERR gelirse fd kapatılıp
      yeniden açılır (recovery).
    - Tüm run() while döngüsü retry mekanizmasıyla sarılır: exception sonrası
      _WATCHER_RETRY_DELAY saniye bekleyip sysfs dosyası yeniden açılır.
    - İnterruption her döngüde kontrol edilir, gecikme olmaz.
    """
    changed = Signal(str)

    def run(self):
        if not SYS_PROFILE_PATH.exists():
            log("Watcher: platform_profile not found, exiting.")
            return

        while not self.isInterruptionRequested():
            try:
                self._watch_loop()
            except Exception as e:
                log(f"Watcher error (will retry in {_WATCHER_RETRY_DELAY}s): {e}")
                # Kısa aralıklarla interruptionu kontrol ederek bekle
                deadline = time.monotonic() + _WATCHER_RETRY_DELAY
                while time.monotonic() < deadline:
                    if self.isInterruptionRequested():
                        return
                    time.sleep(0.2)

    def _watch_loop(self):
        """Tek izleme oturumu. Exception → üst katman retry yapar."""
        with open(SYS_PROFILE_PATH, "r") as f:
            poller = select.poll()
            # Sadece POLLPRI kaydediyoruz; POLLERR kernel tarafından revents'e
            # otomatik eklenir ama biz onu events maskesine dahil etmiyoruz
            # (bazı kernel sürümlerinde POLLERR events'e dahil edilince fd
            # hatalı state'e girip sonraki POLLPRI'leri maskeleyebiliyor).
            poller.register(f, select.POLLPRI)
            last = f.read().strip()
            log(f"Watcher: monitoring started, current='{last}'")

            while not self.isInterruptionRequested():
                events = poller.poll(_WATCHER_POLL_MS)

                if not events:
                    # Timeout → interruptionu kontrol et, devam et
                    continue

                fd, mask = events[0]

                if mask & select.POLLERR:
                    # fd hata durumunda — dosyayı kapatıp yeniden aç
                    log("Watcher: POLLERR on platform_profile fd, reopening...")
                    # with bloğundan çıkmak için exception raise et
                    raise IOError("POLLERR — fd recovered by reopening")

                if mask & select.POLLPRI:
                    f.seek(0)
                    curr = f.read().strip()
                    if curr and curr != last:
                        last = curr
                        log(f"Watcher: profile changed → '{curr}'")
                        self.changed.emit(curr)


class ProfileNotifyListener(QThread):
    """Asıl senkronizasyon düzeltmesi: GUI (ya da tray'in kendisi) bir
    profili uyguladığında, ryzenadj_common.write_active_profile() bu
    dinleyiciye Unix domain socket üzerinden ANINDA bir mesaj gönderir
    (bkz. ryzenadj_common.notify_profile_changed). Bu, ACPI
    platform_profile yan-etkisini izlemekten çok daha güvenilirdir —
    çünkü alienfx_cli'nin ACPI'yi değiştirmesi apply_profile() içinde en
    başta olur, ama GUI'nin gerçek profil ismini yazması işlem bittikten
    SONRA olur; ACPI-izleyici bu ikisi arasındaki yarışı kaybedip eski
    ismi gösterip kalabiliyordu (bkz. ryzenadj_common.py'deki uzun not).

    CPU kullanımı: `accept()` bloklar (1 sn timeout'la, yalnızca
    interruption kontrolü için) — mesaj gelene kadar sıfıra yakın CPU,
    periyodik dosya okuma/polling YOK.
    """
    changed = Signal(str)

    def run(self):
        if _common is None:
            log("NotifyListener: ryzenadj_common yok, devre dışı.")
            return

        sock_path = _common.NOTIFY_SOCKET_PATH
        try:
            if os.path.exists(sock_path):
                os.unlink(sock_path)
        except OSError:
            pass

        srv = None
        try:
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(sock_path)
            os.chmod(sock_path, 0o600)
            srv.listen(4)
            srv.settimeout(1.0)  # yalnızca isInterruptionRequested kontrolü için
            log(f"NotifyListener: listening on {sock_path}")

            while not self.isInterruptionRequested():
                try:
                    conn, _addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    data = conn.recv(256)
                    name = data.decode("utf-8", "replace").strip()
                    if name:
                        self.changed.emit(name)
                except OSError:
                    pass
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass
        except Exception as e:
            log(f"NotifyListener error: {e}")
        finally:
            if srv is not None:
                try:
                    srv.close()
                except OSError:
                    pass
            try:
                os.unlink(sock_path)
            except OSError:
                pass


# ─── TRAY ───────────────────────────────────────────────────────────────

class RyzenTray:
    def __init__(self, app):
        self.app = app

        self.tray = QSystemTrayIcon(app)
        icon_path = SCRIPT_DIR / "Alien.png"
        if icon_path.exists():
            self.tray.setIcon(QIcon(str(icon_path)))
        else:
            self.tray.setIcon(QIcon.fromTheme("preferences-system-power"))

        self.tray.setToolTip("Alienware Power Manager")
        self.menu = QMenu()
        self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activate)

        self._start_watcher()
        self._start_notify_listener()

        # Watchdog: watcher thread'ler ölürse otomatik yeniden başlat
        self._watchdog = QTimer()
        self._watchdog.setInterval(10_000)   # 10 sn
        self._watchdog.timeout.connect(self._check_watcher)
        self._watchdog.start()

        self.tray.show()
        self._refresh_menu()
        log("Tray initialized")

        # Boot'ta varsayılan RGB profilini yükle (geciktirilmiş — USB HID hazır olsun)
        QTimer.singleShot(5000, self._apply_default_rgb)

    # ─── RGB PROFILE AUTO-APPLY ─────────────────────────────────────────

    _RGB_PROFILES_PATH  = Path.home() / ".config/ryzenadj_gui/rgb_profiles.json"
    _RGB_PERKEY_PATH    = Path.home() / ".config/ryzenadj_gui/rgb_perkey.json"
    _ALIENFX_MAPPINGS   = Path.home() / ".local/share/alienfx/mappings.json"

    def _apply_default_rgb(self):
        """Boot sırasında aktif RGB kaynağını uygular.

        Öncelik sırası:
          1. rgb_perkey.json  →  active == True  →  per-key setone komutları
          2. rgb_profiles.json  →  default profil  →  kaydedilmiş komut listesi

        İki kaynak mutually exclusive'dir: GUI tarafında biri aktifleştirilince
        diğeri devre dışı bırakılır.
        """
        import json, re as _re
        try:
            cli = find_tool("alienfx_cli")
            if not cli:
                log("RGB auto-apply: alienfx_cli not found")
                return

            # ── 1. Per-key layout öncelikli ───────────────────────────────
            if self._RGB_PERKEY_PATH.exists():
                pk = json.loads(self._RGB_PERKEY_PATH.read_text(encoding="utf-8"))
                if pk.get("active"):
                    log("RGB auto-apply: per-key layout is active — loading")
                    self._apply_perkey_rgb(pk, cli)
                    return
                else:
                    log("RGB auto-apply: per-key layout exists but active=False, skipping")

            # ── 2. Manual profile fallback ────────────────────────────────
            if not self._RGB_PROFILES_PATH.exists():
                log("RGB auto-apply: no profiles file found")
                return
            data = json.loads(self._RGB_PROFILES_PATH.read_text(encoding="utf-8"))
            default_name = data.get("default", "")
            profiles = data.get("profiles", {})
            if not default_name or default_name not in profiles:
                log("RGB auto-apply: no default profile set")
                return
            commands = profiles[default_name].get("commands", [])
            if not commands:
                log(f"RGB auto-apply: profile '{default_name}' has no commands")
                return
            log(f"RGB auto-apply: applying manual profile '{default_name}' ({len(commands)} commands)")
            self._rgb_boot_queue = list(commands)
            self._rgb_boot_cli = cli
            self._rgb_boot_run_next()

        except Exception as e:
            log(f"RGB auto-apply error: {e}")

    def _apply_perkey_rgb(self, pk_data: dict, cli: str):
        """Per-key layout'u setone komutlarına çevirip kuyruğa alır.

        Adımlar:
          1. mappings.json → key_name → [lightid, ...] sözlüğü
          2. alienfx_cli status → VID/PID ile device index eşleştir
          3. Her key → her lightid için  setone <dev> <lid> <r> <g> <b>
        """
        import json, re as _re
        keys = pk_data.get("keys", {})
        if not keys:
            log("RGB per-key auto-apply: no keys in layout")
            return

        # ── mappings.json → name_to_lids ─────────────────────────────────
        name_to_lids: dict[str, list[int]] = {}
        try:
            if self._ALIENFX_MAPPINGS.exists():
                mdata = json.loads(self._ALIENFX_MAPPINGS.read_text(encoding="utf-8"))
                devices = mdata.get("devices", [])
                if devices:
                    # En fazla light'a sahip cihaz = klavye (GUI ile aynı heuristik)
                    best = max(devices, key=lambda d: len(d.get("lights", [])))
                    for entry in best.get("lights", []):
                        lid  = entry.get("lightid")
                        name = entry.get("name", "")
                        if lid is not None and name:
                            name_to_lids.setdefault(name, []).append(int(lid))
                    log(f"RGB per-key: {len(name_to_lids)} light names from mappings.json")
            else:
                log("RGB per-key: mappings.json not found — lightids unresolvable")
        except Exception as e:
            log(f"RGB per-key: mappings.json read error: {e}")

        # ── VID/PID → device index ────────────────────────────────────────
        dev_vidpid  = pk_data.get("dev_vidpid", [0, 0])
        target_vid  = int(dev_vidpid[0]) if len(dev_vidpid) > 0 else 0
        target_pid  = int(dev_vidpid[1]) if len(dev_vidpid) > 1 else 0
        dev_idx     = 0  # fallback
        dev_matched = False  # O5: eşleşme bulunup bulunmadığını ayrı takip et
        try:
            result = subprocess.run(
                [cli, "status"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=4,
            )
            for m in _re.finditer(
                r'Device\s+#(\d+)\b.*?VID#(0x[\da-fA-F]+|\d+).*?PID#(0x[\da-fA-F]+|\d+)',
                result.stdout or "", _re.IGNORECASE,
            ):
                raw_vid = m.group(2)
                raw_pid = m.group(3)
                vid = int(raw_vid, 16) if raw_vid.lower().startswith("0x") else int(raw_vid)
                pid = int(raw_pid, 16) if raw_pid.lower().startswith("0x") else int(raw_pid)
                if vid == target_vid and pid == target_pid:
                    dev_idx = int(m.group(1))
                    dev_matched = True
                    log(f"RGB per-key: keyboard matched → device #{dev_idx}")
                    break
            else:
                log(f"WARNING: RGB per-key: VID/PID match failed (target {target_vid:#x}:{target_pid:#x}); "
                    f"refusing to guess a device to avoid writing RGB to the wrong hardware.")
        except Exception as e:
            log(f"WARNING: RGB per-key: device match error: {e}; refusing to guess a device.")

        # O5 düzeltmesi: Eşleşme bulunamazsa (regex hiç dönmedi ya da
        # subprocess hata verdi) artık dev_idx=0 varsayımıyla devam
        # edip yanlış cihaza RGB yazmıyoruz — komutları hiç uygulamadan
        # erken çıkıyoruz.
        if not dev_matched:
            log("RGB per-key auto-apply: aborted, no matching device found")
            return

        # ── setone komut listesini oluştur ────────────────────────────────
        commands: list[list[str]] = []
        dev_s = str(dev_idx)
        for key_name, rgb in keys.items():
            if not isinstance(rgb, list) or len(rgb) != 3:
                continue
            r, g, b = str(int(rgb[0])), str(int(rgb[1])), str(int(rgb[2]))
            for lid in name_to_lids.get(key_name, []):
                commands.append(["setone", dev_s, str(lid), r, g, b])

        if not commands:
            log("RGB per-key auto-apply: no resolvable commands (mappings.json missing?)")
            return

        log(f"RGB per-key auto-apply: {len(keys)} keys → {len(commands)} setone commands")
        self._rgb_boot_queue = commands
        self._rgb_boot_cli   = cli
        self._rgb_boot_run_next()

    def _rgb_boot_run_next(self):
        """Boot RGB kuyruğundan sıradaki komutu çalıştırır."""
        if not hasattr(self, '_rgb_boot_queue') or not self._rgb_boot_queue:
            log("RGB auto-apply: done")
            return
        args = self._rgb_boot_queue.pop(0)
        try:
            result = subprocess.run(
                [self._rgb_boot_cli] + [str(a) for a in args],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5
            )
            if result.returncode != 0:
                log(f"RGB auto-apply cmd exit {result.returncode}: {args}")
        except Exception as e:
            log(f"RGB auto-apply cmd error: {e}")
        # Sonraki komutu 100ms sonra çalıştır (USB HID settle)
        QTimer.singleShot(100, self._rgb_boot_run_next)

    # ─── WATCHER YÖNETİMİ ───────────────────────────────────────────────

    def _start_watcher(self):
        self.watcher = ProfileWatcher()
        self.watcher.changed.connect(self._on_kernel_change)
        self.watcher.start()
        log("Watcher started")

    def _start_notify_listener(self):
        self.notify_listener = ProfileNotifyListener()
        self.notify_listener.changed.connect(self._on_profile_pushed)
        self.notify_listener.start()
        log("NotifyListener started")

    def _check_watcher(self):
        """Watchdog callback: watcher/listener thread'leri ölmüşse yeniden başlat."""
        if not self.watcher.isRunning():
            log("Watchdog: watcher thread died, restarting...")
            self.watcher.deleteLater()
            self._start_watcher()
        if not self.notify_listener.isRunning():
            log("Watchdog: notify listener thread died, restarting...")
            self.notify_listener.deleteLater()
            self._start_notify_listener()

    # ─── MENÜ ────────────────────────────────────────────────────────────

    def _build_menu(self):
        self.header = QAction("🛸 Alienware", self.menu)
        self.menu.addAction(self.header)
        self.menu.addSeparator()

        self.profile_actions = {}
        for p in wrapper.PROFILES:
            act = QAction(p.replace("_", " ").title(), self.menu)
            act.setCheckable(True)
            act.triggered.connect(lambda checked=False, name=p: self._apply_profile(name))
            self.menu.addAction(act)
            self.profile_actions[p] = act

        self.menu.addSeparator()
        # Statik buton: GUI'nin çalışıp çalışmadığını göstermez.
        # Çift launch launch_gui() içinde is_gui_running() ile engellenir.
        self.launch_act = QAction("🚀 Open GUI", self.menu)
        self.launch_act.triggered.connect(lambda: launch_gui())
        self.menu.addAction(self.launch_act)

        self.menu.addSeparator()
        self.exit_act = QAction("❌ Exit", self.menu)
        self.exit_act.triggered.connect(self._quit)
        self.menu.addAction(self.exit_act)

    # Menü cache: sadece profil değişince güncellenir
    _last_profile = None

    def _refresh_menu(self):
        try:
            curr = get_current_profile().lower()
            if curr == self._last_profile:
                return
            self._last_profile = curr

            for p, act in self.profile_actions.items():
                act.setChecked(p.lower() == curr)

            display = curr.replace("_", " ").title() if curr else "--"
            self.header.setText(f"🛸 Active: {display}")
            self.tray.setToolTip(f"Alienware Power — {display}")
        except Exception as e:
            log(f"Refresh error: {e}")

    # ─── AKSIYONLAR ─────────────────────────────────────────────────────

    def _apply_profile(self, name: str):
        log(f"Applying {name} from tray...")
        # Boot-defaults: bkz. ryzenadj_gui.py::_apply_profile ile aynı mantık.
        if name in wrapper.TUNING_PROFILES:
            wrapper.ensure_boot_defaults_captured()

        success = wrapper.apply_profile(name)
        if success:
            set_current_profile(name)
            if name in wrapper.SIMPLE_PROFILES:
                wrapper.restore_boot_defaults()
            self._last_profile = None  # cache'i zorla geçersiz kıl
            self._refresh_menu()
            log(f"Applied {name}")
        else:
            log(f"Failed to apply {name}")
            self._notify("❌ Apply Failed", f"Could not apply {name}")

    def _on_kernel_change(self, new_profile: str):
        # Bug fix: alienfx_cli'nin bir profili uygularken ACPI
        # platform_profile'da yaptığı değişiklik de bu watcher'ı tetikler.
        # Örn. "gmode" uygulandığında ACPI değeri "performance" olur — bu
        # RAW ACPI değeri bizim uygulama-seviyesi profil ismimiz DEĞİL.
        # Öncesinde burası STATE_FILE'ı bu ham değerle eziyordu, bu yüzden
        # tray GUI'den seçilen "gmode" yerine "performance" gösteriyordu.
        # Artık: yakın zamanda (son ~10sn) biz apply_profile() çağırdıysak
        # bu değişikliğin kendi yan etkimiz olduğunu varsayıp STATE_FILE'a
        # DOKUNMUYORUZ — asıl senkronizasyon zaten _on_profile_pushed
        # (aşağıda) üzerinden, GUI'nin doğrudan push ettiği isimle yapılıyor.
        if _common is not None and _common.recent_local_apply():
            log(f"Kernel change to '{new_profile}' ignored (our own apply side-effect)")
            return

        log(f"Kernel changed to: {new_profile}")
        set_current_profile(new_profile)
        self._last_profile = None  # cache'i zorla geçersiz kıl
        self._refresh_menu()
        display = new_profile.replace("_", " ").title()
        self._notify("🔄 Profile Changed", f"System switched to: {display}")

    def _on_profile_pushed(self, name: str):
        """Asıl senkronizasyon düzeltmesi: GUI (ya da başka bir tray
        örneği) write_active_profile() çağırdığı anda buraya doğrudan
        push edilir — ACPI yan etkisiyle yarışma yok, polling yok.
        `_last_profile` cache'i zorla sıfırlanır ki state dosyasında
        okunan değer önceki cache ile aynıymış gibi görünse bile menü
        (checkmark'lar dahil) her zaman yeniden hesaplansın; "bazen
        çarpı işareti hiç görünmüyor" bug'ı buradan kaynaklanıyordu."""
        log(f"Profile pushed from GUI/tray: '{name}'")
        self._last_profile = None
        self._refresh_menu()
        display = name.replace("_", " ").title()
        self._notify("🔄 Profile Changed", f"Switched to: {display}")

    def _on_activate(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            launch_gui()

    def _notify(self, title: str, message: str, icon: str = "preferences-system-power"):
        """notify-send dener; başarısız olursa tray baloncuğuna düşer."""
        try:
            result = subprocess.run(
                ["notify-send", "--app-name=RyzenAdj", "--icon", icon, title, message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            if result.returncode == 0:
                return
        except Exception:
            pass
        # Fallback: tray baloncuğu (her zaman çalışır)
        try:
            self.tray.showMessage(title, message, QSystemTrayIcon.Information, 4000)
        except Exception:
            pass

    def _quit(self):
        log("Quitting...")
        self._watchdog.stop()
        if hasattr(self, "watcher"):
            self.watcher.requestInterruption()
            self.watcher.wait(3000)
        if hasattr(self, "notify_listener"):
            self.notify_listener.requestInterruption()
            # accept() en fazla 1sn timeout ile bloklanıyor, kısa sürede döner
            self.notify_listener.wait(3000)

        # GUI'yi /proc üzerinden bul ve kapat
        for pid in get_gui_pids():
            try:
                os.kill(pid, 15)   # SIGTERM
            except OSError:
                pass

        self.tray.hide()
        self.app.quit()


# Küresel referans tanımlayarak çöp toplayıcının nesneyi silmesini engelliyoruz
tray_instance = None

def main():
    global tray_instance
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Nesneyi hem global değişkene hem de uygulama nesnesine bağlıyoruz (Çift Katmanlı Koruma)
    tray_instance = RyzenTray(app)
    app.tray_manager = tray_instance

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
