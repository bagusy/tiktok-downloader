const form = document.getElementById("urlForm");
const urlInput = document.getElementById("url");
const browserSelect = document.getElementById("browser");
const statusEl = document.getElementById("status");
const infoEl = document.getElementById("info");
const thumbEl = document.getElementById("thumb");
const titleEl = document.getElementById("title");
const uploaderEl = document.getElementById("uploader");
const durationEl = document.getElementById("duration");
const dotEl = document.getElementById("dot");
const formatsEl = document.getElementById("formats");
const getBtn = document.getElementById("getBtn");

const profileEl = document.getElementById("profile");
const profileUsernameEl = document.getElementById("profileUsername");
const profileCountEl = document.getElementById("profileCount");
const bulkBtn = document.getElementById("bulkBtn");
const bulkProgressEl = document.getElementById("bulkProgress");
const progressFillEl = document.getElementById("progressFill");
const progressTextEl = document.getElementById("progressText");
const bulkLogEl = document.getElementById("bulkLog");

let currentUrl = "";

function setStatus(msg, type = "info") {
  statusEl.textContent = msg;
  statusEl.className = "status " + type;
}

function clearStatus() {
  statusEl.textContent = "";
  statusEl.className = "status";
}

function hideAll() {
  infoEl.classList.add("hidden");
  profileEl.classList.add("hidden");
  bulkProgressEl.classList.add("hidden");
  formatsEl.innerHTML = "";
  bulkLogEl.innerHTML = "";
  progressFillEl.style.width = "0%";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  const browser = browserSelect.value || null;
  if (!url) return;

  currentUrl = url;
  hideAll();
  setStatus("Mengambil info...", "loading");
  getBtn.disabled = true;

  try {
    const res = await fetch("/api/info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, browser }),
    });
    const data = await res.json();

    if (!res.ok) {
      let msg = data.error || "Gagal mengambil info.";
      if (data.needs_login) {
        msg +=
          "\n\nButuh login. Pilih browser yang sudah login TikTok pada dropdown 'Fallback cookies dari browser', lalu coba lagi.";
      }
      setStatus(msg, "error");
      return;
    }

    clearStatus();
    if (data.type === "profile") {
      showProfile(data);
    } else {
      showVideo(data);
    }
  } catch (err) {
    setStatus("Network error: " + err.message, "error");
  } finally {
    getBtn.disabled = false;
  }
});

function showVideo(data) {
  titleEl.textContent = data.title || "(no title)";
  uploaderEl.textContent = data.uploader ? "@" + data.uploader : "";
  if (data.duration) {
    durationEl.textContent = data.duration + "s";
    dotEl.classList.remove("hidden");
  } else {
    durationEl.textContent = "";
    dotEl.classList.add("hidden");
  }
  if (data.thumbnail) {
    thumbEl.src = data.thumbnail;
    thumbEl.style.display = "";
  } else {
    thumbEl.style.display = "none";
  }

  formatsEl.innerHTML = "";
  for (const fmt of data.formats) {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = fmt.label;
    const btn = document.createElement("button");
    btn.className = "dl-btn";
    btn.textContent = "Download";
    btn.addEventListener("click", () => downloadFormat(fmt, btn));
    li.appendChild(label);
    li.appendChild(btn);
    formatsEl.appendChild(li);
  }

  infoEl.classList.remove("hidden");
}

function showProfile(data) {
  profileUsernameEl.textContent = data.username || "(unknown)";
  profileCountEl.textContent = data.video_count || 0;
  profileEl.classList.remove("hidden");
}

