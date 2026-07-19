import json
import threading
import traceback
import uuid
from pathlib import Path

from flask import Flask, request, jsonify, render_template, send_from_directory

from core import config, library, planner, timeline, reminders, matcher, plan_storage, xml_export
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
    
    # Track raw vector matches for a secondary batch-reranking phase
    segment_matches = {}

    # Phase 1: Run the initial embedding matches across all segments
    for seg in state["segments"]:
        sid = seg["segment_id"]
        good = []
        used_fallback = False

        # 1. Try embedding match on the main suggestion
        candidates = matcher.find_best_action_matches(
            seg["main_suggestion"], clips_with_meta, client.embed_text, cache, top_n=5
        )
        if candidates:
            good = [c for c in candidates if c[2] >= threshold]

        # 2. If embedding fails, leverage the explicit fallback string from the LLM
        if not good and seg.get("fallback"):
            used_fallback = True
            fallback_candidates = matcher.find_best_action_matches(
                seg["fallback"], clips_with_meta, client.embed_text, cache, top_n=5
            )
            if fallback_candidates:
                good = [c for c in fallback_candidates if c[2] >= threshold]

        if good:
            segment_matches[sid] = {
                "query": seg["fallback"] if used_fallback else seg["main_suggestion"],
                "candidates": good
            }
        else:
            resolution[sid] = {"status": "needs_decision"}

    # Phase 2: Batch LLM Reranking using action_interpretations
    rerank_payload = {}
    for sid, match_data in segment_matches.items():
        if len(match_data["candidates"]) > 1:
            clips_payload = []
            for idx, (p, m, s) in enumerate(match_data["candidates"]):
                # Extract actions if available; format as clean list or fallback to description
                actions = m.get("action_interpretations", [])
                if not actions and m.get("description"):
                    actions = [m["description"]]
                
                clips_payload.append({
                    "index": idx,
                    "actions": actions
                })
                
            rerank_payload[sid] = {
                "target_action": match_data["query"],
                "clips": clips_payload
            }
    print("rerank_payload", rerank_payload)  # Debugging line to check the payload before sending to LLM
    if rerank_payload:
        prompt = (
            "You are a professional video editing assistant. I am providing a JSON payload containing several "
            "video segments and their candidate source clips. For each segment, rerank the clips from best to worst "
            "based strictly on how accurately their interpreted meaning ('actions') fulfills the 'target_action'. We don't give a shit about anything other than whether or not this is accurately portraying what is being said.\n\n"
            f"Input Data:\n{json.dumps(rerank_payload, indent=2)}\n\n"
            "Respond ONLY with a valid JSON object matching this schema. Do not include markdown blocks or extra text:\n"
            "{\n"
            "  \"segment_id_1\": [2, 0, 1],\n"
            "  \"segment_id_2\": [1, 0]\n"
            "}\n"
            "Where the arrays contain the clip indices ordered from best match to worst match."
        )
        
        try:
            response_text = client.generate_text(prompt).strip()
            print("LLM rerank response:", response_text)  # Debugging line to check the LLM response
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            reranked_orders = json.loads(response_text)

            # JSON keys are always strings; convert back to int to match segment_matches
            reranked_orders = {
                int(k) if isinstance(k, str) and k.lstrip('-').isdigit() else k: v
                for k, v in reranked_orders.items()
            }

            # Apply LLM reordering back to the segment dictionary
            for sid, ordered_indices in reranked_orders.items():
                if sid in segment_matches:
                    original_list = segment_matches[sid]["candidates"]
                    new_list = [original_list[idx] for idx in ordered_indices if idx < len(original_list)]
                    for item in original_list:
                        if item not in new_list:
                            new_list.append(item)
                    segment_matches[sid]["candidates"] = new_list

                    print(f"Segment {sid} reranked by LLM: {[c[0] for c in new_list]}")
        except Exception as e:
            _log(job_id, f"Batch LLM reranking failed; preserving raw embedding order. Error: {e}")

    # Phase 3: Assign clips globally — each clip used at most once.
    # Segments with the strongest match get first pick so clips go where they fit best.
    used_paths = set()
    sorted_segments = sorted(
        segment_matches.items(),
        key=lambda item: item[1]["candidates"][0][2] if item[1]["candidates"] else 0,
        reverse=True,
    )

    for sid, match_data in sorted_segments:
        available = [
            (p, m, s) for p, m, s in match_data["candidates"]
            if str(p) not in used_paths
        ]

        if not available:
            resolution[sid] = {"status": "needs_decision"}
            continue

        final_selections = available[:3]
        best_match = final_selections[0]
        p, m, s = best_match

        for path, _, _ in final_selections:
            used_paths.add(str(path))

        resolution[sid] = {
            "status": "auto_matched",
            "candidates": [
                (str(path), meta.get("duration_seconds") or library.probe_duration(path))
                for path, meta, score in final_selections
            ],
            "score": round(s, 1),
        }

    state["resolution"] = resolution
    state["clips_with_meta"] = clips_with_meta
    state["embed_cache"] = cache
    state["client"] = client
    return resolution


def _maybe_finalize(job_id):
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

    _log(job_id, "Exporting XML...")
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

    output_path = config.OUTPUTS_DIR / f"{job_id}.xml"                      # changed
    xml_export.export_fcp7_xml(assignments, Path(state["audio_path"]), output_path)  # changed

    JOBS[job_id]["status"] = "done"
    JOBS[job_id]["result"] = {
        "xml_url": f"/api/project/download/{job_id}.xml",                   # changed
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
            _log(job_id, f"Footage resolution failed: {e}")

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


# ---------- CLI commands ----------

def _cli_debloat():
    """Remove all uploads, approved action plans, and regenerate vocabulary."""
    import shutil

    # 1. Wipe uploads/
    if config.UPLOADS_DIR.exists():
        count = sum(1 for _ in config.UPLOADS_DIR.iterdir())
        shutil.rmtree(config.UPLOADS_DIR)
        config.UPLOADS_DIR.mkdir(exist_ok=True)
        print(f"🧹 Deleted {count} uploads")

    # 2. Wipe approved_action_plans/
    plans_dir = config.DATA_DIR / "approved_action_plans"
    if plans_dir.exists():
        count = sum(1 for _ in plans_dir.iterdir())
        shutil.rmtree(plans_dir)
        plans_dir.mkdir(exist_ok=True)
        print(f"🧹 Deleted {count} approved action plans")
    
    _cli_regen_vocab()

def _cli_regen_vocab():
    from core import library
    settings = config.load_settings()
    folder = settings.get("library_folder", "")
    if folder:
        vocab = library.rebuild_action_vocabulary(folder)
        print(f"✅ Regenerated action_vocabulary.json — {len(vocab)} action phrases")
    else:
        print("⚠️  No library folder configured — skipping vocabulary rebuild")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "debloat":
        _cli_debloat()
    elif len(sys.argv) > 1 and sys.argv[1] == "regen-vocab":
        _cli_regen_vocab()

    else:
        app.run(debug=True, port=8080)