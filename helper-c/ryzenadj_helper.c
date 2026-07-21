/*
 * ryzenadj-helper.c
 * ─────────────────────────────────────────────────────────────────────
 * A small, statically-auditable C replacement for the root_helper ops
 * that are mechanical enough to gain something from it:
 *
 *   - read_gaming_status     (read a handful of sysctl/sysfs keys)
 *   - apply_gaming_and_pci   (write a handful of sysctl/sysfs keys, run
 *                             three fixed setpci invocations)
 *   - run_nvctgp             (run one fixed binary with one integer arg)
 *   - set_cpu_epp_governor   (per-CCD governor/EPP sysfs writes)
 *   - set_cpu_boost          (one fixed sysfs boolean)
 *   - capture_boot_defaults  (snapshot the fixed tunable table to /run)
 *   - restore_boot_defaults  (write that snapshot back) *
 * Ops that stay in root_helper.py: profile apply, GPU curve/memlock,
 * nvcurve profile management, activation-script writing. Those either
 * need nested/variable-shape JSON (profile cfg dicts) or just shell out
 * to `python3 -m nvcurve` themselves, so rewriting the wrapper in C
 * would only add a layer of indirection in front of the same Python.
 *
 * DELIBERATELY NOT HERE: apply_cpu_isolation / revert_cpu_isolation.
 * The cgroup v2 CCX-isolation feature is disabled in this project — its
 * UI tab is gone and nothing calls the ops. It was disabled because
 * moving processes out of elogind's cgroup hierarchy breaks elogind's
 * session tracking, which in turn leaves the system unable to reboot
 * cleanly. The op's CRITICAL_SERVICES skip list matches on /proc/<pid>/comm,
 * which cannot fix this even in principle: elogind tracks sessions BY
 * CGROUP, and session processes have arbitrary comm names, so a
 * name-based list will always miss some of them. Porting it here would
 * only have made a broken operation faster.
 *
 * Design goals, in order:
 *   1. Small enough to read top-to-bottom in one sitting.
 *   2. No third-party dependencies (see json_min.c).
 *   3. Same whitelist discipline as root_helper.py: fixed op set, no
 *      shell=True equivalent (execv with argv arrays only), the same
 *      path/value character-class whitelist before touching any sysctl
 *      or sysfs key. Nothing the caller sends is ever used as a path.
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
#include <stdarg.h>
#include <string.h>
#include <ctype.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <sys/types.h>
#include <errno.h>

#include "json_min.h"

#define MAX_STDIN_BYTES   (256 * 1024)
#define MAX_OUT_BYTES      (64 * 1024)
#define TUNABLE_MAX_LEN     256
#define MAX_CPU_INDEX      1023

/* SECURITY: fixed, server-side table of the only sysctl/sysfs keys
 * apply_gaming_and_pci / read_gaming_status / boot-defaults capture and
 * restore are allowed to touch. The first version of this file took
 * `path`/`type` straight from the JSON payload (only checked against a
 * permissive charset) — that is the exact same class of bug found and
 * fixed in root_helper.py's gaming_schema handling: since this binary
 * runs as root, letting the caller choose the path meant any string
 * matching is_safe_tunable_token() (e.g.
 * /proc/sys/kernel/yama/ptrace_scope, /proc/sys/kernel/kptr_restrict)
 * could be written. Now the client only ever supplies a symbolic KEY;
 * the real path/type is looked up here and nowhere else. Keep this
 * table in sync with root_helper.py's GAMING_TUNABLES / THP_TUNABLES. */
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
    if (!key) return NULL;
    for (size_t i = 0; i < N_GAMING_TUNABLES; i++)
        if (strcmp(GAMING_TUNABLES[i].key, key) == 0) return &GAMING_TUNABLES[i];
    for (size_t i = 0; i < N_THP_TUNABLES; i++)
        if (strcmp(THP_TUNABLES[i].key, key) == 0) return &THP_TUNABLES[i];
    return NULL;
}

/* ── allocation helpers: this binary runs as root; a silent NULL deref
 * on OOM is a crash in a privileged context, so every allocation goes
 * through these and dies with a clean JSON error instead. ─────────── */

static void die_oom(void) {
    /* Written directly to fd 1: the OutBuf we'd normally use may itself
     * be the allocation that just failed. */
    static const char msg[] = "{\"ok\":false,\"error\":\"out of memory\"}\n";
    ssize_t r = write(STDOUT_FILENO, msg, sizeof(msg) - 1);
    (void)r;
    _exit(1);
}

static void *xmalloc(size_t n) {
    void *p = malloc(n);
    if (!p) die_oom();
    return p;
}

static void *xrealloc(void *p, size_t n) {
    void *q = realloc(p, n);
    if (!q) die_oom();
    return q;
}

static char *xstrdup(const char *s) {
    char *p = strdup(s);
    if (!p) die_oom();
    return p;
}