async function downloadFormat(fmt, btn) {
  setStatus("Mengunduh " + fmt.label + "... (tunggu sebentar)", "loading");
  btn.disabled = true;
  btn.classList.add("downloading");
  btn.textContent = "Downloading...";

  try {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: currentUrl,
        format_id: fmt.id,
        kind: fmt.kind,
        browser: browserSelect.value || null,
      }),
    });

    if (!res.ok) {
      let msg = "Download gagal";
      try {
        const data = await res.json();
        msg = data.error || msg;
      } catch (_) {}
      setStatus(msg, "error");
      return;
    }

    const filename = parseFilename(res.headers.get("Content-Disposition")) || "tiktok.mp4";
    const blob = await res.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);

    setStatus("Selesai. File: " + filename + "\nCopy juga tersimpan di folder downloads/", "success");
  } catch (err) {
    setStatus("Network error: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.classList.remove("downloading");
    btn.textContent = "Download";
  }
}

bulkBtn.addEventListener("click", async () => {
  bulkBtn.disabled = true;
  bulkBtn.textContent = "Sedang mendownload...";
  bulkProgressEl.classList.remove("hidden");
  bulkLogEl.innerHTML = "";
  progressFillEl.style.width = "0%";
  progressTextEl.textContent = "Memulai...";
  clearStatus();

  let total = 0;
  let current = 0;
  let success = 0;
  let failed = 0;

  try {
    const res = await fetch("/api/bulk-download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: currentUrl,
        browser: browserSelect.value || null,
      }),
    });

    if (!res.ok || !res.body) {
      let msg = "Gagal memulai bulk download";
      try {
        const data = await res.json();
        msg = data.error || msg;
      } catch (_) {}
      setStatus(msg, "error");
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
            progressTextEl.textContent = data.msg;
            addLog(data.msg, "info");
            break;
          case "start":
            total = data.total;
            current = 0;
            progressTextEl.textContent =
              data.total + " video ditemukan dari @" + data.username + ". Mulai download ke " + data.save_dir;
            addLog(`Mulai: ${data.total} video → ${data.save_dir}`, "info");
            break;
          case "progress":
            current = data.current;
            updateProgress(current, total, `[${current}/${total}] ${data.video_id}...`);
            break;
          case "ok":
            success++;
            addLog(`OK [${data.video_id}] ${(data.title || "").slice(0, 80)}`, "ok");
            break;
          case "retry":
            addLog(
              `RETRY [${data.video_id}] attempt ${data.attempt}/${data.max} (tunggu ${data.wait}s)`,
              "info"
            );
            break;
          case "skip":
            failed++;
            addLog(`SKIP [${data.video_id}] ${data.reason}`, "skip");
            break;
          case "error":
            failed++;
            addLog(`ERROR [${data.video_id}] ${data.reason}`, "error");
            break;
          case "done":
            updateProgress(total, total, `Selesai: ${data.success} sukses, ${data.failed} gagal`);
            setStatus(
              `Bulk selesai. ${data.success} berhasil, ${data.failed} gagal.\nSemua file di: ${data.save_dir}`,
              "success"
            );
            break;
          case "fatal":
            let msg = "Fatal: " + data.error;
            if (data.needs_login) {
              msg += "\n\nButuh login. Pilih browser pada dropdown lalu coba lagi.";
            }
            setStatus(msg, "error");
            addLog("FATAL: " + data.error, "error");
            break;
        }
      }
    }
  } catch (err) {
    setStatus("Network error: " + err.message, "error");
  } finally {
    bulkBtn.disabled = false;
    bulkBtn.textContent = "Download Semua Video";
  }
});

function updateProgress(current, total, text) {
  if (total > 0) {
    progressFillEl.style.width = ((current / total) * 100).toFixed(1) + "%";
  }
  progressTextEl.textContent = text;
}

function addLog(msg, type = "info") {
  const li = document.createElement("li");
  li.className = type;
  li.textContent = msg;
  bulkLogEl.appendChild(li);
  bulkLogEl.scrollTop = bulkLogEl.scrollHeight;
}

function parseFilename(cd) {
  if (!cd) return null;
  const ext = cd.match(/filename\*=UTF-8''([^;]+)/i);
  if (ext) return decodeURIComponent(ext[1].trim());
  const plain = cd.match(/filename="?([^";]+)"?/i);
  if (plain) return plain[1].trim();
  return null;
}
