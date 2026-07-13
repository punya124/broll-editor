import subprocess
from turtle import width

from . import matcher
from .library import probe_duration

MAX_SLOWDOWN = 0.5  # never slow a clip more than 50%

def compute_trim(clip_duration: float, need_duration: float):
    """Center trim: take the middle `need_duration` seconds of the clip so
    natural movement (not a static first/last frame) is preserved."""
    if need_duration >= clip_duration:
        return 0.0, clip_duration
    start = (clip_duration - need_duration) / 2.0
    return start, start + need_duration

def fit_clips_to_duration(duration_needed, clip_infos):
    """clip_infos: ordered list of (path, clip_duration) candidates.
    Returns a list of {'path', 'trim_start', 'trim_end', 'speed'} segments
    that together fill duration_needed — trimming if a clip is long enough,
    modestly slowing it down if it's a bit short, and pulling in additional
    clips if one clip alone can't reach the target without excessive slowdown."""
    plan = []
    remaining = duration_needed

    for path, clip_duration in clip_infos:
        if remaining <= 0.01:
            break

        if clip_duration >= remaining:
            start, end = compute_trim(clip_duration, remaining)
            plan.append({"path": path, "trim_start": start, "trim_end": end, "speed": 1.0})
            remaining = 0
            continue

        slowdown_needed = remaining / clip_duration - 1.0
        if slowdown_needed <= MAX_SLOWDOWN:
            speed = clip_duration / remaining  # <1.0 = play slower
            plan.append({"path": path, "trim_start": 0.0, "trim_end": clip_duration, "speed": speed})
            remaining = 0
        else:
            capped_duration = clip_duration * (1 + MAX_SLOWDOWN)
            speed = clip_duration / capped_duration
            plan.append({"path": path, "trim_start": 0.0, "trim_end": clip_duration, "speed": speed})
            remaining -= capped_duration

    return plan, max(0.0, remaining)  # remaining > 0 = still short after all candidates

def select_clips(shots, clips_with_meta, embed_text_fn, threshold=85):
    assignments = []
    missing = []
    embedding_cache = {}
    used_paths = set()

    for idx, shot in enumerate(shots):
        candidates = matcher.find_best_matches(
            shot, clips_with_meta, embed_text_fn, embedding_cache, top_n=5
        )

        chosen = None
        for path, meta, score in candidates:
            if score < threshold:
                continue
            if str(path) not in used_paths:
                chosen = (path, meta, score)
                break
        if chosen is None and candidates and candidates[0][2] >= threshold:
            chosen = candidates[0]

        if chosen is None:
            missing.append({"index": idx, "shot": shot})
            continue

        path, meta, score = chosen
        used_paths.add(str(path))
        duration = meta.get("duration_seconds") or probe_duration(path)
        start, end = compute_trim(duration, shot["duration"])
        assignments.append({
            "index": idx,
            "shot": shot,
            "clip_path": str(path),
            "score": score,
            "trim_start": start,
            "trim_end": end,
        })

    return assignments, missing
    """For each shot, find the best-matching unused clip above `threshold`.
    Returns (assignments, missing) where `missing` is the list of shots that
    couldn't be filled from the library."""
    assignments = []
    missing = []
    embedding_cache = {}
    used_paths = set()

    for shot in shots:
        candidates = matcher.find_best_matches(
            shot, clips_with_meta, embed_text_fn, embedding_cache, top_n=5
        )

        chosen = None
        for path, meta, score in candidates:
            if score < threshold:
                continue
            if str(path) not in used_paths:
                chosen = (path, meta, score)
                break
        # If every good match is already used, allow reuse rather than fail.
        if chosen is None and candidates and candidates[0][2] >= threshold:
            chosen = candidates[0]

        if chosen is None:
            missing.append(shot)
            continue

        path, meta, score = chosen
        used_paths.add(str(path))
        duration = meta.get("duration_seconds") or probe_duration(path)
        start, end = compute_trim(duration, shot["duration"])
        assignments.append({
            "shot": shot,
            "clip_path": str(path),
            "score": score,
            "trim_start": start,
            "trim_end": end,
        })

    return assignments, missing


def render_video(assignments, audio_path, output_path, width=1080, height=1920, fps=30):
    """Trims, scales/crops to 9:16, concatenates the assigned clips (in shot
    order), and muxes the result with the voiceover audio track."""
    if not assignments:
        raise ValueError("No assignments to render.")

    inputs = []
    filter_parts = []
    concat_labels = []

    for i, a in enumerate(assignments):
        # Input-level seeking is fast and accurate enough for B-roll trims.
        inputs += [
            "-ss", f"{a['trim_start']:.3f}",
            "-to", f"{a['trim_end']:.3f}",
            "-i", a["clip_path"],
        ]
        label = f"v{i}"
        speed = a.get("speed", 1.0)
        pts_multiplier = 1.0 / speed if speed != 1.0 else 1.0
        speed_filter = f",setpts={pts_multiplier:.4f}*PTS" if speed != 1.0 else ""
        filter_parts.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1{speed_filter},fps={fps}[{label}]"
        )
        concat_labels.append(f"[{label}]")

    concat_filter = "".join(concat_labels) + f"concat=n={len(assignments)}:v=1:a=0[outv]"
    filter_complex = ";".join(filter_parts + [concat_filter])

    audio_input_index = len(assignments)
    cmd = (
        ["ffmpeg", "-y"] + inputs
        + ["-i", str(audio_path)]
        + ["-filter_complex", filter_complex]
        + ["-map", "[outv]", "-map", f"{audio_input_index}:a"]
        + ["-c:v", "libx264", "-c:a", "aac", "-shortest"]
        + [str(output_path)]
    )

    subprocess.run(cmd, check=True, capture_output=True)
    return str(output_path)
