/* json_min.c — see json_min.h for scope/intent. */
#define _POSIX_C_SOURCE 200809L
#include "json_min.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

typedef struct {
    const char *s;
    size_t pos;
    size_t len;
} Parser;

static void skip_ws(Parser *p) {
    while (p->pos < p->len) {
        char c = p->s[p->pos];
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
            p->pos++;
        } else {
            break;
        }
    }
}

static char peek(Parser *p) {
    return p->pos < p->len ? p->s[p->pos] : '\0';
}

static JsonValue *json_new(JsonType type) {
    JsonValue *v = calloc(1, sizeof(JsonValue));
    if (v) v->type = type;
    return v;
}

static int fail(char **err, const char *msg) {
    if (err) {
        *err = strdup(msg);
    }
    return 0;
}

/* Parses a JSON string literal starting at the opening quote. Returns a
 * malloc'd, unescaped C string, or NULL on error. Advances p->pos past
 * the closing quote. */
static char *parse_string_raw(Parser *p, char **err) {
    if (peek(p) != '"') { fail(err, "expected string"); return NULL; }
    p->pos++; /* skip opening quote */

    size_t cap = 32, len = 0;
    char *buf = malloc(cap);
    if (!buf) { fail(err, "out of memory"); return NULL; }

    while (p->pos < p->len) {
        char c = p->s[p->pos];
        if (c == '"') {
            p->pos++;
            buf[len] = '\0';
            return buf;
        }
        if (c == '\\') {
            p->pos++;
            if (p->pos >= p->len) { free(buf); fail(err, "unterminated escape"); return NULL; }
            char e = p->s[p->pos];
            char out;
            switch (e) {
                case '"':  out = '"';  break;
                case '\\': out = '\\'; break;
                case '/':  out = '/';  break;
                case 'b':  out = '\b'; break;
                case 'f':  out = '\f'; break;
                case 'n':  out = '\n'; break;
                case 'r':  out = '\r'; break;
                case 't':  out = '\t'; break;
                case 'u': {
                    /* Only need to accept \uXXXX for well-formed input;
                     * this project's payloads never rely on values
                     * outside the BMP, and we emit '?' for anything
                     * we can't represent as a single byte. */
                    if (p->pos + 4 >= p->len) { free(buf); fail(err, "bad \\u escape"); return NULL; }
                    char hex[5] = { p->s[p->pos+1], p->s[p->pos+2], p->s[p->pos+3], p->s[p->pos+4], 0 };
                    long cp = strtol(hex, NULL, 16);
                    p->pos += 4;
                    out = (cp >= 0x20 && cp < 0x7f) ? (char)cp : '?';
                    break;
                }
                default:
                    free(buf);
                    fail(err, "unknown escape sequence");
                    return NULL;
            }
            if (len + 1 >= cap) { cap *= 2; char *nb = realloc(buf, cap); if (!nb) { free(buf); fail(err, "out of memory"); return NULL; } buf = nb; }
            buf[len++] = out;
            p->pos++;
        } else {
            if (len + 1 >= cap) { cap *= 2; char *nb = realloc(buf, cap); if (!nb) { free(buf); fail(err, "out of memory"); return NULL; } buf = nb; }
            buf[len++] = c;
            p->pos++;
        }
    }
    free(buf);
    fail(err, "unterminated string");
    return NULL;
}

static JsonValue *parse_value(Parser *p, char **err);

static JsonValue *parse_object(Parser *p, char **err) {
    JsonValue *v = json_new(JSON_OBJ);
    if (!v) { fail(err, "out of memory"); return NULL; }
    p->pos++; /* skip '{' */
    skip_ws(p);

    size_t cap = 4, count = 0;
    JsonMember *items = malloc(cap * sizeof(JsonMember));
    if (!items) { free(v); fail(err, "out of memory"); return NULL; }

    if (peek(p) == '}') {
        p->pos++;
        v->u.object.items = items;
        v->u.object.count = 0;
        return v;
    }

    while (1) {
        skip_ws(p);
        if (peek(p) != '"') { free(items); free(v); fail(err, "expected object key"); return NULL; }
        char *key = parse_string_raw(p, err);
        if (!key) { free(items); free(v); return NULL; }
        skip_ws(p);
        if (peek(p) != ':') { free(key); free(items); free(v); fail(err, "expected ':'"); return NULL; }
        p->pos++;
        skip_ws(p);
        JsonValue *val = parse_value(p, err);
        if (!val) { free(key); free(items); free(v); return NULL; }

        if (count == cap) {
            cap *= 2;
            JsonMember *ni = realloc(items, cap * sizeof(JsonMember));
            if (!ni) { free(key); free(items); free(v); fail(err, "out of memory"); return NULL; }
            items = ni;
        }
        items[count].key = key;
        items[count].value = val;
        count++;

        skip_ws(p);
        char c = peek(p);
        if (c == ',') { p->pos++; continue; }
        if (c == '}') { p->pos++; break; }
        free(items); free(v); fail(err, "expected ',' or '}'"); return NULL;
    }
    v->u.object.items = items;
    v->u.object.count = count;
    return v;
}

