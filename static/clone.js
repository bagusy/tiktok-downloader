const form = document.getElementById("cloneForm");
const urlInput = document.getElementById("cloneUrl");
const maxInput = document.getElementById("cloneMax");
const browserSelect = document.getElementById("cloneBrowser");
const btn = document.getElementById("cloneBtn");
const progressEl = document.getElementById("cloneProgress");
const progressText = document.getElementById("cloneProgressText");
const logEl = document.getElementById("cloneLog");
const loginBadge = document.getElementById("loginBadge");
const loginLink = document.getElementById("loginLink");

const cntUploaded = document.getElementById("cntUploaded");
const cntFailed = document.getElementById("cntFailed");
const cntSkipped = document.getElementById("cntSkipped");
const cntTotal = document.getElementById("cntTotal");

function setBadge(state, text) {
  loginBadge.className = "badge " + state;
  loginBadge.textContent = text;
}

async function checkLogin({ autoLoginIfNeeded = false } = {}) {
  setBadge("unknown", "Mengecek...");
  loginLink.style.display = "none";
  try {
    const res = await fetch("/api/upload/status");
    const data = await res.json();
    if (!data.available) {
      setBadge("error", "Playwright belum terinstall");
      loginLink.style.display = "";
      return;
    }
    if (data.logged_in) {
      const who = data.username ? ` @${data.username}` : "";
      setBadge("ok", `Logged in${who}`);
      return;
    }
    if (!autoLoginIfNeeded) {
      setBadge("warn", "Belum login");
      loginLink.style.display = "";
      return;
    }
    const detected = data.detected_browsers || [];
    const hint = detected.length ? detected.join(", ") : "scan...";
    setBadge("warn", `Auto-login (${hint})...`);
    try {
      const auto = await fetch("/api/upload/auto-login", { method: "POST" });
      const ad = await auto.json().catch(() => ({}));
      if (ad.ok) {
        // Refresh status untuk dapetin username yang baru aja ke-import
        await checkLogin({ autoLoginIfNeeded: false });
      } else {
        setBadge("warn", "Auto-login gagal: " + (ad.error || "?").slice(0, 140));
        loginLink.style.display = "";
      }
    } catch (e) {
      setBadge("warn", "Auto-login error: " + e.message);
      loginLink.style.display = "";
    }
  } catch (e) {
    setBadge("error", "Network error");
  }
}

function addLog(msg, type = "info") {
  const li = document.createElement("li");
  li.className = type;
  li.textContent = msg;
  logEl.appendChild(li);
  logEl.scrollTop = logEl.scrollHeight;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  const browser = browserSelect.value;
  const maxCount = parseInt(maxInput.value, 10);
  if (!url) return;

  btn.disabled = true;
  btn.textContent = "Berjalan...";
  progressEl.classList.remove("hidden");
  logEl.innerHTML = "";
  progressText.textContent = "Memulai...";
  cntUploaded.textContent = "0";
  cntFailed.textContent = "0";
  cntSkipped.textContent = "0";
  cntTotal.textContent = "?";

  let uploaded = 0;
  let failed = 0;

  try {
    const res = await fetch("/api/clone/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        browser: browser || null,
        max_count: Number.isFinite(maxCount) && maxCount > 0 ? maxCount : null,
      }),
    });

    if (!res.ok || !res.body) {
      let msg = "Gagal memulai";
      try {
        const data = await res.json();
        msg = data.error || msg;
      } catch (_) {}
      addLog("ERROR: " + msg, "error");
      progressText.textContent = "Gagal: " + msg;
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const evt of events) {
        if (!evt.startsWith("data: ")) continue;
        const data = JSON.parse(evt.slice(6));

        switch (data.event) {
          case "start":
            cntTotal.textContent = String(data.pending);
            cntSkipped.textContent = String(data.skipped_already || 0);
            if (data.dest_username) {
              addLog(`Akun tujuan upload: @${data.dest_username}`, "info");
            }
            addLog(
              `Sumber @${data.username}: ${data.total} video total, ${data.pending} pending` +
                (data.skipped_already
                  ? `, ${data.skipped_already} di-skip (sudah pernah di-upload)`
                  : ""),
              "info"
            );
            addLog(`Save dir: ${data.save_dir}`, "info");
            progressText.textContent = `Clone @${data.username} → @${data.dest_username || "?"} — ${data.pending} pending`;
            break;
          case "progress":
            progressText.textContent = `Video ${data.current}/${data.total} — id ${data.video_id || "?"}`;
            break;
          case "status":
            addLog(data.msg, "info");
            break;
          case "downloaded":
            addLog(`Downloaded (${data.tier}): ${data.filename}`, "ok");
            break;
          case "ok":
            uploaded++;
            cntUploaded.textContent = String(uploaded);
            const note = data.deleted ? "file lokal dihapus" : "file lokal masih ada";
            addLog(`Upload OK: ${data.filename} — ${note}`, "ok");
            break;
          case "error":
            failed++;
            cntFailed.textContent = String(failed);
            addLog(
              `[${data.phase || "?"}] error ${data.video_id || "?"}: ${data.reason || ""}`,
              "error"
            );
            break;
          case "fatal":
            addLog(`FATAL: ${data.error || ""}`, "error");
            progressText.textContent = "Gagal: " + (data.error || "");
            break;
          case "done":
            addLog(
              `SELESAI @${data.username || "?"} — ${data.uploaded} uploaded, ${data.failed} failed, ${data.skipped} skipped.`,
              "ok"
            );
            progressText.textContent = `Selesai: ${data.uploaded} ke-upload, ${data.failed} gagal.`;
            break;
        }
      }
    }
  } catch (e) {
    addLog("Network error: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Mulai Clone";
    checkLogin();
  }
});

// Page load: auto-login dari browser yang lagi running
checkLogin({ autoLoginIfNeeded: true });
