#!/bin/bash
#
# RyzenAdj GUI — Kurulum Scripti
# ════════════════════════════════════════════════════════════════════════
#
# Bu script, uygulamayı Linux Filesystem Hierarchy Standard (FHS)'e uygun,
# kurulum yerinden bağımsız, merkezi sistem dizinlerine kurar:
#
#   /usr/lib/ryzenadj-gui/               uygulama kodu (Python, ikonlar, nvcurve/)
#   /usr/lib/ryzenadj-gui/root_helper.py         (root:root, 0700 — ayrı kurulur)
#   /usr/bin/ryzenadj-gui                başlatıcı (launcher)
#   /usr/bin/ryzenadj-tray               başlatıcı (launcher)
#   /usr/sbin/nvctgp, /usr/sbin/nvctgpd  cTGP güç yöneticisi (opsiyonel)
#
# NOT: Uygulama /usr/local/... yollarını kaynak koda hardcode eder; bu script
# dosyaları kurduktan SONRA sed ile kurulum diziniyle (/usr/lib, /usr/sbin)
# hizalar — ebuild'in src_prepare'deki sed'iyle birebir aynı mantık.
#   /etc/ryzenadj-gui/profiles/          güç profilleri (root_helper yazar, GUI okur)
#   /etc/nvcurve/profiles/               nvcurve GPU V/F eğri profilleri
#   /var/lib/ryzenadj-gui/scripts/       kalıcı, üretilmiş profil aktivasyon script'leri
#   /usr/share/polkit-1/actions/         Polkit action tanımı (com.ryzenadj.gui.policy)
#   /usr/share/applications/             .desktop uygulama girişi
#
# Kullanım:
#   $ chmod +x install.sh
#   $ sudo ./install.sh
#
# Eski (SCRIPT_DIR bağımlı) bir kurulumunuz / profil klasörünüz varsa:
#   $ sudo ./install.sh --migrate-profiles /home/KULLANICI/Ryzen/profiles
#
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────
# 0. Renkler ve yardımcılar
# ─────────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

info()  { echo -e "${BLUE}→${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
err()   { echo -e "${RED}✗${NC} $*"; }

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─────────────────────────────────────────────────────────────────────────
# 1. root kontrolü
# ─────────────────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    err "Bu script root ile çalıştırılmalı (sudo ./install.sh)."
    exit 1
fi

# Kurulumu başlatan gerçek kullanıcı (sudo ile çalıştırıldıysa)
REAL_USER="${SUDO_USER:-}"
if [ -z "$REAL_USER" ]; then
    REAL_USER="$(logname 2>/dev/null || true)"
fi
if [ -n "$REAL_USER" ]; then
    REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
else
    REAL_HOME=""
fi

# ─────────────────────────────────────────────────────────────────────────
# 2. Argümanları ayrıştır
# ─────────────────────────────────────────────────────────────────────────
MIGRATE_PROFILES_FROM=""
SKIP_AUTOSTART=0
SKIP_PASSWORDLESS_RULE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --migrate-profiles)
            MIGRATE_PROFILES_FROM="${2:-}"
            shift 2
            ;;
        --no-autostart)
            SKIP_AUTOSTART=1
            shift
            ;;
        --no-passwordless)
            SKIP_PASSWORDLESS_RULE=1
            shift
            ;;
        -h|--help)
            echo "Kullanım: sudo ./install.sh [--migrate-profiles DİZİN] [--no-autostart] [--no-passwordless]"
            exit 0
            ;;
        *)
            warn "Bilinmeyen argüman yok sayıldı: $1"
            shift
            ;;
    esac
done

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║             RyzenAdj GUI — Kurulum (FHS düzeni)                 ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"

# ─────────────────────────────────────────────────────────────────────────
# 3. Bağımlılıkları kontrol et
# ─────────────────────────────────────────────────────────────────────────
info "[1/8] Bağımlılıklar kontrol ediliyor..."

