#!/bin/bash
#
# RyzenAdj GUI — Kaldırma Scripti
# ════════════════════════════════════════════════════════════════════════
#
# Kullanım:
#   $ sudo ./uninstall.sh              # uygulamayı kaldırır, profilleri KORUR
#   $ sudo ./uninstall.sh --purge      # profilleri ve tüm veriyi de siler
#
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}→${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }

if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}✗${NC} Bu script root ile çalıştırılmalı (sudo ./uninstall.sh)."
    exit 1
fi

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

REAL_USER="${SUDO_USER:-}"
[ -z "$REAL_USER" ] && REAL_USER="$(logname 2>/dev/null || true)"
if [ -n "$REAL_USER" ]; then
    REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
else
    REAL_HOME=""
fi

APP_DIR="/usr/lib/ryzenadj-gui"
BIN_DIR="/usr/bin"
PROFILES_DIR="/etc/ryzenadj-gui"
VAR_DIR="/var/lib/ryzenadj-gui"
RUN_DIR="/run/ryzenadj-gui"
POLKIT_ACTION="/usr/share/polkit-1/actions/com.ryzenadj.gui.policy"
POLKIT_RULE="/etc/polkit-1/rules.d/49-ryzenadj-gui.rules"
DESKTOP_FILE="/usr/share/applications/ryzenadj-gui.desktop"

info "Uygulama durduruluyor (çalışıyorsa)..."
pkill -f "ryzenadj_gui.py" 2>/dev/null || true
pkill -f "ryzenadj_tray.py" 2>/dev/null || true

info "Uygulama dosyaları kaldırılıyor..."
rm -rf "$APP_DIR"
rm -f "$BIN_DIR/ryzenadj-gui" "$BIN_DIR/ryzenadj-tray"
rm -f "$POLKIT_ACTION"
rm -f "$POLKIT_RULE"
rm -f "$DESKTOP_FILE"
rm -rf "$RUN_DIR"
ok "Uygulama kaldırıldı: $APP_DIR, launcher'lar, polkit action/rule, .desktop"

# nvctgp (opsiyonel bileşen) — kuruluysa kaldır, kurulu değilse sessiz geç
info "nvctgp bileşeni kaldırılıyor (varsa)..."
if command -v rc-service >/dev/null 2>&1; then
    rc-service nvctgpd stop 2>/dev/null || true
    rc-update del nvctgpd default 2>/dev/null || true
fi
pkill -9 -f "/usr/sbin/nvctgpd" 2>/dev/null || true
pkill -9 -f "inotifywait.*platform_profile" 2>/dev/null || true
rm -f /usr/sbin/nvctgp /usr/sbin/nvctgpd
rm -f /etc/init.d/nvctgpd
rm -f /run/nvctgpd.pid
ok "nvctgp kaldırıldı (kuruluysa)."

if [ -n "$REAL_HOME" ] && [ -f "$REAL_HOME/.config/autostart/ryzenadj-tray.desktop" ]; then
    rm -f "$REAL_HOME/.config/autostart/ryzenadj-tray.desktop"
    ok "Tray autostart girişi kaldırıldı."
fi

if command -v rc-service >/dev/null 2>&1; then
    rc-service polkit restart >/dev/null 2>&1 || true
elif command -v systemctl >/dev/null 2>&1; then
    systemctl restart polkit >/dev/null 2>&1 || true
fi

if [ "$PURGE" -eq 1 ]; then
    warn "--purge: güç profilleri ve script'ler de siliniyor..."
    rm -rf "$PROFILES_DIR" "$VAR_DIR"
    rm -f /etc/conf.d/nvctgpd /var/log/nvctgpd.log
    ok "Profiller ve script'ler silindi: $PROFILES_DIR, $VAR_DIR"
    ok "nvctgp yapılandırması da silindi: /etc/conf.d/nvctgpd"
    echo "  (nvcurve profilleri /etc/nvcurve/profiles bilerek dokunulmadan bırakıldı"
    echo "   — nvcurve ayrı bir bileşen olarak kabul edildi.)"
else
    info "Güç profilleri korundu: $PROFILES_DIR"
    info "Tamamen silmek isterseniz: sudo ./uninstall.sh --purge"
fi

echo ""
echo -e "${GREEN}✓ Kaldırma tamamlandı.${NC}"
