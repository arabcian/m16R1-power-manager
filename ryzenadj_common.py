#!/usr/bin/env python3
"""
ryzenadj_common.py — ryzenadj_gui.py / ryzenadj_wrapper.py / ryzenadj_tray.py
arasında paylaşılan ortak yardımcılar.

Q1 düzeltmesi: log() üç dosyada da neredeyse birebir kopyalanmıştı, hepsi
/tmp/ryzenadj_tray.log'a dönüşümsüz (sonsuza kadar büyüyen) şekilde yazıyordu.
Artık tek bir yerde, logging.handlers.RotatingFileHandler ile (5 MB × 3) ve
/tmp yerine $XDG_STATE_HOME (veya ~/.local/state/ryzenadj) altında.

Q3 düzeltmesi: "platform_profile → state dosyası → 'balanced' fallback"
okuma zinciri gui/tray/wrapper'da üç kez tekrarlanıyordu; artık
read_active_profile() burada tek yerde.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import time
from pathlib import Path

# ─── Log dosyası konumu ─────────────────────────────────────────────────
def _state_dir() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    d = base / "ryzenadj"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Son çare: eski /tmp konumuna düş (salt salt-okunur ev dizini vb.)
        return Path("/tmp")
    return d

_LOG_DIR = _state_dir()
LOG_FILE = _LOG_DIR / "ryzenadj.log"

# Geriye dönük uyumluluk: eski state dosyaları hâlâ /tmp altında aranıyor
# (tray'in dışarıdan başlattığı süreçlerle paylaşılan yol olduğu için).
ACTIVE_PROFILE_STATE_PATHS = [
    "/tmp/ryzenadj_active_profile.state",
    "/tmp/ryzenadj_current_profile.state",
]

_logger = logging.getLogger("ryzenadj")
if not _logger.handlers:
    _logger.setLevel(logging.INFO)
    try:
        handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
        _logger.addHandler(handler)
    except OSError:
        pass  # log yazılamıyorsa sessizce devam et (log kritik değil)


def log(msg: str) -> None:
    """wrapper.py/tray.py'deki eski log() ile aynı imza; artık rotating."""
    try:
        _logger.info(msg)
    except Exception:
        pass


def read_active_profile(default: str = "balanced") -> str:
    """Q3: gui/tray/wrapper'da üç kez kopyalanan
    'platform_profile → state dosyası → fallback' zincirinin tek hali.

    Sıra:
      1. /tmp'deki state dosyalarından biri (tray tarafından yazılır)
      2. /sys/firmware/acpi/platform_profile
      3. `default`
    """
    for sp in ACTIVE_PROFILE_STATE_PATHS:
        try:
            with open(sp, "r") as f:
                v = f.read().strip()
                if v:
                    return v
        except (FileNotFoundError, OSError):
            continue

    try:
        with open("/sys/firmware/acpi/platform_profile", "r") as f:
            v = f.read().strip()
            if v:
                return v
    except (FileNotFoundError, OSError):
        pass

    return default


def write_active_profile(name: str, path: str = "/tmp/ryzenadj_active_profile.state") -> bool:
    """Durum dosyasını yazar ve tray'e (varsa) push bildirimi gönderir.
    Dönüş: notify_profile_changed()'in sonucu (tray bildirimi ALDIYSA
    True) — çağıranlar tray kapalıyken kendi fallback bildirimlerini
    göstermek için kullanabilir."""
    try:
        with open(path, "w") as f:
            f.write(name.strip())
    except OSError:
        pass
    # Asıl senkronizasyon düzeltmesi: state dosyasını yazdıktan hemen sonra
    # tray'e (çalışıyorsa) anında haber ver. Aşağıdaki notify_profile_changed
    # bkz. açıklaması — polling YOK, tray'de bloklayan bir accept() ile
    # beklenip anında uyanıyor.
    return notify_profile_changed(name)


# ─── Push tabanlı çapraz-süreç bildirimi (Unix domain socket) ─────────────
# Bug: GUI bir profil uyguladığında tray bunu iki dolaylı yoldan öğrenmeye
# çalışıyordu: (a) ACPI platform_profile sysfs'indeki yan etkiyi izleyerek,
# (b) periyodik menü tazeleme. Sorun: alienfx_cli'nin ACPI'yi değiştirmesi
# apply_profile() içinde EN BAŞTA oluyor, ama GUI'nin gerçek profil ismini
# STATE_FILE'a yazması işlem TAMAMEN bittikten SONRA oluyor. Yani tray'in
# ACPI-izleyicisi olayı, doğru isim daha yazılmadan ÖNCE görüyor ve o anda
# state dosyasında hâlâ ESKİ isim var — tray eski/yanlış profili gösterip
# kalıyor, çünkü sonradan (isim güncellendiğinde) tray'i tekrar tetikleyen
# hiçbir şey yok (ACPI bir daha değişmiyor).
#
# Çözüm: STATE_FILE her güncellendiğinde (write_active_profile), tray
# çalışıyorsa ona doğrudan, anında bir mesaj gönderiyoruz. Tray tarafında
# bloklayan bir socket.accept() bekliyor (CPU'da 0 maliyet; sadece bir
# mesaj geldiğinde uyanıyor) — periyodik dosya kontrolü/polling YOK.
def _runtime_dir() -> Path:
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        d = Path(xdg) / "ryzenadj-gui"
    else:
        d = Path(f"/tmp/ryzenadj-gui-{os.getuid()}")
    try:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        pass
    return d


