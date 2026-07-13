import json
import time

from google import genai
from google.genai import types

from . import config



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

SEGMENT_PLAN_SYSTEM_INSTRUCTION = """You are an experienced short-form video editor. You will
hear an audio file containing several narration segments in a row, each separated by a short
inserted silence. You are also given the full script for context.

Timing is NOT your responsibility — it has already been determined deterministically outside
this request. Do not return any timestamps or durations.

Your only job is to imagine, for each segment, a real B-roll shot a camera could actually
film — never a restatement of what's being said. `purpose` and `shot_description` must
describe DIFFERENT things:

- "purpose": one short phrase for the narrative beat (e.g. "advice #2", "example - context",
  "closing line"). This is bookkeeping, not a visual.
- "shot_description": one concrete sentence describing an actual filmable shot — subject,
  action, setting, framing. This is what a camera operator would be handed as a shot list
  entry. It must NEVER just paraphrase or summarize the narration.

EVERY segment gets a real shot, even transitions, examples, or a closing line with nothing
literal to film. Translate the FEELING or FUNCTION of the line into an image instead of
leaving it blank. Never return empty required/preferred/fallback objects.

Examples of the transformation you must do (this is the whole point of your job):

Narration: "Firstly, stop listing classes."
BAD shot_description: "Advice about not listing classes." (this just restates the line)
GOOD shot_description: "Close-up of a hand crossing out a bullet-point list on paper with a pen."

Narration: "Deploy something. Let people see you actually know how to build."
BAD: "Advocating for real deployment."
GOOD: "Person clicking a button on a laptop, then leaning back with a satisfied expression."

Narration: "Specific numbers matter."
BAD: "Emphasizing data."
GOOD: "Close-up of a hand pointing at numbers on a printed chart or spreadsheet."

Narration: "Thanks for watching."
BAD: "Closing the video." / leaving required/preferred empty
GOOD: "Person smiling and giving a small wave toward the camera."

For each narration segment, in order, return:
- "text": the transcribed narration spoken in that segment
- "purpose": short narrative-beat label (NOT a visual)
- "shot_description": one concrete filmable sentence (the actual shot — see examples above)
- "required"/"preferred"/"fallback": concrete, filmable visual requirements derived from
  shot_description — subjects, actions, settings. Never abstract-only, never empty.
- "pexels_search_terms": 2-4 short, concrete keywords for a stock footage search, drawn
  directly from shot_description

STRICT RULES:
- Only request real-world camera footage. This video contains ONLY B-roll under the
  voiceover — no talking head, no on-camera host, no screenshots, no UI, no captions,
  no overlays, no graphics.
- Each shot should represent ONE clear, simple, filmable visual idea.
- Never combine two narration segments into one object. Never skip a segment.
- Return the objects in the same order as the narration segments.

Return ONLY valid JSON: a list of exactly N objects, nothing else (no markdown fences):
[
  {
    "text": "...",
    "purpose": "...",
    "shot_description": "...",
    "required": {"communicates": ["idea1"]},
    "preferred": {"primary_action": ["action1"]},
    "fallback": {"primary_action": ["action2"]},
    "pexels_search_terms": ["keyword1", "keyword2"]
  }
]
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

    
    def generate_segment_plans(self, script_text: str, combined_audio_path, segment_durations: list) -> list:
        uploaded = self._upload_and_wait(combined_audio_path)
        try:
            n = len(segment_durations)
            segment_list_text = "\n".join(
                f"Segment {i + 1}: ~{d:.2f}s of narration" for i, d in enumerate(segment_durations)
            )
            prompt = (
                f"Full script for context:\n{script_text}\n\n"
                f"The audio contains exactly {n} narration segments, in order, each separated by "
                f"an inserted silence. Do not combine or skip segments.\n\n{segment_list_text}\n\n"
                f"Return exactly {n} JSON objects, one per segment, in order."
            )
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[uploaded, prompt],
                config=types.GenerateContentConfig(
                    system_instruction=SEGMENT_PLAN_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    max_output_tokens=65536,
                ),
            )
            finish_reason = response.candidates[0].finish_reason if response.candidates else None
            if str(finish_reason) == "MAX_TOKENS":
                print("WARNING: segment plan generation hit the token limit and was cut off.")
            plans = self._parse_json(response.text)
            if not isinstance(plans, list) or len(plans) != n:
                got = len(plans) if isinstance(plans, list) else "a non-list response"
                raise ValueError(f"Expected {n} segment plans from Gemini, got {got}.")
            return plans
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