# Copyright 2025 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit desktop toolchain-funcs

DESCRIPTION="AMD Ryzen power management GUI for Alienware M16 R1 (RyzenAdj + nvcurve + cTGP)"
HOMEPAGE="https://github.com/arabcian/m16R1-power-manager"

# Live ebuild by default (9999). For a tagged release, drop the git eclass
# block below and set SRC_URI to the release tarball instead.
if [[ ${PV} == 9999 ]]; then
	inherit git-r3
	EGIT_REPO_URI="https://github.com/arabcian/m16R1-power-manager.git"
	EGIT_BRANCH="main"
	EGIT_CLONE_TYPE="shallow"
	KEYWORDS=""
else
	SRC_URI="https://github.com/arabcian/m16R1-power-manager/archive/refs/tags/v${PV}.tar.gz -> ${P}.tar.gz"
	S="${WORKDIR}/m16R1-power-manager-${PV}"
	KEYWORDS="~amd64"
fi

LICENSE="MIT"
SLOT="0"

# nvctgp: the hardened C /dev/mem cTGP writer + its OpenRC daemon. Optional
# because it needs acpi_call and only makes sense on this exact platform.
# passwordless: install the polkit rules.d rule granting wheel/sudo local
# active sessions passwordless access to the two audited helpers. Off by
# default — the auth_admin_keep polkit fallback still works without it.
IUSE="+nvctgp passwordless"

# We compile two small C binaries (ryzenadj-helper + nvctgp) — need a
# C compiler at build time. Everything else is Python/Qt at runtime.
RDEPEND="
	dev-python/pyside6
	sys-auth/polkit
	dev-python/fastapi
	dev-python/uvicorn
"
DEPEND=""
BDEPEND="sys-devel/gcc"

# Byte-compiled .pyc under our private libdir would be owned by portage
# but written at first run by root — skip QA on that.
QA_PREBUILT=""

APPDIR="/usr/lib/${PN}"
SBINDIR="/usr/sbin"

src_prepare() {
	default

	# Align the hardcoded /usr/local/... paths in the source with the FHS
	# install locations — identical intent to install.sh's post-copy sed.
	sed -i "s|/usr/local/lib/ryzenadj-gui|${APPDIR}|g" \
		ryzenadj_gui.py ryzenadj_wrapper.py || die "sed appdir failed"
	sed -i "s|/usr/local/sbin/nvctgp|${SBINDIR}/nvctgp|g" \
		ryzenadj_gui.py || die "sed nvctgp path failed"
	sed -i "s|/usr/local/lib/ryzenadj-gui|${APPDIR}|g" \
		com.ryzenadj.gui.policy || die "sed policy failed"
}

src_compile() {
	# Build both C helpers. NVCTGP_PATH is baked in so the C ryzenadj-helper's
	# run_nvctgp op and nvctgpd resolve the installed location. Honour the
	# user's CC/CFLAGS/LDFLAGS via emake.
	emake -C helper-c \
		CC="$(tc-getCC)" \
		NVCTGP_PATH="${SBINDIR}/nvctgp" \
		|| die "helper-c build failed"
}