/* ── output buffer: we build the JSON reply by hand (it's always a flat
 * {"ok":bool, "message"/"error":str, [values:{...}]} shape) ─────────── */

typedef struct {
    char *buf;
    size_t len, cap;
} OutBuf;

static void ob_init(OutBuf *o) { o->cap = 4096; o->len = 0; o->buf = xmalloc(o->cap); o->buf[0] = '\0'; }

static void ob_ensure(OutBuf *o, size_t extra) {
    if (o->len + extra + 1 > o->cap) {
        while (o->len + extra + 1 > o->cap) o->cap *= 2;
        o->buf = xrealloc(o->buf, o->cap);
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
    if (!s) s = "";
    ob_raw(o, "\"");
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        switch (*p) {
            case '"':  ob_raw(o, "\\\""); break;
            case '\\': ob_raw(o, "\\\\"); break;
            case '\n': ob_raw(o, "\\n");  break;
            case '\r': ob_raw(o, "\\r");  break;
            case '\t': ob_raw(o, "\\t");  break;
            default:
                if (*p < 0x20 || *p == 0x7f) {
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

static void ob_error(OutBuf *o, const char *msg) {
    ob_raw(o, "{\"ok\":false,\"error\":");
    ob_json_string(o, msg);
    ob_raw(o, "}");
}

static void ob_ok_message(OutBuf *o, const char *msg) {
    ob_raw(o, "{\"ok\":true,\"message\":");
    ob_json_string(o, msg);
    ob_raw(o, "}");
}

/* ── string log accumulator, used to build a multi-line "message" ──────
 * Bounded at MAX_OUT_BYTES: apply_cpu_isolation can touch hundreds of
 * PIDs, and an unbounded reply would be both a memory and a
 * GUI-log-flooding hazard. Once the cap is hit we stop appending and
 * note the truncation once. */

typedef struct {
    char *buf;
    size_t len, cap;
    int truncated;
} LogBuf;

static void lb_init(LogBuf *l) { l->cap = 2048; l->len = 0; l->truncated = 0; l->buf = xmalloc(l->cap); l->buf[0] = '\0'; }

static void lb_line(LogBuf *l, const char *line) {
    size_t add = strlen(line);
    if (l->len + add + 2 > MAX_OUT_BYTES) {
        if (!l->truncated) {
            l->truncated = 1;
            const char *t = "\n... (log truncated)";
            size_t tn = strlen(t);
            if (l->len + tn + 1 > l->cap) { l->cap = l->len + tn + 1; l->buf = xrealloc(l->buf, l->cap); }
            memcpy(l->buf + l->len, t, tn);
            l->len += tn;
            l->buf[l->len] = '\0';
        }
        return;
    }
    size_t need = add + 2;
    if (l->len + need + 1 > l->cap) {
        while (l->len + need + 1 > l->cap) l->cap *= 2;
        l->buf = xrealloc(l->buf, l->cap);
    }
    if (l->len > 0) { l->buf[l->len++] = '\n'; }
    memcpy(l->buf + l->len, line, add);
    l->len += add;
    l->buf[l->len] = '\0';
}

__attribute__((format(printf, 2, 3)))
static void lb_linef(LogBuf *l, const char *fmt, ...) {
    char tmp[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    lb_line(l, tmp);
}

/* ── whitelist: same character class as root_helper.py's
 * _TUNABLE_SAFE_RE = ^[A-Za-z0-9._/-]{1,256}$ ──────────────────────── */

static int is_safe_tunable_token(const char *s) {
    if (!s) return 0;
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
    char *rel = xstrdup(key);
    for (char *p = rel; *p; p++) if (*p == '.') *p = '/';
    if (rel[0] == '/' || has_dotdot_segment(rel)) { free(rel); return NULL; }
    size_t n = strlen(rel) + strlen("/proc/sys/") + 1;
    char *out = xmalloc(n);
    snprintf(out, n, "/proc/sys/%s", rel);
    free(rel);
    return out;
}

/* Resolves a TunableDef to the real filesystem path to open. Returns a
 * malloc'd string (caller frees) or NULL. */
static char *tunable_fs_path(const TunableDef *def) {
    if (!def) return NULL;
    if (def->is_sysctl) return sysctl_to_proc_path(def->path);
    return xstrdup(def->path);
}

/* Writes `value` to `path` (already resolved to an absolute filesystem
 * path). Returns 0 on success, -1 on failure (errno set). */
static int write_tunable_file(const char *path, const char *value) {
    /* O_NOFOLLOW: every path we write here comes from a fixed
     * compiled-in table (never the caller), and all of them are real
     * sysfs/proc files, not symlinks — so if the final component is a
     * symlink, something is wrong and we fail closed rather than
     * following it. O_CLOEXEC so a fork/exec elsewhere can't inherit
     * the descriptor. */
    int fd = open(path, O_WRONLY | O_TRUNC | O_CLOEXEC | O_NOFOLLOW);
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
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
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
    /* pipe2 + O_CLOEXEC so the read end can't leak into any process we
     * exec later; the child re-dups the write end explicitly. */
    if (pipe2(pipefd, O_CLOEXEC) != 0) { *out = xstrdup("pipe() failed"); return -1; }

    pid_t pid = fork();
    if (pid < 0) {
        close(pipefd[0]); close(pipefd[1]);
        *out = xstrdup("fork() failed");
        return -1;
    }
    if (pid == 0) {
        /* child */
        close(pipefd[0]);
        /* dup2 clears O_CLOEXEC on the new descriptors, which is what we
         * want for 1 and 2 specifically. */
        if (dup2(pipefd[1], STDOUT_FILENO) < 0) _exit(127);
        if (dup2(pipefd[1], STDERR_FILENO) < 0) _exit(127);
        close(pipefd[1]);
        /* Reset signal dispositions: we install a SIGALRM handler in the
         * parent, and an inherited handler in a child that then execs is
         * a needless surprise. */
        signal(SIGALRM, SIG_DFL);
        signal(SIGPIPE, SIG_DFL);
        execv(path, argv);
        /* execv only returns on failure */
        _exit(127);
    }

    /* parent */
    close(pipefd[1]);

    struct sigaction sa, old_sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_alarm;
    sigemptyset(&sa.sa_mask);
    /* Deliberately NOT SA_RESTART: we need read() to return EINTR so the
     * timeout can actually break the loop. */
    sa.sa_flags = 0;
    sigaction(SIGALRM, &sa, &old_sa);
    g_timed_out = 0;
    alarm((unsigned)timeout_sec);

    size_t cap = 4096, len = 0;
    char *buf = xmalloc(cap);
    while (1) {
        /* Cap captured child output: a runaway child writing forever
         * would otherwise grow this buffer until the OOM killer settles
         * it. MAX_OUT_BYTES is far more than setpci/nvctgp ever emit. */
        if (len >= MAX_OUT_BYTES) break;
        if (len + 1 >= cap) { cap *= 2; buf = xrealloc(buf, cap); }
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
        /* waitpid can itself be interrupted; loop until it settles so we
         * never leave a zombie behind. */
        while (waitpid(pid, &status, 0) < 0 && errno == EINTR) { }
        free(buf);
        *out = xstrdup("timed out");
        return -1;
    }
    while (waitpid(pid, &status, 0) < 0 && errno == EINTR) { }
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
        ob_error(ob, "Missing keys array");
        return;
    }
    /* Bound the request: the fixed table has a known size, so asking for
     * vastly more keys than exist is either a bug or an attempt to make
     * this loop expensive. */
    if (keys->u.array.count > (N_GAMING_TUNABLES + N_THP_TUNABLES) * 4) {
        ob_error(ob, "too many keys requested");
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

        const TunableDef *def = lookup_tunable(key);
        if (!def) { ob_json_string(ob, "(unknown key)"); continue; }

        char *fs_path = tunable_fs_path(def);
        if (!fs_path) { ob_json_string(ob, def->is_sysctl ? "(no sysctl)" : "(no file)"); continue; }
        if (!path_exists(fs_path)) {
            ob_json_string(ob, def->is_sysctl ? "(no sysctl)" : "(no file)");
            free(fs_path);
            continue;
        }
        if (read_file_trimmed(fs_path, valbuf, sizeof(valbuf)) != 0) {
            ob_json_string(ob, errno == EACCES ? "(perm denied)" : "(err: read failed)");
            free(fs_path);
            continue;
        }
        free(fs_path);

        if (def->is_sysctl) {
            result = valbuf;
        } else {
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

/* Shared by apply_gaming_and_pci and restore_boot_defaults: resolve a
 * symbolic key through the fixed table, validate the value, write it,
 * and log the outcome. Returns 1 on success, 0 on any failure. */
static int apply_one_tunable(const char *key, const char *value, LogBuf *lg, const char *kind_label) {
    const TunableDef *def = lookup_tunable(key);
    if (!def) {
        lb_linef(lg, "WARNING: %s: unknown %s setting, skipped", key ? key : "(null)", kind_label);
        return 0;
    }
    if (!is_safe_tunable_token(value)) {
        lb_linef(lg, "WARNING: %s: unsafe value, skipped", key);
        return 0;
    }
    char *fs_path = tunable_fs_path(def);
    if (!fs_path) {
        lb_linef(lg, "WARNING: %s: path could not be resolved, skipped", key);
        return 0;
    }
    int ok = write_tunable_file(fs_path, value) == 0;
    if (ok) {
        lb_linef(lg, "OK: %s -> %s", def->path, value);
    } else {
        lb_linef(lg, "WARNING: %s (%s): %s", key, def->path, strerror(errno));
    }
    free(fs_path);
    return ok;
}

/* ── op: apply_gaming_and_pci ─────────────────────────────────────────
 * params: {"gaming": {key: value_str}, "thp": {key: value_str}}
 * — symbolic keys only; path/type come from the fixed GAMING_TUNABLES /
 * THP_TUNABLES table above, never from the caller.
 * reply:  {"ok": true, "message": "...multi-line log..."}
 */
static void op_apply_gaming_and_pci(const JsonValue *params, OutBuf *ob) {
    LogBuf lg; lb_init(&lg);
    int had_error = 0;

    const char *sections[2] = { "gaming", "thp" };
    for (int s = 0; s < 2; s++) {
        const JsonValue *sec = json_obj_get(params, sections[s]);
        if (!sec || sec->type != JSON_OBJ) continue;
        for (size_t i = 0; i < sec->u.object.count; i++) {
            const char *key = sec->u.object.items[i].key;
            const JsonValue *val_node = sec->u.object.items[i].value;
            const char *value = (val_node && val_node->type == JSON_STR) ? val_node->u.string : NULL;
            if (!apply_one_tunable(key, value, &lg, sections[s])) had_error = 1;
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
            if (rc == 0) {
                if (out && out[0]) {
                    /* strip trailing newlines for a clean log line */
                    size_t tl = strlen(out);
                    while (tl > 0 && (out[tl-1] == '\n' || out[tl-1] == '\r')) out[--tl] = '\0';
                    if (out[0]) lb_line(&lg, out);
                }
            } else {
                had_error = 1;
                lb_linef(&lg, "WARNING: setpci %s %s %s %s: %s",
                         pci_argsets[i][1], pci_argsets[i][2], pci_argsets[i][3], pci_argsets[i][4],
                         out ? out : "failed");
            }
            free(out);
        }
    }

    lb_line(&lg, had_error ? "All settings applied (warnings may have occurred)."
                           : "All settings applied successfully.");

    ob_ok_message(ob, lg.buf);
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
        ob_error(ob, "watts out of expected range");
        return;
    }
    const char *nvctgp_path = NVCTGP_PATH;
    if (!path_exists(nvctgp_path)) {
        char msg[512];
        snprintf(msg, sizeof(msg), "%s not found", nvctgp_path);
        ob_error(ob, msg);
        return;
    }
    char wattstr[16];
    snprintf(wattstr, sizeof(wattstr), "%ld", watts);
    char *argv[3] = { (char *)nvctgp_path, wattstr, NULL };
    char *out = NULL;
    int rc = run_argv(nvctgp_path, argv, 15, &out);

    if (rc != 0) {
        char msg[768];
        snprintf(msg, sizeof(msg), "nvctgp error (code %d): %s", rc, out ? out : "unknown");
        ob_error(ob, msg);
    } else {
        ob_ok_message(ob, out ? out : "");
    }
    free(out);
}

/* ═══════════════════════════════════════════════════════════════════
 * CPU governor / EPP / boost  (ported from root_helper.py)
 * ═══════════════════════════════════════════════════════════════════
 * These are pure sysfs writes at paths built from a bounded integer CPU
 * index, with fixed value whitelists — there is no free-form path from
 * the client at all, which is exactly why they were worth moving out of
 * Python: no interpreter startup, no subprocess, and the whole trust
 * boundary is these ~80 lines.
 */

static const char *CPU_GOVERNOR_WHITELIST[] = {
    "performance", "powersave", "schedutil", "ondemand", "conservative", NULL
};

/* energy_performance_preference is named strings on most amd-pstate-epp
 * systems, but some kernel/driver combos expose it as a raw 0-255
 * integer instead (0 = most performance-hungry, 255 = most power-saving)
 * — accept both forms, mirroring root_helper.py's _is_valid_epp(). */
static const char *CPU_EPP_NAMED_WHITELIST[] = {
    "performance", "balance_performance", "balance_power", "power", NULL
};

static int in_whitelist(const char *const *list, const char *value) {
    if (!value) return 0;
    for (int i = 0; list[i]; i++) if (strcmp(list[i], value) == 0) return 1;
    return 0;
}

static int is_valid_epp(const char *value) {
    if (!value) return 0;
    if (in_whitelist(CPU_EPP_NAMED_WHITELIST, value)) return 1;
    /* all-digits, 0-255 */
    size_t n = strlen(value);
    if (n == 0 || n > 3) return 0;
    for (size_t i = 0; i < n; i++) if (!isdigit((unsigned char)value[i])) return 0;
    long v = strtol(value, NULL, 10);
    return v >= 0 && v <= 255;
}

/* Extracts a JSON value as a bare string for whitelist checking. Numbers
 * are rendered as integers (the EPP numeric form); anything else yields
 * NULL. Writes into `buf`. */
static const char *scalar_as_string(const JsonValue *v, char *buf, size_t buf_sz) {
    if (!v) return NULL;
    if (v->type == JSON_STR) return v->u.string;
    if (v->type == JSON_NUM) {
        /* Reject non-integral / out-of-range numbers rather than letting
         * a silent (long) truncation decide what gets written. */
        if (v->u.number < 0 || v->u.number > 255) return NULL;
        if (v->u.number != (double)(long)v->u.number) return NULL;
        snprintf(buf, buf_sz, "%ld", (long)v->u.number);
        return buf;
    }
    return NULL; /* JSON_BOOL deliberately excluded: true must not become "1" */
}

/* ── op: set_cpu_epp_governor ─────────────────────────────────────────
 * params: {"cpus": [int, ...], "governor": str?, "epp": str|int?}
 * reply:  {"ok": true, "message": "Applied to N/M CPUs."}
 */
static void op_set_cpu_epp_governor(const JsonValue *params, OutBuf *ob) {
    const JsonValue *cpus = json_obj_get(params, "cpus");
    if (!cpus || cpus->type != JSON_ARR || cpus->u.array.count == 0) {
        ob_error(ob, "cpus must be a non-empty list");
        return;
    }
    if (cpus->u.array.count > MAX_CPU_INDEX) {
        ob_error(ob, "cpus list implausibly large");
        return;
    }

    char gov_buf[64], epp_buf[64];
    const JsonValue *gov_node = json_obj_get(params, "governor");
    const JsonValue *epp_node = json_obj_get(params, "epp");
    /* JSON null is treated the same as absent. */
    if (gov_node && gov_node->type == JSON_NULL) gov_node = NULL;
    if (epp_node && epp_node->type == JSON_NULL) epp_node = NULL;

    const char *governor = gov_node ? scalar_as_string(gov_node, gov_buf, sizeof(gov_buf)) : NULL;
    const char *epp      = epp_node ? scalar_as_string(epp_node, epp_buf, sizeof(epp_buf)) : NULL;

    if (!gov_node && !epp_node) {
        ob_error(ob, "Nothing to set (governor and epp both missing)");
        return;
    }
    if (gov_node && !in_whitelist(CPU_GOVERNOR_WHITELIST, governor)) {
        ob_error(ob, "Invalid governor");
        return;
    }
    if (epp_node && !is_valid_epp(epp)) {
        ob_error(ob, "Invalid epp");
        return;
    }

    LogBuf lg; lb_init(&lg);
    int ok_count = 0, warn_count = 0;

    for (size_t i = 0; i < cpus->u.array.count; i++) {
        const JsonValue *item = cpus->u.array.items[i];
        /* JSON_BOOL is excluded on purpose: in Python bool is an int
         * subclass and `true` would have slipped through as CPU 1;
         * the C type check makes that impossible, but stay explicit. */
        if (!item || item->type != JSON_NUM) {
            warn_count++;
            lb_line(&lg, "skipped invalid cpu index (not a number)");
            continue;
        }
        double d = item->u.number;
        if (d < 0 || d > MAX_CPU_INDEX || d != (double)(long)d) {
            warn_count++;
            lb_linef(&lg, "skipped out-of-range cpu index: %g", d);
            continue;
        }
        long cpu = (long)d;

        char path[256];
        int applied_this_cpu = 1;

        if (governor) {
            snprintf(path, sizeof(path), "/sys/devices/system/cpu/cpu%ld/cpufreq/scaling_governor", cpu);
            if (!path_exists(path)) {
                applied_this_cpu = 0; warn_count++;
                lb_linef(&lg, "cpu%ld: no scaling_governor (offline/missing cpufreq policy?)", cpu);
            } else if (write_tunable_file(path, governor) != 0) {
                applied_this_cpu = 0; warn_count++;
                lb_linef(&lg, "cpu%ld governor: %s", cpu, strerror(errno));
            }
        }

        if (epp) {
            snprintf(path, sizeof(path), "/sys/devices/system/cpu/cpu%ld/cpufreq/energy_performance_preference", cpu);
            if (!path_exists(path)) {
                applied_this_cpu = 0; warn_count++;
                lb_linef(&lg, "cpu%ld: no energy_performance_preference (driver not amd-pstate-epp?)", cpu);
            } else if (write_tunable_file(path, epp) != 0) {
                applied_this_cpu = 0; warn_count++;
                lb_linef(&lg, "cpu%ld epp: %s", cpu, strerror(errno));
            }
        }

        if (applied_this_cpu) ok_count++;
    }

    char header[128];
    snprintf(header, sizeof(header), "Applied to %d/%zu CPUs.", ok_count, cpus->u.array.count);

    LogBuf out; lb_init(&out);
    lb_line(&out, header);
    if (warn_count > 0 && lg.buf[0]) lb_line(&out, lg.buf);
    ob_ok_message(ob, out.buf);
    free(out.buf);
    free(lg.buf);
}

/* ── op: set_cpu_boost ────────────────────────────────────────────────
 * params: {"enabled": bool}   reply: {"ok": true, "message": "..."}
 */
#define CPU_BOOST_PATH "/sys/devices/system/cpu/cpufreq/boost"

static void op_set_cpu_boost(const JsonValue *params, OutBuf *ob) {
    const JsonValue *node = json_obj_get(params, "enabled");
    if (!node || node->type != JSON_BOOL) {
        ob_error(ob, "enabled must be a boolean");
        return;
    }
    if (!path_exists(CPU_BOOST_PATH)) {
        ob_error(ob, CPU_BOOST_PATH " not found (not supported on this kernel/CPU?)");
        return;
    }
    if (write_tunable_file(CPU_BOOST_PATH, node->u.boolean ? "1" : "0") != 0) {
        ob_error(ob, strerror(errno));
        return;
    }
    ob_ok_message(ob, node->u.boolean ? "CPU boost enabled." : "CPU boost disabled.");
}

/* ═══════════════════════════════════════════════════════════════════
 * Boot-defaults capture / restore  (ported from root_helper.py)
 * ═══════════════════════════════════════════════════════════════════
 * BOOT_DEFAULTS_FILE lives on /run (tmpfs), so "capture once per boot"
 * falls out for free — the file vanishes on reboot.
 */
#define BOOT_DEFAULTS_DIR  "/run/ryzenadj-gui"
#define BOOT_DEFAULTS_FILE "/run/ryzenadj-gui/boot_defaults.json"
#define MAX_SNAPSHOT_BYTES (256 * 1024)

/* Creates BOOT_DEFAULTS_DIR root-owned 0755. Returns 0 on success. */
static int ensure_run_dir(void) {
    if (mkdir(BOOT_DEFAULTS_DIR, 0755) != 0 && errno != EEXIST) return -1;
    /* If it already existed, make sure it is a real directory we own and
     * not a symlink an unprivileged process planted pointing elsewhere. */
    struct stat st;
    if (lstat(BOOT_DEFAULTS_DIR, &st) != 0) return -1;
    if (!S_ISDIR(st.st_mode) || st.st_uid != 0) { errno = EPERM; return -1; }
    return 0;
}

/* Atomically writes `content` to BOOT_DEFAULTS_FILE as root, 0644 so the
 * unprivileged GUI/tray can read it back.
 *
 * Mirrors root_helper.py's _atomic_write_run_json() discipline: write to
 * a temp file in the same root-owned dir with O_CREAT|O_EXCL|O_NOFOLLOW
 * (a pre-planted symlink or file can neither be followed nor reused —
 * we fail closed), then rename() over the target. A leftover .tmp can
 * only come from a previous root run that crashed mid-write, so that one
 * benign case is cleaned up after an lstat confirms it is a plain,
 * root-owned regular file.
 */
static int atomic_write_boot_defaults(const char *content) {
    if (ensure_run_dir() != 0) return -1;

    const char *tmp_path = BOOT_DEFAULTS_FILE ".tmp";
    int flags = O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC;
    int fd = open(tmp_path, flags, 0644);
    if (fd < 0 && errno == EEXIST) {
        struct stat st;
        if (lstat(tmp_path, &st) == 0 && S_ISREG(st.st_mode) && st.st_uid == 0) {
            unlink(tmp_path);
            fd = open(tmp_path, flags, 0644);
        }
    }
    if (fd < 0) return -1;

    size_t n = strlen(content);
    ssize_t w = write(fd, content, n);
    if (w < 0 || (size_t)w != n) { close(fd); unlink(tmp_path); return -1; }
    if (fchmod(fd, 0644) != 0) { /* non-fatal */ }
    /* fsync before rename: /run is tmpfs so this is cheap, and it keeps
     * the "reader never sees a half-written file" guarantee honest. */
    fsync(fd);
    close(fd);

    if (rename(tmp_path, BOOT_DEFAULTS_FILE) != 0) { unlink(tmp_path); return -1; }
    return 0;
}

/* ── op: capture_boot_defaults ────────────────────────────────────────
 * No params (any the client sends are ignored — path/type come only from
 * the fixed table, closing the arbitrary-root-read-and-leak hole that
 * the client-supplied `tunables` mapping used to open).
 */
static void op_capture_boot_defaults(const JsonValue *params, OutBuf *ob) {
    (void)params;
    if (path_exists(BOOT_DEFAULTS_FILE)) {
        ob_ok_message(ob, "Boot defaults already captured this boot, skipping.");
        return;
    }

    OutBuf js; ob_init(&js);
    ob_raw(&js, "{");
    int count = 0;

    const TunableDef *tables[2] = { GAMING_TUNABLES, THP_TUNABLES };
    size_t sizes[2] = { N_GAMING_TUNABLES, N_THP_TUNABLES };

    for (int t = 0; t < 2; t++) {
        for (size_t i = 0; i < sizes[t]; i++) {
            const TunableDef *def = &tables[t][i];
            char valbuf[4096];
            char *fs_path = tunable_fs_path(def);
            if (!fs_path) continue;
            if (!path_exists(fs_path) || read_file_trimmed(fs_path, valbuf, sizeof(valbuf)) != 0) {
                free(fs_path);
                continue;
            }
            free(fs_path);

            const char *value = valbuf;
            if (!def->is_sysctl) {
                /* THP-style "always [madvise] never" → store the bare
                 * accepted token, since that is what we write back. */
                char *lb = strchr(valbuf, '[');
                char *rb = lb ? strchr(lb, ']') : NULL;
                if (lb && rb && rb > lb) { *rb = '\0'; value = lb + 1; }
            }

            if (count > 0) ob_raw(&js, ",");
            ob_json_string(&js, def->key);
            ob_raw(&js, ":{\"path\":");
            ob_json_string(&js, def->path);
            ob_raw(&js, ",\"type\":");
            ob_json_string(&js, def->is_sysctl ? "sysctl" : "file");
            ob_raw(&js, ",\"value\":");
            ob_json_string(&js, value);
            ob_raw(&js, "}");
            count++;
        }
    }
    ob_raw(&js, "}");

    if (atomic_write_boot_defaults(js.buf) != 0) {
        char msg[512];
        snprintf(msg, sizeof(msg), "Could not write boot defaults snapshot: %s", strerror(errno));
        ob_error(ob, msg);
        free(js.buf);
        return;
    }
    free(js.buf);

    char msg[256];
    snprintf(msg, sizeof(msg), "Boot defaults captured (%d values) -> %s", count, BOOT_DEFAULTS_FILE);
    ob_ok_message(ob, msg);
}

/* ── op: restore_boot_defaults ────────────────────────────────────────
 * reply: {"ok": true, "restored": bool, "message": "..."}
 *
 * The `restored` flag distinguishes "actually put values back" from
 * "there was no snapshot, nothing to do" — the GUI only shows its
 * visible "Restored boot-time defaults" line when this is true.
 *
 * HARDENING vs. the Python version: that one read `path` and `type`
 * back out of the snapshot file and wrote to whatever it found there.
 * The snapshot is root-written and 0644, so it isn't attacker-writable
 * today — but it means the write target is data rather than code. Here
 * the snapshot's `path`/`type` fields are ignored entirely: only the
 * KEY is used, and the destination is looked up in the same fixed
 * compiled-in table every other op uses.
 */
static void op_restore_boot_defaults(const JsonValue *params, OutBuf *ob) {
    (void)params;
    if (!path_exists(BOOT_DEFAULTS_FILE)) {
        ob_raw(ob, "{\"ok\":true,\"restored\":false,\"message\":");
        ob_json_string(ob, "No boot-defaults snapshot for this boot, nothing to restore.");
        ob_raw(ob, "}");
        return;
    }

    int fd = open(BOOT_DEFAULTS_FILE, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        ob_raw(ob, "{\"ok\":false,\"restored\":false,\"error\":");
        ob_json_string(ob, "Could not open boot defaults snapshot");
        ob_raw(ob, "}");
        return;
    }
    char *content = xmalloc(MAX_SNAPSHOT_BYTES + 1);
    ssize_t n = read(fd, content, MAX_SNAPSHOT_BYTES);
    close(fd);
    if (n < 0) {
        free(content);
        ob_raw(ob, "{\"ok\":false,\"restored\":false,\"error\":");
        ob_json_string(ob, "Could not read boot defaults snapshot");
        ob_raw(ob, "}");
        return;
    }
    content[n] = '\0';

    char *perr = NULL;
    JsonValue *snap = json_parse(content, &perr);
    free(content);
    if (!snap || snap->type != JSON_OBJ) {
        if (snap) json_free(snap);
        free(perr);
        ob_raw(ob, "{\"ok\":false,\"restored\":false,\"error\":");
        ob_json_string(ob, "Corrupt boot defaults snapshot");
        ob_raw(ob, "}");
        return;
    }
    free(perr);

    LogBuf lg; lb_init(&lg);
    int restored = 0, failed = 0;
    size_t total = snap->u.object.count;

    for (size_t i = 0; i < total; i++) {
        const char *key = snap->u.object.items[i].key;
        const JsonValue *entry = snap->u.object.items[i].value;
        if (!entry || entry->type != JSON_OBJ) continue;

        const JsonValue *vnode = json_obj_get(entry, "value");
        char vbuf[64];
        const char *value = NULL;
        if (vnode && vnode->type == JSON_STR) value = vnode->u.string;
        else if (vnode && vnode->type == JSON_NUM) {
            snprintf(vbuf, sizeof(vbuf), "%ld", (long)vnode->u.number);
            value = vbuf;
        }
        if (!value) continue;

        /* Note: entry's own "path"/"type" are deliberately NOT consulted. */
        const TunableDef *def = lookup_tunable(key);
        if (!def) { failed++; lb_linef(&lg, "%s: not a known tunable, skipped", key); continue; }
        if (!is_safe_tunable_token(value)) { failed++; lb_linef(&lg, "%s: unsafe value, skipped", key); continue; }

        char *fs_path = tunable_fs_path(def);
        if (!fs_path) { failed++; lb_linef(&lg, "%s: path could not be resolved", key); continue; }
        if (write_tunable_file(fs_path, value) == 0) restored++;
        else { failed++; lb_linef(&lg, "%s: %s", key, strerror(errno)); }
        free(fs_path);
    }
    json_free(snap);

    char header[128];
    snprintf(header, sizeof(header), "Restored %d/%zu boot-default values.", restored, total);

    ob_raw(ob, "{\"ok\":true,\"restored\":true,\"message\":");
    if (failed > 0 && lg.buf[0]) {
        LogBuf out; lb_init(&out);
        lb_line(&out, header);
        lb_line(&out, lg.buf);
        ob_json_string(ob, out.buf);
        free(out.buf);
    } else {
        ob_json_string(ob, header);
    }
    ob_raw(ob, "}");
    free(lg.buf);
}

/* ═══════════════════════════════════════════════════════════════════
 * dispatch
 * ═══════════════════════════════════════════════════════════════════ */

typedef void (*OpFn)(const JsonValue *params, OutBuf *ob);

typedef struct {
    const char *name;
    OpFn fn;
} OpEntry;

static const OpEntry OPERATIONS[] = {
    { "read_gaming_status",    op_read_gaming_status },
    { "apply_gaming_and_pci",  op_apply_gaming_and_pci },
    { "run_nvctgp",            op_run_nvctgp },
    { "set_cpu_epp_governor",  op_set_cpu_epp_governor },
    { "set_cpu_boost",         op_set_cpu_boost },
    { "capture_boot_defaults", op_capture_boot_defaults },
    { "restore_boot_defaults", op_restore_boot_defaults },
};
#define N_OPERATIONS (sizeof(OPERATIONS) / sizeof(OPERATIONS[0]))

int main(void) {
    if (geteuid() != 0) {
        printf("{\"ok\":false,\"error\":\"ryzenadj-helper must run as root\"}\n");
        return 1;
    }

    /* Every file this binary creates should be root-owned and not
     * group/world-writable regardless of the inherited umask (pkexec
     * passes through the caller's). Explicit fchmod calls cover the
     * files we care about, but a restrictive umask is the cheap
     * belt-and-braces default. */
    umask(022);

    /* If a child we exec dies with its stdout closed we want the write()
     * to return EPIPE, not to kill this privileged process outright. */
    signal(SIGPIPE, SIG_IGN);

    /* Read all of stdin (bounded). */
    size_t cap = 8192, len = 0;
    char *input = xmalloc(cap);
    while (1) {
        if (len + 1 >= cap) {
            if (cap >= MAX_STDIN_BYTES) {
                free(input);
                printf("{\"ok\":false,\"error\":\"payload too large\"}\n");
                return 1;
            }
            cap *= 2;
            if (cap > MAX_STDIN_BYTES) cap = MAX_STDIN_BYTES;
            input = xrealloc(input, cap);
        }
        ssize_t n = read(STDIN_FILENO, input + len, cap - len - 1);
        if (n < 0) {
            if (errno == EINTR) continue;
            free(input);
            printf("{\"ok\":false,\"error\":\"stdin read failed\"}\n");
            return 1;
        }
        if (n == 0) break;
        len += (size_t)n;
    }
    input[len] = '\0';

    char *err = NULL;
    JsonValue *root = json_parse(input, &err);
    free(input);
    if (!root) {
        OutBuf eb; ob_init(&eb);
        char msg[512];
        snprintf(msg, sizeof(msg), "Invalid JSON on stdin: %s", err ? err : "parse error");
        ob_error(&eb, msg);
        printf("%s\n", eb.buf);
        free(eb.buf);
        free(err);
        return 1;
    }
    free(err);

    OutBuf ob; ob_init(&ob);

    if (root->type != JSON_OBJ) {
        ob_error(&ob, "Payload must be a JSON object");
    } else {
        const char *op = json_get_str(root, "op", NULL);
        OpFn fn = NULL;
        for (size_t i = 0; op && i < N_OPERATIONS; i++) {
            if (strcmp(OPERATIONS[i].name, op) == 0) { fn = OPERATIONS[i].fn; break; }
        }
        if (!op) {
            ob_error(&ob, "Missing op");
        } else if (!fn) {
            ob_error(&ob, "Unknown or disallowed op");
        } else {
            fn(root, &ob);
        }
    }

    json_free(root);
    printf("%s\n", ob.buf);
    /* Make sure the reply actually reaches the caller before we exit:
     * the GUI parses the LAST line of stdout, so a lost flush would
     * surface as "Invalid root_helper response" rather than as an error. */
    if (fflush(stdout) != 0) {
        free(ob.buf);
        return 1;
    }

    int ok = strncmp(ob.buf, "{\"ok\":true", 10) == 0;
    free(ob.buf);
    return ok ? 0 : 1;
}
