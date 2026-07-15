async function api(path, opts) {
  const res = await fetch(path, opts);
  return res.json();
}

function refreshLibraryStatus() {
  api("/api/library/status").then((s) => {
    document.getElementById("libraryStatus").textContent =
      `${s.total} clips - ${s.confirmed || 0} confirmed, ${s.pending_review || 0} awaiting review, ${s.unanalyzed || 0} unanalyzed`;
  });
}

function pollJob(jobId, logEl, onFinal) {
  const interval = setInterval(() => {
    api(`/api/job/${jobId}`).then((job) => {
      if (logEl) logEl.textContent = job.log.join("\n");
      if (job.status !== "running") {
        clearInterval(interval);
        if (job.status === "error") {
          if (logEl) logEl.textContent += `\nError: ${job.error}`;
        }
        onFinal(job);
      }
    });
  }, 1000);
}

// ---------- Library scan + review modal ----------

document.getElementById("saveFolder").onclick = () => {
  const folder = document.getElementById("libraryFolder").value;
  api("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ library_folder: folder }),
  }).then(refreshLibraryStatus);
};

document.getElementById("scanBtn").onclick = () => {
  const log = document.getElementById("libraryLog");
  log.textContent = "Starting scan...\n";
  api("/api/library/scan", { method: "POST" }).then((res) => {
    if (res.error) {
      log.textContent = res.error;
      return;
    }
    pollJob(res.job_id, log, () => {
      refreshLibraryStatus();
      loadNextPendingReview();
    });
  });
};

function loadNextPendingReview() {
  api("/api/library/pending").then((res) => {
    const clips = res.clips || [];
    if (!clips.length) return;
    showReviewModal(clips[0], clips.length);
  });
}

function showReviewModal(clip, remainingCount) {
  const overlay = document.createElement("div");
  overlay.className = "overlay";
  overlay.innerHTML = `
    <div class="modal">
      <h3>Review: ${clip.name}</h3>
      <p class="modal-subtext">${remainingCount} clip(s) awaiting review</p>
      <video controls width="280" src="/api/library/clip-file/${encodeURIComponent(clip.name)}"></video>
      <p><em>${clip.description}</em></p>
      <ul id="actionList"></ul>
      <div class="add-row">
        <input id="newActionInput" type="text" placeholder="Add an action...">
        <button id="addActionBtn">Add</button>
      </div>
      <button id="confirmClipBtn">Confirm</button>
    </div>`;
  document.body.appendChild(overlay);

  let actions = [...clip.action_interpretations];

  function renderActions() {
    const list = overlay.querySelector("#actionList");
    list.innerHTML = actions
      .map((a, i) => `<li>${a} <button data-i="${i}" class="removeActionBtn">x</button></li>`)
      .join("");
    list.querySelectorAll(".removeActionBtn").forEach((btn) => {
      btn.onclick = () => {
        actions.splice(Number(btn.dataset.i), 1);
        renderActions();
      };
    });
  }
  renderActions();

  overlay.querySelector("#addActionBtn").onclick = () => {
    const input = overlay.querySelector("#newActionInput");
    const val = input.value.trim();
    if (val) {
      actions.push(val);
      input.value = "";
      renderActions();
    }
  };

  overlay.querySelector("#confirmClipBtn").onclick = () => {
    api("/api/library/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clip_name: clip.name, actions }),
    }).then(() => {
      document.body.removeChild(overlay);
      refreshLibraryStatus();
      loadNextPendingReview();
    });
  };
}

// ---------- New project: generate / choose existing ----------

document.getElementById("chooseExistingBtn").onclick = () => {
  const picker = document.getElementById("existingPlanPicker");
  picker.classList.toggle("hidden");
  if (!picker.classList.contains("hidden")) {
    api("/api/action-plan/list").then((res) => {
      const select = document.getElementById("planSelect");
      select.innerHTML = (res.plans || [])
        .map((p) => `<option value="${p.file}">${p.project_name} (${p.created_at}, ${p.segment_count} segments)</option>`)
        .join("");
    });
  }
};

