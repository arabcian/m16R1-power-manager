/*
 * ryzenadj-helper.c
 * ─────────────────────────────────────────────────────────────────────
 * A small, statically-auditable C replacement for the THREE root_helper
 * ops that are simple enough to gain something from it:
 *
 *   - read_gaming_status   (read a handful of sysctl/sysfs keys)
 *   - apply_gaming_and_pci (write a handful of sysctl/sysfs keys, run
 *                           three fixed setpci invocations)
 *   - run_nvctgp           (run one fixed binary with one integer arg)
 *
 * Every other op (profile apply, GPU curve, cgroup isolation, boot
 * defaults, nvcurve profile management, ...) is intentionally left in
 * root_helper.py. Most of those either need nested/variable-shape JSON
 * (profile cfg dicts) that's simpler to keep in Python, or — in the GPU
 * curve case — just shell out to `python3 -m nvcurve ...` themselves, so
 * rewriting *that* wrapper in C would only add a second layer of
 * indirection in front of the same Python code, not remove it.
 *
 * Design goals, in order:
 *   1. Small enough to read top-to-bottom in one sitting.
 *   2. No third-party dependencies (see json_min.c).
 *   3. Same whitelist discipline as root_helper.py: fixed op set, no
 *      shell=True equivalent (execv with argv arrays only), the same
 *      path/value character-class whitelist before touching any sysctl
 *      or sysfs key.
 *   4. Same stdin-JSON-in / stdout-JSON-out contract, so the GUI's
 *      _run_root_helper_command()/_call_root_helper() parsing code
 *      doesn't need to change based on which binary answered.
 *
 * Build: see Makefile. Install: root:root 0700 next to root_helper.py,
 * invoked via its own polkit action (see com.ryzenadj.gui.policy).
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/wait.h>
#include <sys/types.h>
#include <errno.h>

#include "json_min.h"

#define MAX_STDIN_BYTES   (256 * 1024)
#define MAX_OUT_BYTES      (64 * 1024)
#define TUNABLE_MAX_LEN     256

/* SECURITY: fixed, server-side table of the only sysctl/sysfs keys
 * apply_gaming_and_pci / read_gaming_status are allowed to touch. The
 * first version of this file took `path`/`type` straight from the JSON
 * payload (only checked against a permissive charset) — that is the
 * exact same class of bug found and fixed in root_helper.py's
 * gaming_schema handling: since this binary runs as root, letting the
 * caller choose the path meant any string matching
 * is_safe_tunable_token() (e.g. /proc/sys/kernel/yama/ptrace_scope,
 * /proc/sys/kernel/kptr_restrict) could be written. Now the client only
 * ever supplies a symbolic KEY; the real path/type is looked up here
 * and nowhere else. Keep this table in sync with root_helper.py's
 * GAMING_TUNABLES / THP_TUNABLES. */
typedef struct {
    const char *key;
    const char *path;
    int is_sysctl; /* 1 = sysctl (path is dotted, resolved via /proc/sys), 0 = file */
} TunableDef;

static const TunableDef GAMING_TUNABLES[] = {
    { "vm.compaction_proactiveness",       "vm.compaction_proactiveness",       1 },
    { "vm.watermark_boost_factor",         "vm.watermark_boost_factor",         1 },
    { "vm.min_free_kbytes",                "vm.min_free_kbytes",                1 },
    { "vm.watermark_scale_factor",         "vm.watermark_scale_factor",         1 },
    { "vm.swappiness",                     "vm.swappiness",                     1 },
    { "vm.zone_reclaim_mode",              "vm.zone_reclaim_mode",              1 },
    { "vm.page_lock_unfairness",           "vm.page_lock_unfairness",           1 },
    { "kernel.sched_child_runs_first",     "kernel.sched_child_runs_first",     1 },
    { "kernel.sched_autogroup_enabled",    "kernel.sched_autogroup_enabled",    1 },
    { "kernel.sched_cfs_bandwidth_slice_us", "kernel.sched_cfs_bandwidth_slice_us", 1 },
    { "lru_gen",               "/sys/kernel/mm/lru_gen/enabled",             0 },
    { "sched_min_base_slice",  "/sys/kernel/debug/sched/min_base_slice_ns",  0 },
    { "sched_migration_cost",  "/sys/kernel/debug/sched/migration_cost_ns",  0 },
    { "sched_nr_migrate",      "/sys/kernel/debug/sched/nr_migrate",         0 },
};
#define N_GAMING_TUNABLES (sizeof(GAMING_TUNABLES) / sizeof(GAMING_TUNABLES[0]))

