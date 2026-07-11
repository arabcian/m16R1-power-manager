#!/usr/bin/env python3
"""tool_paths.py — Central resolver for external binary dependencies.

Two trust modes, because the trust model differs:

  * User-session context (GUI/tray, root_context=False):
    user override file -> $PATH -> known candidate paths.

  * Root context (root_helper.py, root_context=True):
    the user's $PATH and $HOME are NEVER trusted. Only a fixed list of
    system directories is searched, plus the built-in candidate paths.
    (pkexec already sanitizes PATH, but we don't lean on that assumption
    here — the restriction is explicit in code.)

Results are cached per (name, root_context) for the lifetime of the
process, so repeated calls (e.g. status polling, script generation)
don't re-scan PATH or the filesystem every time.
"""

import json
import os
import shutil
from pathlib import Path

# Directories searched in root context, in priority order.
TRUSTED_DIRS = [
    "/usr/local/sbin", "/usr/local/bin",
    "/usr/sbin", "/usr/bin",
    "/sbin", "/bin",
]

# Known absolute candidate paths per tool, to cover distro layout
# differences (e.g. sbin vs bin placement across distros).
CANDIDATES = {
    "ryzenadj":     ["/usr/bin/ryzenadj", "/usr/local/bin/ryzenadj"],
    "alienfx_cli":  ["/usr/local/bin/alienfx_cli", "/usr/bin/alienfx_cli"],
    "corefreq-cli": ["/usr/bin/corefreq-cli", "/usr/local/bin/corefreq-cli"],
    "sysctl":       ["/usr/sbin/sysctl", "/sbin/sysctl", "/usr/bin/sysctl"],
    "modprobe":     ["/usr/sbin/modprobe", "/sbin/modprobe"],
    "rmmod":        ["/usr/sbin/rmmod", "/sbin/rmmod"],
    "lspci":        ["/usr/bin/lspci", "/usr/sbin/lspci"],
    "pgrep":        ["/usr/bin/pgrep", "/bin/pgrep"],
    "uname":        ["/usr/bin/uname", "/bin/uname"],
}

# User-editable override file (user-session context only). Lets a power
# user point at a nonstandard install location without touching code.
# Example content: {"ryzenadj": "/opt/custom/bin/ryzenadj"}
_USER_OVERRIDE_PATH = Path.home() / ".config/ryzenadj_gui/tools.json"

_cache: dict[tuple[str, bool], str | None] = {}


def _is_usable(path: str) -> bool:
    try:
        p = Path(path)
        return p.is_file() and os.access(p, os.X_OK)
    except OSError:
        return False


def _load_user_overrides() -> dict:
    try:
        if _USER_OVERRIDE_PATH.exists():
            data = json.loads(_USER_OVERRIDE_PATH.read_text())
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def find_tool(name: str, root_context: bool = False) -> str | None:
    """Resolve a tool name to an absolute path, or None if not found.

    root_context=True ignores the user's PATH and any override file —
    only CANDIDATES and TRUSTED_DIRS are consulted. Use this for every
    subprocess call made from root_helper.py (i.e. anything that runs
    with root privileges via pkexec).
    """
    key = (name, root_context)
    if key in _cache:
        return _cache[key]

    path = None

    if not root_context:
        overrides = _load_user_overrides()
        override = overrides.get(name)
        if override and _is_usable(override):
            path = override
        if not path:
            found = shutil.which(name)
            if found:
                path = found

    if not path:
        for cand in CANDIDATES.get(name, []):
            if _is_usable(cand):
                path = cand
                break

    if not path and root_context:
        for d in TRUSTED_DIRS:
            cand = os.path.join(d, name)
            if _is_usable(cand):
                path = cand
                break

    _cache[key] = path
    return path


def require_tool(name: str, root_context: bool = False) -> str:
    """Like find_tool, but raises FileNotFoundError instead of
    returning None. Use where the caller has no sane fallback."""
    path = find_tool(name, root_context)
    if not path:
        raise FileNotFoundError(f"Required tool not found: {name}")
    return path


def clear_cache() -> None:
    """Mainly for tests / the redetect button — forces a fresh scan."""
    _cache.clear()
