import json
import subprocess
from pathlib import Path
from . import config
import time

VIDEO_EXTS = {".mp4", ".mov", ".m4v"}


def _sidecar_path(video_path: Path) -> Path:
    return video_path.with_suffix(video_path.suffix + ".json")


def scan_library(folder: str):
    """Every video file inside `folder` becomes part of the library."""
    folder = Path(folder)
    if not folder.exists():
        return []
    clips = []
    for p in sorted(folder.rglob("*")):
        if p.suffix.lower() in VIDEO_EXTS:
            clips.append(p)
    return clips


def load_metadata(video_path: Path):
    sidecar = _sidecar_path(Path(video_path))
    if sidecar.exists():
        try:
            with open(sidecar, "r") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_metadata(video_path: Path, data: dict):
    sidecar = _sidecar_path(Path(video_path))
    with open(sidecar, "w") as f:
        json.dump(data, f, indent=2)


def probe_duration(path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    return float(data["format"]["duration"])


def clip_text_for_embedding(meta: dict) -> str:
    parts = [
        meta.get("description", ""),
        " ".join(meta.get("action_interpretations", []) or []),
        meta.get("environment", ""),
        meta.get("notes", ""),
    ]
    return " | ".join([p for p in parts if p])


def rebuild_action_vocabulary(folder: str) -> dict:
    """Scans every analyzed clip and builds a deduplicated
    action -> [clip filenames] map — the source of truth for what actions
    we can PROVE we already have footage of."""
    clips = scan_library(folder)
    vocabulary = {}
    for c in clips:
        meta = load_metadata(c)
        if not meta:
            continue
        for action in meta.get("action_interpretations", []) or []:
            key = action.strip().lower()
            if not key:
                continue
            vocabulary.setdefault(key, [])
            if c.name not in vocabulary[key]:
                vocabulary[key].append(c.name)

    config.ensure_dirs()
    with open(config.ACTION_VOCAB_PATH, "w") as f:
        json.dump({"actions": vocabulary}, f, indent=2)
    return vocabulary


def get_action_vocabulary() -> dict:
    if not config.ACTION_VOCAB_PATH.exists():
        return {}
    try:
        with open(config.ACTION_VOCAB_PATH, "r") as f:
            return json.load(f).get("actions", {})
    except Exception:
        return {}
    

def get_library_status(folder: str) -> dict:
    clips = scan_library(folder)
    unanalyzed = 0
    for c in clips:
        meta = load_metadata(c)
        if not meta or "embedding" not in meta:
            unanalyzed += 1
    return {"total": len(clips), "unanalyzed": unanalyzed}

def scan_and_analyze_pending(folder: str, gemini_client, log=None, batch_size=10, pause_seconds=60):
    """Analyzes every never-seen clip — exactly ONE Gemini request each — and
    saves the result immediately with review_status='pending' so it's durable
    even if interrupted mid-batch. Paces itself: after every `batch_size`
    clips, sleeps `pause_seconds` before continuing, to stay under rate limits."""
    log = log or (lambda m: None)
    clips = scan_library(folder)
    to_analyze = [c for c in clips if load_metadata(c) is None]

    for i, c in enumerate(to_analyze):
        if i > 0 and i % batch_size == 0:
            log(f"Analyzed {i} clips — pausing {pause_seconds}s to respect Gemini rate limits...")
            time.sleep(pause_seconds)

        log(f"Analyzing {c.name}... ({i + 1}/{len(to_analyze)})")
        meta = gemini_client.analyze_clip(c)  # exactly one API request
        meta["id"] = c.stem
        meta["review_status"] = "pending"
        save_metadata(c, meta)

    return to_analyze


def get_pending_review_clips(folder: str):
    """Everything analyzed but not yet confirmed by the user."""
    clips = scan_library(folder)
    pending = []
    for c in clips:
        meta = load_metadata(c)
        if meta and meta.get("review_status") == "pending":
            pending.append({
                "name": c.name,
                "description": meta.get("description", ""),
                "action_interpretations": meta.get("action_interpretations", []) or [],
                "environment": meta.get("environment", ""),
            })
    return pending


def confirm_clip_review(folder: str, clip_name: str, actions: list, gemini_client):
    """Finalizes one clip using the (possibly user-edited) action list.
    This is the ONE place embedding happens — one request, at confirm time."""
    clip_path = Path(folder) / clip_name
    meta = load_metadata(clip_path)
    if not meta:
        raise ValueError(f"No pending analysis found for {clip_name}")

    cleaned_actions = [a.strip() for a in actions if a and a.strip()]
    meta["action_interpretations"] = cleaned_actions
    meta["review_status"] = "confirmed"

    if "duration_seconds" not in meta:
        meta["duration_seconds"] = probe_duration(clip_path)

    meta["embedding"] = gemini_client.embed_text(clip_text_for_embedding(meta))
    meta["_path"] = str(clip_path)
    save_metadata(clip_path, meta)

    rebuild_action_vocabulary(folder)
    return meta