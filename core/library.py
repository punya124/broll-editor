import json
import subprocess
from pathlib import Path

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
    """Flattens a clip's metadata into one string for semantic embedding."""
    parts = [
        meta.get("description", ""),
        " ".join(meta.get("subjects", []) or []),
        meta.get("primary_action", ""),
        " ".join(meta.get("secondary_actions", []) or []),
        meta.get("environment", ""),
        " ".join(meta.get("mood", []) or []),
        " ".join(meta.get("themes", []) or []),
        " ".join(meta.get("communicates", []) or []),
        " ".join(meta.get("use_cases", []) or []),
        " ".join(meta.get("works_for", []) or []),
        " ".join(meta.get("keywords", []) or []),
        meta.get("notes", ""),
    ]
    return " | ".join([p for p in parts if p])


def get_library_status(folder: str) -> dict:
    clips = scan_library(folder)
    unanalyzed = 0
    for c in clips:
        meta = load_metadata(c)
        if not meta or "embedding" not in meta:
            unanalyzed += 1
    return {"total": len(clips), "unanalyzed": unanalyzed}


def ensure_analyzed(folder: str, gemini_client, log=None):
    """Analyzes (once) every clip in the library that doesn't already have metadata
    and an embedding, then saves the metadata as a JSON sidecar file."""
    log = log or (lambda msg: None)
    clips = scan_library(folder)
    for c in clips:
        meta = load_metadata(c)
        if meta and "embedding" in meta and "duration_seconds" in meta:
            continue

        if not meta:
            log(f"Analyzing {c.name}...")
            meta = gemini_client.analyze_clip(c)
            meta["id"] = c.stem

        if "duration_seconds" not in meta:
            meta["duration_seconds"] = probe_duration(c)

        if "embedding" not in meta:
            meta["embedding"] = gemini_client.embed_text(clip_text_for_embedding(meta))

        meta["_path"] = str(c)
        save_metadata(c, meta)
    return clips
