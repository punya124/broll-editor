REQUIRED_KEYS = {"duration", "purpose", "required", "preferred", "fallback"}


def build_shot_plan(script_text: str, audio_path, gemini_client) -> list:
    shots = gemini_client.generate_shot_plan(script_text, audio_path)
    if not isinstance(shots, list):
        raise ValueError("Gemini did not return a list of shots.")

    for shot in shots:
        missing = REQUIRED_KEYS - set(shot.keys())
        for key in missing:
            shot[key] = {} if key in ("required", "preferred", "fallback") else ""
        shot["duration"] = float(shot.get("duration") or 2.0)

    return shots
