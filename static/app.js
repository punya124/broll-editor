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
  api("/api/project/generate", { method: "POST", body: form }).then((res) => {
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
          <ul>${r.assignments
            .map((a) => `<li>${a.purpose} — ${a.clip} (${a.score}%)</li>`)
            .join("")}</ul>`;
      } else if (job.status === "missing_footage") {
        const missing = job.result.missing;
        result.innerHTML = `
          <h3>Missing Footage</h3>
          <ul>${missing
            .map(
              (m) =>
                `<li>${m.purpose} — ${m.duration}s — needs: ${JSON.stringify(
                  m.required
                )}</li>`
            )
            .join("")}</ul>`;
      }
    });
  });
};

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
