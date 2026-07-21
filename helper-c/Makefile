# ryzenadj-gui — C fast-path binaries.
#
#   ryzenadj-helper : root_helper.py fast-path (gaming/PCI tunables, nvctgp,
#                     governor/EPP, boost, boot-defaults, cgroup CCX
#                     isolation). See ryzenadj_helper.c for what it
#                     deliberately does NOT implement.
#   nvctgp          : hardened replacement for the nvctgp shell script — the
#                     /dev/mem raw-write cTGP primitive. See nvctgp.c header
#                     for why this one, above all, belongs in C.
#
# Both build with the same hardening flags (stack protector, FORTIFY,
# full RELRO + BIND_NOW).

CC      ?= gcc
# ── flags ─────────────────────────────────────────────────────────────
# CFLAGS/LDFLAGS stay user-overridable (Gentoo's portage exports both from
# make.conf, and in GNU make an ENVIRONMENT variable beats a `?=`
# assignment). That is exactly why the hardening flags are NOT in here:
# with everything under `CFLAGS ?=`, a distro build with its own CFLAGS
# silently dropped -fstack-protector-strong, -D_FORTIFY_SOURCE,
# -fPIE and the whole LDFLAGS line — verified by building with
# CFLAGS/LDFLAGS exported. For a binary that runs as root under pkexec
# that is not an acceptable failure mode, so the hardening set below is
# appended separately and cannot be overridden by accident.
CFLAGS  ?= -O2
LDFLAGS ?=

# Mandatory hardening — always appended AFTER $(CFLAGS)/$(LDFLAGS):
#   -fstack-protector-strong  stack canaries on anything with a local buffer
#   -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=2
#                             fortified str/mem/printf wrappers. The -U
#                             first avoids a redefinition warning on
#                             distros (e.g. Gentoo hardened) that already
#                             define it. Needs an optimising build to do
#                             anything, hence the -O2 default above.
#   -fPIE / -pie              position-independent executable so ASLR
#                             covers the binary's own text, not just libs
#   -Wl,-z,relro,-z,now       full RELRO: GOT resolved and made read-only
#                             at startup, so it can't be overwritten later
#   -Wl,-z,noexecstack        never mark the stack executable
#   -Wformat -Wformat-security
#                             catch a non-literal format string before it
#                             becomes a bug in a privileged process
HARDEN_CFLAGS  = -Wall -Wextra -Wpedantic -std=c11 \
                 -fstack-protector-strong \
                 -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=2 \
                 -fPIE -Wformat -Wformat-security
HARDEN_LDFLAGS = -Wl,-z,relro,-z,now -Wl,-z,noexecstack -pie

# Overridable at build time so install.sh can bake in the actual
# nvctgp location (e.g. /usr/sbin/nvctgp) instead of the source
# default — same idea as the sed-based path rewrite install.sh already
# does for ryzenadj_gui.py's hardcoded /usr/local/sbin/nvctgp.
NVCTGP_PATH ?= /usr/local/sbin/nvctgp
CPPFLAGS    += -DNVCTGP_PATH='"$(NVCTGP_PATH)"'

HELPER_BIN   = ryzenadj-helper
HELPER_SRCS  = ryzenadj_helper.c json_min.c

NVCTGP_BIN   = nvctgp
NVCTGP_SRCS  = nvctgp.c

PREFIX      ?= /usr/local/lib/ryzenadj-gui
SBIN_PREFIX ?= /usr/local/sbin

.PHONY: all clean install

all: $(HELPER_BIN) $(NVCTGP_BIN)

$(HELPER_BIN): $(HELPER_SRCS) json_min.h
	$(CC) $(CFLAGS) $(HARDEN_CFLAGS) $(CPPFLAGS) -o $(HELPER_BIN) $(HELPER_SRCS) $(LDFLAGS) $(HARDEN_LDFLAGS)

$(NVCTGP_BIN): $(NVCTGP_SRCS)
	$(CC) $(CFLAGS) $(HARDEN_CFLAGS) -o $(NVCTGP_BIN) $(NVCTGP_SRCS) $(LDFLAGS) $(HARDEN_LDFLAGS)

# ryzenadj-helper: root:root 0700 (only root reads/executes — a local
# user can't inspect it for 0-days or replace it and keep pkexec's trust).
# nvctgp: root:root 0755 — invoked by nvctgpd and by ryzenadj-helper's
# run_nvctgp op with a fixed path; it takes only a clamped integer arg.
install: all
	install -d -m 0755 $(PREFIX)
	install -o root -g root -m 0700 $(HELPER_BIN) $(PREFIX)/$(HELPER_BIN)
	install -d -m 0755 $(SBIN_PREFIX)
	install -o root -g root -m 0755 $(NVCTGP_BIN) $(SBIN_PREFIX)/$(NVCTGP_BIN)

clean:
	rm -f $(HELPER_BIN) $(NVCTGP_BIN)
