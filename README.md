# B-Roll Auto Editor

A personal, local desktop tool that assembles vertical Instagram Reels automatically from
a reusable library of your own B-roll footage. It's a small Flask app you run on your own
machine — everything (footage, metadata, generated videos) stays on your laptop.

## How it's built (intentionally simple)

```
broll-editor/
  app.py                  Flask server: routes + background job runner
  core/
    config.py              Settings (library folder, model, threshold) stored as JSON
    gemini_client.py        All Gemini calls: clip analysis, shot planning, embeddings
    library.py             Scans your folder, stores metadata as a .json file next to each clip
    matcher.py              Scores clips against a shot's requirements (embeddings + tag overlap)
    planner.py               Validates the shot plan Gemini returns
    timeline.py              Picks clips, computes center-trims, runs ffmpeg
  templates/index.html      One-page UI
  static/app.js, style.css  Vanilla JS — no build step, no frontend framework
  data/                    Created automatically: settings, uploads, rendered outputs
```

No database, no task queue, no frontend build tooling. Clip metadata is stored as a plain
`clip.mp4.json` file next to each clip (human-readable, easy to inspect or hand-edit),
matching the "video.mp4 / video.json" option from the spec. Long-running work (analyzing
clips, generating a video) runs in a background thread and the browser polls a `/api/job/<id>`
endpoint for progress — no websockets needed.

## Setup

1. **Install FFmpeg** (required for trimming/rendering):
   - macOS: `brew install ffmpeg`
   - Windows: `winget install ffmpeg` (or download from ffmpeg.org and add to PATH)
   - Linux: `sudo apt install ffmpeg`

2. **Get a Gemini API key** from [Google AI Studio](https://aistudio.google.com/apikey).

3. **Install Python dependencies**:
   ```bash
   cd broll-editor
   pip install -r requirements.txt
   ```

4. **Set your API key** as an environment variable:
   ```bash
   export GEMINI_API_KEY="your-key-here"      # macOS/Linux
   setx GEMINI_API_KEY "your-key-here"          # Windows
   ```

5. **Run it**:
   ```bash
   python app.py
   ```
   Then open **http://localhost:5000** in your browser.

## Using it

1. **Library** — paste the absolute path to your B-roll folder (e.g. `/Users/me/BRoll`),
   click Save, then **Scan & Analyze**. Every new clip gets sent to Gemini once, tagged
   with structured metadata (description, mood, what it communicates, etc.), embedded for
   semantic search, and cached as a `.json` sidecar file next to the clip. Re-scanning
   only analyzes clips that don't already have metadata, so you can add new footage over
   time without re-processing everything.

2. **New Project** — paste your script and upload your finished voiceover audio file, then
   click **Generate Video**. The app:
   - Sends the script + audio to Gemini to get a shot-by-shot plan (durations + the
     *meaning* each shot needs to communicate — never screenshots, captions, or overlays).
   - Searches your library for the best-matching clip for each shot (embedding similarity,
     boosted when a clip's own tags literally match the shot's requirements).
   - If every shot clears the match threshold, it center-trims each selected clip, stitches
     them in order with FFmpeg, and muxes in your voiceover. You'll get a video player and
     a list of which clip was used for each shot.
   - If any shot can't be matched above the threshold, no video is rendered — you'll see a
     **Missing Footage** report instead (what's needed, its purpose, its duration) so you
     know exactly what to go film next.

3. **Settings** — switch between `gemini-2.5-flash` and `gemini-3.1-flash-lite`, and adjust
   the match threshold (default 85%) if you want the matcher to be stricter or looser.

## Notes / things worth knowing

- **Model names**: `gemini-3.1-flash-lite` is set up as an option since you mentioned it,
  but I haven't been able to verify it against the live API from this environment (no
  network access here). If it errors, switch the model dropdown to `gemini-2.5-flash`,
  which is a known-good current model.
- **What I actually tested**: I ran the full FFmpeg trim → scale/crop to 1080×1920 →
  concatenate → audio-mux pipeline end-to-end with synthetic test clips, and confirmed the
  output is correct (1080×1920, 30fps, H.264/AAC). I was **not** able to test the Gemini
  calls themselves (clip analysis, shot planning, embeddings) since this build environment
  has no network access — those are standard `google-generativeai` SDK calls, but if you
  hit an error on your first real run, paste it back to me and I'll fix it fast.
- **Costs**: clip analysis uses video input (charged per video, not just text) and only
  runs once per clip, ever, unless you delete the sidecar `.json`. Shot planning uses audio
  input each time you generate a video.
- **Scale**: matching uses plain Python cosine similarity over embeddings (no vector DB) —
  totally fine for a personal library in the tens-to-low-hundreds of clips, as scoped in the
  spec. If your library grows into the thousands, that's the first thing worth optimizing.
- **Out of scope** (per the spec): captions, overlays, motion graphics, screen recordings,
  music selection, multi-folder libraries — all left for your manual editing pass.
