import os
import json
from pathlib import Path

# core/config.py -> project root is one level up from core/
APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
CACHE_DIR = DATA_DIR / "cache"
UPLOADS_DIR = DATA_DIR / "uploads"
OUTPUTS_DIR = DATA_DIR / "outputs"
SETTINGS_PATH = DATA_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "library_folder": "",
    "gemini_model": "gemini-3.1-flash-lite",
    "embedding_model": "gemini-embedding-001",
    "match_threshold": 85,
    "api_key_env": "GEMINI_API_KEY",
}


def ensure_dirs():
    for d in (DATA_DIR, PROJECTS_DIR, CACHE_DIR, UPLOADS_DIR, OUTPUTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_settings():
    ensure_dirs()
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r") as f:
                data = json.load(f)
            return {**DEFAULT_SETTINGS, **data}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict):
    ensure_dirs()
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)


def get_api_key(settings=None) -> str:
    settings = settings or load_settings()
    key_env = settings.get("api_key_env", "GEMINI_API_KEY")
    return os.environ.get(key_env, "") or os.environ.get("GEMINI_API_KEY", "")
