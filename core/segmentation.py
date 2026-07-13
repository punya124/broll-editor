import re
import subprocess
from pathlib import Path

MAX_SEGMENT_SECONDS = 6.0
MIN_SEGMENT_SECONDS = 0.5
PRIMARY_SILENCE_DB = "-30dB"
PRIMARY_SILENCE_DURATION = 0.4
SECONDARY_SILENCE_DURATION = 0.15
DEFAULT_GAP_SECONDS = 0.75


def _run_silencedetect(audio_path, noise_db, min_silence_duration):
    cmd = [
        "ffmpeg", "-i", str(audio_path),
        "-af", f"silencedetect=noise={noise_db}:d={min_silence_duration}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stderr  # ffmpeg logs silencedetect hits to stderr


def _parse_silences(ffmpeg_stderr: str):
    starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", ffmpeg_stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", ffmpeg_stderr)]
    return list(zip(starts, ends))


def detect_silences(audio_path, noise_db=PRIMARY_SILENCE_DB, min_duration=PRIMARY_SILENCE_DURATION):
    stderr = _run_silencedetect(audio_path, noise_db, min_duration)
    return _parse_silences(stderr)


def _segments_from_silences(total_duration, silences):
    """Cuts at the midpoint of each detected silence."""
    cut_points = [0.0]
    for s_start, s_end in silences:
        cut_points.append((s_start + s_end) / 2.0)
    cut_points.append(total_duration)
    cut_points = sorted(set(cut_points))

    segments = []
    for i in range(len(cut_points) - 1):
        start, end = cut_points[i], cut_points[i + 1]
        if end - start > 0.01:
            segments.append((start, end))
    return segments


def _find_split_point(start, end, secondary_silences):
    midpoint = (start + end) / 2.0
    candidates = []
    for s_start, s_end in secondary_silences:
        cut = (s_start + s_end) / 2.0
        if start < cut < end:
            left, right = cut - start, end - cut
            if left >= MIN_SEGMENT_SECONDS and right >= MIN_SEGMENT_SECONDS:
                candidates.append(cut)
    if candidates:
        candidates.sort(key=lambda c: abs(c - midpoint))
        return candidates[0]
    return midpoint  # no valid pause found -> split at midpoint


def _split_recursive(start, end, secondary_silences):
    if end - start <= MAX_SEGMENT_SECONDS:
        return [(start, end)]
    cut = _find_split_point(start, end, secondary_silences)
    return _split_recursive(start, cut, secondary_silences) + _split_recursive(cut, end, secondary_silences)


def _enforce_max_duration(segments, secondary_silences):
    result = []
    for start, end in segments:
        result.extend(_split_recursive(start, end, secondary_silences))
    return result


def _merge_short_segments(segments):
    segments = list(segments)
    changed = True
    while changed:
        changed = False
        for i, (start, end) in enumerate(segments):
            if end - start > MIN_SEGMENT_SECONDS or len(segments) == 1:
                continue

            candidates = []
            if i > 0:
                prev_start, _ = segments[i - 1]
                candidates.append(("prev", end - prev_start))
            if i < len(segments) - 1:
                _, next_end = segments[i + 1]
                candidates.append(("next", next_end - start))

            valid = [c for c in candidates if c[1] <= MAX_SEGMENT_SECONDS]
            pool = valid if valid else candidates
            pool.sort(key=lambda c: c[1])  # prefer the smaller/more-balanced merge result
            choice = pool[0][0]

            if choice == "prev":
                segments[i - 1] = (segments[i - 1][0], end)
            else:
                segments[i + 1] = (start, segments[i + 1][1])
            del segments[i]
            changed = True
            break
    return segments


def build_narration_segments(audio_path):
    """Returns a list of (start, end) tuples: deterministic narration segments
    covering the full audio with no gaps or overlaps, each strictly between
    MIN_SEGMENT_SECONDS and MAX_SEGMENT_SECONDS."""
    from .library import probe_duration  # local import avoids a circular import

    total_duration = probe_duration(audio_path)
    primary = detect_silences(audio_path, PRIMARY_SILENCE_DB, PRIMARY_SILENCE_DURATION)
    secondary = detect_silences(audio_path, PRIMARY_SILENCE_DB, SECONDARY_SILENCE_DURATION)

    segments = _segments_from_silences(total_duration, primary)
    segments = _enforce_max_duration(segments, secondary)
    segments = _merge_short_segments(segments)
    return segments


def build_combined_audio_with_gaps(audio_path, segments, output_path, gap_seconds=DEFAULT_GAP_SECONDS):
    """Extracts each narration segment and concatenates them with a fixed
    silent gap in between, into one temporary audio file for a single Gemini
    request covering the whole video."""
    inputs = ["-i", str(audio_path)]
    filter_parts = []
    concat_labels = []

    for i, (start, end) in enumerate(segments):
        label = f"seg{i}"
        filter_parts.append(f"[0:a]atrim={start:.3f}:{end:.3f},asetpts=PTS-STARTPTS[{label}]")
        concat_labels.append(f"[{label}]")
        if i < len(segments) - 1:
            gap_label = f"gap{i}"
            filter_parts.append(
                f"anullsrc=channel_layout=mono:sample_rate=44100,atrim=0:{gap_seconds}[{gap_label}]"
            )
            concat_labels.append(f"[{gap_label}]")

    concat_filter = "".join(concat_labels) + f"concat=n={len(concat_labels)}:v=0:a=1[outa]"
    filter_complex = ";".join(filter_parts + [concat_filter])

    cmd = ["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[outa]", str(output_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return str(output_path)