static JsonValue *parse_array(Parser *p, char **err) {
    JsonValue *v = json_new(JSON_ARR);
    if (!v) { fail(err, "out of memory"); return NULL; }
    p->pos++; /* skip '[' */
    skip_ws(p);

    size_t cap = 4, count = 0;
    JsonValue **items = malloc(cap * sizeof(JsonValue *));
    if (!items) { free(v); fail(err, "out of memory"); return NULL; }

    if (peek(p) == ']') {
        p->pos++;
        v->u.array.items = items;
        v->u.array.count = 0;
        return v;
    }

    while (1) {
        skip_ws(p);
        JsonValue *val = parse_value(p, err);
        if (!val) { free(items); free(v); return NULL; }
        if (count == cap) {
            cap *= 2;
            JsonValue **ni = realloc(items, cap * sizeof(JsonValue *));
            if (!ni) { free(items); free(v); fail(err, "out of memory"); return NULL; }
            items = ni;
        }
        items[count++] = val;
        skip_ws(p);
        char c = peek(p);
        if (c == ',') { p->pos++; continue; }
        if (c == ']') { p->pos++; break; }
        free(items); free(v); fail(err, "expected ',' or ']'"); return NULL;
    }
    v->u.array.items = items;
    v->u.array.count = count;
    return v;
}

static JsonValue *parse_value(Parser *p, char **err) {
    skip_ws(p);
    char c = peek(p);
    if (c == '{') return parse_object(p, err);
    if (c == '[') return parse_array(p, err);
    if (c == '"') {
        char *s = parse_string_raw(p, err);
        if (!s) return NULL;
        JsonValue *v = json_new(JSON_STR);
        if (!v) { free(s); fail(err, "out of memory"); return NULL; }
        v->u.string = s;
        return v;
    }
    if (c == 't' && p->pos + 4 <= p->len && strncmp(p->s + p->pos, "true", 4) == 0) {
        p->pos += 4;
        JsonValue *v = json_new(JSON_BOOL);
        if (v) v->u.boolean = 1;
        return v;
    }
    if (c == 'f' && p->pos + 5 <= p->len && strncmp(p->s + p->pos, "false", 5) == 0) {
        p->pos += 5;
        JsonValue *v = json_new(JSON_BOOL);
        if (v) v->u.boolean = 0;
        return v;
    }
    if (c == 'n' && p->pos + 4 <= p->len && strncmp(p->s + p->pos, "null", 4) == 0) {
        p->pos += 4;
        return json_new(JSON_NULL);
    }
    if (c == '-' || isdigit((unsigned char)c)) {
        size_t start = p->pos;
        if (peek(p) == '-') p->pos++;
        while (isdigit((unsigned char)peek(p))) p->pos++;
        if (peek(p) == '.') { p->pos++; while (isdigit((unsigned char)peek(p))) p->pos++; }
        if (peek(p) == 'e' || peek(p) == 'E') {
            p->pos++;
            if (peek(p) == '+' || peek(p) == '-') p->pos++;
            while (isdigit((unsigned char)peek(p))) p->pos++;
        }
        char numbuf[64];
        size_t nlen = p->pos - start;
        if (nlen >= sizeof(numbuf)) nlen = sizeof(numbuf) - 1;
        memcpy(numbuf, p->s + start, nlen);
        numbuf[nlen] = '\0';
        JsonValue *v = json_new(JSON_NUM);
        if (v) v->u.number = strtod(numbuf, NULL);
        return v;
    }
    fail(err, "unexpected character");
    return NULL;
}

JsonValue *json_parse(const char *text, char **err) {
    if (err) *err = NULL;
    Parser p = { .s = text, .pos = 0, .len = strlen(text) };
    skip_ws(&p);
    JsonValue *v = parse_value(&p, err);
    if (!v) return NULL;
    skip_ws(&p);
    if (p.pos != p.len) {
        json_free(v);
        fail(err, "trailing data after JSON value");
        return NULL;
    }
    return v;
}

void json_free(JsonValue *v) {
    if (!v) return;
    switch (v->type) {
        case JSON_STR:
            free(v->u.string);
            break;
        case JSON_OBJ:
            for (size_t i = 0; i < v->u.object.count; i++) {
                free(v->u.object.items[i].key);
                json_free(v->u.object.items[i].value);
            }
            free(v->u.object.items);
            break;
        case JSON_ARR:
            for (size_t i = 0; i < v->u.array.count; i++) {
                json_free(v->u.array.items[i]);
            }
            free(v->u.array.items);
            break;
        default:
            break;
    }
    free(v);
}

const JsonValue *json_obj_get(const JsonValue *obj, const char *key) {
    if (!obj || obj->type != JSON_OBJ) return NULL;
    for (size_t i = 0; i < obj->u.object.count; i++) {
        if (strcmp(obj->u.object.items[i].key, key) == 0) {
            return obj->u.object.items[i].value;
        }
    }
    return NULL;
}

const char *json_get_str(const JsonValue *obj, const char *key, const char *def) {
    const JsonValue *v = json_obj_get(obj, key);
    if (v && v->type == JSON_STR) return v->u.string;
    return def;
}

long json_get_int(const JsonValue *obj, const char *key, long def) {
    const JsonValue *v = json_obj_get(obj, key);
    if (v && v->type == JSON_NUM) return (long)v->u.number;
    if (v && v->type == JSON_STR) return strtol(v->u.string, NULL, 10);
    return def;
}

int json_get_bool(const JsonValue *obj, const char *key, int def) {
    const JsonValue *v = json_obj_get(obj, key);
    if (v && v->type == JSON_BOOL) return v->u.boolean;
    return def;
}
