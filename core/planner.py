from pathlib import Path

from . import segmentation


def build_action_plan(audio_path, gemini_client, action_vocabulary, feedback=None,
                       cached_segments=None, combined_audio_path=None):
    """Deterministic timing (from segmentation) + one Gemini call for semantic
    action planning. Returns (project_name, segments, raw_segments, combined_audio_path).

    raw_segments and combined_audio_path are returned so a Reject-with-feedback
    regeneration can reuse the exact same segmentation and combined audio file
    instead of re-running ffmpeg silence detection from scratch."""
    if cached_segments is not None and combined_audio_path is not None:
        segments = cached_segments
    else:
        segments = segmentation.build_narration_segments(audio_path)
        combined_audio_path = Path(audio_path).parent / f"{Path(audio_path).stem}_combined.wav"
        segmentation.build_combined_audio_with_gaps(audio_path, segments, combined_audio_path)

    durations = [end - start for start, end in segments]
    project_name, plans = gemini_client.generate_action_plan(
        combined_audio_path, durations, action_vocabulary, feedback=feedback
    )

    shot_list = []
    for i, ((start, end), plan) in enumerate(zip(segments, plans)):
        shot_list.append({
            "segment_id": i + 1,
            "text": plan.get("text", ""),
            "start": start,
            "end": end,
            "duration": end - start,
            "main_suggestion": plan.get("main_suggestion", ""),
            "fallback": plan.get("fallback", ""),
        })

    return project_name, shot_list, segments, combined_audio_path