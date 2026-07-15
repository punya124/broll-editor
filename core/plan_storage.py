import json
import re
import time
from pathlib import Path

from . import config

APPROVED_PLANS_DIR = config.DATA_DIR / "approved_action_plans"


def _sanitize_filename(name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_\-]", "_", (name or "").strip())
    return name or "untitled_project"


def save_plan(project_name: str, segments: list) -> Path:
    """Saves an approved plan to disk under a name derived from project_name.
    Handles filename collisions by appending a counter rather than overwriting."""
    APPROVED_PLANS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_filename(project_name)
    path = APPROVED_PLANS_DIR / f"{safe_name}.json"

    counter = 1
    while path.exists():
        path = APPROVED_PLANS_DIR / f"{safe_name}_{counter}.json"
        counter += 1

    data = {
        "project_name": project_name,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "segments": segments,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def list_plans() -> list:
    APPROVED_PLANS_DIR.mkdir(parents=True, exist_ok=True)
    plans = []
    for p in sorted(APPROVED_PLANS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            with open(p, "r") as f:
                data = json.load(f)
            plans.append({
                "file": p.name,
                "project_name": data.get("project_name", p.stem),
                "created_at": data.get("created_at", ""),
                "segment_count": len(data.get("segments", [])),
            })
        except Exception:
            continue
    return plans


def load_plan(file_name: str) -> dict:
    path = APPROVED_PLANS_DIR / file_name
    if not path.exists():
        raise ValueError(f"No such plan: {file_name}")
    with open(path, "r") as f:
        return json.load(f)