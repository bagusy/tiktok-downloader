"""TikTok Downloader — Flask web UI.

Run:
    python app.py

Open http://127.0.0.1:5000 di browser yang sudah login TikTok.
"""

import shutil
import uuid
from pathlib import Path

import yt_dlp
from flask import Flask, abort, jsonify, render_template, request, send_file

from tiktok import (
    build_options,
    categorize_formats,
    download as do_download,
    fetch_info,
)

app = Flask(__name__)
DOWNLOAD_DIR = Path(__file__).parent / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/info")
def api_info():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    browser = (data.get("browser") or "").strip() or None

    if not url:
        return jsonify(error="URL kosong"), 400

    try:
        info = _fetch_info_smart(url, browser)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        return jsonify(
            error=msg,
            needs_login=("Log in" in msg or "cookies" in msg.lower()),
        ), 400
    except Exception as e:
        return jsonify(error=f"Error tak terduga: {e}"), 500

    no_wm, with_wm, audios = categorize_formats(info)
    options = build_options(no_wm, with_wm, audios)

    return jsonify(
        title=info.get("title") or info.get("id", ""),
        uploader=info.get("uploader") or info.get("uploader_id", ""),
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
        webpage_url=info.get("webpage_url"),
        formats=[
            {"id": fid, "label": label, "kind": kind}
            for kind, fid, label in options
        ],
    )


@app.post("/api/download")
def api_download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    format_id = (data.get("format_id") or "").strip()
    kind = data.get("kind") or "video"
    browser = (data.get("browser") or "").strip() or None

    if not url or not format_id:
        return jsonify(error="URL dan format_id wajib diisi"), 400

    job_dir = DOWNLOAD_DIR / f".tmp-{uuid.uuid4().hex[:8]}"
    try:
        _download_smart(url, kind, format_id, job_dir, browser)
    except yt_dlp.utils.DownloadError as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify(error=str(e)), 400
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify(error=f"Error tak terduga: {e}"), 500

    files = [p for p in job_dir.iterdir() if p.is_file()] if job_dir.exists() else []
    if not files:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify(error="Download selesai tapi file tidak ditemukan"), 500

    src = files[0]
    final = _unique_path(DOWNLOAD_DIR / src.name)
    src.rename(final)
    shutil.rmtree(job_dir, ignore_errors=True)

    return send_file(final, as_attachment=True, download_name=final.name)


def _needs_cookies_retry(err: Exception) -> bool:
    """Apakah error kemungkinan bisa diperbaiki dengan cookies login?"""
    msg = str(err).lower()
    return "log in" in msg or "cookies" in msg or "login" in msg


def _fetch_info_smart(url: str, browser: str | None):
    """Coba tanpa cookies dulu. Kalau gagal & ada browser hint, retry dengan cookies."""
    try:
        return fetch_info(url, cookies_browser=None)
    except yt_dlp.utils.DownloadError as first_err:
        if browser and _needs_cookies_retry(first_err):
            return fetch_info(url, cookies_browser=browser)
        raise


def _download_smart(url, kind, format_id, output_dir, browser):
    """Sama seperti _fetch_info_smart tapi untuk download."""
    try:
        do_download(url, kind, format_id, output_dir, cookies_browser=None)
        return
    except yt_dlp.utils.DownloadError as first_err:
        if browser and _needs_cookies_retry(first_err):
            shutil.rmtree(output_dir, ignore_errors=True)
            do_download(url, kind, format_id, output_dir, cookies_browser=browser)
            return
        raise


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, ext = path.stem, path.suffix
    i = 1
    while True:
        candidate = path.with_name(f"{stem} ({i}){ext}")
        if not candidate.exists():
            return candidate
        i += 1


if __name__ == "__main__":
    print("=" * 50)
    print("TikTok Downloader")
    print("Buka di browser: http://127.0.0.1:5000")
    print("Tekan Ctrl+C untuk stop")
    print("=" * 50)
    app.run(host="127.0.0.1", port=5000, debug=False)
