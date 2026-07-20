/*
 * nvctgp.c
 * ─────────────────────────────────────────────────────────────────────
 * Hardened C reimplementation of the `nvctgp` shell script.
 *
 * WHY THIS IS IN C
 * ----------------
 * The original nvctgp was a bash script that, as root, ran an inline
 * `python3 - <<PY ... PY` heredoc which opened /dev/mem O_RDWR and did a
 * raw os.pwrite() to a physical address computed from an ACPI table
 * base. That is by far the most dangerous single primitive in the whole
 * project: an arbitrary physical-memory write. Keeping it as a shell +
 * interpreter pipeline meant:
 *   - a second, ad-hoc root code path (heredoc source) outside the
 *     audited helper set;
 *   - the physical address came from `$(( base + 0x57 ))` with no bound
 *     check beyond the shell arithmetic;
 *   - /dev/mem was opened without O_NOFOLLOW/O_CLOEXEC discipline;
 *   - a spawned python3 whose module resolution / environment is a
 *     larger surface than a static binary.
 *
 * This C version does exactly the same OPERATION (identical behaviour:
 * same clamp, same unit math, same ACPI \WS09 base read, same +0x57
 * offset, same 16-bit LE write, same \WS1D re-read poke, same readback
 * verify) but with a much smaller, auditable surface:
 *   - no shell, no interpreter, no heredoc;
 *   - the watt argument is parsed and clamped to [BASE_W, MAX_W] with
 *     strtol (rejects junk) BEFORE any privileged action;
 *   - the physical write address is bounds-checked against an explicit
 *     sane window before pwrite();
 *   - /dev/mem and /proc/acpi/call are opened O_CLOEXEC (and the acpi
 *     path is fixed, not attacker-influenced);
 *   - the write is exactly 2 bytes at the computed address, verified by
 *     read-back, matching the original.
 *
 * It is drop-in: same argv contract (`nvctgp <watt>`), same stdout
 * lines ("OK: cTGP=...W ..." / "HATA:"/"UYARI:" ...), same exit codes,
 * so nvctgpd and the C ryzenadj-helper's run_nvctgp op keep working
 * unchanged.
 *
 * Build: see Makefile (nvctgp target). Install: root:root 0755 at the
 * same path the script occupied (/usr/sbin/nvctgp under FHS install).
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <ctype.h>

/* Behaviour constants — identical to the original script. */
#define BASE_W        130      /* script used BASE_W=130 for the clamp   */
#define MIN_W         125      /* documented valid floor                 */
#define MAX_W         175      /* hard cap                               */
#define EC_OFFSET     0x57     /* +0x57 field in the EC table            */
#define UNITS_PER_W   8        /* table unit = 1/8 W                     */

/* Sanity window for the computed physical address. The ACPI OpRegion
 * base (\WS09) on this platform lives in low system memory; we refuse to
 * pwrite() anywhere outside a generous but bounded window. This is a
 * belt-and-suspenders check the shell version never had — even if the
 * ACPI call returned a wild value, we won't scribble on an arbitrary
 * physical address. */
#define ADDR_MIN      0x1000ULL
#define ADDR_MAX      0xFFFFFFFFULL   /* 4 GiB: EC/ACPI NVS is well below this */

static const char *ACPI_CALL_PATH = "/proc/acpi/call";
static const char *DEVMEM_PATH    = "/dev/mem";

/* Issue one ACPI method call via /proc/acpi/call and return its string
 * result in `out` (NUL-terminated, trailing NULs/newlines trimmed).
 * Returns 0 on success, -1 on error. The method string is a fixed
 * compile-time constant at every call site — never attacker-controlled. */
static int acpi_call(const char *method, char *out, size_t out_sz) {
    int fd = open(ACPI_CALL_PATH, O_RDWR | O_CLOEXEC);
    if (fd < 0) return -1;
    ssize_t w = write(fd, method, strlen(method));
    if (w < 0) { close(fd); return -1; }
    /* /proc/acpi/call returns the result on read after the write. */
    lseek(fd, 0, SEEK_SET);
    ssize_t n = read(fd, out, out_sz - 1);
    close(fd);
    if (n < 0) return -1;
    out[n] = '\0';
    /* Trim trailing NULs / whitespace (the interface pads with '\0'). */
    while (n > 0 && (out[n - 1] == '\0' || out[n - 1] == '\n' ||
                     out[n - 1] == '\r' || out[n - 1] == ' ' || out[n - 1] == '\t')) {
        out[--n] = '\0';
    }
    return 0;
}

