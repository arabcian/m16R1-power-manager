# RyzenAdj GUI

PySide6 GUI + system tray for AMD Ryzen (Alienware M16 R1 AMD) power management, GPU V/F curve (nvcurve), and gaming/system optimizations.

## Installation

```bash
chmod +x install.sh
sudo ./install.sh
```

If you are coming from an older installation (a previous version of this project that used relative directories based on `SCRIPT_DIR`) and your actual profile data is in a different location:

```bash
sudo ./install.sh --migrate-profiles /home/USER/Ryzen/profiles
```

`install.sh` also attempts to automatically detect the `Ryzen/profiles` folder under `$SUDO_USER`'s home directory; `--migrate-profiles` is only used when you need to specify a different path.

If you do not want the tray to autostart upon KDE/GNOME login:

```bash
sudo ./install.sh --no-autostart
```

If you do not want to be prompted for a password at all during power mode changes (this is the default behavior for users in the `wheel`/`sudo` group), you do not need to do anything — the installation configures this automatically. If you prefer to be prompted for a password every time or with a short-term cached password instead:

```bash
sudo ./install.sh --no-passwordless
```

## Uninstallation

```bash
sudo ./uninstall.sh            # removes the application, preserves profiles
sudo ./uninstall.sh --purge    # also deletes profiles
```

## Known Issues and Fixes (Resolved in this Version)

**"Tray does not show the profile changed from the GUI / sometimes the cross icon does not appear at all" (Root Cause and Real Fix):** The previous fix (read order + "recently applied by us" stamp) did not fully resolve the issue because the ONLY way for the tray to learn about a profile change was to monitor the ACPI `platform_profile` sysfs. The actual race condition was as follows:

1. `alienfx_cli` modifying the ACPI occurs at the VERY BEGINNING of `apply_profile()`.
2. The GUI writing the actual profile name to the state file occurs AFTER the operation is COMPLETELY finished (after ryzenadj limits, fan boost, etc., are applied).
3. The tray's ACPI-monitor would see the event at step (1), meaning BEFORE the correct name was written; at that moment, the OLD profile name was still in the state file. Later, when the GUI wrote the correct name, since ACPI did not change again, there was NOTHING left to trigger the tray — leaving the tray one step behind indefinitely.

**Solution — Push-based, event-driven synchronization:** `ryzenadj_common.write_active_profile()` now directly and instantly notifies the tray (if it is running) via a Unix domain socket as soon as it writes the state file (`notify_profile_changed()`). On the tray side (the new `ProfileNotifyListener` class inside `ryzenadj_tray.py`), a blocking `socket.accept()` waits for this message — no polling, CPU overhead is near zero (it only wakes up once every second for a shutdown check, similar to the existing ACPI monitor's 500 ms polling pattern). The moment the message arrives, the tray menu cache is forcibly invalidated and instantly refreshed (which also solves the "sometimes the cross icon does not appear at all" issue), and a `notify-send` notification is displayed. The old ACPI-based monitor remains as a secondary fallback to catch real external changes (e.g., an Fn key combination), but it is no longer the sole source of synchronization.

If the tray is not running (or the socket does not exist), `notify_profile_changed()` silently does nothing — it never delays or interrupts the profile application flow.

**Double notification (the same "profile changed" popup was appearing twice):** Both the GUI and the tray were making their own `notify-send` calls. Now, `write_active_profile()` / `set_active_profile_state()` returns a boolean indicating whether the message actually reached the tray: if the tray is running (message delivered), only the **tray** notification is shown; the GUI does not display its own popup. If the tray is not running, the GUI displays its own notification as a fallback so the user remains informed. Thus, in normal use (when the tray is open), exactly **one** notification appears.

**"Extra Tools" settings in Custom/G-MODE (THP, sysctls, lru_gen, sched_*) remained in their previous states when switching to other profiles:** The quiet/cool/balanced/balanced-performance profiles contained no "extra" data for these settings, meaning when switching to them, the values left behind by custom/gmode remained exactly as they were in the system. Now:
- IMMEDIATELY BEFORE switching to `custom`/`gmode`, if a snapshot has not yet been taken for this boot (`/run/ryzenadj-gui/boot_defaults.json` — since `/run` is a tmpfs, this automatically means "once per boot"), the current (clean) values are saved via `root_helper`'s new `capture_boot_defaults` op.
- When returning to `quiet`/`cool`/`balanced`/`balanced-performance`, the `restore_boot_defaults` op restores these values.
- If `custom`/`gmode` is never used, the capture is never triggered, and the restore is a no-op (values are still in their boot state).
- The gaming-tunables list in the GUI (`self.gaming_settings`) has been moved to `ryzenadj_wrapper.GAMING_TUNABLES` — a single source of truth, eliminating the risk of drift between the UI list and capture/restore.

**"lru_gen" status showing "?" in the UI (Diagnostic Improvement):** `root_helper.py`'s `op_read_gaming_status` (and the GUI's rootless pre-check) now displays a distinctive reason instead of an ambiguous "?": `(no file)` (this path does not exist in this kernel), `(no sysctl)`, `(perm denied)`, or `(err: ...)`. See the item below for the actual implementation bug.

