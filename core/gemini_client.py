import json
import time

from google import genai
from google.genai import types

from . import config

SHOT_PLAN_SYSTEM_INSTRUCTION = """You are an experienced short-form video editor building a
B-roll shot plan for a vertical Instagram Reel.

IMPORTANT CONTEXT: This video will contain ONLY B-roll footage playing under the voiceover.
There is no talking-head shot of the narrator, no on-camera host, no interview setup —
just a continuous sequence of B-roll clips synced to the audio from start to finish.

Listen to the audio carefully and follow its actual pacing, emphasis, and sentence breaks —
don't just divide the total duration evenly. Start a new shot at natural beat changes in the
narration: a new sentence, a new idea, a change in tone, a listed item, a transition word.

For each shot, give an exact start_seconds and end_seconds timestamp anchored to the audio
(e.g. the first shot starts at 0.0). Shots must be contiguous and non-overlapping — each
shot's start_seconds must exactly equal the previous shot's end_seconds. No single shot may
span more than 3 seconds (end_seconds - start_seconds <= 3.0); split longer narration
segments into multiple consecutive shots instead.

STRICT RULES:
- Only request real-world camera footage: people, places, objects, actions, environments.
- NEVER request screenshots, UI recordings, screen recordings, text overlays, captions,
  graphics, charts, or animations.
- Each shot should represent ONE clear, simple visual idea a stock footage search could
  realistically find.
- Describe the PURPOSE and MEANING of each shot in the `purpose` field, but make
  `required`/`preferred`/`fallback` concrete and visual, not abstract.

For every shot, include a `pexels_search_terms` field: 2-4 short, concrete keywords (not
sentences, not abstract concepts) for a stock footage search. Example: ["man", "typing",
"laptop"]. Bad: ["a shift from passive learning to active creation"].

Return ONLY valid JSON, a list of shot objects, nothing else (no markdown fences):

[
  {
    "start_seconds": 0.0,
    "end_seconds": 2.4,
    "purpose": "short human description of the narrative purpose of this moment",
    "required": {"communicates": ["idea1", "idea2"]},
    "preferred": {"primary_action": ["action1"]},
    "fallback": {"primary_action": ["action2"]},
    "pexels_search_terms": ["keyword1", "keyword2", "keyword3"]
  }
]
"""

CLIP_ANALYSIS_SYSTEM_INSTRUCTION = """You are cataloguing a personal B-roll video library so
clips can be found and reused automatically in future videos.

Watch the clip and return ONLY valid JSON (no markdown fences, no commentary) matching this
exact schema:

{
  "description": "one sentence describing what happens in the clip",
  "subjects": ["..."],
  "primary_action": "single dominant action, e.g. typing",
  "secondary_actions": ["..."],
  "environment": "e.g. home office, city street, kitchen",
  "perspective": "e.g. first-person, over-the-shoulder, wide, close-up",
  "camera_motion": "e.g. static, handheld, pan, slow zoom",
  "mood": ["..."],
  "themes": ["..."],
  "search_intent": ["short phrases someone might search to find this clip"],
  "communicates": ["abstract ideas this footage communicates, e.g. focus, productivity, calm"],
  "use_cases": ["types of videos this would fit, e.g. morning routine, productivity tips"],
  "works_for": ["broader topics/niches this fits"],
  "keywords": ["..."],
  "reusability_score": 0,
  "notes": "anything else worth remembering about this clip"
}

reusability_score is 0-100: how broadly useful/generic this clip is across many different
videos (higher = more reusable, e.g. generic walking/typing shots score high; a very
specific one-off scene scores low).
"""


class GeminiClient:
    """Thin wrapper around google-genai for the three calls this app needs:
    analyzing a clip, generating a shot plan, and embedding text for search."""

    def __init__(self, settings=None):
        self.settings = settings or config.load_settings()
        api_key = config.get_api_key(self.settings)
        if not api_key:
            raise RuntimeError(
                "No Gemini API key found. Set the GEMINI_API_KEY environment variable "
                "before starting the app."
            )
        self.client = genai.Client(api_key=api_key)
        self.model_name = self.settings.get("gemini_model", "gemini-3.1-flash-lite")
        self.embedding_model = self.settings.get("embedding_model", "gemini-embedding-001")

    @staticmethod
    def _parse_json(text: str):
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)

    def _upload_and_wait(self, path):
        uploaded = self.client.files.upload(file=str(path))
        while uploaded.state.name == "PROCESSING":
            time.sleep(2)
            uploaded = self.client.files.get(name=uploaded.name)
        if uploaded.state.name != "ACTIVE":
            raise RuntimeError(f"Gemini failed to process file {path}: {uploaded.state.name}")
        return uploaded

    def analyze_clip(self, video_path) -> dict:
        uploaded = self._upload_and_wait(video_path)
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[uploaded, "Analyze this clip and return the JSON metadata."],
                config=types.GenerateContentConfig(
                    system_instruction=CLIP_ANALYSIS_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                ),
            )
            return self._parse_json(response.text)
        finally:
            try:
                self.client.files.delete(name=uploaded.name)
            except Exception:
                pass

    def generate_shot_plan(self, script_text: str, audio_path, audio_duration=None) -> list:
        uploaded = self._upload_and_wait(audio_path)
        try:
            duration_hint = (
                f"\n\nThe audio is exactly {audio_duration:.2f} seconds long. Your shots' "
                f"start_seconds/end_seconds must cover exactly 0.00 to {audio_duration:.2f} "
                f"with no gaps or overlaps."
                if audio_duration is not None else ""
            )
            prompt = (
                f"Script:\n{script_text}\n\n"
                "Build the shot plan from the voiceover audio and script above."
                f"{duration_hint}"
            )
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[uploaded, prompt],
                config=types.GenerateContentConfig(
                    system_instruction=SHOT_PLAN_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    max_output_tokens=65536,
                ),
            )
            finish_reason = response.candidates[0].finish_reason if response.candidates else None
            if str(finish_reason) == "MAX_TOKENS":
                print("WARNING: shot plan generation hit the token limit and was cut off.")
            return self._parse_json(response.text)
        finally:
            try:
                self.client.files.delete(name=uploaded.name)
            except Exception:
                pass

    def generate_text(self, prompt: str, system_instruction=None, response_mime_type=None):
        config_kwargs = {}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if response_mime_type:
            config_kwargs["response_mime_type"] = response_mime_type

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        return response.text

    def embed_text(self, text: str):
        response = self.client.models.embed_content(
            model=self.embedding_model,
            contents=text,
        )
        return response.embeddings[0].values