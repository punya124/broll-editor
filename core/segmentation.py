import re
import subprocess
from pathlib import Path

# Forces cuts to happen much more frequently, aligning with the 1-1.5s target pacing
MAX_SEGMENT_SECONDS = 2.0  
MIN_SEGMENT_SECONDS = 0.4  

# Keeps silence detection highly sensitive to rapid breaks between words/phrases
PRIMARY_SILENCE_DB = "-35dB"
PRIMARY_SILENCE_DURATION = 0.20  # Lowered from 0.3s to catch even tiny breath pauses
SECONDARY_SILENCE_DURATION = 0.10 # Lowered from 0.15s for precision micro-pause splitting
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
    """
    MODIFIED: Instead of splitting at the midpoint of silences, this isolates 
    the actual speech zones. The cut locations are now placed tightly around 
    the speech boundaries (silence_end to the next silence_start).
    """
    if not silences:
        return [(0.0, total_duration)]

    segments = []
    current_start = 0.0

    for s_start, s_end in silences:
        # If there is valid audio between the last silence end and this silence start
        if s_start - current_start > 0.05:
            segments.append((current_start, s_start))
        current_start = s_end

    # Handle the final audio segment after the last detected silence
    if total_duration - current_start > 0.05:
        segments.append((current_start, total_duration))

    return segments


def _find_split_point(start, end, secondary_silences):
    """
    MODIFIED: Looks for a secondary breath/pause boundary to split overly 
    long speech segments, prioritizing actual pauses over a blind mathematical midpoint.
    """
    midpoint = (start + end) / 2.0
    candidates = []
    for s_start, s_end in secondary_silences:
        # Look for a clean cut right when the speaker stops talking inside this window
        cut = s_start 
        if start < cut < end:
            left, right = cut - start, end - cut
            if left >= MIN_SEGMENT_SECONDS and right >= MIN_SEGMENT_SECONDS:
                candidates.append(cut)
    if candidates:
        candidates.sort(key=lambda c: abs(c - midpoint))
        return candidates[0]
    return midpoint  


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
            pool.sort(key=lambda c: c[1])  
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
    """
    Returns a list of clean (start, end) tuples representing exact speech intervals.
    Silences are stripped out so your video editor knows exactly where dialogue lives.
    """
    from .library import probe_duration  

    total_duration = probe_duration(audio_path)
    primary = detect_silences(audio_path, PRIMARY_SILENCE_DB, PRIMARY_SILENCE_DURATION)
    secondary = detect_silences(audio_path, PRIMARY_SILENCE_DB, SECONDARY_SILENCE_DURATION)

    segments = _segments_from_silences(total_duration, primary)
    segments = _enforce_max_duration(segments, secondary)
    segments = _merge_short_segments(segments)
    return segments


def build_combined_audio_with_gaps(audio_path, segments, output_path, gap_seconds=DEFAULT_GAP_SECONDS):
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