**"lru_gen" status was showing "?" in the UI — and more importantly, it was never actually written when custom/gmode was applied:** The command provided (`echo 5 > /sys/kernel/mm/lru_gen/enabled`) already matched the path/value in the code exactly — meaning the write command itself was always correct. The actual bug was that the `extra.gaming` dictionary uses a **NAME**→value mapping like `{"lru_gen": "5", "vm.swappiness": "10", ...}`, where the key is NOT the actual sysfs path. `root_helper.py` (and the script preview) attempted to differentiate between sysctl and file by checking if the key started with `vm.`/`kernel.` or `/`, but `"lru_gen"`, `"sched_min_base_slice"`, `"sched_migration_cost"`, and `"sched_nr_migrate"` matched none of these three patterns. Result: "recommended: 5" appeared in the UI, but when the profile was applied, this value was **never actually written** — it was silently skipped.

Now, a `gaming_schema` derived from the `ryzenadj_wrapper.GAMING_TUNABLES` schema (name → actual path/type) is sent to `root_helper.py`; each gaming setting is no longer written by name but by the actual path/type found in this schema. The same fix was applied to the script-preview code (`_build_shell_script_content`). Furthermore, an unknown gaming key (with no match in the schema) is no longer silently swallowed; it is explicitly flagged in the log as "unknown gaming setting (no schema)".

**"Prompts for password on every power mode change":** The `com.ryzenadj.gui.policy` file was using the `<allow_active>auth_admin_keep_always</allow_active>` value — which is an INVALID polkit value (polkit only recognizes `no`, `yes`, `auth_self`, `auth_self_keep`, `auth_admin`, `auth_admin_keep`). The invalid value caused polkit to reject this action, forcing pkexec to fall back to the default action (uncached, prompting for a password every time). Now:
- The valid `auth_admin_keep` stands as a backup in the `.policy` file,
- The actual authorization comes from the newly added `/etc/polkit-1/rules.d/49-ryzenadj-gui.rules` JavaScript rule: it grants **completely passwordless** permission for local, active users in the `wheel`/`sudo` group (which is kept secure since `root_helper.py` is already locked down with `0700` + allowlist + validation).

**Additionally (also fixed in this version):** `ryzenadj_wrapper.py::apply_profile()` — the main "apply profile" function — used to run a bash script using `sudo bash script`; this completely bypassed the pkexec/Polkit architecture and could not work with centralized (root-owned) directories. It is now applied via native Python (without an intermediate bash script) through `root_helper.py`'s new `apply_power_profile` op.

## Directory Structure (Post-Installation)

The application now uses fixed system paths compliant with the **Linux Filesystem Hierarchy Standard (FHS)**, completely independent of the installation directory:

| Path | Content | Ownership |
|---|---|---|
| `/usr/local/lib/ryzenadj-gui/` | Application code (`.py`, icons, `nvcurve/` package) | root:root, 0755 |
| `/usr/local/lib/ryzenadj-gui/root_helper.py` | Root helper process | root:root, **0700** |
| `/usr/local/bin/ryzenadj-gui` | Launcher | root:root, 0755 |
| `/usr/local/bin/ryzenadj-tray` | Launcher | root:root, 0755 |
| `/etc/ryzenadj-gui/profiles/` | Power profiles (`.json`) | root:root, 0755 (root_helper writes, GUI reads) |
| `/etc/nvcurve/profiles/` | nvcurve GPU V/F curve profiles | root:root, 0755 |
| `/var/lib/ryzenadj-gui/scripts/` | Persistent, generated profile activation scripts | root:root, 0755 |
| `/run/ryzenadj-gui/scripts/` | root_helper's temporary script execution area (tmpfs) | root:root, 0700 |
| `/run/ryzenadj-gui/boot_defaults.json` | Boot-time gaming/THP settings snapshot (tmpfs) | root:root, 0644 |
| `/usr/share/polkit-1/actions/com.ryzenadj.gui.policy` | Polkit action | root:root, 0644 |
| `/etc/polkit-1/rules.d/49-ryzenadj-gui.rules` | Passwordless authorization rule (JS) | root:root, 0644 |
| `/usr/share/applications/ryzenadj-gui.desktop` | Desktop entry | root:root, 0644 |
| `~/.cache/ryzenadj-gui/scripts/` | **Local preview** copy of the "Generate Script" button | user |
| `~/.local/state/ryzenadj/` | Rotating log file | user |
| `$XDG_RUNTIME_DIR/ryzenadj-gui/notify.sock` | GUI→tray instant profile-change notification (Unix socket) | user, 0600 |
| `~/.config/autostart/ryzenadj-tray.desktop` | Tray session autostart | user |

