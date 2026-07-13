async function api(path, opts) {
  const res = await fetch(path, opts);
  return res.json();
}

function refreshLibraryStatus() {
  api("/api/library/status").then((s) => {
    document.getElementById("libraryStatus").textContent =
      `${s.total} clips, ${s.unanalyzed} unanalyzed`;
  });
}

function pollJob(jobId, logEl, onFinal) {
  const interval = setInterval(() => {
    api(`/api/job/${jobId}`).then((job) => {
      logEl.textContent = job.log.join("\n");
      if (job.status !== "running") {
        clearInterval(interval);
        if (job.status === "error") {
          logEl.textContent += `\nError: ${job.error}`;
        }
        onFinal(job);
      }
    });
  }, 1000);
}

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
    pollJob(res.job_id, log, () => refreshLibraryStatus());
  });
};

document.getElementById("generateBtn").onclick = () => {
  const script = document.getElementById("script").value;
  const audioInput = document.getElementById("audio");
  const log = document.getElementById("genLog");
  const result = document.getElementById("result");
  result.innerHTML = "";

  if (!script || !audioInput.files.length) {
    log.textContent = "Please provide both a script and an audio file.";
    return;
  }

  const form = new FormData();
  form.append("script", script);
  form.append("audio", audioInput.files[0]);

  log.textContent = "Starting...\n";
  api("/api/project/plan", { method: "POST", body: form }).then((res) => {
    if (res.error) {
      log.textContent = res.error;
      return;
    }
    const jobId = res.job_id;
    pollJob(jobId, log, (job) => {
      if (job.status === "awaiting_approval") {
        renderApproval(jobId, job.result.shots, log, result);
      }
    });
  });
};

function formatTimestamp(seconds) {
  const m = Math.floor(seconds / 60);
  const s = (seconds % 60).toFixed(1).padStart(4, "0");
  return `${String(m).padStart(2, "0")}:${s}`;
}

function renderApproval(jobId, shots, log, result) {
  result.innerHTML = `
    <h3>Review Shot Plan</h3>
    <ol>${shots
      .map(
        (s) => `<li>
          <strong>"${s.text}"</strong><br>
          ${s.shot_description}<br>
          <small>${formatTimestamp(s.start)} - ${formatTimestamp(s.end)}</small>
        </li>`
      )
      .join("")}</ol>
    <button id="approveBtn">Approve &amp; Continue</button>`;

  document.getElementById("approveBtn").onclick = () => {
    result.innerHTML = "";
    log.textContent = "Approved — searching for footage...\n";
    api("/api/project/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId }),
    }).then((res) => {
      if (res.error) {
        log.textContent = res.error;
        return;
      }
      pollJob(res.job_id, log, (job) => {
        if (job.status === "done") {
          const r = job.result;
          result.innerHTML = `
            <video controls width="300" src="${r.video_url}"></video>
            <h3>Clips used</h3>
            <ul>${r.assignments.map((a) => `<li>${a.purpose} — ${a.clip}</li>`).join("")}</ul>
            ${r.still_missing.length
              ? `<h3>Still missing footage for</h3><ul>${r.still_missing
                .map((p) => `<li>${p} (added to Reminders)</li>`)
                .join("")}</ul>`
              : ""
            }`;
        }
      });
    });
  };
}

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
