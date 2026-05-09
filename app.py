"""TikTok Downloader — Flask web UI.

Run:
    python app.py

Open http://127.0.0.1:5000 di browser yang sudah login TikTok.
"""

import json
import shutil
import time
import uuid
from pathlib import Path

import yt_dlp
from flask import Flask, Response, jsonify, render_template, request, send_file, stream_with_context

from tiktok import (
    build_options,
    categorize_formats,
    detect_url_type,
    download as do_download,
    fetch_info,
    fetch_profile_videos,
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

    url_type = detect_url_type(url)

    if url_type == "profile":
        try:
            profile = _fetch_profile_smart(url, browser)
        except yt_dlp.utils.DownloadError as e:
            msg = str(e)
            return jsonify(
                error=msg,
                needs_login=("Log in" in msg or "cookies" in msg.lower()),
            ), 400
        except Exception as e:
            return jsonify(error=f"Error tak terduga: {e}"), 500
        return jsonify(
            type="profile",
            username=profile["username"],
            video_count=profile["video_count"],
        )

    # Default: anggap video tunggal
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
        type="video",
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


@app.post("/api/bulk-download")
def api_bulk_download():
    """Streaming bulk download semua video dari URL profil. Output: text/event-stream."""
    data = request.get_json(silent=True) or {}
    profile_url = (data.get("url") or "").strip()
    browser = (data.get("browser") or "").strip() or None

    if not profile_url:
        return jsonify(error="URL kosong"), 400
    if detect_url_type(profile_url) != "profile":
        return jsonify(error="URL bukan URL profil TikTok"), 400

    @stream_with_context
    def generate():
        try:
            yield _sse({"event": "status", "msg": "Mengambil daftar video dari profil..."})
            try:
                profile = fetch_profile_videos(profile_url, cookies_browser=None)
                cookies_to_use = None
            except yt_dlp.utils.DownloadError as e:
                if browser and _needs_cookies_retry(e):
                    profile = fetch_profile_videos(profile_url, cookies_browser=browser)
                    cookies_to_use = browser
                else:
                    raise

            videos = profile["videos"]
            username = profile["username"] or "unknown"
            total = len(videos)

            user_dir = DOWNLOAD_DIR / _safe_dirname(username)
            user_dir.mkdir(parents=True, exist_ok=True)

            yield _sse({
                "event": "start",
                "username": username,
                "total": total,
                "save_dir": str(user_dir),
            })

            if total == 0:
                yield _sse({"event": "done", "success": 0, "failed": 0, "failed_items": [],
                            "save_dir": str(user_dir)})
                return

            success = 0
            failed = 0
            failed_items = []

            for i, v in enumerate(videos, start=1):
                video_url = v["url"]
                video_id = v.get("id", "")
                yield _sse({
                    "event": "progress",
                    "current": i,
                    "total": total,
                    "video_id": video_id,
                    "url": video_url,
                })

                try:
                    info = fetch_info(video_url, cookies_browser=cookies_to_use)
                    no_wm, with_wm, audios = categorize_formats(info)
                    options = build_options(no_wm, with_wm, audios)

                    target = next((o for o in options if "No-Watermark" in o[2]), None)
                    if not target:
                        target = next((o for o in options if o[0] == "video"), None)
                    if not target:
                        failed += 1
                        failed_items.append({"id": video_id, "reason": "tidak ada format video"})
                        yield _sse({"event": "skip", "video_id": video_id, "reason": "no format"})
                        continue

                    kind, fmt_id, _label = target
                    do_download(video_url, kind, fmt_id, user_dir, cookies_browser=cookies_to_use)
                    success += 1
                    yield _sse({"event": "ok", "video_id": video_id, "title": info.get("title", "")})
                except Exception as e:
                    failed += 1
                    reason = str(e)[:300]
                    failed_items.append({"id": video_id, "reason": reason})
                    yield _sse({"event": "error", "video_id": video_id, "reason": reason})

                # Jeda kecil supaya tidak di-rate-limit TikTok
                time.sleep(0.5)

            yield _sse({
                "event": "done",
                "success": success,
                "failed": failed,
                "failed_items": failed_items,
                "save_dir": str(user_dir),
            })
        except yt_dlp.utils.DownloadError as e:
            yield _sse({
                "event": "fatal",
                "error": str(e),
                "needs_login": _needs_cookies_retry(e),
            })
        except Exception as e:
            yield _sse({"event": "fatal", "error": f"Error tak terduga: {e}"})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


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


def _fetch_info_smart(url: str, browser):
    """Coba tanpa cookies dulu. Kalau gagal & ada browser hint, retry dengan cookies."""
    try:
        return fetch_info(url, cookies_browser=None)
    except yt_dlp.utils.DownloadError as first_err:
        if browser and _needs_cookies_retry(first_err):
            return fetch_info(url, cookies_browser=browser)
        raise


def _fetch_profile_smart(url: str, browser):
    """Sama seperti _fetch_info_smart tapi untuk profile listing."""
    try:
        return fetch_profile_videos(url, cookies_browser=None)
    except yt_dlp.utils.DownloadError as first_err:
        if browser and _needs_cookies_retry(first_err):
            return fetch_profile_videos(url, cookies_browser=browser)
        raise


def _safe_dirname(name: str) -> str:
    """Buang karakter ilegal untuk nama folder Windows."""
    bad = '<>:"/\\|?*'
    cleaned = "".join(c for c in name if c not in bad).strip().rstrip(".")
    return cleaned or "unknown"


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


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
