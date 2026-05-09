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

let currentUrl = "";

function setStatus(msg, type = "info") {
  statusEl.textContent = msg;
  statusEl.className = "status " + type;
}

function clearStatus() {
  statusEl.textContent = "";
  statusEl.className = "status";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  const browser = browserSelect.value || null;
  if (!url) return;

  currentUrl = url;
  infoEl.classList.add("hidden");
  formatsEl.innerHTML = "";
  setStatus("Mengambil info video...", "loading");
  getBtn.disabled = true;

  try {
    const res = await fetch("/api/info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, browser }),
    });
    const data = await res.json();

    if (!res.ok) {
      let msg = data.error || "Gagal mengambil info video.";
      if (data.needs_login) {
        msg +=
          "\n\nVideo ini butuh login. Pilih browser yang sudah login TikTok pada dropdown 'Cookies dari browser', lalu coba lagi.";
      }
      setStatus(msg, "error");
      return;
    }

    clearStatus();
    showInfo(data);
  } catch (err) {
    setStatus("Network error: " + err.message, "error");
  } finally {
    getBtn.disabled = false;
  }
});

function showInfo(data) {
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

async function downloadFormat(fmt, btn) {
  setStatus("Mengunduh " + fmt.label + "... (proses di server, tunggu sebentar)", "loading");
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

function parseFilename(cd) {
  if (!cd) return null;
  // Try filename*=UTF-8''... first (RFC 5987), then plain filename=
  const ext = cd.match(/filename\*=UTF-8''([^;]+)/i);
  if (ext) return decodeURIComponent(ext[1].trim());
  const plain = cd.match(/filename="?([^";]+)"?/i);
  if (plain) return plain[1].trim();
  return null;
}