static const TunableDef THP_TUNABLES[] = {
    { "thp_enabled", "/sys/kernel/mm/transparent_hugepage/enabled",      0 },
    { "thp_defrag",  "/sys/kernel/mm/transparent_hugepage/defrag",       0 },
    { "thp_shmem",   "/sys/kernel/mm/transparent_hugepage/shmem_enabled", 0 },
};
#define N_THP_TUNABLES (sizeof(THP_TUNABLES) / sizeof(THP_TUNABLES[0]))

static const TunableDef *lookup_tunable(const char *key) {
    for (size_t i = 0; i < N_GAMING_TUNABLES; i++)
        if (strcmp(GAMING_TUNABLES[i].key, key) == 0) return &GAMING_TUNABLES[i];
    for (size_t i = 0; i < N_THP_TUNABLES; i++)
        if (strcmp(THP_TUNABLES[i].key, key) == 0) return &THP_TUNABLES[i];
    return NULL;
}

/* ── output buffer: we build the JSON reply by hand (it's always a flat
 * {"ok":bool, "message"/"error":str, [values:{...}]} shape) ─────────── */

typedef struct {
    char *buf;
    size_t len, cap;
} OutBuf;

static void ob_init(OutBuf *o) { o->cap = 4096; o->len = 0; o->buf = malloc(o->cap); o->buf[0] = '\0'; }

static void ob_ensure(OutBuf *o, size_t extra) {
    if (o->len + extra + 1 > o->cap) {
        while (o->len + extra + 1 > o->cap) o->cap *= 2;
        o->buf = realloc(o->buf, o->cap);
    }
}

static void ob_raw(OutBuf *o, const char *s) {
    size_t n = strlen(s);
    ob_ensure(o, n);
    memcpy(o->buf + o->len, s, n);
    o->len += n;
    o->buf[o->len] = '\0';
}

/* Appends `s` as a JSON string literal (with quotes + escaping). */
static void ob_json_string(OutBuf *o, const char *s) {
    ob_raw(o, "\"");
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        switch (*p) {
            case '"':  ob_raw(o, "\\\""); break;
            case '\\': ob_raw(o, "\\\\"); break;
            case '\n': ob_raw(o, "\\n");  break;
            case '\r': ob_raw(o, "\\r");  break;
            case '\t': ob_raw(o, "\\t");  break;
            default:
                if (*p < 0x20) {
                    char tmp[8];
                    snprintf(tmp, sizeof(tmp), "\\u%04x", *p);
                    ob_raw(o, tmp);
                } else {
                    char tmp[2] = { (char)*p, 0 };
                    ob_raw(o, tmp);
                }
        }
    }
    ob_raw(o, "\"");
}

/* ── string log accumulator, used to build a multi-line "message" ────── */

typedef struct {
    char *buf;
    size_t len, cap;
} LogBuf;

static void lb_init(LogBuf *l) { l->cap = 2048; l->len = 0; l->buf = malloc(l->cap); l->buf[0] = '\0'; }

static void lb_line(LogBuf *l, const char *line) {
    size_t need = strlen(line) + 2;
    if (l->len + need + 1 > l->cap) {
        while (l->len + need + 1 > l->cap) l->cap *= 2;
        l->buf = realloc(l->buf, l->cap);
    }
    if (l->len > 0) { l->buf[l->len++] = '\n'; }
    memcpy(l->buf + l->len, line, strlen(line));
    l->len += strlen(line);
    l->buf[l->len] = '\0';
}

/* ── whitelist: same character class as root_helper.py's
 * _TUNABLE_SAFE_RE = ^[A-Za-z0-9._/-]{1,256}$ ──────────────────────── */

static int is_safe_tunable_token(const char *s) {
    size_t n = strlen(s);
    if (n < 1 || n > TUNABLE_MAX_LEN) return 0;
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if (!(isalnum(c) || c == '.' || c == '_' || c == '/' || c == '-')) return 0;
    }
    return 1;
}