document.getElementById("loadExistingBtn").onclick = () => {
  const audioInput = document.getElementById("audio");
  const planFile = document.getElementById("planSelect").value;
  const log = document.getElementById("genLog");
  const result = document.getElementById("result");
  result.innerHTML = "";

  if (!audioInput.files.length || !planFile) {
    log.textContent = "Please choose a plan and upload the matching audio file.";
    return;
  }

  const form = new FormData();
  form.append("plan_file", planFile);
  form.append("audio", audioInput.files[0]);

  log.textContent = "Loading plan and searching for footage...\n";
  api("/api/action-plan/load-existing", { method: "POST", body: form }).then((res) => {
    if (res.error) {
      log.textContent = res.error;
      return;
    }
    pollJob(res.job_id, log, (job) => handleResolutionJob(res.job_id, job, log, result));
  });
};

document.getElementById("generateBtn").onclick = () => {
  const audioInput = document.getElementById("audio");
  const log = document.getElementById("genLog");
  const result = document.getElementById("result");
  result.innerHTML = "";

  if (!audioInput.files.length) {
    log.textContent = "Please upload an audio file.";
    return;
  }

  const form = new FormData();
  form.append("audio", audioInput.files[0]);

  log.textContent = "Starting...\n";
  api("/api/action-plan/generate", { method: "POST", body: form }).then((res) => {
    if (res.error) {
      log.textContent = res.error;
      return;
    }
    const jobId = res.job_id;
    pollJob(jobId, log, (job) => {
      if (job.status === "awaiting_approval") {
        renderApprovalScreen(jobId, job.result, log, result);
      }
    });
  });
};

function formatTimestamp(seconds) {
  const m = Math.floor(seconds / 60);
  const s = (seconds % 60).toFixed(1).padStart(4, "0");
  return `${String(m).padStart(2, "0")}:${s}`;
}

// ---------- Approval screen (with reject + feedback) ----------

function renderApprovalScreen(jobId, planResult, log, result) {
  const segments = planResult.segments;
  result.innerHTML = `
    <h3>Review Action Plan: "${planResult.project_name}"</h3>
    <ol>${segments
      .map(
        (s) => `<li>
          <strong>"${s.text}"</strong><br>
          ${s.main_suggestion}<br>
          <small>${formatTimestamp(s.start)} - ${formatTimestamp(s.end)}</small>
        </li>`
      )
      .join("")}</ol>
    <div class="row">
      <button id="rejectBtn">Reject</button>
      <button id="approveBtn">Approve &amp; Generate</button>
    </div>
    <div id="rejectFeedbackBox" class="hidden">
      <label for="feedbackInput">What should change?</label>
      <textarea id="feedbackInput" rows="3" placeholder="e.g. too generic, more energetic actions, etc."></textarea>
      <button id="submitFeedbackBtn">Regenerate</button>
    </div>`;

  result.querySelector("#rejectBtn").onclick = () => {
    result.querySelector("#rejectFeedbackBox").classList.remove("hidden");
  };

  result.querySelector("#submitFeedbackBtn").onclick = () => {
    const feedback = result.querySelector("#feedbackInput").value.trim();
    log.textContent = "Regenerating plan with your feedback...\n";
    result.innerHTML = "";
    api("/api/action-plan/reject", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId, feedback }),
    }).then((res) => {
      pollJob(jobId, log, (job) => {
        if (job.status === "awaiting_approval") {
          renderApprovalScreen(jobId, job.result, log, result);
        }
      });
    });
  };

  result.querySelector("#approveBtn").onclick = () => {
    log.textContent = "Approved - saving plan and searching for footage...\n";
    result.innerHTML = "";
    api("/api/action-plan/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId }),
    }).then((res) => {
      if (res.error) {
        log.textContent = res.error;
        return;
      }
      pollJob(jobId, log, (job) => handleResolutionJob(jobId, job, log, result));
    });
  };
}

