import threading
import uuid
from pathlib import Path

from flask import Flask, request, jsonify, render_template, send_from_directory

from core import config, library, planner, timeline, reminders, footage_fill, matcher
from core.gemini_client import GeminiClient
from core.pexels_client import PexelsClient

import traceback

app = Flask(__name__)
config.ensure_dirs()

JOBS = {}  # job_id -> {"status": ..., "log": [...], "result": ..., "error": ...}
PROJECT_STATE = {}  # job_id -> {"shots", "folder", "audio_path", "threshold", "approved"}


def _log(job_id, message):
    JOBS[job_id]["log"].append(message)


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


@app.route("/api/library/status")
def library_status():
    settings = config.load_settings()
    folder = settings.get("library_folder", "")
    if not folder:
        return jsonify({"total": 0, "unanalyzed": 0})
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
            library.ensure_analyzed(folder, client, log=lambda m: _log(job_id, m))
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["result"] = library.get_library_status(folder)
        except Exception as e:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)
            tb = traceback.format_exc()
            JOBS[job_id]["traceback"] = tb
            print(tb)  # also prints to your terminal immediately

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id})


# --- Stage 1: Plan (segmentation + single Gemini call). No footage, no render. ---
@app.route("/api/project/plan", methods=["POST"])
def project_plan():
    settings = config.load_settings()
    folder = settings.get("library_folder", "")
    if not folder:
        return jsonify({"error": "No library folder configured."}), 400

    script_text = request.form.get("script", "").strip()
    audio_file = request.files.get("audio")
    if not script_text or not audio_file:
        return jsonify({"error": "Script and audio file are required."}), 400

    job_id = str(uuid.uuid4())
    audio_path = config.UPLOADS_DIR / f"{job_id}_{audio_file.filename}"
    audio_file.save(audio_path)
    config.mark_upload_active(audio_path)
    config.prune_uploads(limit=3)

    JOBS[job_id] = {"status": "running", "log": [], "result": None, "error": None}

    def worker():
        try:
            client = GeminiClient(settings)

            _log(job_id, "Making sure the library is up to date...")
            library.ensure_analyzed(folder, client, log=lambda m: _log(job_id, m))

            _log(job_id, "Segmenting narration and generating shot plan...")
            shots = planner.build_shot_plan(script_text, audio_path, client)

            PROJECT_STATE[job_id] = {
                "shots": shots,
                "folder": folder,
                "audio_path": str(audio_path),
                "threshold": float(settings.get("match_threshold", 85)),
                "approved": False,
            }

            JOBS[job_id]["status"] = "awaiting_approval"
            JOBS[job_id]["result"] = {
                "shots": [
                    {
                        "segment_id": s["segment_id"],
                        "text": s["text"],
                        "purpose": s["purpose"],
                        "shot_description": s["shot_description"],
                        "start": s["start"],
                        "end": s["end"],
                        "duration": s["duration"],
                    }
                    for s in shots
                ]
            }
        except Exception as e:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)
            tb = traceback.format_exc()
            JOBS[job_id]["traceback"] = tb
            print(tb)
            config.unmark_upload_active(audio_path)
            config.prune_uploads(limit=3)

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id})


# --- Stage 2: Approve -> footage search/fit/render. Only runs after this call. ---
@app.route("/api/project/approve", methods=["POST"])
def project_approve():
    body = request.get_json(force=True)
    job_id = body.get("job_id")
    state = PROJECT_STATE.get(job_id)
    if not state:
        return jsonify({"error": "Unknown or expired project."}), 400
    if state.get("approved"):
        return jsonify({"error": "This project has already been approved."}), 400

    state["approved"] = True
    settings = config.load_settings()
    JOBS[job_id]["status"] = "running"
    JOBS[job_id]["result"] = None

    def worker():
        audio_path = Path(state["audio_path"])
        try:
            client = GeminiClient(settings)
            pexels = PexelsClient()
            folder = state["folder"]
            threshold = state["threshold"]

            _log(job_id, "Searching library...")
            clips = library.scan_library(folder)
            clips_with_meta = [(c, m) for c in clips if (m := library.load_metadata(c))]

            assignments = []
            still_missing = []

            for shot in state["shots"]:
                _log(job_id, f"Finding footage for: {shot['purpose']}")
                candidates = matcher.find_best_matches(shot, clips_with_meta, client.embed_text, top_n=5)
                best = next((c for c in candidates if c[2] >= threshold), None)

                clip_infos = []
                if best:
                    path, meta, score = best
                    duration = meta.get("duration_seconds") or library.probe_duration(path)
                    clip_infos.append((path, duration))
                else:
                    query = footage_fill.build_search_query(shot)
                    _log(job_id, f"  Not in library — searching Pexels for: {query}")
                    result = footage_fill.resolve_shot_with_pexels(shot, folder, client, pexels)
                    if result.get("success"):
                        clip_infos = [(Path(p), d) for p, d in result["candidates"]]
                    else:
                        _log(job_id, f"  {result.get('reason')}")

                plan, remaining = timeline.fit_clips_to_duration(shot["duration"], clip_infos)
                for entry in plan:
                    assignments.append({
                        "shot": shot,
                        "clip_path": str(entry["path"]),
                        "trim_start": entry["trim_start"],
                        "trim_end": entry["trim_end"],
                        "speed": entry["speed"],
                    })

                if remaining > 0.01:
                    still_missing.append(shot)

            if still_missing:
                _log(job_id, f"{len(still_missing)} shot(s) still have no usable footage — adding reminders.")
                reminders.add_missing_footage_reminders(
                    [{"index": s["segment_id"], "shot": s} for s in still_missing]
                )

            if not assignments:
                raise RuntimeError("No footage could be assembled for any shot.")

            _log(job_id, "Rendering...")
            output_path = config.OUTPUTS_DIR / f"{job_id}.mp4"
            timeline.render_video(assignments, audio_path, output_path)

            _log(job_id, "Done.")
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["result"] = {
                "video_url": f"/api/project/download/{job_id}.mp4",
                "assignments": [
                    {"purpose": a["shot"].get("purpose"), "clip": Path(a["clip_path"]).name}
                    for a in assignments
                ],
                "still_missing": [s["purpose"] for s in still_missing],
            }
        except Exception as e:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)
            tb = traceback.format_exc()
            JOBS[job_id]["traceback"] = tb
            print(tb)
        finally:
            config.unmark_upload_active(audio_path)
            config.prune_uploads(limit=3)

    threading.Thread(target=worker, daemon=True).start()
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


@app.route("/api/missing/film", methods=["POST"])
def missing_film():
    body = request.get_json(force=True)
    shot = body.get("shot")
    if not shot:
        return jsonify({"error": "Missing shot data."}), 400
    title, notes = reminders.shot_to_reminder_fields(shot)
    ok = reminders.add_apple_reminder(title=title, notes=notes, list_name="B-Roll To Film")
    return jsonify({"success": ok})


if __name__ == "__main__":
    app.run(debug=True, port=8080)