/* Rejects ".." path-traversal segments in a sysctl key once dots have
 * been turned into slashes (mirrors root_helper.py's check). */
static int has_dotdot_segment(const char *rel) {
    const char *p = rel;
    while (p) {
        const char *slash = strchr(p, '/');
        size_t seg_len = slash ? (size_t)(slash - p) : strlen(p);
        if (seg_len == 2 && p[0] == '.' && p[1] == '.') return 1;
        p = slash ? slash + 1 : NULL;
    }
    return 0;
}

/* Converts a dotted sysctl key ("vm.swappiness") into a /proc/sys path.
 * Returns a malloc'd string, or NULL if the key is unsafe. */
static char *sysctl_to_proc_path(const char *key) {
    if (!is_safe_tunable_token(key)) return NULL;
    char *rel = strdup(key);
    for (char *p = rel; *p; p++) if (*p == '.') *p = '/';
    if (rel[0] == '/' || has_dotdot_segment(rel)) { free(rel); return NULL; }
    size_t n = strlen(rel) + strlen("/proc/sys/") + 1;
    char *out = malloc(n);
    snprintf(out, n, "/proc/sys/%s", rel);
    free(rel);
    return out;
}

/* Writes `value` to `path` (already resolved to an absolute filesystem
 * path). Returns 0 on success, -1 on failure (errno set). */
static int write_tunable_file(const char *path, const char *value) {
    int fd = open(path, O_WRONLY | O_TRUNC);
    if (fd < 0) return -1;
    size_t n = strlen(value);
    ssize_t w = write(fd, value, n);
    int saved_errno = errno;
    close(fd);
    if (w < 0 || (size_t)w != n) { errno = saved_errno ? saved_errno : EIO; return -1; }
    return 0;
}

/* Reads a file into a fixed-size buffer, trims trailing whitespace.
 * Returns 0 on success (out filled, always NUL-terminated), -1 on error. */
static int read_file_trimmed(const char *path, char *out, size_t out_sz) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    ssize_t n = read(fd, out, out_sz - 1);
    int saved_errno = errno;
    close(fd);
    if (n < 0) { errno = saved_errno; return -1; }
    out[n] = '\0';
    while (n > 0 && (out[n - 1] == '\n' || out[n - 1] == '\r' || out[n - 1] == ' ' || out[n - 1] == '\t')) {
        out[--n] = '\0';
    }
    return 0;
}

static int path_exists(const char *path) {
    return access(path, F_OK) == 0;
}

/* ── child process execution: fixed argv, no shell, stdout+stderr
 * combined into one pipe, SIGALRM-based timeout ────────────────────── */

static volatile sig_atomic_t g_timed_out = 0;
static void on_alarm(int sig) { (void)sig; g_timed_out = 1; }

/* Runs `path` with `argv` (NULL-terminated, argv[0] conventionally ==
 * path). Captures combined stdout+stderr into *out (malloc'd, caller
 * frees). Returns the child's exit code (0-255), or -1 if the process
 * could not be started/timed out (*out will contain a diagnostic). */
static int run_argv(const char *path, char *const argv[], int timeout_sec, char **out) {
    int pipefd[2];
    if (pipe(pipefd) != 0) { *out = strdup("pipe() failed"); return -1; }

    pid_t pid = fork();
    if (pid < 0) {
        close(pipefd[0]); close(pipefd[1]);
        *out = strdup("fork() failed");
        return -1;
    }
    if (pid == 0) {
        /* child */
        close(pipefd[0]);
        dup2(pipefd[1], STDOUT_FILENO);
        dup2(pipefd[1], STDERR_FILENO);
        close(pipefd[1]);
        execv(path, argv);
        /* execv only returns on failure */
        _exit(127);
    }

    /* parent */
    close(pipefd[1]);

    struct sigaction sa = {0}, old_sa;
    sa.sa_handler = on_alarm;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGALRM, &sa, &old_sa);
    g_timed_out = 0;
    alarm((unsigned)timeout_sec);

    size_t cap = 4096, len = 0;
    char *buf = malloc(cap);
    while (1) {
        if (len + 1 >= cap) { cap *= 2; buf = realloc(buf, cap); }
        ssize_t n = read(pipefd[0], buf + len, cap - len - 1);
        if (n > 0) { len += (size_t)n; continue; }
        if (n == 0) break; /* EOF: child closed its end */
        if (errno == EINTR) {
            if (g_timed_out) break;
            continue;
        }
        break;
    }
    buf[len] = '\0';
    alarm(0);
    sigaction(SIGALRM, &old_sa, NULL);
    close(pipefd[0]);

    int status;
    if (g_timed_out) {
        kill(pid, SIGKILL);
        waitpid(pid, &status, 0);
        free(buf);
        *out = strdup("timed out");
        return -1;
    }
    waitpid(pid, &status, 0);
    *out = buf;
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    return -1;
}

