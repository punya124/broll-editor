import shutil
import tempfile
from pathlib import Path

from . import library, matcher
from .library import probe_duration

def _download_is_valid(path: Path, min_bytes=10_000) -> bool:
    """Cheap sanity check: a real video file should be at least a few KB.
    Catches truncated downloads and HTML error pages saved as .mp4."""
    return path.exists() and path.stat().st_size >= min_bytes

def build_search_query(shot: dict) -> str:
    """Prefer Gemini's dedicated pexels_search_terms field — it's written
    specifically to be search-engine-friendly, unlike the required/preferred/
    fallback fields which are meant for semantic matching, not keyword search."""
    terms = shot.get("pexels_search_terms")
    if terms:
        return " ".join(str(t) for t in terms[:4])

    # Fallback for older shot plans generated before this field existed
    preferred = shot.get("preferred", {}) or {}
    required = shot.get("required", {}) or {}
    fallback = shot.get("fallback", {}) or {}

    fallback_terms = []
    for block in (preferred, required, fallback):
        for values in block.values():
            values = values if isinstance(values, list) else [values]
            for v in values:
                short = " ".join(str(v).split()[:3])
                if short:
                    fallback_terms.append(short)

    if not fallback_terms:
        fallback_terms = [" ".join(shot.get("purpose", "b-roll").split()[:3])]
    return " ".join(fallback_terms[:3])


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
        try:
            pexels_client.download_video(candidate["video_url"], tmp_path)

            if not _download_is_valid(tmp_path):
                tmp_path.unlink(missing_ok=True)
                continue

            # No Gemini video analysis here — metadata is built directly from
            # the shot's own requirements and Pexels search terms instead,
            # to avoid the token cost of analyzing every candidate video.
            search_terms = shot.get("pexels_search_terms", []) or []
            communicates = (shot.get("required", {}) or {}).get("communicates", [])

            meta = {
                "id": f"pexels_{candidate['id']}",
                "description": shot.get("purpose", ""),
                "subjects": [],
                "primary_action": "",
                "secondary_actions": [],
                "environment": "",
                "perspective": "",
                "camera_motion": "",
                "mood": [],
                "themes": [],
                "search_intent": search_terms,
                "communicates": communicates,
                "use_cases": [],
                "works_for": [],
                "keywords": search_terms,
                "reusability_score": 50,
                "notes": f"sourced from Pexels (id {candidate['id']}), search: \"{query}\"",
            }
            meta["duration_seconds"] = probe_duration(tmp_path)
            meta["embedding"] = gemini_client.embed_text(library.clip_text_for_embedding(meta))

            score = matcher.score_clip(shot, meta, gemini_client.embed_text, embedding_cache)

            final_path = library_folder / f"pexels_{candidate['id']}.mp4"
            shutil.move(str(tmp_path), str(final_path))
            meta["_path"] = str(final_path)
            library.save_metadata(final_path, meta)
            return {"success": True, "clip": final_path.name, "score": round(score, 1), "query": query}

        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            print(f"Skipping Pexels candidate {candidate['id']}: {e}")
            continue

    return {
        "success": False,
        "reason": f"Found Pexels clips for '{query}' but none matched well enough (below {threshold}%).",
    }

