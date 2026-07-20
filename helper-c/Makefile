# ryzenadj-gui — C fast-path binaries.
#
#   ryzenadj-helper : narrow root_helper.py fast-path (apply_gaming_and_pci /
#                     run_nvctgp / read_gaming_status). See ryzenadj_helper.c
#                     for what it deliberately does NOT implement.
#   nvctgp          : hardened replacement for the nvctgp shell script — the
#                     /dev/mem raw-write cTGP primitive. See nvctgp.c header
#                     for why this one, above all, belongs in C.
#
# Both build with the same hardening flags (stack protector, FORTIFY,
# full RELRO + BIND_NOW).

CC      ?= gcc
CFLAGS  ?= -Wall -Wextra -Wpedantic -std=c11 -O2 -fstack-protector-strong -D_FORTIFY_SOURCE=2
LDFLAGS ?= -Wl,-z,relro,-z,now

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
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $(HELPER_BIN) $(HELPER_SRCS) $(LDFLAGS)

$(NVCTGP_BIN): $(NVCTGP_SRCS)
	$(CC) $(CFLAGS) -o $(NVCTGP_BIN) $(NVCTGP_SRCS) $(LDFLAGS)

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