src_install() {
	# ── Application code (root:root; root_helper.py is 0700) ──────────────
	insinto "${APPDIR}"
	doins ryzenadj_gui.py ryzenadj_tray.py ryzenadj_wrapper.py \
		ryzenadj_common.py tool_paths.py

	# Executable launchers' sources
	fperms 0755 "${APPDIR}/ryzenadj_gui.py"
	fperms 0755 "${APPDIR}/ryzenadj_tray.py"
	fperms 0755 "${APPDIR}/ryzenadj_wrapper.py"

	# Icons (best-effort — only if present)
	for asset in Alien.png alienfx.svg alienware_app.png; do
		[[ -f ${asset} ]] && doins "${asset}"
	done

	# nvcurve package
	insinto "${APPDIR}/nvcurve"
	doins -r nvcurve/.
	# strip any stray bytecode
	find "${ED}${APPDIR}/nvcurve" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null

	# root_helper.py — the privileged Python helper: root:root, 0700.
	# This mode is load-bearing for the polkit trust model (a user must not
	# be able to read or replace it), so install it explicitly.
	exeinto "${APPDIR}"
	# We can't set 0700 with doexe (it forces 0755); use install directly
	# into the image and let fperms fix the mode.
	insinto "${APPDIR}"
	doins root_helper.py
	fperms 0700 "${APPDIR}/root_helper.py"

	# ryzenadj-helper — the C fast-path binary: root:root, 0700, same posture.
	exeinto "${APPDIR}"
	newexe helper-c/ryzenadj-helper ryzenadj-helper
	fperms 0700 "${APPDIR}/ryzenadj-helper"

	# ── Launchers ─────────────────────────────────────────────────────────
	dodir /usr/bin
	cat > "${ED}/usr/bin/ryzenadj-gui" <<-EOF || die
		#!/bin/sh
		exec python3 "${APPDIR}/ryzenadj_gui.py" "\$@"
	EOF
	cat > "${ED}/usr/bin/ryzenadj-tray" <<-EOF || die
		#!/bin/sh
		exec python3 "${APPDIR}/ryzenadj_tray.py" "\$@"
	EOF
	fperms 0755 /usr/bin/ryzenadj-gui /usr/bin/ryzenadj-tray

	# ── Polkit action (always) + rule (USE=passwordless) ─────────────────
	insinto /usr/share/polkit-1/actions
	doins com.ryzenadj.gui.policy

	if use passwordless; then
		insinto /etc/polkit-1/rules.d
		doins 49-ryzenadj-gui.rules
	fi

	# ── Desktop entry ─────────────────────────────────────────────────────
	make_desktop_entry ryzenadj-gui "RyzenAdj GUI" "${APPDIR}/Alien.png" "System;Settings;"

	# ── Runtime state dirs (FHS) ──────────────────────────────────────────
	keepdir /etc/ryzenadj-gui/profiles
	keepdir /etc/nvcurve/profiles
	keepdir /var/lib/ryzenadj-gui/scripts

	# Seed default power profiles (never clobber — see pkg_postinst note)
	if [[ -d profiles ]]; then
		insinto /usr/share/${PN}/profiles
		doins profiles/*.json
	fi

	# ── nvctgp (USE=nvctgp): hardened C binary + daemon + OpenRC service ──
	if use nvctgp; then
		# Prefer the C binary we built in src_compile (hardened /dev/mem
		# writer). Fall back to the shell script only if the C build is
		# somehow absent — same argv/stdout contract either way.
		if [[ -f helper-c/nvctgp ]]; then
			into /usr
			newsbin helper-c/nvctgp nvctgp
		elif [[ -f nvctgp/nvctgp ]]; then
			into /usr
			newsbin nvctgp/nvctgp nvctgp
		fi
		[[ -f nvctgp/nvctgpd ]] && { into /usr; newsbin nvctgp/nvctgpd nvctgpd; }

		# OpenRC service + default conf.d (WATTS ceiling)
		if [[ -f nvctgp/nvctgpd.initd ]]; then
			newinitd nvctgp/nvctgpd.initd nvctgpd
			newconfd - nvctgpd <<-EOF || die
				# GPU Configurable-TGP ceiling in watts (valid 125-175).
				WATTS=175
			EOF
		fi
	fi
}

pkg_postinst() {
	# Seed profiles into /etc without clobbering user edits.
	local src="${EROOT}/usr/share/${PN}/profiles"
	local dst="${EROOT}/etc/ryzenadj-gui/profiles"
	if [[ -d ${src} ]]; then
		local f base
		for f in "${src}"/*.json; do
			[[ -e ${f} ]] || continue
			base=$(basename "${f}")
			if [[ ! -e ${dst}/${base} ]]; then
				cp -n "${f}" "${dst}/${base}"
			fi
		done
	fi

	elog "RyzenAdj GUI kuruldu."
	elog ""
	elog "  Başlat:  ryzenadj-gui   /   ryzenadj-tray"
	elog ""
	if use passwordless; then
		elog "Parolasız yetkilendirme kuralı kuruldu (USE=passwordless)."
		elog "  Kullanıcınız 'wheel' grubunda olmalı:"
		elog "    usermod -aG wheel <kullanıcı>   (sonra oturumu yeniden açın)"
	else
		elog "Parolasız kural KURULMADI. İlk root işleminde polkit parola sorar"
		elog "(auth_admin_keep — kısa süre önbelleğe alır). Parolasız çalışma"
		elog "için: USE=\"passwordless\" ile yeniden kurun."
	fi
	if use nvctgp; then
		elog ""
		elog "nvctgp (cTGP güç yöneticisi, sertleştirilmiş C sürümü) kuruldu."
		elog "  Gerekli çekirdek modülü:  modprobe acpi_call"
		elog "  Boot'ta sabitlemek için:  rc-update add nvctgpd default"
		elog "  Watt tavanı:              /etc/conf.d/nvctgpd (WATTS=125..175)"
	fi

	# Refresh polkit if running
	if [[ -x $(command -v rc-service 2>/dev/null) ]]; then
		rc-service --ifexists polkit restart >/dev/null 2>&1
	fi
}
