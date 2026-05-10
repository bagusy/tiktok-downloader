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

    const data = await res.json();
    if (!data.ok) {
      setStatus(data.error || "Download gagal", "error");
      return;
    }
    const sizeMB = data.size ? (data.size / 1024 / 1024).toFixed(2) + " MB" : "";
    setStatus(
      "Selesai. Tersimpan di:\n" + data.path + (sizeMB ? "\n(" + sizeMB + ")" : ""),
      "success"
    );
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
            const tier = data.tier ? ` [${data.tier}]` : "";
            addLog(`OK${tier} [${data.video_id}] ${(data.title || "").slice(0, 80)}`, "ok");
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

// ============================================================
//   Upload ke TikTok
// ============================================================

const loginStatusBadge = document.getElementById("loginStatusBadge");
const loginBtn = document.getElementById("loginBtn");
const recheckLoginBtn = document.getElementById("recheckLoginBtn");
const importLoginBtn = document.getElementById("importLoginBtn");
const loginBrowserSelect = document.getElementById("loginBrowserSelect");
const refreshVideosBtn = document.getElementById("refreshVideosBtn");
const headlessChk = document.getElementById("headlessChk");
const videoListEl = document.getElementById("videoList");
const videoListEmpty = document.getElementById("videoListEmpty");
const uploadBtn = document.getElementById("uploadBtn");
const uploadProgressEl = document.getElementById("uploadProgress");
const uploadProgressFill = document.getElementById("uploadProgressFill");
const uploadProgressText = document.getElementById("uploadProgressText");
const uploadLogEl = document.getElementById("uploadLog");

const localVideos = []; // {path, rel, name, size, mtime, caption, selected}

function setLoginBadge(state, text) {
  loginStatusBadge.className = "badge " + state;
  loginStatusBadge.textContent = text;
}

async function checkLoginStatus({ autoLoginIfNeeded = false } = {}) {
  setLoginBadge("unknown", "Mengecek...");
  try {
    const res = await fetch("/api/upload/status");
    const data = await res.json();
    if (!data.available) {
      setLoginBadge("error", "Playwright belum terinstall");
      return;
    }
    if (data.logged_in) {
      setLoginBadge("ok", "Logged in");
      return;
    }
    if (!autoLoginIfNeeded) {
      setLoginBadge("warn", "Belum login");
      return;
    }
    const detected = data.detected_browsers || [];
    const hint = detected.length ? detected.join(", ") : "scan...";
    setLoginBadge("warn", `Auto-login (${hint})...`);
    try {
      const auto = await fetch("/api/upload/auto-login", { method: "POST" });
      const ad = await auto.json().catch(() => ({}));
      if (ad.ok) {
        setLoginBadge("ok", `Logged in via ${ad.browser}`);
      } else {
        const reason = (ad.error || "tidak diketahui").slice(0, 140);
        setLoginBadge("warn", "Auto-login gagal: " + reason);
      }
    } catch (e) {
      setLoginBadge("warn", "Auto-login error: " + e.message);
    }
  } catch (e) {
    setLoginBadge("error", "Network error");
  }
}

importLoginBtn.addEventListener("click", async () => {
  const browser = loginBrowserSelect.value;
  importLoginBtn.disabled = true;
  importLoginBtn.textContent = "Importing...";
  setLoginBadge("warn", `Import cookies dari ${browser}...`);
  try {
    const res = await fetch("/api/upload/login-from-browser", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ browser }),
    });
    const data = await res.json().catch(() => ({}));
    if (data.ok) {
      await checkLoginStatus();
    } else {
      let msg = data.error || "unknown";
      if (/DPAPI|app[- ]bound/i.test(msg)) {
        msg =
          "Chrome 127+ pakai enkripsi cookies baru yang tidak bisa dibaca otomatis. " +
          "Solusi: pakai Firefox (paling reliable) atau Brave dari dropdown ini.";
      }
      setLoginBadge("warn", "Import gagal: " + msg.slice(0, 200));
    }
  } catch (e) {
    setLoginBadge("error", "Network error: " + e.message);
  } finally {
    importLoginBtn.disabled = false;
    importLoginBtn.textContent = "Import login dari browser";
  }
});

