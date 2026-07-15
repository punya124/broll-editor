import threading
import traceback
import uuid
from pathlib import Path

from flask import Flask, request, jsonify, render_template, send_from_directory

from core import config, library, planner, timeline, reminders, matcher, plan_storage
from core.gemini_client import GeminiClient

app = Flask(__name__)
config.ensure_dirs()

JOBS = {}          # job_id -> {"status", "log", "result", "error", "traceback"}
PROJECT_STATE = {}  # job_id -> in-progress project data (segments, resolution, etc.)


def _log(job_id, message):
    JOBS[job_id]["log"].append(message)


def _fail(job_id, e):
    JOBS[job_id]["status"] = "error"
    JOBS[job_id]["error"] = str(e)
    tb = traceback.format_exc()
    JOBS[job_id]["traceback"] = tb
    print(tb)


@app.route("/")
def index():
    settings = config.load_settings()
    return render_template("index.html", settings=settings)


@app.route("/api/settings", methods=["GET", "POST"])
def settings_route():
    if request.method == "POST":
        settings = config.load_settings()
        body = request.get_json(force=True)
        for key in ("library_folder", "gemini_model", "embedding_model", "match_threshold"):
            if key in body:
                settings[key] = body[key]
        config.save_settings(settings)
        return jsonify(settings)
    return jsonify(config.load_settings())


# ---------- Library: scan (rate-limited, 1 request/clip) -> review -> confirm ----------

@app.route("/api/library/status")
def library_status():
    settings = config.load_settings()
    folder = settings.get("library_folder", "")
    if not folder:
        return jsonify({"total": 0, "unanalyzed": 0, "pending_review": 0, "confirmed": 0})
    return jsonify(library.get_library_status(folder))


@app.route("/api/library/scan", methods=["POST"])
def library_scan():
    settings = config.load_settings()
    folder = settings.get("library_folder", "")
    if not folder:
        return jsonify({"error": "No library folder configured."}), 400

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "running", "log": [], "result": None, "error": None}

    def worker():
        try:
            client = GeminiClient(settings)
            library.scan_and_analyze_pending(folder, client, log=lambda m: _log(job_id, m))
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["result"] = library.get_library_status(folder)
        except Exception as e:
            _fail(job_id, e)

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/library/pending")
def library_pending():
    settings = config.load_settings()
    folder = settings.get("library_folder", "")
    if not folder:
        return jsonify({"clips": []})
    return jsonify({"clips": library.get_pending_review_clips(folder)})


