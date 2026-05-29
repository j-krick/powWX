"""Load the YAML config (locations + models) into plain dicts."""

from __future__ import annotations

from pathlib import Path

import yaml

# repo_root/powwx/config.py -> repo_root
REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"


def load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_locations() -> list[dict]:
    return load_yaml("locations.yaml")["locations"]


def load_models() -> dict:
    """Return the full models.yaml dict (api, variables, models)."""
    return load_yaml("models.yaml")


def model_ids(models_cfg: dict) -> list[str]:
    return [m["id"] for m in models_cfg["models"]]