// ---------- Footage resolution: auto-matched + per-segment decisions ----------

function handleResolutionJob(jobId, job, log, result) {
  if (job.status === "awaiting_footage_decisions") {
    renderDecisionScreen(jobId, job.result.segments, job.result.resolution, log, result);
  } else if (job.status === "done") {
    renderFinishedVideo(job.result, result);
  } else if (job.status === "incomplete") {
    renderIncomplete(job.result, result);
  }
}

function renderDecisionScreen(jobId, segments, resolution, log, result) {
  const decided = {}; // segment_id -> {status, message} for this browser session

  function rowHtml(seg) {
    const r = resolution[seg.segment_id];
    if (r.status === "auto_matched") {
      return `<li class="resolved">"${seg.text}"<br>${seg.main_suggestion}<br>
        <small>Matched from library (${r.score}%)</small></li>`;
    }
    return `<li data-segment="${seg.segment_id}">
      "${seg.text}"<br>
      <strong>${seg.main_suggestion}</strong><br>
      <small>Fallback available: ${seg.fallback || "(none)"}</small>
      <div class="decision-actions">
        <button class="fallbackBtn" data-segment="${seg.segment_id}" ${seg.fallback ? "" : "disabled"}>Use Fallback</button>
        <button class="filmBtn" data-segment="${seg.segment_id}">I'll Film This</button>
      </div>
      <div class="decision-status"></div>
    </li>`;
  }

  function render() {
    result.innerHTML = `
      <h3>Footage Resolution</h3>
      <p class="modal-subtext">Answer every row below - nothing renders until all are resolved.</p>
      <ul id="decisionList">${segments.map(rowHtml).join("")}</ul>`;

    result.querySelectorAll(".fallbackBtn").forEach((btn) => {
      btn.onclick = () => makeDecision(btn, "fallback");
    });
    result.querySelectorAll(".filmBtn").forEach((btn) => {
      btn.onclick = () => makeDecision(btn, "film");
    });
  }

  function makeDecision(btn, decision) {
    const segmentId = Number(btn.dataset.segment);
    const li = btn.closest("li");
    const statusEl = li.querySelector(".decision-status");
    statusEl.textContent = decision === "film" ? "Adding reminder..." : "Finding fallback footage...";
    li.querySelectorAll("button").forEach((b) => (b.disabled = true));

    api("/api/action-plan/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId, segment_id: segmentId, decision }),
    }).then((res) => {
      if (res.error) {
        statusEl.textContent = res.error;
        li.querySelectorAll("button").forEach((b) => (b.disabled = false));
        return;
      }
      if (decision === "film") {
        statusEl.textContent =
          "Okay, happy filming! Once your new clip(s) are added, please reupload the audio and action plan.";
      } else {
        statusEl.textContent = "Resolved using fallback footage.";
      }

      if (res.finalized) {
        pollJob(jobId, log, (job) => {
          if (job.status === "done") {
            renderFinishedVideo(job.result, result);
          } else if (job.status === "incomplete") {
            renderIncomplete(job.result, result);
          }
        });
      }
    });
  }

  render();
}

function renderFinishedVideo(resultData, result) {
  result.innerHTML = `
    <video controls width="300" src="${resultData.video_url}"></video>
    <h3>Clips used</h3>
    <ul>${resultData.assignments.map((a) => `<li>${a.action} - ${a.clip}</li>`).join("")}</ul>`;
}

function renderIncomplete(resultData, result) {
  result.innerHTML = `
    <h3>Video Incomplete</h3>
    <p>${resultData.message}</p>
    <ul>${resultData.pending_film.map((p) => `<li>${p}</li>`).join("")}</ul>`;
}

// ---------- Settings ----------

document.getElementById("saveSettings").onclick = () => {
  const model = document.getElementById("model").value;
  const threshold = document.getElementById("threshold").value;
  api("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ gemini_model: model, match_threshold: Number(threshold) }),
  });
};

refreshLibraryStatus();