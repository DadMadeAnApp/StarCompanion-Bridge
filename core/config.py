"""Persisted settings for the GUI.

Precedence is CLI flag > config file > default. `apply_cli` records which
fields a flag supplied so the settings panel can disable them rather than
appear to control something it cannot.

A config file is a convenience, never a prerequisite: anything unreadable or
unparseable is moved aside and the defaults are used, because refusing to
start over a bad settings file would be worse than ignoring it.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "bridge_config.json"
CONFIG_VERSION = 1

# Keys this version owns. Anything else in the file is preserved in `extras` so
# a config written by a newer build survives a round-trip through an older one.
_KNOWN = {
    "version", "port", "log_path", "telemetry_enabled",
    "start_with_windows", "minimize_to_tray", "window",
}


@dataclass
class Config:
    port: int = 8765
    log_path: str | None = None
    telemetry_enabled: bool = True
    start_with_windows: bool = False
    # Reserved for a future tray icon. Read and written, never surfaced in the
    # settings panel -- the tray is out of scope for this work.
    minimize_to_tray: bool = False
    window_height: int = 620
    extras: dict = field(default_factory=dict)
    # Provenance, not settings: never persisted.
    port_from_cli: bool = False
    log_from_cli: bool = False


def config_path(app_dir: Path) -> Path:
    return Path(app_dir) / CONFIG_NAME


def load(app_dir: Path) -> tuple[Config, str | None]:
    """Read the config. Returns (config, warning-to-log-or-None)."""
    path = config_path(app_dir)
    if not path.exists():
        return Config(), None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Config(), _quarantine(path)
    if not isinstance(raw, dict):
        return Config(), _quarantine(path)

    cfg = Config()
    bad_fields = []

    def take(key: str, kind: type, target: str) -> None:
        if key not in raw:
            return
        value = raw[key]
        # bool is a subclass of int, so an explicit check keeps `"port": true`
        # from being accepted as a port number.
        if isinstance(value, kind) and not (kind is int and isinstance(value, bool)):
            setattr(cfg, target, value)
        else:
            bad_fields.append(key)

    take("port", int, "port")
    take("telemetry_enabled", bool, "telemetry_enabled")
    take("start_with_windows", bool, "start_with_windows")
    take("minimize_to_tray", bool, "minimize_to_tray")

    if "log_path" in raw:
        if raw["log_path"] is None or isinstance(raw["log_path"], str):
            cfg.log_path = raw["log_path"]
        else:
            bad_fields.append("log_path")

    window = raw.get("window")
    if isinstance(window, dict):
        h = window.get("height")
        if isinstance(h, int) and not isinstance(h, bool):
            cfg.window_height = h
        elif h is not None:
            bad_fields.append("window")
    elif window is not None:
        bad_fields.append("window")

    cfg.extras = {k: v for k, v in raw.items() if k not in _KNOWN}

    warning = None
    if bad_fields:
        warning = (
            f"{CONFIG_NAME}: ignoring unusable value(s) for "
            f"{', '.join(sorted(bad_fields))} — using defaults for those."
        )
    return cfg, warning


def _quarantine(path: Path) -> str:
    bad = path.with_suffix(path.suffix + ".bad")
    with contextlib.suppress(OSError):
        bad.unlink()
    with contextlib.suppress(OSError):
        path.rename(bad)
    return f"{CONFIG_NAME} was unreadable — moved to {bad.name} and using defaults."


def save(cfg: Config, app_dir: Path) -> bool:
    """Write the config, preserving any keys this version does not know about.

    Returns False (rather than raising) if the file could not be written --
    e.g. `app_dir` is `C:\\Program Files` and the process is a standard user.
    Callers decide how to surface that; a caller in a WM_DELETE_WINDOW handler
    or a Tk button callback that let this raise would have the exception
    swallowed by Tk into a stream that's None in a --windowed build, which
    looks like nothing happened at all.
    """
    payload = dict(cfg.extras)
    payload.update({
        "version": CONFIG_VERSION,
        "port": cfg.port,
        "log_path": cfg.log_path,
        "telemetry_enabled": cfg.telemetry_enabled,
        "start_with_windows": cfg.start_with_windows,
        "minimize_to_tray": cfg.minimize_to_tray,
        "window": {"height": cfg.window_height},
    })
    path = config_path(app_dir)
    # Write via a temp file so an interrupted save can't leave a truncated
    # config that the next launch has to quarantine.
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return False
    return True


def apply_cli(cfg: Config, cli_port: int | None, cli_log: str | None) -> Config:
    """Overlay command-line flags onto a loaded config, recording provenance."""
    if cli_port is not None:
        cfg.port = cli_port
        cfg.port_from_cli = True
    if cli_log is not None:
        cfg.log_path = cli_log
        cfg.log_from_cli = True
    return cfg
