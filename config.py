"""
config.py – Loads and validates config.yaml.
"""

import os
from pathlib import Path
from typing import Optional

import yaml

_CONFIG_PATHS = [
    Path(os.environ.get("HOCKEY_CONFIG", "")) if os.environ.get("HOCKEY_CONFIG") else None,
    Path("/data/config.yaml"),          # Home Assistant add-on data dir
    Path("/config/hockey.yaml"),        # Alternative HA path
    Path("config.yaml"),                # CWD (dev / Docker workdir)
]


def load_config(path: Optional[Path] = None) -> dict:
    """
    Load configuration from a YAML file.
    Search order: explicit path → HOCKEY_CONFIG env → /data/config.yaml
    → /config/hockey.yaml → ./config.yaml
    """
    candidates = ([path] if path else []) + _CONFIG_PATHS
    for p in candidates:
        if p and p.exists():
            with p.open(encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            _validate(cfg, str(p))
            return cfg

    raise FileNotFoundError(
        "No config.yaml found. Copy config.yaml.example to config.yaml "
        "and edit it."
    )


def _validate(cfg: dict, source: str) -> None:
    errors = []
    if not cfg.get("team"):
        errors.append("'team' is required (e.g. team: HV71)")
    if not cfg.get("season_ids"):
        errors.append(
            "'season_ids' is required (list of swehockey.se season IDs)"
        )
    if errors:
        raise ValueError(
            f"Config errors in {source}:\n" + "\n".join(f"  - {e}" for e in errors)
        )


# Expose a simple accessor used by app.py
_cfg: Optional[dict] = None


def get() -> dict:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg
