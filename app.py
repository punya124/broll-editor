import threading
import uuid
from pathlib import Path

from flask import Flask, request, jsonify, render_template, send_from_directory

from core import config, library, planner, timeline, reminders, footage_fill
from core.gemini_client import GeminiClient
from core.pexels_client import PexelsClient

app = Flask(__name__)
config.ensure_dirs()

JOBS = {}  # job_id -> {"status": ..., "log": [...], "result": ..., "error": ...}
PROJECT_STATE = {}

def _render_and_finish(job_id):
    state = PROJECT_STATE[job_id]
    assignments = sorted(state["assignments"], key=lambda a: a["index"])
    output_path = config.OUTPUTS_DIR / f"{job_id}.mp4"
    timeline.render_video(assignments, state["audio_path"], output_path)

    _log(job_id, "Done.")
    JOBS[job_id]["status"] = "done"
    JOBS[job_id]["result"] = {
        "video_url": f"/api/project/download/{job_id}.mp4",
        "assignments": [
            {
                "purpose": a["shot"].get("purpose"),
                "clip": Path(a["clip_path"]).name,
                "score": round(a["score"], 1),
            }
            for a in assignments
        ],
    }
    Path(state["audio_path"]).unlink(missing_ok=True)


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

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/project/generate", methods=["POST"])
def project_generate():
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

    JOBS[job_id] = {"status": "running", "log": [], "result": None, "error": None}

    def worker():
        try:
            client = GeminiClient(settings)

            _log(job_id, "Making sure the library is up to date...")
            library.ensure_analyzed(folder, client, log=lambda m: _log(job_id, m))

            _log(job_id, "Generating shot plan...")
            shots = planner.build_shot_plan(script_text, audio_path, client)

            _log(job_id, "Searching library...")
            clips = library.scan_library(folder)
            clips_with_meta = []
            for c in clips:
                meta = library.load_metadata(c)
                if meta:
                    clips_with_meta.append((c, meta))

            _log(job_id, "Validating coverage...")
            threshold = float(settings.get("match_threshold", 85))
            assignments, missing = timeline.select_clips(
                shots, clips_with_meta, client.embed_text, threshold=threshold
            )

            PROJECT_STATE[job_id] = {
                "shots": shots,
                "assignments": assignments,
                "missing": missing,
                "audio_path": str(audio_path),
                "folder": folder,
                "threshold": threshold,
            }

            if missing:
                JOBS[job_id]["status"] = "missing_footage"
                JOBS[job_id]["result"] = {"missing": missing}
                reminders.add_missing_footage_reminders(missing)
                return

            _log(job_id, "Rendering...")
            _render_and_finish(job_id)
        except Exception as e:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)
            Path(audio_path).unlink(missing_ok=True)
        try:
            client = GeminiClient(settings)

            _log(job_id, "Making sure the library is up to date...")
            library.ensure_analyzed(folder, client, log=lambda m: _log(job_id, m))

            _log(job_id, "Generating shot plan...")
            shots = planner.build_shot_plan(script_text, audio_path, client)

            _log(job_id, "Searching library...")
            clips = library.scan_library(folder)
            clips_with_meta = []
            for c in clips:
                meta = library.load_metadata(c)
                if meta:
                    clips_with_meta.append((c, meta))

            _log(job_id, "Validating coverage...")
            threshold = float(settings.get("match_threshold", 85))
            assignments, missing = timeline.select_clips(
                shots, clips_with_meta, client.embed_text, threshold=threshold
            )

            if missing:
                JOBS[job_id]["status"] = "missing_footage"
                JOBS[job_id]["result"] = {"missing": missing}
                reminders.add_missing_footage_reminders(missing)
                return

            _log(job_id, "Rendering...")
            output_path = config.OUTPUTS_DIR / f"{job_id}.mp4"
            timeline.render_video(assignments, audio_path, output_path)

            _log(job_id, "Done.")
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["result"] = {
                "video_url": f"/api/project/download/{job_id}.mp4",
                "assignments": [
                    {
                        "purpose": a["shot"].get("purpose"),
                        "clip": Path(a["clip_path"]).name,
                        "score": round(a["score"], 1),
                    }
                    for a in assignments
                ],
            }
        except Exception as e:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)
        finally:
            audio_path.unlink(missing_ok=True)

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


@app.route("/api/missing/pexels", methods=["POST"])
def missing_pexels():
    body = request.get_json(force=True)
    project_job_id = body.get("job_id")
    shot_index = body.get("index")

    state = PROJECT_STATE.get(project_job_id)
    if not state:
        return jsonify({"error": "Unknown or expired project."}), 400

    missing_item = next((m for m in state["missing"] if m["index"] == shot_index), None)
    if not missing_item:
        return jsonify({"error": "That shot is no longer missing."}), 400

    settings = config.load_settings()
    pexels_job_id = str(uuid.uuid4())
    JOBS[pexels_job_id] = {"status": "running", "log": [], "result": None, "error": None}

    def worker():
        try:
            client = GeminiClient(settings)
            pexels = PexelsClient()
            _log(pexels_job_id, "Searching Pexels...")
            result = footage_fill.resolve_shot_with_pexels(
                missing_item["shot"], state["folder"], client, pexels,
                threshold=state["threshold"],
            )

            if not result.get("success"):
                JOBS[pexels_job_id]["status"] = "done"
                JOBS[pexels_job_id]["result"] = {"success": False, "reason": result.get("reason")}
                return

            clip_path = Path(state["folder"]) / result["clip"]
            meta = library.load_metadata(clip_path)
            duration = meta.get("duration_seconds") or library.probe_duration(clip_path)
            start, end = timeline.compute_trim(duration, missing_item["shot"]["duration"])

            state["assignments"].append({
                "index": missing_item["index"],
                "shot": missing_item["shot"],
                "clip_path": str(clip_path),
                "score": result["score"],
                "trim_start": start,
                "trim_end": end,
            })
            state["missing"] = [m for m in state["missing"] if m["index"] != missing_item["index"]]

            if not state["missing"]:
                _render_and_finish(project_job_id)
                JOBS[pexels_job_id]["status"] = "done"
                JOBS[pexels_job_id]["result"] = {
                    "success": True,
                    "video_ready": True,
                    "clip": result["clip"],
                    "score": result["score"],
                    "video_url": JOBS[project_job_id]["result"]["video_url"],
                    "assignments": JOBS[project_job_id]["result"]["assignments"],
                }
            else:
                JOBS[pexels_job_id]["status"] = "done"
                JOBS[pexels_job_id]["result"] = {
                    "success": True,
                    "video_ready": False,
                    "clip": result["clip"],
                    "score": result["score"],
                    "remaining_missing": len(state["missing"]),
                }
        except Exception as e:
            JOBS[pexels_job_id]["status"] = "error"
            JOBS[pexels_job_id]["error"] = str(e)

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": pexels_job_id})

if __name__ == "__main__":
    app.run(debug=True, port=8080)