MISSING_DEPS=()
command -v python3 >/dev/null 2>&1 || MISSING_DEPS+=("python3")
command -v pkexec  >/dev/null 2>&1 || MISSING_DEPS+=("polkit (pkexec)")
python3 -c "from PySide6 import QtCore" 2>/dev/null || MISSING_DEPS+=("python3-pyside6")
command -v ryzenadj >/dev/null 2>&1 || warn "ryzenadj bulunamadı (PATH içinde) — güç limiti uygulama çalışmaz."
command -v alienfx_cli >/dev/null 2>&1 || warn "alienfx_cli bulunamadı — RGB/platform-profile adımları atlanacaktır (bkz. https://github.com/tr1xem/alienfx-linux)."

if [ ${#MISSING_DEPS[@]} -gt 0 ]; then
    err "Eksik bağımlılıklar:"
    for d in "${MISSING_DEPS[@]}"; do echo "   • $d"; done
    echo ""
    echo "  Arch/Manjaro : sudo pacman -S polkit pyside6"
    echo "  Debian/Ubuntu: sudo apt install policykit-1 python3-pyside6"
    echo "  Fedora       : sudo dnf install polkit python3-pyside6"
    echo "  Gentoo       : sudo emerge polkit dev-python/pyside6"
    exit 1
fi
ok "Bağımlılıklar tamam."

# ─────────────────────────────────────────────────────────────────────────
# 4. Dizinleri oluştur (FHS)
# ─────────────────────────────────────────────────────────────────────────
info "[2/8] Sistem dizinleri oluşturuluyor..."

APP_DIR="/usr/lib/ryzenadj-gui"
BIN_DIR="/usr/bin"
SBIN_DIR="/usr/sbin"
PROFILES_DIR="/etc/ryzenadj-gui/profiles"
NVCURVE_PROFILES_DIR="/etc/nvcurve/profiles"
NVCURVE_SNAPSHOT_DIR="/var/cache/nvcurve/snapshots"
VAR_SCRIPTS_DIR="/var/lib/ryzenadj-gui/scripts"
RUN_SCRIPTS_DIR="/run/ryzenadj-gui/scripts"
POLKIT_ACTIONS_DIR="/usr/share/polkit-1/actions"
DESKTOP_DIR="/usr/share/applications"

install -d -o root -g root -m 0755 "$APP_DIR"
install -d -o root -g root -m 0755 "$APP_DIR/nvcurve"
install -d -o root -g root -m 0755 "$PROFILES_DIR"
install -d -o root -g root -m 0755 "$NVCURVE_PROFILES_DIR"
install -d -o root -g root -m 0755 "$NVCURVE_SNAPSHOT_DIR"
install -d -o root -g root -m 0755 "$VAR_SCRIPTS_DIR"
install -d -o root -g root -m 0700 "$RUN_SCRIPTS_DIR"
ok "Dizinler hazır."

# ─────────────────────────────────────────────────────────────────────────
# 5. Uygulama dosyalarını kopyala
# ─────────────────────────────────────────────────────────────────────────
info "[3/8] Uygulama dosyaları kopyalanıyor..."

install -o root -g root -m 0755 "$SOURCE_DIR/ryzenadj_gui.py"     "$APP_DIR/ryzenadj_gui.py"
install -o root -g root -m 0755 "$SOURCE_DIR/ryzenadj_tray.py"    "$APP_DIR/ryzenadj_tray.py"
install -o root -g root -m 0755 "$SOURCE_DIR/ryzenadj_wrapper.py" "$APP_DIR/ryzenadj_wrapper.py"
install -o root -g root -m 0644 "$SOURCE_DIR/ryzenadj_common.py"  "$APP_DIR/ryzenadj_common.py"
# tool_paths.py: central dynamic tool resolver, imported by ryzenadj_gui.py,
# ryzenadj_tray.py, ryzenadj_wrapper.py (user session) AND root_helper.py
# (root, via pkexec) — must stay world-readable (0644), it holds no secrets,
# only path-resolution logic.
install -o root -g root -m 0644 "$SOURCE_DIR/tool_paths.py"       "$APP_DIR/tool_paths.py"
install -o root -g root -m 0644 "$SOURCE_DIR/Alien.png"           "$APP_DIR/Alien.png"
[ -f "$SOURCE_DIR/alienfx.svg" ]        && install -o root -g root -m 0644 "$SOURCE_DIR/alienfx.svg"        "$APP_DIR/alienfx.svg"
[ -f "$SOURCE_DIR/alienware_app.png" ]  && install -o root -g root -m 0644 "$SOURCE_DIR/alienware_app.png"  "$APP_DIR/alienware_app.png"

# nvcurve/ paketini bütünüyle kopyala (pycache hariç)
rm -rf "$APP_DIR/nvcurve"
cp -a "$SOURCE_DIR/nvcurve" "$APP_DIR/nvcurve"
find "$APP_DIR/nvcurve" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
chown -R root:root "$APP_DIR/nvcurve"
find "$APP_DIR/nvcurve" -type d -exec chmod 0755 {} \;
find "$APP_DIR/nvcurve" -type f -exec chmod 0644 {} \;

# root_helper.py: root:root, 0700 — kullanıcı tarafından okunamaz/değiştirilemez.
# com.ryzenadj.gui.policy bu dosyayı auth_admin_keep_always ile eşleştirir;
# 0700 + root sahipliği olmadan bu güven modeli GEÇERSİZ olur.
install -o root -g root -m 0700 "$SOURCE_DIR/root_helper.py" "$APP_DIR/root_helper.py"

# ryzenadj-helper: C fast-path for apply_gaming_and_pci / run_nvctgp /
# read_gaming_status (bkz. helper-c/ryzenadj_helper.c). Kaynaktan derlenir;
# NVCTGP_PATH derleme zamanında $SBIN_DIR ile hizalanır (Python tarafındaki
# sed ile aynı amaç). root:root, 0700 — root_helper.py ile aynı güven modeli.
# C binary'leri derle: ryzenadj-helper (fast-path) VE nvctgp (sertleştirilmiş
# /dev/mem cTGP yazıcısı — bkz. helper-c/nvctgp.c başlığı). C nvctgp derlenirse
# shell script yerine O KURULUR; derlenemezse shell script'e düşülür (aşağıda
# nvctgp opsiyonel bileşen bölümünde).
NVCTGP_C_BUILT=0
if command -v gcc >/dev/null 2>&1 || command -v cc >/dev/null 2>&1; then
    info "C yardımcıları derleniyor (ryzenadj-helper + nvctgp)..."
    make -C "$SOURCE_DIR/helper-c" clean >/dev/null
    make -C "$SOURCE_DIR/helper-c" NVCTGP_PATH="$SBIN_DIR/nvctgp"
    install -o root -g root -m 0700 "$SOURCE_DIR/helper-c/ryzenadj-helper" "$APP_DIR/ryzenadj-helper"
    ok "ryzenadj-helper derlendi ve kuruldu: $APP_DIR/ryzenadj-helper"
    if [ -f "$SOURCE_DIR/helper-c/nvctgp" ]; then
        NVCTGP_C_BUILT=1
        ok "nvctgp (C, sertleştirilmiş) derlendi — shell script yerine bu kurulacak."
    fi
else
    warn "gcc/cc bulunamadı — ryzenadj-helper (C fast-path) atlanıyor. Gaming ayarları ve GPU TGP, root_helper.py üzerinden (yavaş yol) çalışmaya devam edecek şekilde GUI'de bir fallback YOKTUR; bu binary olmadan bu üç işlem başarısız olur. gcc kurup kurulumu tekrar çalıştırın. nvctgp, C derlenemediği için shell script olarak kurulacak."
fi

# Hardcoded /usr/local/lib path'lerini kurulum diziniyle hizala (ebuild ile aynı sed).
# Kurulan KOPYALAR üzerinde çalışır — kaynak checkout kirlenmez.
# (root_helper.py'deki referans yalnızca yorum, işleve etkisi yok — dokunulmadı.)
sed -i "s|/usr/local/lib/ryzenadj-gui|$APP_DIR|g" \
    "$APP_DIR/ryzenadj_gui.py" "$APP_DIR/ryzenadj_wrapper.py"
# GUI'nin hardcode çağırdığı nvctgp yolunu /usr/sbin'e çevir
sed -i "s|/usr/local/sbin/nvctgp|$SBIN_DIR/nvctgp|g" "$APP_DIR/ryzenadj_gui.py"
ok "Uygulama dosyaları kuruldu ve path'ler $APP_DIR ile hizalandı."

# ─────────────────────────────────────────────────────────────────────────
# 6. Başlatıcılar (launchers)
# ─────────────────────────────────────────────────────────────────────────
info "[4/8] Başlatıcı komutlar oluşturuluyor..."

cat > "$BIN_DIR/ryzenadj-gui" <<EOF
#!/bin/sh
exec python3 "$APP_DIR/ryzenadj_gui.py" "\$@"
EOF
chmod 0755 "$BIN_DIR/ryzenadj-gui"

cat > "$BIN_DIR/ryzenadj-tray" <<EOF
#!/bin/sh
exec python3 "$APP_DIR/ryzenadj_tray.py" "\$@"
EOF
chmod 0755 "$BIN_DIR/ryzenadj-tray"

ok "Başlatıcılar kuruldu: $BIN_DIR/ryzenadj-gui, $BIN_DIR/ryzenadj-tray"

# ─────────────────────────────────────────────────────────────────────────
# 7. Polkit action + kural
# ─────────────────────────────────────────────────────────────────────────
info "[5/8] Polkit yetkilendirmesi kuruluyor..."

install -o root -g root -m 0644 "$SOURCE_DIR/com.ryzenadj.gui.policy" \
    "$POLKIT_ACTIONS_DIR/com.ryzenadj.gui.policy"
# Polkit .policy içindeki root_helper.py yolunu da kurulum diziniyle hizala
sed -i "s|/usr/local/lib/ryzenadj-gui|$APP_DIR|g" \
    "$POLKIT_ACTIONS_DIR/com.ryzenadj.gui.policy"

# Parolasız yetkilendirme için gerçek JS kuralı (bkz. dosyanın içindeki
# açıklama). Kullanıcı istemezse --no-passwordless ile atlanabilir.
if [ "$SKIP_PASSWORDLESS_RULE" -eq 0 ] && [ -f "$SOURCE_DIR/49-ryzenadj-gui.rules" ]; then
    install -d -o root -g root -m 0755 /etc/polkit-1/rules.d
    install -o root -g root -m 0644 "$SOURCE_DIR/49-ryzenadj-gui.rules" \
        /etc/polkit-1/rules.d/49-ryzenadj-gui.rules
    ok "Parolasız yetkilendirme kuralı kuruldu: /etc/polkit-1/rules.d/49-ryzenadj-gui.rules"
    if [ -n "$REAL_USER" ] && ! id -nG "$REAL_USER" 2>/dev/null | grep -qE '\b(wheel|sudo)\b'; then
        warn "Kullanıcı '$REAL_USER' wheel/sudo grubunda değil — parolasız kural bu kullanıcı için ÇALIŞMAYACAK."
        warn "  Eklemek için: sudo usermod -aG wheel $REAL_USER   (sonra oturumu yeniden açın)"
    fi
else
    warn "Parolasız yetkilendirme kuralı atlandı (--no-passwordless)."
fi

if command -v rc-service >/dev/null 2>&1; then
    rc-service polkit restart >/dev/null 2>&1 || warn "polkit OpenRC servisi yeniden başlatılamadı (manuel: rc-service polkit restart)"
elif command -v systemctl >/dev/null 2>&1; then
    systemctl restart polkit >/dev/null 2>&1 || warn "polkit systemd servisi yeniden başlatılamadı (manuel: systemctl restart polkit)"
else
    warn "polkit servisi otomatik yeniden başlatılamadı; polkitd'yi manuel yeniden başlatın."
fi
ok "Polkit action kuruldu: $POLKIT_ACTIONS_DIR/com.ryzenadj.gui.policy"

# ─────────────────────────────────────────────────────────────────────────
# 8. .desktop girişi
# ─────────────────────────────────────────────────────────────────────────
info "[6/8] Masaüstü girişi ekleniyor..."

cat > "$DESKTOP_DIR/ryzenadj-gui.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=RyzenAdj GUI
Comment=AMD Ryzen power management for Alienware laptops
Exec=$BIN_DIR/ryzenadj-gui
Icon=$APP_DIR/Alien.png
Terminal=false
Categories=System;Settings;
StartupWMClass=ryzenadj_gui.py
EOF
chmod 0644 "$DESKTOP_DIR/ryzenadj-gui.desktop"
ok "Masaüstü girişi kuruldu: $DESKTOP_DIR/ryzenadj-gui.desktop"

# ─────────────────────────────────────────────────────────────────────────
# 9. Profil verisi taşıma / seed
# ─────────────────────────────────────────────────────────────────────────
info "[7/8] Güç profilleri yerleştiriliyor..."

seed_profiles_from() {
    local src="$1"
    local copied=0
    if [ -d "$src" ]; then
        for f in "$src"/*.json; do
            [ -e "$f" ] || continue
            local base
            base="$(basename "$f")"
            if [ -f "$PROFILES_DIR/$base" ]; then
                warn "  $base zaten mevcut, atlanıyor (üzerine yazılmadı)."
            else
                install -o root -g root -m 0644 "$f" "$PROFILES_DIR/$base"
                echo "  + $base"
                copied=$((copied + 1))
            fi
        done
    fi
    echo "$copied"
}

# a) Repo içindeki varsayılan/örnek profilller (varsa)
if [ -d "$SOURCE_DIR/profiles" ]; then
    seed_profiles_from "$SOURCE_DIR/profiles" >/dev/null
fi

# b) Kullanıcının eski (SCRIPT_DIR bağımlı) kurulumundan gelen gerçek profiller
if [ -n "$MIGRATE_PROFILES_FROM" ]; then
    if [ -d "$MIGRATE_PROFILES_FROM" ]; then
        info "  Eski profiller taşınıyor: $MIGRATE_PROFILES_FROM → $PROFILES_DIR"
        seed_profiles_from "$MIGRATE_PROFILES_FROM" >/dev/null
    else
        warn "  --migrate-profiles ile verilen dizin bulunamadı: $MIGRATE_PROFILES_FROM"
    fi
elif [ -n "$REAL_HOME" ] && [ -d "$REAL_HOME/Ryzen/profiles" ]; then
    # Yaygın eski konum otomatik algılanıyor (bu projenin orijinal SCRIPT_DIR/profiles yerleşimi)
    info "  Eski profil dizini otomatik algılandı: $REAL_HOME/Ryzen/profiles"
    seed_profiles_from "$REAL_HOME/Ryzen/profiles" >/dev/null
fi
ok "Profiller: $PROFILES_DIR"

# ─────────────────────────────────────────────────────────────────────────
# 10. Kullanıcı düzeyinde: tray otomatik başlatma (opsiyonel)
# ─────────────────────────────────────────────────────────────────────────
info "[8/8] Kullanıcı ayarları..."

if [ "$SKIP_AUTOSTART" -eq 0 ] && [ -n "$REAL_HOME" ] && [ -d "$REAL_HOME" ]; then
    AUTOSTART_DIR="$REAL_HOME/.config/autostart"
    install -d -o "$REAL_USER" -g "$REAL_USER" -m 0755 "$AUTOSTART_DIR"
    cat > "$AUTOSTART_DIR/ryzenadj-tray.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=RyzenAdj Tray
Comment=RyzenAdj GUI sistem tepsisi
Exec=$BIN_DIR/ryzenadj-tray
Icon=$APP_DIR/Alien.png
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
    chown "$REAL_USER:$REAL_USER" "$AUTOSTART_DIR/ryzenadj-tray.desktop"
    chmod 0644 "$AUTOSTART_DIR/ryzenadj-tray.desktop"
    ok "Tray otomatik başlatma eklendi: $AUTOSTART_DIR/ryzenadj-tray.desktop"
else
    warn "Tray otomatik başlatması atlandı (--no-autostart veya kullanıcı ev dizini bulunamadı)."
fi

# ─────────────────────────────────────────────────────────────────────────
# Opsiyonel bileşen: nvctgp (GPU Configurable-TGP güç yöneticisi)
# Repo kökünde nvctgp/ klasörü VARSA kurulur; YOKSA hiçbir şey yapılmaz
# (sessizce atlanır, normal prosedür kesintisiz devam eder).
# GUI kaynak kodda /usr/local/sbin/nvctgp'yi çağırır; yukarıda bu yol
# $SBIN_DIR/nvctgp (/usr/sbin) olacak şekilde sed'lendiği için buraya kuruyoruz.
# ─────────────────────────────────────────────────────────────────────────
if [ -d "$SOURCE_DIR/nvctgp" ]; then
    info "[nvctgp] cTGP güç yöneticisi bulundu, kuruluyor..."

    # SBIN_DIR global olarak /usr/sbin (ebuild ile aynı düzen)
    install -d -o root -g root -m 0755 "$SBIN_DIR"

    NVCTGP_OK=0
    # nvctgp: sertleştirilmiş C binary derlendiyse ONU kur (shell + inline
    # python3 heredoc /dev/mem yazıcısının yerine geçer — bkz. helper-c/nvctgp.c).
    # Derlenmediyse orijinal shell script'e düş; ikisi de aynı argv/çıktı
    # sözleşmesine sahip olduğundan nvctgpd fark etmez.
    if [ "${NVCTGP_C_BUILT:-0}" -eq 1 ] && [ -f "$SOURCE_DIR/helper-c/nvctgp" ]; then
        install -o root -g root -m 0755 "$SOURCE_DIR/helper-c/nvctgp" "$SBIN_DIR/nvctgp"; NVCTGP_OK=1
        ok "nvctgp (C, sertleştirilmiş) kuruldu: $SBIN_DIR/nvctgp"
    elif [ -f "$SOURCE_DIR/nvctgp/nvctgp" ]; then
        install -o root -g root -m 0755 "$SOURCE_DIR/nvctgp/nvctgp" "$SBIN_DIR/nvctgp"; NVCTGP_OK=1
        warn "nvctgp shell script (yedek) kuruldu — C sürümü için gcc kurup tekrar çalıştırın."
    fi
    [ -f "$SOURCE_DIR/nvctgp/nvctgpd" ] && { install -o root -g root -m 0755 "$SOURCE_DIR/nvctgp/nvctgpd" "$SBIN_DIR/nvctgpd"; }

    # OpenRC init script + conf.d (yalnızca OpenRC anlamlı; dosya varsa kurulur)
    if [ -f "$SOURCE_DIR/nvctgp/nvctgpd.initd" ]; then
        install -o root -g root -m 0755 "$SOURCE_DIR/nvctgp/nvctgpd.initd" /etc/init.d/nvctgpd
        # conf.d yalnızca YOKSA yazılır → kullanıcının WATTS ayarı korunur
        if [ ! -f /etc/conf.d/nvctgpd ]; then
            echo 'WATTS=175' > /etc/conf.d/nvctgpd
            chmod 0644 /etc/conf.d/nvctgpd
        fi
    fi

    if [ "$NVCTGP_OK" -eq 1 ]; then
        ok "nvctgp kuruldu: $SBIN_DIR/nvctgp (+ nvctgpd, initd, conf.d)"
        echo "     • Gerekli: 'acpi_call' çekirdek modülü (sudo modprobe acpi_call)."
        if command -v rc-update >/dev/null 2>&1; then
            echo "     • Boot'ta sabitlemek için:"
            echo "         sudo rc-update add nvctgpd default && sudo rc-service nvctgpd start"
            echo "     • Watt tavanı: /etc/conf.d/nvctgpd içindeki WATTS= (125-175)."
        fi
    else
        warn "nvctgp/ klasörü var ama içinde 'nvctgp' betiği yok — atlandı."
    fi
fi

# ─────────────────────────────────────────────────────────────────────────
# Özet
# ─────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    ✅ KURULUM TAMAMLANDI                        ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Uygulama kodu     : $APP_DIR"
echo "  Güç profilleri    : $PROFILES_DIR"
echo "  Aktivasyon script.: $VAR_SCRIPTS_DIR"
echo "  nvcurve profilleri: $NVCURVE_PROFILES_DIR"
echo ""
echo "  Başlatmak için:"
echo "    $ ryzenadj-gui"
echo "    $ ryzenadj-tray"
echo ""
echo -e "${YELLOW}İlk çalıştırma:${NC}"
echo "  • GUI root olmadan açılır."
echo "  • Bir parametre değiştirdiğinizde pkexec/Polkit parola ister (1 kez)."
echo "  • Sonraki 15 dakika (auth_admin_keep_always) tekrar sormaz."
echo ""
echo -e "${YELLOW}Kaldırmak için:${NC}"
echo "  $ sudo ./uninstall.sh"
echo ""