/* ── op: read_gaming_status ──────────────────────────────────────────
 * params: {"keys": [str, ...]}  — symbolic keys only; path/type are
 * looked up from the fixed GAMING_TUNABLES/THP_TUNABLES table above,
 * never taken from the caller (see the SECURITY comment on that table).
 * reply:  {"ok": true, "values": {key: str}}
 */
static void op_read_gaming_status(const JsonValue *params, OutBuf *ob) {
    const JsonValue *keys = json_obj_get(params, "keys");
    if (!keys || keys->type != JSON_ARR) {
        ob_raw(ob, "{\"ok\":false,\"error\":\"Missing keys array\"}");
        return;
    }

    ob_raw(ob, "{\"ok\":true,\"values\":{");
    for (size_t i = 0; i < keys->u.array.count; i++) {
        const JsonValue *item = keys->u.array.items[i];
        const char *key = (item && item->type == JSON_STR) ? item->u.string : NULL;
        char valbuf[4096];
        const char *result;

        if (i > 0) ob_raw(ob, ",");
        ob_json_string(ob, key ? key : "");
        ob_raw(ob, ":");

        const TunableDef *def = key ? lookup_tunable(key) : NULL;
        if (!def) { ob_json_string(ob, "(unknown key)"); continue; }

        if (def->is_sysctl) {
            char *proc_path = sysctl_to_proc_path(def->path);
            if (!proc_path) { ob_json_string(ob, "(no sysctl)"); continue; }
            if (!path_exists(proc_path)) { ob_json_string(ob, "(no sysctl)"); free(proc_path); continue; }
            if (read_file_trimmed(proc_path, valbuf, sizeof(valbuf)) != 0) {
                ob_json_string(ob, errno == EACCES ? "(perm denied)" : "(err: read failed)");
                free(proc_path);
                continue;
            }
            free(proc_path);
            result = valbuf;
        } else {
            if (!path_exists(def->path)) { ob_json_string(ob, "(no file)"); continue; }
            if (read_file_trimmed(def->path, valbuf, sizeof(valbuf)) != 0) {
                ob_json_string(ob, errno == EACCES ? "(perm denied)" : "(err: read failed)");
                continue;
            }
            /* THP-style files look like "always [madvise] never" — pull
             * out the bracketed selection, matching root_helper.py. */
            char *lb = strchr(valbuf, '[');
            char *rb = lb ? strchr(lb, ']') : NULL;
            if (lb && rb && rb > lb) {
                *rb = '\0';
                result = lb + 1;
            } else {
                result = valbuf[0] ? valbuf : "(empty)";
            }
        }
        ob_json_string(ob, result);
    }
    ob_raw(ob, "}}");
}

/* ── op: apply_gaming_and_pci ─────────────────────────────────────────
 * params: {"gaming": {key: value_str}, "thp": {key: value_str}}
 * — symbolic keys only; path/type come from the fixed GAMING_TUNABLES /
 * THP_TUNABLES table above, never from the caller (see the SECURITY
 * comment on that table). This closes the same class of bug found and
 * fixed in root_helper.py's op_apply_power_profile (client-supplied
 * gaming_schema letting a write land at an arbitrary path).
 * reply:  {"ok": true, "message": "...multi-line log..."}
 */
