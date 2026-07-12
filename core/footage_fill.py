import shutil
import tempfile
from pathlib import Path

from . import library, matcher
from .library import probe_duration


def build_search_query(shot: dict) -> str:
    """Prefer concrete terms (primary_action) over abstract ones (communicates)
    since Pexels search works better on literal visual subjects."""
    preferred = shot.get("preferred", {}) or {}
    required = shot.get("required", {}) or {}
    fallback = shot.get("fallback", {}) or {}

    terms = []
    for block in (preferred, required, fallback):
        for values in block.values():
            if isinstance(values, list):
                terms.extend(values)
            elif values:
                terms.append(values)

    if not terms:
        terms = [shot.get("purpose", "b-roll")]
    return " ".join(terms[:4])


def resolve_shot_with_pexels(shot, library_folder, gemini_client, pexels_client, threshold=85):
    """Searches Pexels for the shot, downloads and analyzes candidates the same
    way library clips are analyzed, and keeps the first one that scores above
    `threshold`. On success, the clip is saved into the library folder with its
    metadata sidecar so it's reusable in future videos."""
    query = build_search_query(shot)
    candidates = pexels_client.search_videos(query, per_page=5)
    if not candidates:
        return {"success": False, "reason": f"No Pexels results for '{query}'."}

    library_folder = Path(library_folder)
    embedding_cache = {}

    for candidate in candidates:
        tmp_path = Path(tempfile.gettempdir()) / f"pexels_{candidate['id']}.mp4"
        pexels_client.download_video(candidate["video_url"], tmp_path)

        meta = gemini_client.analyze_clip(tmp_path)
        meta["id"] = f"pexels_{candidate['id']}"
        meta["duration_seconds"] = probe_duration(tmp_path)
        meta["embedding"] = gemini_client.embed_text(library.clip_text_for_embedding(meta))
        meta["notes"] = (
            meta.get("notes", "") + f" | sourced from Pexels (id {candidate['id']})"
        ).strip(" |")

        score = matcher.score_clip(shot, meta, gemini_client.embed_text, embedding_cache)

        if score >= threshold:
            final_path = library_folder / f"pexels_{candidate['id']}.mp4"
            shutil.move(str(tmp_path), str(final_path))
            meta["_path"] = str(final_path)
            library.save_metadata(final_path, meta)
            return {"success": True, "clip": final_path.name, "score": round(score, 1), "query": query}

        tmp_path.unlink(missing_ok=True)

    return {
        "success": False,
        "reason": f"Found Pexels clips for '{query}' but none matched well enough (below {threshold}%).",
    }