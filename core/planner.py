from . import segmentation

def _flatten_to_strings(value):
    """Recursively flattens whatever Gemini returned into a flat list of
    strings — handles the case where a value that should be a simple list of
    strings comes back as a nested dict or list instead."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_flatten_to_strings(item))
        return out
    if isinstance(value, dict):
        out = []
        for v in value.values():
            out.extend(_flatten_to_strings(v))
        return out
    return [str(value)]


def _sanitize_block(block):
    """Guarantees required/preferred/fallback is always {key: [str, str, ...]}
    no matter what shape Gemini actually returned."""
    if not isinstance(block, dict):
        block = {"value": block}
    return {key: _flatten_to_strings(val) for key, val in block.items()}

def build_shot_plan(script_text: str, audio_path, gemini_client) -> list:
    """Deterministic timing + one Gemini call for semantic planning.
    Returns a list of shot objects — the single source of truth used by
    every later stage of the pipeline."""
    segments = segmentation.build_narration_segments(audio_path)
    durations = [end - start for start, end in segments]

    combined_audio_path = audio_path.parent / f"{audio_path.stem}_combined.wav"
    segmentation.build_combined_audio_with_gaps(audio_path, segments, combined_audio_path)

    try:
        plans = gemini_client.generate_segment_plans(script_text, combined_audio_path, durations)
    finally:
        combined_audio_path.unlink(missing_ok=True)

    shots = []
    for i, ((start, end), plan) in enumerate(zip(segments, plans)):
        shots.append({
            "segment_id": i + 1,
            "text": plan.get("text", ""),
            "start": start,
            "end": end,
            "duration": end - start,
            "purpose": plan.get("purpose", ""),
            "shot_description": plan.get("shot_description", ""),   # <- new
            "required": _sanitize_block(plan.get("required", {})),
            "preferred": _sanitize_block(plan.get("preferred", {})),
            "fallback": _sanitize_block(plan.get("fallback", {})),
            "pexels_search_terms": plan.get("pexels_search_terms", []) or [],
            "approved": False,
        })
    return shots