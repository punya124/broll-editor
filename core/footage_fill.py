import shutil
import tempfile
from pathlib import Path

from . import library, matcher
from .library import probe_duration


def _download_is_valid(path: Path, min_bytes=10_000) -> bool:
    return path.exists() and path.stat().st_size >= min_bytes


def build_search_query(shot: dict) -> str:
    terms = shot.get("pexels_search_terms")
    if terms:
        return " ".join(str(t) for t in terms[:4])

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


def resolve_shot_with_pexels(shot, library_folder, gemini_client, pexels_client, max_candidates=3):
    """Searches Pexels, downloads/validates candidates, tags each with
    lightweight metadata (no video analysis — text-only, cheap), and saves
    every good one into the library. Returns up to `max_candidates`
    (path, duration) tuples ranked by match score, for
    timeline.fit_clips_to_duration to stitch together as needed."""
    query = build_search_query(shot)
    search_results = pexels_client.search_videos(query, per_page=max_candidates + 2)
    if not search_results:
        return {"success": False, "reason": "No Pexels results found.", "query": query}

    library_folder = Path(library_folder)
    embedding_cache = {}
    scored_candidates = []  # (score, path, duration)

    for candidate in search_results:
        if len(scored_candidates) >= max_candidates:
            break

        tmp_path = Path(tempfile.gettempdir()) / f"pexels_{candidate['id']}.mp4"
        try:
            pexels_client.download_video(candidate["video_url"], tmp_path)

            if not _download_is_valid(tmp_path):
                tmp_path.unlink(missing_ok=True)
                continue

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

            scored_candidates.append((score, final_path, meta["duration_seconds"]))

        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            print(f"Skipping Pexels candidate {candidate['id']}: {e}")
            continue

    if not scored_candidates:
        return {"success": False, "reason": "No usable Pexels candidates were downloaded.", "query": query}

    scored_candidates.sort(key=lambda c: c[0], reverse=True)
    return {
        "success": True,
        "query": query,
        "candidates": [(str(path), duration) for _, path, duration in scored_candidates],
        "top_score": round(scored_candidates[0][0], 1),
    }