Due to being independent of the installation source path, manually copying `/usr/local/lib/ryzenadj-gui` to another location and directly running `ryzenadj_gui.py` is **no longer supported** — all paths are hardcoded to these fixed locations. For development/testing, re-running `install.sh` repeatedly is the safest approach.

## Architectural Note: Why Everything Goes Through `root_helper.py`

The GUI never runs as root. Every operation that modifies hardware parameters (`ryzenadj`, fan boost, CPU isolation, GPU curve, profile saving) is delegated to `/usr/local/lib/ryzenadj-gui/root_helper.py` via `pkexec`. `root_helper.py`:

- Only accepts operations from a fixed **allowlist** (`OPERATIONS` dict) — there is no arbitrary command/script execution,
- Is owned by root:root with `0700` permissions (unreadable/unmodifiable by the user), meaning Polkit `auth_admin_keep_always` (caching the password for 15 minutes) can be securely defined,
- Performs path-traversal checks and name validation on all file writes.

In a previous version, the profile application process (`ryzenadj_wrapper.py::apply_profile`) was left out of this and executed a bash script via `sudo bash script`; this both bypassed the architecture and usually hung or silently failed when launched from the GUI (without a TTY). It is now also performed via native Python (without an intermediate bash script) through `root_helper.py`'s `apply_power_profile` op.

## Files Removed from the Project

The following files were not imported or used anywhere and have been removed:

- `tab.py` — An unused, never-imported copy/draft of the `_build_tab_extra_tools` method that already exists in `ryzenadj_gui.py`.
- `secure_qprocess.py`, `secure_privilege_escalation.py` — `ryzenadj_gui.py` tried to import these via `try/except ImportError` "if available", but `SECURE_MODE` / `SecureQProcess` / `run_as_root` were not used anywhere in the code (all root calls had already been migrated to pkexec + `root_helper.py`). Removed along with dead code and the confusing "unsafe mode" warning.
- `51-ryzenadj-gui.rules` — Intended to be a JavaScript rule file for `/etc/polkit-1/rules.d/`, but its content was entirely a `<policyconfig>` XML (i.e., `.policy` format, incorrect directory/format). If polkit tried to load this, it would likely reject it with a syntax error. The already correct and complete `com.ryzenadj.gui.policy` was left as the sole authorization source.
- `scripts/` (static, pre-generated `set_*.sh` files) — these are already regenerated at runtime by `ryzenadj_wrapper.write_shell_script()`; there is no need to carry them in the source tree.
- `redirect-tasks/` and `redirector-*.zip` (removed in a previous session) — The original bash implementation of CPU isolation; it has now been completely rewritten using native Python and cgroup v2 in `root_helper.py`'s `apply_cpu_isolation` / `revert_cpu_isolation` ops.
- Path-based `op_run_script` inside `root_helper.py` — An unused root-privileged code path since all calls from the GUI were migrated to `op_run_script_content`; removed to reduce the attack surface.

`patches/` (kernel patches) are left in the repository as a developer reference; they are not copied to the system by the installer.

## Security Hardening (Summary)

Fixes implemented in a previous review that are still valid in this tree: whitelisting of arbitrary file writes/execution, path-traversal checks, whitelist verification against bash command injection, reading `/proc` instead of forking for CPU isolation, logging silently swallowed errors, and ensuring callbacks are invoked on every error path. For details, refer to the comments labeled `K1`–`K5`, `O1`–`O7`, and `Q1`–`Q10` within the code.
