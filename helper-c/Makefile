# ryzenadj-helper — build the narrow C fast-path for root_helper.py.
# See ryzenadj_helper.c for what this is and, just as importantly, what
# it deliberately does NOT implement (GPU curve / profile-JSON / cgroup
# ops stay in root_helper.py — see the file header for why).

CC      ?= gcc
CFLAGS  ?= -Wall -Wextra -Wpedantic -std=c11 -O2 -fstack-protector-strong -D_FORTIFY_SOURCE=2
LDFLAGS ?= -Wl,-z,relro,-z,now

# Overridable at build time so install.sh can bake in the actual
# nvctgp location (e.g. /usr/sbin/nvctgp) instead of the source
# default — same idea as the sed-based path rewrite install.sh already
# does for ryzenadj_gui.py's hardcoded /usr/local/sbin/nvctgp.
NVCTGP_PATH ?= /usr/local/sbin/nvctgp
CPPFLAGS    += -DNVCTGP_PATH='"$(NVCTGP_PATH)"'

BIN      = ryzenadj-helper
SRCS     = ryzenadj_helper.c json_min.c
PREFIX  ?= /usr/local/lib/ryzenadj-gui

.PHONY: all clean install

all: $(BIN)

$(BIN): $(SRCS) json_min.h
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $(BIN) $(SRCS) $(LDFLAGS)

# Installed root:root, 0700 — same posture as root_helper.py: only root
# can read or execute it, so a local user can't inspect it for 0-days
# or (more importantly) replace it and have pkexec still trust it.
install: $(BIN)
	install -d -m 0755 $(PREFIX)
	install -o root -g root -m 0700 $(BIN) $(PREFIX)/$(BIN)

clean:
	rm -f $(BIN)