/* Parse an ACPI-call result like "0x1234" or "1234" into a u64.
 * Returns 0 on success. */
static int parse_acpi_u64(const char *s, unsigned long long *val) {
    while (*s == ' ' || *s == '\t') s++;
    char *end = NULL;
    errno = 0;
    unsigned long long v = strtoull(s, &end, 0); /* base 0: honours 0x prefix */
    if (errno != 0 || end == s) return -1;
    *val = v;
    return 0;
}

int main(int argc, char **argv) {
    if (geteuid() != 0) {
        fprintf(stderr, "HATA: nvctgp must run as root\n");
        return 1;
    }
    if (argc != 2) {
        fprintf(stderr, "kullanim: nvctgp <watt %d-%d>\n", MIN_W, MAX_W);
        return 1;
    }

    /* Parse + clamp the watt argument BEFORE touching any hardware.
     * strtol rejects non-numeric junk (unlike the shell's arithmetic
     * expansion, which would silently treat garbage as 0). */
    char *end = NULL;
    errno = 0;
    long w = strtol(argv[1], &end, 10);
    if (errno != 0 || end == argv[1] || *end != '\0') {
        fprintf(stderr, "HATA: gecersiz watt degeri: %s\n", argv[1]);
        return 1;
    }
    if (w < BASE_W) w = BASE_W;
    if (w > MAX_W)  w = MAX_W;

    long units_signed = (w - BASE_W) * UNITS_PER_W;
    if (units_signed < 0) units_signed = 0;
    uint16_t units = (uint16_t)(units_signed & 0xFFFF);

    /* Read the EC table base via \WS09. */
    char resp[256];
    if (acpi_call("\\WS09", resp, sizeof(resp)) != 0) {
        fprintf(stderr, "HATA: \\WS09 cagrisi basarisiz (acpi_call modulu yuklu mu?)\n");
        return 1;
    }
    unsigned long long base = 0;
    if (parse_acpi_u64(resp, &base) != 0) {
        fprintf(stderr, "HATA: \\WS09 sonucu ayristirilamadi: '%s'\n", resp);
        return 1;
    }
    if (base == 0) {
        fprintf(stderr, "HATA: WS09 base=0 (tablo dolu degil)\n");
        return 1;
    }

    unsigned long long addr = base + EC_OFFSET;

    /* Bounds-check the physical address before writing. The shell
     * version had NO such check — this is the key hardening. */
    if (addr < ADDR_MIN || (addr + sizeof(units)) > ADDR_MAX) {
        fprintf(stderr, "HATA: hesaplanan adres pencere disinda: 0x%llX\n", addr);
        return 1;
    }

    /* Open /dev/mem and write exactly 2 bytes (16-bit LE), then read
     * back to verify — identical to the original heredoc. */
    int fd = open(DEVMEM_PATH, O_RDWR | O_CLOEXEC | O_SYNC);
    if (fd < 0) {
        fprintf(stderr, "HATA: /dev/mem acilamadi: %s\n", strerror(errno));
        return 1;
    }

    unsigned char wbuf[2] = { (unsigned char)(units & 0xFF),
                              (unsigned char)((units >> 8) & 0xFF) };
    ssize_t wn = pwrite(fd, wbuf, sizeof(wbuf), (off_t)addr);
    if (wn != (ssize_t)sizeof(wbuf)) {
        fprintf(stderr, "HATA: pwrite basarisiz: %s\n", strerror(errno));
        close(fd);
        return 1;
    }

    unsigned char rbuf[2] = { 0, 0 };
    ssize_t rn = pread(fd, rbuf, sizeof(rbuf), (off_t)addr);
    close(fd);
    if (rn != (ssize_t)sizeof(rbuf)) {
        fprintf(stderr, "HATA: pread (readback) basarisiz: %s\n", strerror(errno));
        return 1;
    }
    uint16_t got = (uint16_t)(rbuf[0] | (rbuf[1] << 8));

    /* Poke the driver to re-read the table (\WS1D), same as the script.
     * Best-effort: a failure here doesn't undo the write we verified. */
    char poke[256];
    acpi_call("\\WS1D", poke, sizeof(poke));

    if (got == units) {
        printf("OK: cTGP=%ldW (units=%u @ 0x%llX)\n", w, units, addr);
        return 0;
    } else {
        printf("UYARI: readback tutmadi (wrote=%u got=%u @ 0x%llX)\n",
               units, got, addr);
        return 2;
    }
}
