/*
 * json_min.h — a small, dependency-free JSON reader.
 *
 * This is NOT a general-purpose JSON library. It supports exactly what
 * root_helper's op payloads need: objects, strings, numbers, booleans,
 * null, and arrays-of-the-above, with standard escape handling in
 * strings. It exists so ryzenadj-helper (the privileged, pkexec-invoked
 * binary) has zero third-party dependencies to audit — the whole parser
 * is ~200 lines, written for this project, and easy to read top to
 * bottom in one sitting.
 *
 * Usage:
 *   char *err = NULL;
 *   JsonValue *root = json_parse(buf, &err);
 *   if (!root) { fprintf(stderr, "%s\n", err); free(err); ... }
 *   const JsonValue *gaming = json_obj_get(root, "gaming");
 *   ...
 *   json_free(root);
 */
#ifndef JSON_MIN_H
#define JSON_MIN_H

#include <stddef.h>

typedef enum {
    JSON_NULL,
    JSON_BOOL,
    JSON_NUM,
    JSON_STR,
    JSON_OBJ,
    JSON_ARR
} JsonType;

typedef struct JsonValue JsonValue;

typedef struct {
    char *key;
    JsonValue *value;
} JsonMember;

struct JsonValue {
    JsonType type;
    union {
        int boolean;
        double number;
        char *string;
        struct { JsonMember *items; size_t count; } object;
        struct { JsonValue **items; size_t count; } array;
    } u;
};

/* HARDENING: maximum container nesting depth accepted by json_parse().
 *
 * The parser is recursive descent, soeach nested '[' / '{' consumes a C
 * stack frame. Without a cap, an unprivileged caller could send a
 * payload of ~130k nested '[' — which fits comfortably inside
 * ryzenadj-helper's MAX_STDIN_BYTES (256 KB) — and blow the stack of a
 * process running as ROOT under pkexec. Confirmed: the pre-fix parser
 * segfaults (signal 11) on exactly that input. The real payloads this
 * project sends are at most 2 levels deep ({"gaming": {...}}), so 32 is
 * already an order of magnitude more headroom than anything legitimate
 * needs, and the parser now fails closed with a normal JSON error
 * instead of dying. */
#define JSON_MAX_DEPTH 32

/* Parses `text` (NUL-terminated). Returns NULL and sets *err (malloc'd,
 * caller frees) on failure. On success, caller owns the returned tree
 * and must call json_free() on it. */
JsonValue *json_parse(const char *text, char **err);

void json_free(JsonValue *v);

/* Object helpers. All return NULL / default if `obj` is not a JSON_OBJ
 * or the key is absent — never crash on a malformed/adversarial payload. */
const JsonValue *json_obj_get(const JsonValue *obj, const char *key);
const char *json_get_str(const JsonValue *obj, const char *key, const char *def);
long json_get_int(const JsonValue *obj, const char *key, long def);
int json_get_bool(const JsonValue *obj, const char *key, int def);

#endif /* JSON_MIN_H */
