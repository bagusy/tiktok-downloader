const form = document.getElementById("quickForm");
const urlInput = document.getElementById("quickUrl");
const captionInput = document.getElementById("quickCaption");
const btn = document.getElementById("quickBtn");
const progressEl = document.getElementById("quickProgress");
const progressText = document.getElementById("quickProgressText");
const logEl = document.getElementById("quickLog");
const loginBadge = document.getElementById("loginBadge");
const loginLink = document.getElementById("loginLink");

const STEP_IDS = {
  login: "stepLogin",
  info: "stepInfo",
  download: "stepDownload",
  upload: "stepUpload",
  cleanup: "stepCleanup",
};

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

function setStep(step, state) {
  const id = STEP_IDS[step];
  if (!id) return;
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove("active", "done", "fail");
  if (state) el.classList.add(state);
}

function markPreviousStepsDone(currentStep) {
  const order = ["login", "info", "download", "upload", "cleanup"];
  const idx = order.indexOf(currentStep);
  if (idx === -1) return;
  for (let i = 0; i < idx; i++) setStep(order[i], "done");
}

function addLog(msg, type = "info") {
  const li = document.createElement("li");
  li.className = type;
  li.textContent = msg;
  logEl.appendChild(li);
  logEl.scrollTop = logEl.scrollHeight;
}

function resetSteps() {
  Object.keys(STEP_IDS).forEach((s) => setStep(s, ""));
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  const caption = captionInput.value.trim();
  if (!url) return;

  btn.disabled = true;
  btn.textContent = "Berjalan...";
  progressEl.classList.remove("hidden");
  logEl.innerHTML = "";
  progressText.textContent = "Memulai...";
  resetSteps();

  let currentStep = null;

  try {
    const res = await fetch("/api/quick/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, caption }),
    });

    if (!res.ok || !res.body) {
      let msg = "Gagal memulai";
      try {
        const data = await res.json();
        msg = data.error || msg;
      } catch (_) {}
      addLog("ERROR: " + msg, "error");
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
          case "status":
            if (data.step && data.step !== currentStep) {
              if (currentStep) setStep(currentStep, "done");
              markPreviousStepsDone(data.step);
              setStep(data.step, "active");
              currentStep = data.step;
            }
            progressText.textContent = data.msg;
            addLog(data.msg, "info");
            break;
          case "info":
            addLog(
              `Video: "${(data.title || "").slice(0, 80)}" oleh @${data.uploader || "?"}`,
              "info"
            );
            addLog(
              `Caption (${data.caption_source}): "${(data.caption_used || "").slice(0, 120)}"`,
              "info"
            );
            break;
          case "ok":
            addLog(`Upload OK: ${data.filename || ""}`, "ok");
            break;
          case "error":
            addLog(`Upload error: ${data.reason || ""}`, "error");
            if (currentStep) setStep(currentStep, "fail");
            break;
          case "fatal":
            addLog(`FATAL: ${data.error || ""}`, "error");
            if (currentStep) setStep(currentStep, "fail");
            progressText.textContent = "Gagal: " + (data.error || "");
            break;
          case "complete":
            if (data.ok) {
              setStep("upload", "done");
              setStep("cleanup", "done");
              const note = data.deleted
                ? "File lokal sudah dihapus."
                : "File lokal masih ada (gagal hapus).";
              addLog(`SELESAI: video ke-upload. ${note}`, "ok");
              progressText.textContent = "Selesai — video sudah ke-upload ke TikTok";
            } else {
              setStep("upload", "fail");
              addLog(
                `SELESAI dengan error. File disimpan di: ${data.file_kept || "?"}`,
                "error"
              );
              progressText.textContent = "Gagal upload — file lokal tetap disimpan";
            }
            break;
        }
      }
    }
  } catch (e) {
    addLog("Network error: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Download & Upload Sekarang";
    checkLogin();
  }
});

// Page load: auto-coba login dari browser yang lagi running
checkLogin({ autoLoginIfNeeded: true });