static void op_apply_gaming_and_pci(const JsonValue *params, OutBuf *ob) {
    LogBuf lg; lb_init(&lg);
    int had_error = 0;

    const JsonValue *gaming = json_obj_get(params, "gaming");
    if (gaming && gaming->type == JSON_OBJ) {
        for (size_t i = 0; i < gaming->u.object.count; i++) {
            const char *key = gaming->u.object.items[i].key;
            const JsonValue *val_node = gaming->u.object.items[i].value;
            const char *value = (val_node && val_node->type == JSON_STR) ? val_node->u.string : NULL;
            char line[512];

            const TunableDef *def = lookup_tunable(key);
            if (!def) {
                had_error = 1;
                snprintf(line, sizeof(line), "WARNING: %s: unknown gaming setting, skipped", key);
                lb_line(&lg, line);
                continue;
            }
            if (!value || !is_safe_tunable_token(value)) {
                had_error = 1;
                snprintf(line, sizeof(line), "WARNING: %s: unsafe value, skipped", key);
                lb_line(&lg, line);
                continue;
            }

            int ok;
            if (def->is_sysctl) {
                char *proc_path = sysctl_to_proc_path(def->path);
                ok = proc_path && write_tunable_file(proc_path, value) == 0;
                free(proc_path);
            } else {
                ok = write_tunable_file(def->path, value) == 0;
            }

            if (ok) {
                snprintf(line, sizeof(line), "OK: %s -> %s", def->path, value);
            } else {
                had_error = 1;
                snprintf(line, sizeof(line), "WARNING: %s (%s): %s", key, def->path, strerror(errno));
            }
            lb_line(&lg, line);
        }
    }

    const JsonValue *thp = json_obj_get(params, "thp");
    if (thp && thp->type == JSON_OBJ) {
        for (size_t i = 0; i < thp->u.object.count; i++) {
            const char *key = thp->u.object.items[i].key;
            const JsonValue *val_node = thp->u.object.items[i].value;
            const char *value = (val_node && val_node->type == JSON_STR) ? val_node->u.string : NULL;
            char line[512];

            const TunableDef *def = lookup_tunable(key);
            if (!def) {
                had_error = 1;
                snprintf(line, sizeof(line), "WARNING: %s: unknown THP setting, skipped", key);
                lb_line(&lg, line);
                continue;
            }
            if (!value || !is_safe_tunable_token(value)) {
                had_error = 1;
                snprintf(line, sizeof(line), "WARNING: %s: unsafe value, skipped", key);
                lb_line(&lg, line);
                continue;
            }
            if (write_tunable_file(def->path, value) == 0) {
                snprintf(line, sizeof(line), "OK: %s -> %s", def->path, value);
            } else {
                had_error = 1;
                snprintf(line, sizeof(line), "WARNING: %s (%s): %s", key, def->path, strerror(errno));
            }
            lb_line(&lg, line);
        }
    }

    /* Fixed, non-user-controlled argv lists — nothing here comes from
     * the GUI payload, so there is nothing to interpolate. */
    static const char *pci_argsets[3][5] = {
        { "setpci", "-v", "-s", "*:*", "latency_timer=20" },
        { "setpci", "-v", "-s", "0:0", "latency_timer=0" },
        { "setpci", "-v", "-d", "*:*:04xx", "latency_timer=80" },
    };
    const char *setpci_path = "/usr/sbin/setpci";
    if (!path_exists(setpci_path)) setpci_path = "/usr/bin/setpci";
    if (!path_exists(setpci_path)) {
        lb_line(&lg, "WARNING: setpci not found, skipping PCI latency tuning");
    } else {
        for (int i = 0; i < 3; i++) {
            char *argv[6];
            for (int j = 0; j < 5; j++) argv[j] = (char *)pci_argsets[i][j];
            argv[0] = (char *)setpci_path;
            argv[5] = NULL;
            char *out = NULL;
            int rc = run_argv(setpci_path, argv, 10, &out);
            char line[600];
            if (rc == 0) {
                if (out && out[0]) {
                    char trimmed[512];
                    snprintf(trimmed, sizeof(trimmed), "%s", out);
                    /* strip trailing newline for a clean log line */
                    size_t tl = strlen(trimmed);
                    while (tl > 0 && (trimmed[tl-1] == '\n' || trimmed[tl-1] == '\r')) trimmed[--tl] = '\0';
                    if (trimmed[0]) lb_line(&lg, trimmed);
                }
            } else {
                had_error = 1;
                snprintf(line, sizeof(line), "WARNING: setpci %s %s %s %s: %s",
                         pci_argsets[i][1], pci_argsets[i][2], pci_argsets[i][3], pci_argsets[i][4],
                         out ? out : "failed");
                lb_line(&lg, line);
            }
            free(out);
        }
    }

    lb_line(&lg, had_error ? "All settings applied (warnings may have occurred)."
                           : "All settings applied successfully.");

    ob_raw(ob, "{\"ok\":true,\"message\":");
    ob_json_string(ob, lg.buf);
    ob_raw(ob, "}");
    free(lg.buf);
}