NOTIFY_SOCKET_PATH = str(_runtime_dir() / "notify.sock")


def notify_profile_changed(name: str) -> bool:
    """Tray'e (dinliyorsa) profilin değiştiğini anında bildirir.
    Tray çalışmıyorsa ya da soket yoksa sessizce yok sayılır — bu asla
    profil uygulama akışını kesintiye uğratmamalı.

    Dönüş değeri: mesaj gerçekten bir dinleyiciye (tray) teslim edildiyse
    True, tray çalışmıyorsa/ulaşılamadıysa False. Çağıranlar bunu, tray
    çalışmıyorken kendi bildirim popup'larına (fallback) düşmek için
    kullanabilir — böylece tray açıkken ÇİFT bildirim gösterilmez, ama
    tray kapalıyken kullanıcı yine de haberdar olur."""
    import socket
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        sock.connect(NOTIFY_SOCKET_PATH)
        sock.sendall(name.strip().encode("utf-8"))
        sock.close()
        return True
    except OSError:
        return False  # tray çalışmıyor / soket yok — sorun değil, state dosyası zaten güncel


# ─── "Yakın zamanda biz uyguladık" işaretleyicisi ──────────────────────────
# Bug: profil uygulandığında (örn. "gmode") alienfx_cli, ACPI
# platform_profile sysfs'ini kendi haritalanmış değerine ("performance")
# değiştirir. tray.py'deki ProfileWatcher bu ACPI değişimini "harici bir
# araç profili değiştirdi" sanıp STATE_FILE'ı "performance" ile eziyordu —
# GUI'nin az önce yazdığı doğru "gmode" değerini kaybettiriyordu. Bu da
# tray'de yanlış/eski profilin görünmesine yol açıyordu.
#
# Çözüm: apply_profile() başlarken bir zaman damgası bırakılır. Tray'in
# kernel-watcher'ı, ACPI değişimini bu damgadan kısa süre sonra görürse
# bunun kendi yan etkimiz olduğunu anlar ve STATE_FILE'ı EZMEZ.
_LOCAL_APPLY_MARKER = "/tmp/ryzenadj_last_apply.ts"


def mark_local_apply() -> None:
    """Bir profil uygulama işlemi BAŞLARKEN çağrılır (apply_profile())."""
    try:
        with open(_LOCAL_APPLY_MARKER, "w") as f:
            f.write(repr(time.monotonic()))
    except OSError:
        pass


def recent_local_apply(window_s: float = 10.0) -> bool:
    """Son `window_s` saniye içinde bizim tarafımızdan bir apply_profile()
    çağrısı yapılmış mı? (time.monotonic() Linux'ta CLOCK_MONOTONIC'e
    dayanır ve süreçler arasında karşılaştırılabilir.)"""
    try:
        with open(_LOCAL_APPLY_MARKER) as f:
            ts = float(f.read().strip())
        return (time.monotonic() - ts) < window_s
    except (OSError, ValueError):
        return False


class SysfsReader:
    """Q9: TelemetryWorker (_read_sys_file) ve wrapper (_read_sysfs)
    içindeki iki ayrı, ama neredeyse birebir aynı persistent-handle +
    seek(0) sysfs okuma deseninin ortak hali."""

    def __init__(self):
        self._handles: dict[str, object] = {}

    def read(self, path: str, default: str = "?") -> str:
        f = self._handles.get(path)
        if f is None:
            try:
                if os.path.exists(path):
                    f = open(path, "r")
                    self._handles[path] = f
            except OSError:
                return default
        if f is None:
            return default
        try:
            f.seek(0)
            val = f.read().strip()
            return val if val else default
        except OSError:
            try:
                f.close()
            except OSError:
                pass
            self._handles[path] = None
            return default

    def close_all(self):
        for f in self._handles.values():
            if f:
                try:
                    f.close()
                except OSError:
                    pass
        self._handles.clear()
