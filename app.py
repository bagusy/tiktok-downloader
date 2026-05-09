"""TikTok Downloader — Flask web UI.

Run:
    python app.py

Open http://127.0.0.1:5000 di browser yang sudah login TikTok.
"""

import json
import re
import shutil
import time
import uuid
from pathlib import Path

import yt_dlp
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from tiktok import (
    build_options,
    categorize_formats,
    detect_url_type,
    download as do_download,
    download_savetik_to_file,
    fetch_info,
    fetch_profile_videos,
    fetch_savetik_hd,
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

    # Coba ambil HD 1080p dari savetik.co (best effort, jangan blok kalau gagal)
    sav = fetch_savetik_hd(url)
    formats_out = []
    if sav and sav.get("hd_url"):
        sz = sav.get("hd_size")
        size_str = f" · {sz/1024/1024:.1f} MB" if sz else ""
        formats_out.append({
            "id": "savetik_hd",
            "label": f"1080p HD No-Watermark (best){size_str}",
            "kind": "video",
        })
    formats_out.extend([
        {"id": fid, "label": label, "kind": kind}
        for kind, fid, label in options
    ])

    return jsonify(
        type="video",
        title=info.get("title") or info.get("id", ""),
        uploader=info.get("uploader") or info.get("uploader_id", ""),
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
        webpage_url=info.get("webpage_url"),
        formats=formats_out,
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
            # Strategi: kalau user pilih browser, langsung pakai cookies dari awal —
            # cookies (bahkan dari browser anonim) bantu pass anti-bot TikTok lebih reliable.
            # Kalau tidak pilih browser, coba tanpa cookies — beresiko anti-bot reject sebagian video.
            if browser:
                cookies_to_use = browser
                yield _sse({"event": "status", "msg": f"Menggunakan cookies dari {browser}..."})
                profile = fetch_profile_videos(profile_url, cookies_browser=browser)
            else:
                profile = fetch_profile_videos(profile_url, cookies_browser=None)
                cookies_to_use = None

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

            MAX_ATTEMPTS = 3
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

                # Skip kalau video sudah pernah di-download (file dengan video_id sudah ada)
                if video_id and _has_video_in_dir(user_dir, video_id):
                    success += 1
                    yield _sse({
                        "event": "ok",
                        "video_id": video_id,
                        "title": v.get("title", ""),
                        "tier": "skip - sudah ada",
                    })
                    continue

                outcome = None  # ("ok", title, tier) | ("skip", reason, "") | ("error", reason, "")
                last_err_msg = ""

                # Step A: coba 1080p HD via savetik (best effort, fast fail kalau service down)
                title_hint = v.get("title") or ""
                hd_filename = _make_bulk_filename(username, title_hint, video_id)
                hd_path = user_dir / hd_filename
                hd_path = _unique_path(hd_path)
                hd_ok = False
                try:
                    sav = fetch_savetik_hd(video_url, timeout=15)
                    if sav and sav.get("hd_url"):
                        hd_ok = download_savetik_to_file(sav["hd_url"], hd_path, timeout=180)
                except Exception:
                    hd_ok = False

                if hd_ok:
                    outcome = ("ok", title_hint, "1080p HD")
                else:
                    # Step B: fallback yt-dlp 720p dengan retry
                    for attempt in range(1, MAX_ATTEMPTS + 1):
                        try:
                            info = fetch_info(video_url, cookies_browser=cookies_to_use)
                            no_wm, with_wm, audios = categorize_formats(info)
                            options = build_options(no_wm, with_wm, audios)

                            target = next((o for o in options if "No-Watermark" in o[2]), None)
                            if not target:
                                target = next((o for o in options if o[0] == "video"), None)
                            if not target:
                                outcome = ("skip", "tidak ada format video", "")
                                break

                            kind, fmt_id, _label = target
                            do_download(video_url, kind, fmt_id, user_dir, cookies_browser=cookies_to_use)
                            outcome = ("ok", info.get("title", title_hint), "720p (fallback)")
                            break
                        except yt_dlp.utils.DownloadError as e:
                            last_err_msg = str(e)
                            if _is_transient(last_err_msg) and attempt < MAX_ATTEMPTS:
                                backoff = 2 * attempt  # 2s, 4s
                                yield _sse({
                                    "event": "retry",
                                    "video_id": video_id,
                                    "attempt": attempt,
                                    "max": MAX_ATTEMPTS,
                                    "wait": backoff,
                                })
                                time.sleep(backoff)
                                continue
                            outcome = ("error", last_err_msg[:300], "")
                            break
                        except Exception as e:
                            outcome = ("error", str(e)[:300], "")
                            break

                kind_, payload, tier = outcome
                if kind_ == "ok":
                    success += 1
                    yield _sse({"event": "ok", "video_id": video_id, "title": payload, "tier": tier})
                elif kind_ == "skip":
                    failed += 1
                    failed_items.append({"id": video_id, "reason": payload})
                    yield _sse({"event": "skip", "video_id": video_id, "reason": payload})
                else:
                    failed += 1
                    failed_items.append({"id": video_id, "reason": payload})
                    yield _sse({"event": "error", "video_id": video_id, "reason": payload})

                # Jeda antar video supaya tidak di-rate-limit TikTok
                time.sleep(1.5)

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

    # Hapus file lama dari video yang sama (dedup per video_id)
    vid = _video_id_from_url(url)
    if vid:
        _cleanup_existing_video(DOWNLOAD_DIR, vid)

    # Format khusus: savetik HD
    if format_id == "savetik_hd":
        return _download_savetik_hd_to_disk(url)

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

    return jsonify(
        ok=True,
        filename=final.name,
        path=str(final.resolve()),
        size=final.stat().st_size,
    )


def _needs_cookies_retry(err: Exception) -> bool:
    """Apakah error kemungkinan bisa diperbaiki dengan cookies login?"""
    msg = str(err).lower()
    return "log in" in msg or "cookies" in msg or "login" in msg


def _is_transient(msg: str) -> bool:
    """Error yang sering hilang sendiri kalau di-retry (challenge anti-bot, parser glitch, dst)."""
    m = msg.lower()
    transient_markers = (
        "unexpected response",
        "rehydration",
        "timed out",
        "timeout",
        "temporarily",
        "503",
        "429",
        "connection",
        "ssl",
    )
    return any(t in m for t in transient_markers)


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


def _video_id_from_url(url: str) -> str:
    """Ekstrak video_id dari URL TikTok format /video/<id> atau /photo/<id>."""
    m = re.search(r"/(?:video|photo)/(\d+)", url or "")
    return m.group(1) if m else ""


def _cleanup_existing_video(directory: Path, video_id: str) -> int:
    """Hapus semua file di directory yang nama-nya mengandung video_id.
    Return jumlah file yang dihapus."""
    if not video_id or not directory.exists():
        return 0
    count = 0
    for f in directory.iterdir():
        if f.is_file() and video_id in f.name:
            try:
                f.unlink()
                count += 1
            except Exception:
                pass
    return count


def _has_video_in_dir(directory: Path, video_id: str) -> bool:
    if not video_id or not directory.exists():
        return False
    return any(f.is_file() and video_id in f.name for f in directory.iterdir())


def _make_bulk_filename(uploader: str, title: str, video_id: str) -> str:
    """Buat filename konsisten untuk bulk download: '<uploader> - <title> [<id>].mp4'."""
    bad = '<>:"/\\|?*\n\r\t'
    def safe(s):
        return "".join(c for c in str(s) if c not in bad).strip().rstrip(".")
    title_safe = safe(title)[:80] or "video"
    return f"{safe(uploader) or 'tiktok'} - {title_safe} [{safe(video_id)}].mp4"


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


def _download_savetik_hd_to_disk(video_url: str):
    """Download HD 1080p via savetik ke folder downloads/. Return JSON status."""
    sav = fetch_savetik_hd(video_url)
    if not sav or not sav.get("hd_url"):
        return jsonify(error="HD 1080p tidak tersedia untuk video ini (savetik gagal)."), 502

    safe_name = (sav.get("filename") or f"tiktok_hd_{uuid.uuid4().hex[:8]}.mp4")
    safe_name = safe_name.translate(str.maketrans("", "", '<>:"/\\|?*\n\r\t')).strip().rstrip(".")
    if not safe_name:
        safe_name = f"tiktok_hd_{uuid.uuid4().hex[:8]}.mp4"
    final = _unique_path(DOWNLOAD_DIR / safe_name)

    ok = download_savetik_to_file(sav["hd_url"], final, timeout=300)
    if not ok:
        return jsonify(error="Gagal download dari savetik CDN."), 502

    return jsonify(
        ok=True,
        filename=final.name,
        path=str(final.resolve()),
        size=final.stat().st_size,
    )


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