/* ── op: run_nvctgp ───────────────────────────────────────────────────
 * params: {"watts": int}
 * reply:  {"ok": true, "message": "..."} or {"ok": false, "error": "..."}
 */
#ifndef NVCTGP_PATH
#define NVCTGP_PATH "/usr/local/sbin/nvctgp"
#endif

static void op_run_nvctgp(const JsonValue *params, OutBuf *ob) {
    long watts = json_get_int(params, "watts", -1);
    if (watts < 1 || watts > 300) {
        ob_raw(ob, "{\"ok\":false,\"error\":\"watts out of expected range\"}");
        return;
    }
    const char *nvctgp_path = NVCTGP_PATH;
    if (!path_exists(nvctgp_path)) {
        ob_raw(ob, "{\"ok\":false,\"error\":\"");
        ob_raw(ob, nvctgp_path);
        ob_raw(ob, " not found\"}");
        return;
    }
    char wattstr[16];
    snprintf(wattstr, sizeof(wattstr), "%ld", watts);
    char *argv[3] = { (char *)nvctgp_path, wattstr, NULL };
    char *out = NULL;
    int rc = run_argv(nvctgp_path, argv, 15, &out);

    if (rc != 0) {
        ob_raw(ob, "{\"ok\":false,\"error\":");
        char msg[768];
        snprintf(msg, sizeof(msg), "nvctgp error (code %d): %s", rc, out ? out : "unknown");
        ob_json_string(ob, msg);
        ob_raw(ob, "}");
    } else {
        ob_raw(ob, "{\"ok\":true,\"message\":");
        ob_json_string(ob, out ? out : "");
        ob_raw(ob, "}");
    }
    free(out);
}

int main(void) {
    if (geteuid() != 0) {
        printf("{\"ok\":false,\"error\":\"ryzenadj-helper must run as root\"}\n");
        return 1;
    }

    /* Read all of stdin (bounded) */
    size_t cap = 8192, len = 0;
    char *input = malloc(cap);
    while (1) {
        if (len + 1 >= cap) {
            if (cap >= MAX_STDIN_BYTES) { printf("{\"ok\":false,\"error\":\"payload too large\"}\n"); return 1; }
            cap *= 2;
            input = realloc(input, cap);
        }
        ssize_t n = read(STDIN_FILENO, input + len, cap - len - 1);
        if (n < 0) { printf("{\"ok\":false,\"error\":\"stdin read failed\"}\n"); return 1; }
        if (n == 0) break;
        len += (size_t)n;
    }
    input[len] = '\0';

    char *err = NULL;
    JsonValue *root = json_parse(input, &err);
    free(input);
    if (!root) {
        printf("{\"ok\":false,\"error\":\"Invalid JSON on stdin: %s\"}\n", err ? err : "parse error");
        free(err);
        return 1;
    }

    const char *op = json_get_str(root, "op", NULL);
    OutBuf ob; ob_init(&ob);

    if (!op) {
        ob_raw(&ob, "{\"ok\":false,\"error\":\"Missing op\"}");
    } else if (strcmp(op, "read_gaming_status") == 0) {
        op_read_gaming_status(root, &ob);
    } else if (strcmp(op, "apply_gaming_and_pci") == 0) {
        op_apply_gaming_and_pci(root, &ob);
    } else if (strcmp(op, "run_nvctgp") == 0) {
        op_run_nvctgp(root, &ob);
    } else {
        ob_raw(&ob, "{\"ok\":false,\"error\":\"Unknown or disallowed op\"}");
    }

    json_free(root);
    printf("%s\n", ob.buf);

    int ok = strncmp(ob.buf, "{\"ok\":true", 10) == 0;
    free(ob.buf);
    return ok ? 0 : 1;
}