@app.route("/api/library/confirm", methods=["POST"])
def library_confirm():
    settings = config.load_settings()
    folder = settings.get("library_folder", "")
    body = request.get_json(force=True)
    clip_name = body.get("clip_name")
    actions = body.get("actions", [])
    if not folder or not clip_name:
        return jsonify({"error": "Missing library folder or clip name."}), 400

    try:
        client = GeminiClient(settings)
        meta = library.confirm_clip_review(folder, clip_name, actions, client)
        return jsonify({"success": True, "action_interpretations": meta["action_interpretations"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/library/clip-file/<path:clip_name>")
def library_clip_file(clip_name):
    settings = config.load_settings()
    folder = settings.get("library_folder", "")
    folder_path = Path(folder).resolve()
    requested = (folder_path / clip_name).resolve()
    if not str(requested).startswith(str(folder_path)):
        return jsonify({"error": "Invalid path."}), 400
    return send_from_directory(folder_path, clip_name)


# ---------- Action plan: generate -> reject(+feedback) / approve -> footage resolution ----------

@app.route("/api/action-plan/generate", methods=["POST"])
def action_plan_generate():
    settings = config.load_settings()
    folder = settings.get("library_folder", "")
    if not folder:
        return jsonify({"error": "No library folder configured."}), 400

    audio_file = request.files.get("audio")
    if not audio_file:
        return jsonify({"error": "Audio file is required."}), 400

    job_id = str(uuid.uuid4())
    audio_path = config.UPLOADS_DIR / f"{job_id}_{audio_file.filename}"
    audio_file.save(audio_path)

    JOBS[job_id] = {"status": "running", "log": [], "result": None, "error": None}

    def worker():
        try:
            client = GeminiClient(settings)
            vocabulary = library.get_action_vocabulary()

            _log(job_id, "Segmenting narration and generating action plan...")
            project_name, segments, raw_segments, combined_audio_path = planner.build_action_plan(
                audio_path, client, vocabulary
            )

            PROJECT_STATE[job_id] = {
                "project_name": project_name,
                "segments": segments,
                "raw_segments": raw_segments,
                "combined_audio_path": str(combined_audio_path),
                "folder": folder,
                "audio_path": str(audio_path),
                "threshold": float(settings.get("match_threshold", 85)),
            }

            JOBS[job_id]["status"] = "awaiting_approval"
            JOBS[job_id]["result"] = {"project_name": project_name, "segments": segments}
        except Exception as e:
            _fail(job_id, e)

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/action-plan/reject", methods=["POST"])
def action_plan_reject():
    body = request.get_json(force=True)
    job_id = body.get("job_id")
    feedback = body.get("feedback", "")

    state = PROJECT_STATE.get(job_id)
    if not state:
        return jsonify({"error": "Unknown or expired project."}), 400

    settings = config.load_settings()
    JOBS[job_id]["status"] = "running"

    def worker():
        try:
            client = GeminiClient(settings)
            vocabulary = library.get_action_vocabulary()
            project_name, segments, raw_segments, combined_audio_path = planner.build_action_plan(
                Path(state["audio_path"]), client, vocabulary,
                feedback=feedback,
                cached_segments=state["raw_segments"],
                combined_audio_path=Path(state["combined_audio_path"]),
            )
            state["project_name"] = project_name
            state["segments"] = segments

            JOBS[job_id]["status"] = "awaiting_approval"
            JOBS[job_id]["result"] = {"project_name": project_name, "segments": segments}
        except Exception as e:
            _fail(job_id, e)

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id})


def _run_auto_match(job_id):
    """Tries to match every segment's main_suggestion against the confirmed
    library. Segments that clear the threshold are auto-resolved (possibly
    with several candidate clips stitched together); the rest are marked
    needs_decision for the user to answer via /api/action-plan/decide."""
    state = PROJECT_STATE[job_id]
    settings = config.load_settings()
    client = GeminiClient(settings)
    folder = state["folder"]
    threshold = state["threshold"]

    clips = library.scan_library(folder)
    clips_with_meta = []
    for c in clips:
        meta = library.load_metadata(c)
        if meta and meta.get("review_status") == "confirmed":
            clips_with_meta.append((c, meta))

    cache = {}
    resolution = {}
    for seg in state["segments"]:
        sid = seg["segment_id"]
        candidates = matcher.find_best_action_matches(
            seg["main_suggestion"], clips_with_meta, client.embed_text, cache, top_n=5
        )
        good = [c for c in candidates if c[2] >= threshold][:3]
        if good:
            resolution[sid] = {
                "status": "auto_matched",
                "candidates": [
                    (str(p), m.get("duration_seconds") or library.probe_duration(p))
                    for p, m, s in good
                ],
                "score": round(good[0][2], 1),
            }
        else:
            resolution[sid] = {"status": "needs_decision"}

    state["resolution"] = resolution
    state["clips_with_meta"] = clips_with_meta
    state["embed_cache"] = cache
    state["client"] = client
    return resolution


def _maybe_finalize(job_id):
    """Checks whether every segment has a decided status. If any are still
    needs_decision, does nothing (waits for the user). If all are decided but
    at least one is film_it, stops without rendering. Otherwise, renders."""
    state = PROJECT_STATE[job_id]
    resolution = state["resolution"]
    statuses = [r["status"] for r in resolution.values()]

    if any(s == "needs_decision" for s in statuses):
        return False

    if any(s == "film_it" for s in statuses):
        pending = [
            seg["main_suggestion"]
            for seg in state["segments"]
            if resolution[seg["segment_id"]]["status"] == "film_it"
        ]
        JOBS[job_id]["status"] = "incomplete"
        JOBS[job_id]["result"] = {
            "message": (
                "Some segments still need real footage. Reminders have been added under "
                f"'{state['project_name']}'. Once new clips are filmed and confirmed in the "
                "library, choose this plan from 'Choose Existing', reupload the audio, and "
                "pick up where you left off."
            ),
            "pending_film": pending,
        }
        return True

    _log(job_id, "Rendering...")
    assignments = []
    for seg in state["segments"]:
        r = resolution[seg["segment_id"]]
        clip_infos = [(Path(p), d) for p, d in r["candidates"]]
        plan, remaining = timeline.fit_clips_to_duration(seg["duration"], clip_infos)
        for entry in plan:
            assignments.append({
                "shot": seg,
                "clip_path": str(entry["path"]),
                "trim_start": entry["trim_start"],
                "trim_end": entry["trim_end"],
                "speed": entry["speed"],
            })

    output_path = config.OUTPUTS_DIR / f"{job_id}.mp4"
    timeline.render_video(assignments, Path(state["audio_path"]), output_path)

    JOBS[job_id]["status"] = "done"
    JOBS[job_id]["result"] = {
        "video_url": f"/api/project/download/{job_id}.mp4",
        "assignments": [
            {"action": a["shot"]["main_suggestion"], "clip": Path(a["clip_path"]).name}
            for a in assignments
        ],
    }
    return True


def _start_footage_resolution(job_id):
    def worker():
        try:
            _log(job_id, "Searching library for matching footage...")
            resolution = _run_auto_match(job_id)
            if any(r["status"] == "needs_decision" for r in resolution.values()):
                JOBS[job_id]["status"] = "awaiting_footage_decisions"
                JOBS[job_id]["result"] = {
                    "segments": PROJECT_STATE[job_id]["segments"],
                    "resolution": resolution,
                }
            else:
                _maybe_finalize(job_id)
        except Exception as e:
            _fail(job_id, e)

    threading.Thread(target=worker, daemon=True).start()


@app.route("/api/action-plan/approve", methods=["POST"])
def action_plan_approve():
    body = request.get_json(force=True)
    job_id = body.get("job_id")
    state = PROJECT_STATE.get(job_id)
    if not state:
        return jsonify({"error": "Unknown or expired project."}), 400

    plan_path = plan_storage.save_plan(state["project_name"], state["segments"])
    state["plan_file"] = str(plan_path)

    JOBS[job_id]["status"] = "running"
    _log(job_id, f"Plan approved and saved as {plan_path.name}.")

    _start_footage_resolution(job_id)
    return jsonify({"job_id": job_id, "plan_file": plan_path.name})


@app.route("/api/action-plan/decide", methods=["POST"])
def action_plan_decide():
    body = request.get_json(force=True)
    job_id = body.get("job_id")
    segment_id = body.get("segment_id")
    decision = body.get("decision")  # "fallback" or "film"

    state = PROJECT_STATE.get(job_id)
    if not state:
        return jsonify({"error": "Unknown or expired project."}), 400

    seg = next((s for s in state["segments"] if s["segment_id"] == segment_id), None)
    if not seg:
        return jsonify({"error": "Unknown segment."}), 400

    if decision == "fallback":
        if not seg.get("fallback"):
            return jsonify({"error": "This segment has no fallback action available."}), 400
        client = state["client"]
        candidates = matcher.find_best_action_matches(
            seg["fallback"], state["clips_with_meta"], client.embed_text,
            state["embed_cache"], top_n=3
        )
        candidates = [c for c in candidates if c[2] > 0]
        if not candidates:
            return jsonify({"error": "Fallback action not found in library - try rescanning."}), 400
        state["resolution"][segment_id] = {
            "status": "fallback_chosen",
            "candidates": [
                (str(p), m.get("duration_seconds") or library.probe_duration(p))
                for p, m, s in candidates
            ],
            "score": round(candidates[0][2], 1),
        }
    elif decision == "film":
        title, notes = reminders.segment_to_reminder_fields(seg)
        reminders.add_apple_reminder(title=title, notes=notes, list_name=state["project_name"])
        state["resolution"][segment_id] = {"status": "film_it"}
    else:
        return jsonify({"error": "decision must be 'fallback' or 'film'."}), 400

    try:
        finalized = _maybe_finalize(job_id)
    except Exception as e:
        _fail(job_id, e)
        finalized = True

    return jsonify({
        "resolved_segment": segment_id,
        "finalized": finalized,
        "job_status": JOBS[job_id]["status"],
    })


@app.route("/api/action-plan/list")
def action_plan_list():
    return jsonify({"plans": plan_storage.list_plans()})


@app.route("/api/action-plan/load-existing", methods=["POST"])
def action_plan_load_existing():
    settings = config.load_settings()
    folder = settings.get("library_folder", "")
    plan_file = request.form.get("plan_file")
    audio_file = request.files.get("audio")
    if not folder or not plan_file or not audio_file:
        return jsonify({"error": "Library folder, plan file, and audio are all required."}), 400

    try:
        plan_data = plan_storage.load_plan(plan_file)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    job_id = str(uuid.uuid4())
    audio_path = config.UPLOADS_DIR / f"{job_id}_{audio_file.filename}"
    audio_file.save(audio_path)

    PROJECT_STATE[job_id] = {
        "project_name": plan_data["project_name"],
        "segments": plan_data["segments"],
        "folder": folder,
        "audio_path": str(audio_path),
        "threshold": float(settings.get("match_threshold", 85)),
        "plan_file": plan_file,
    }
    JOBS[job_id] = {
        "status": "running",
        "log": [f"Loaded plan '{plan_data['project_name']}'."],
        "result": None, "error": None,
    }

    _start_footage_resolution(job_id)
    return jsonify({"job_id": job_id})


@app.route("/api/job/<job_id>")
def job_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job id."}), 404
    return jsonify(job)


@app.route("/api/project/download/<filename>")
def download(filename):
    return send_from_directory(config.OUTPUTS_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True, port=8080)