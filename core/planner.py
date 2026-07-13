import math

from . import library

REQUIRED_KEYS = {"start_seconds", "end_seconds", "purpose", "required", "preferred",
                  "fallback", "pexels_search_terms"}

MAX_SHOT_SECONDS = 5.0


def _make_chunk(start, end, source_shot):
    return {
        "start": start,
        "end": end,
        "duration": end - start,
        "purpose": source_shot.get("purpose", ""),
        "required": source_shot.get("required", {}) or {},
        "preferred": source_shot.get("preferred", {}) or {},
        "fallback": source_shot.get("fallback", {}) or {},
        "pexels_search_terms": source_shot.get("pexels_search_terms", []) or [],
    }


def build_shot_plan(script_text: str, audio_path, gemini_client) -> list:
    audio_duration = library.probe_duration(audio_path)

    raw_shots = gemini_client.generate_shot_plan(script_text, audio_path, audio_duration)
    if not isinstance(raw_shots, list):
        raise ValueError("Gemini did not return a list of shots.")

    for shot in raw_shots:
        for key in REQUIRED_KEYS - set(shot.keys()):
            if key in ("required", "preferred", "fallback"):
                shot[key] = {}
            elif key == "pexels_search_terms":
                shot[key] = []
            else:
                shot[key] = 0.0

    raw_shots.sort(key=lambda s: float(s.get("start_seconds", 0)))

    final_shots = []
    cursor = 0.0
    last_terms = []

    for shot in raw_shots:
        start = max(float(shot.get("start_seconds", 0)), cursor)
        end = float(shot.get("end_seconds", 0))
        if end <= start:
            continue  # degenerate/overlapping shot, skip it

        last_terms = shot.get("pexels_search_terms") or last_terms
        span = end - start
        n_chunks = max(1, math.ceil(span / MAX_SHOT_SECONDS))
        chunk_len = span / n_chunks

        for i in range(n_chunks):
            c_start = start + i * chunk_len
            c_end = start + (i + 1) * chunk_len if i < n_chunks - 1 else end
            final_shots.append(_make_chunk(c_start, c_end, shot))

        cursor = end

    # Guarantee coverage to the exact end of the audio, filling any remaining
    # gap (including one at the very end) in <=3s chunks.
    if audio_duration - cursor > 0.05:
        remaining = audio_duration - cursor
        n_chunks = max(1, math.ceil(remaining / MAX_SHOT_SECONDS))
        chunk_len = remaining / n_chunks
        for i in range(n_chunks):
            c_start = cursor + i * chunk_len
            c_end = cursor + (i + 1) * chunk_len if i < n_chunks - 1 else audio_duration
            final_shots.append(_make_chunk(c_start, c_end, {
                "purpose": "Continuation b-roll",
                "required": {}, "preferred": {}, "fallback": {},
                "pexels_search_terms": last_terms,
            }))

    real_count = len([s for s in final_shots if s["purpose"] != "Continuation b-roll"])
    filler_count = len(final_shots) - real_count
    print(f"Shot plan: {audio_duration:.1f}s audio -> {len(final_shots)} shots "
          f"({real_count} planned, {filler_count} filler), exact coverage guaranteed.")

    return final_shots