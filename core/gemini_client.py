import json
import time

from google import genai
from google.genai import types

from . import config

CLIP_ANALYSIS_SYSTEM_INSTRUCTION = """You are cataloguing a personal video clip library for a 
social media video editor. This editor builds fast-paced, high-energy skits that blend narrative 
acting (A-roll) with quick functional or creative cutaways (B-roll).

Watch the clip and identify the concrete action(s) taking place. This could be a person acting 
out a scene, a screen recording of an application, or a close-up of a physical prop. Then generate 
several DIFFERENT plausible descriptions of what that same action or visual could reasonably be 
interpreted as, ranging from literal to a little more interpretive. A clip of someone tossing papers 
could be described as "tossing papers", "throwing away work", "failing a task", or "decluttering".

Return ONLY valid JSON (no markdown fences, no commentary) matching this exact schema:

{
  "description": "one factual sentence describing exactly what happens in the clip",
  "action_interpretations": ["3 to 6 short action phrases, literal to interpretive"],
  "environment": "brief setting, e.g. cubicle office, home desk, screen recording",
  "notes": "anything else worth remembering about this clip for future matching (e.g., shirt color, props used)"
}

action_interpretations should each be short (2-6 words), plain, and describe a visual ACTION, 
STATE, or OBJECT being showcased—not a mood or abstract concept. Every entry must be a 
plausible reading of what's ACTUALLY visible in the clip; don't invent actions that aren't 
happening.
"""

ACTION_PLAN_SYSTEM_INSTRUCTION = """You are planning footage for a high-energy, fast-paced social 
media video edit. The style relies on rapid cuts (1-1.5 seconds per clip) alternating between 
narrative acting shots (A-roll), close-up screen recordings, and creative physical props to 
maintain high visual interest. Lip sync is irrelevant (it's a voiceover).

You will hear an audio file containing several narration segments in a row, each separated by 
a short inserted silence. Timing is NOT your responsibility—it has already been determined 
deterministically outside this request. Do not return any timestamps or durations.

For every segment, in order, provide TWO action ideas:
- "main_suggestion": the ideal shot for this fast-paced moment—a short, concrete, filmable action 
  description. It should vary dynamically between narrative acting (e.g. "person facepalming at desk"), 
  functional screen B-roll (e.g. "POV scrolling a massive GitHub list"), or creative prop B-roll (e.g. "tossing a massive stack of papers"). 
  This does NOT need to already exist in the footage library.
- "fallback": an action you are CERTAIN is already available, because it MUST be copied 
  EXACTLY (case-insensitive is fine, but don't paraphrase) from this list of actions the 
  library already has real footage for:
  {vocab_text}
  Pick whichever existing action is the closest reasonable substitute for this segment, even 
  if imperfect. If the list above says the library is empty, return an empty string for 
  fallback on every segment.

Also propose one overall "project_name" for this whole video: short, filesystem-safe (letters, 
numbers, underscores or hyphens only), descriptive of the video's topic.

{feedback_block}

For each narration segment, in order, return "text" (the transcribed narration spoken in that 
segment) plus main_suggestion/fallback as described above. Never combine two segments into one 
object. Never skip a segment. Return objects in the same order as the narration segments.

Return ONLY valid JSON, nothing else (no markdown fences):
{{
  "project_name": "...",
  "segments": [
    {{"text": "...", "main_suggestion": "...", "fallback": "..."}}
  ]
}}
"""


class GeminiClient:
    """Wrapper around google-genai for the calls this app needs: analyzing a
    clip's actions, generating an action plan, and embedding text for search."""

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
        self.embedding_model = self.settings.get("embedding_model", "gemini-embedding-2")

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
        """Exactly ONE Gemini request. Returns the raw analysis - caller decides
        whether/how to persist it (library.py saves it as review_status='pending')."""
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

    def generate_action_plan(self, combined_audio_path, segment_durations: list,
                              action_vocabulary: dict, feedback: str = None):
        """One request covering the whole video. Returns (project_name, segments)."""
        uploaded = self._upload_and_wait(combined_audio_path)
        try:
            n = len(segment_durations)
            vocab_list = sorted(action_vocabulary.keys()) if action_vocabulary else []
            vocab_text = (
                ", ".join(f'"{a}"' for a in vocab_list)
                if vocab_list else
                "(the library is currently empty - no fallback actions are available yet)"
            )
            feedback_block = (
                f'The user reviewed a previous version of this plan and rejected it with this '
                f'feedback - revise the plan accordingly: "{feedback}"'
                if feedback else ""
            )
            system_instruction = ACTION_PLAN_SYSTEM_INSTRUCTION.format(
                vocab_text=vocab_text, feedback_block=feedback_block
            )

            segment_list_text = "\n".join(
                f"Segment {i + 1}: ~{d:.2f}s" for i, d in enumerate(segment_durations)
            )
            prompt = (
                f"The audio contains exactly {n} narration segments, in order, each separated "
                f"by an inserted silence. Do not combine or skip segments.\n\n{segment_list_text}\n\n"
                f"Return exactly {n} segment objects, in order, plus one project_name."
            )

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[uploaded, prompt],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    max_output_tokens=65536,
                ),
            )
            finish_reason = response.candidates[0].finish_reason if response.candidates else None
            if str(finish_reason) == "MAX_TOKENS":
                print("WARNING: action plan generation hit the token limit and was cut off.")

            data = self._parse_json(response.text)
            segments = data.get("segments")
            if not isinstance(segments, list) or len(segments) != n:
                got = len(segments) if isinstance(segments, list) else "a non-list response"
                raise ValueError(f"Expected {n} segment plans from Gemini, got {got}.")
            return data.get("project_name", "untitled_project"), segments
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
            config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY"),
        )
        return response.embeddings[0].values