loginBtn.addEventListener("click", async () => {
  loginBtn.disabled = true;
  loginBtn.textContent = "Login berjalan (5 menit timeout)...";
  setLoginBadge("warn", "Login berjalan — login di window browser yang terbuka");
  try {
    const res = await fetch("/api/upload/login", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (data.ok) {
      await checkLoginStatus();
    } else {
      setLoginBadge("warn", "Login gagal: " + (data.error || "unknown").slice(0, 80));
    }
  } catch (e) {
    setLoginBadge("error", "Network error: " + e.message);
  } finally {
    loginBtn.disabled = false;
    loginBtn.textContent = "Login manual via Playwright";
  }
});

recheckLoginBtn.addEventListener("click", () => checkLoginStatus({ autoLoginIfNeeded: true }));

async function refreshVideos() {
  refreshVideosBtn.disabled = true;
  refreshVideosBtn.textContent = "Memuat...";
  try {
    const res = await fetch("/api/local-videos");
    const data = await res.json();
    localVideos.length = 0;
    for (const v of data.videos || []) {
      localVideos.push({ ...v, caption: "", selected: false });
    }
    renderVideoList();
  } catch (e) {
    videoListEmpty.textContent = "Gagal memuat: " + e.message;
  } finally {
    refreshVideosBtn.disabled = false;
    refreshVideosBtn.textContent = "Refresh daftar video";
  }
}

function renderVideoList() {
  videoListEl.innerHTML = "";
  if (!localVideos.length) {
    const p = document.createElement("p");
    p.className = "hint";
    p.id = "videoListEmpty";
    p.textContent = "Tidak ada video di folder downloads/.";
    videoListEl.appendChild(p);
    updateUploadBtn();
    return;
  }
  for (const v of localVideos) {
    const row = document.createElement("div");
    row.className = "video-row";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = v.selected;
    cb.addEventListener("change", () => {
      v.selected = cb.checked;
      row.classList.toggle("selected", v.selected);
      captionInput.disabled = !v.selected;
      updateUploadBtn();
    });

    const meta = document.createElement("div");
    meta.className = "video-meta";
    const name = document.createElement("div");
    name.className = "video-name";
    name.textContent = v.rel;
    const sub = document.createElement("div");
    sub.className = "video-sub";
    const sizeMB = (v.size / 1024 / 1024).toFixed(2);
    sub.textContent = sizeMB + " MB";
    meta.appendChild(name);
    meta.appendChild(sub);

    const captionInput = document.createElement("textarea");
    captionInput.className = "caption-input";
    captionInput.placeholder = "Caption + #hashtag (opsional)";
    captionInput.rows = 2;
    captionInput.value = v.caption;
    captionInput.disabled = !v.selected;
    captionInput.addEventListener("input", () => {
      v.caption = captionInput.value;
    });

    row.appendChild(cb);
    row.appendChild(meta);
    row.appendChild(captionInput);
    if (v.selected) row.classList.add("selected");
    videoListEl.appendChild(row);
  }
  updateUploadBtn();
}

function updateUploadBtn() {
  const n = localVideos.filter((v) => v.selected).length;
  uploadBtn.textContent = `Upload Video Terpilih (${n})`;
  uploadBtn.disabled = n === 0;
}

refreshVideosBtn.addEventListener("click", refreshVideos);

uploadBtn.addEventListener("click", async () => {
  const items = localVideos
    .filter((v) => v.selected)
    .map((v) => ({ path: v.path, caption: v.caption }));
  if (!items.length) return;

  uploadBtn.disabled = true;
  uploadBtn.textContent = "Sedang upload...";
  uploadProgressEl.classList.remove("hidden");
  uploadLogEl.innerHTML = "";
  uploadProgressFill.style.width = "0%";
  uploadProgressText.textContent = "Memulai...";

  let total = items.length;
  let current = 0;

  function addUploadLog(msg, type = "info") {
    const li = document.createElement("li");
    li.className = type;
    li.textContent = msg;
    uploadLogEl.appendChild(li);
    uploadLogEl.scrollTop = uploadLogEl.scrollHeight;
  }

  function updUpProgress(c, t, text) {
    if (t > 0) uploadProgressFill.style.width = ((c / t) * 100).toFixed(1) + "%";
    uploadProgressText.textContent = text;
  }

  try {
    const res = await fetch("/api/upload/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items, headless: headlessChk.checked }),
    });
    if (!res.ok || !res.body) {
      let msg = "Gagal start upload";
      try {
        const data = await res.json();
        msg = data.error || msg;
      } catch (_) {}
      addUploadLog("ERROR: " + msg, "error");
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
            total = data.total;
            addUploadLog(`Mulai upload ${data.total} video...`, "info");
            break;
          case "status":
            uploadProgressText.textContent = data.msg;
            addUploadLog(data.msg, "info");
            break;
          case "progress":
            current = data.current;
            updUpProgress(current - 1, total, `[${current}/${total}] ${data.filename}`);
            addUploadLog(`[${current}/${total}] Upload: ${data.filename}`, "info");
            break;
          case "ok":
            addUploadLog(`OK [${data.filename}]`, "ok");
            updUpProgress(current, total, `[${current}/${total}] ${data.filename} OK`);
            break;
          case "error":
            addUploadLog(`ERROR [${data.filename}] ${data.reason}`, "error");
            break;
          case "done":
            updUpProgress(total, total, `Selesai: ${data.success} sukses, ${data.failed} gagal`);
            addUploadLog(`SELESAI: ${data.success} sukses, ${data.failed} gagal`, "ok");
            break;
          case "fatal":
            addUploadLog(`FATAL: ${data.error}`, "error");
            break;
        }
      }
    }
  } catch (e) {
    addUploadLog("Network error: " + e.message, "error");
  } finally {
    updateUploadBtn();
    // Refresh login status setelah upload
    checkLoginStatus();
  }
});

// Inisialisasi saat page load — kalau belum login, auto-coba dari browser yang lagi running
checkLoginStatus({ autoLoginIfNeeded: true });
refreshVideos();
