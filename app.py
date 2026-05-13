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
from datetime import datetime, timezone
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

# Import upload module — Playwright opsional, jangan fail keras kalau belum di-install
try:
    from tiktok_upload import (
        auto_login as tt_auto_login,
        check_login_status as tt_check_login,
        detect_running_browsers as tt_detect_running_browsers,
        login_from_browser as tt_login_from_browser,
        open_login_window as tt_open_login,
        upload_session as tt_upload_session,
        upload_videos as tt_upload_videos,
    )
    UPLOAD_AVAILABLE = True
    UPLOAD_IMPORT_ERROR = None
except Exception as _e:
    UPLOAD_AVAILABLE = False
    UPLOAD_IMPORT_ERROR = str(_e)

app = Flask(__name__)
DOWNLOAD_DIR = Path(__file__).parent / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)
CLONE_STATE_DIR = Path(__file__).parent / "clone-state"


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/quick")
def quick_page():
    return render_template("quick.html")


@app.get("/clone")
def clone_page():
    return render_template("clone.html")


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


VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


@app.get("/api/local-videos")
def api_local_videos():
    """List semua video di folder downloads/ (rekursif)."""
    items = []
    if DOWNLOAD_DIR.exists():
        for p in sorted(DOWNLOAD_DIR.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() not in VIDEO_EXTS:
                continue
            try:
                rel = str(p.relative_to(DOWNLOAD_DIR)).replace("\\", "/")
                items.append({
                    "path": str(p.resolve()),
                    "rel": rel,
                    "name": p.name,
                    "size": p.stat().st_size,
                    "mtime": int(p.stat().st_mtime),
                })
            except Exception:
                continue
    # Urutkan berdasarkan mtime descending (paling baru di atas)
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return jsonify(videos=items, count=len(items))


@app.get("/api/upload/status")
def api_upload_status():
    """Cek apakah Playwright tersedia + apakah profile sudah login TikTok.

    Juga return list browser yang sedang running (untuk hint UI auto-login).
    """
    if not UPLOAD_AVAILABLE:
        return jsonify(
            available=False,
            logged_in=False,
            detected_browsers=[],
            error=f"Playwright belum terinstall: {UPLOAD_IMPORT_ERROR}",
        )
    try:
        logged_in, username = tt_check_login()
    except Exception as e:
        return jsonify(available=True, logged_in=False, username=None,
                       detected_browsers=[], error=str(e))
    try:
        detected = tt_detect_running_browsers()
    except Exception:
        detected = []
    return jsonify(available=True, logged_in=logged_in, username=username,
                   detected_browsers=detected)


@app.post("/api/upload/auto-login")
def api_upload_auto_login():
    """Auto-deteksi browser running → extract cookies → inject → verify login.

    Tidak butuh body. Return: {ok, browser, error, log}.
    """
    if not UPLOAD_AVAILABLE:
        return jsonify(error=f"Playwright belum terinstall: {UPLOAD_IMPORT_ERROR}"), 500

    statuses: list[str] = []

    def collect(msg: str) -> None:
        statuses.append(msg)

    try:
        ok, browser_used, err = tt_auto_login(on_status=collect)
    except Exception as e:
        return jsonify(ok=False, error=str(e), log=statuses), 500

    return jsonify(ok=bool(ok), browser=browser_used, error=err, log=statuses)


@app.post("/api/upload/login-from-browser")
def api_upload_login_from_browser():
    """Import cookies TikTok dari browser user (Chrome/Edge/Firefox/dst) ke Playwright.

    Body: {"browser": "chrome"|"edge"|"firefox"|"brave"|"opera"|"vivaldi"|"chromium"}
    """
    if not UPLOAD_AVAILABLE:
        return jsonify(error=f"Playwright belum terinstall: {UPLOAD_IMPORT_ERROR}"), 500

    data = request.get_json(silent=True) or {}
    browser = (data.get("browser") or "").strip().lower()
    valid_browsers = {"chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium"}
    if browser not in valid_browsers:
        return jsonify(ok=False, error=f"Browser tidak valid. Pilih: {', '.join(sorted(valid_browsers))}"), 400

    statuses: list[str] = []

    def collect(msg: str) -> None:
        statuses.append(msg)

    try:
        ok, err = tt_login_from_browser(browser, on_status=collect)
    except Exception as e:
        return jsonify(ok=False, error=str(e), log=statuses), 500

    return jsonify(ok=bool(ok), error=err, log=statuses)


@app.post("/api/upload/login")
def api_upload_login():
    """Buka browser untuk user login manual. Block sampai login terdeteksi atau timeout 5 menit."""
    if not UPLOAD_AVAILABLE:
        return jsonify(error=f"Playwright belum terinstall: {UPLOAD_IMPORT_ERROR}"), 500

    statuses: list[str] = []

    def collect(msg: str) -> None:
        statuses.append(msg)

    try:
        ok = tt_open_login(timeout_s=300, on_status=collect)
    except Exception as e:
        return jsonify(ok=False, error=str(e), log=statuses), 500

    return jsonify(
        ok=bool(ok),
        log=statuses,
        error=None if ok else "Login tidak terdeteksi (timeout atau window ditutup).",
    )


@app.post("/api/upload/run")
def api_upload_run():
    """Streaming SSE: upload list video ke TikTok dengan caption masing-masing.

    Body: {"items": [{"path": "...", "caption": "..."}, ...], "headless": false}
    Path harus berada di dalam folder downloads/.
    """
    if not UPLOAD_AVAILABLE:
        return jsonify(error=f"Playwright belum terinstall: {UPLOAD_IMPORT_ERROR}"), 500

    data = request.get_json(silent=True) or {}
    raw_items = data.get("items") or []
    headless = bool(data.get("headless", False))

    if not isinstance(raw_items, list) or not raw_items:
        return jsonify(error="items kosong"), 400

    # Validasi semua path → resolved harus di dalam DOWNLOAD_DIR
    safe_items: list[tuple[Path, str]] = []
    download_root = DOWNLOAD_DIR.resolve()
    for raw in raw_items:
        if not isinstance(raw, dict):
            return jsonify(error="format item tidak valid"), 400
        p = (raw.get("path") or "").strip()
        if not p:
            return jsonify(error="path kosong di salah satu item"), 400
        try:
            resolved = Path(p).resolve()
        except Exception:
            return jsonify(error=f"path invalid: {p}"), 400
        try:
            resolved.relative_to(download_root)
        except ValueError:
            return jsonify(error=f"path di luar folder downloads/: {p}"), 400
        if not resolved.is_file():
            return jsonify(error=f"file tidak ditemukan: {p}"), 400
        caption = (raw.get("caption") or "").strip()
        safe_items.append((resolved, caption))

    @stream_with_context
    def generate():
        try:
            yield _sse({"event": "start", "total": len(safe_items)})
            for evt in tt_upload_videos(safe_items, headless=headless):
                yield _sse(evt)
        except Exception as e:
            yield _sse({"event": "fatal", "error": f"Error tak terduga: {e}"})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@app.post("/api/quick/run")
def api_quick_run():
    """1-click mode: URL → download kualitas terbaik → upload → hapus file. SSE."""
    if not UPLOAD_AVAILABLE:
        return jsonify(error=f"Playwright belum terinstall: {UPLOAD_IMPORT_ERROR}"), 500

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    caption_override = (data.get("caption") or "").strip()

    if not url:
        return jsonify(error="URL kosong"), 400
    if detect_url_type(url) == "profile":
        return jsonify(error="URL profil tidak didukung di Quick Mode — pakai URL video tunggal"), 400

    @stream_with_context
    def generate():
        target_path: Path | None = None
        try:
            yield _sse({"event": "status", "step": "login", "msg": "Cek login TikTok..."})
            try:
                logged_in, _username = tt_check_login()
            except Exception as e:
                yield _sse({"event": "fatal", "error": f"Cek login gagal: {e}"})
                return
            if not logged_in:
                yield _sse({
                    "event": "fatal",
                    "error": "Belum login TikTok. Buka halaman utama (/) → Import login dari browser dulu.",
                })
                return

            # Untuk video age-restricted / "Log in for access", yt-dlp butuh cookies
            # juga (terpisah dari Playwright login). Pakai browser running yang sama.
            running = []
            try:
                running = tt_detect_running_browsers()
            except Exception:
                pass
            cookies_browser = running[0] if running else None
            cookie_hint = f" (cookies: {cookies_browser})" if cookies_browser else ""

            yield _sse({"event": "status", "step": "info",
                        "msg": f"Mengambil info video{cookie_hint}..."})
            info = None
            last_err: Exception | None = None
            # Coba dengan cookies dari running browser dulu, fallback tanpa cookies
            attempts = []
            if cookies_browser:
                attempts.append(cookies_browser)
            attempts.append(None)
            for attempt_browser in attempts:
                try:
                    info = fetch_info(url, cookies_browser=attempt_browser)
                    cookies_browser = attempt_browser
                    break
                except yt_dlp.utils.DownloadError as e:
                    last_err = e
                    if not _needs_cookies_retry(e):
                        break
                except Exception as e:
                    last_err = e
                    break
            if info is None:
                yield _sse({"event": "fatal", "error": f"Gagal ambil info: {last_err}"})
                return

            # Caption priority: user override > description (full caption + hashtag) > title
            original_caption = info.get("description") or info.get("title") or ""
            caption = caption_override or original_caption
            yield _sse({
                "event": "info",
                "title": info.get("title", "")[:200],
                "uploader": info.get("uploader", ""),
                "caption_used": caption[:300],
                "caption_source": "user" if caption_override else "tiktok-original",
            })

            yield _sse({"event": "status", "step": "download", "msg": "Download kualitas tertinggi (1080p HD via savetik)..."})
            try:
                sav = fetch_savetik_hd(url, timeout=20)
            except Exception:
                sav = None

            if sav and sav.get("hd_url"):
                safe_name = sav.get("filename") or f"quick_{uuid.uuid4().hex[:8]}.mp4"
                safe_name = safe_name.translate(str.maketrans("", "", '<>:"/\\|?*\n\r\t')).strip().rstrip(".")
                if not safe_name.lower().endswith(".mp4"):
                    safe_name += ".mp4"
                final = _unique_path(DOWNLOAD_DIR / safe_name)
                if download_savetik_to_file(sav["hd_url"], final, timeout=300):
                    target_path = final
                    sz_mb = final.stat().st_size / 1024 / 1024
                    yield _sse({"event": "status", "step": "download",
                                "msg": f"Download HD 1080p sukses: {final.name} ({sz_mb:.1f} MB)"})

            if target_path is None:
                yield _sse({"event": "status", "step": "download", "msg": "HD savetik gagal, fallback ke yt-dlp 720p..."})
                try:
                    no_wm, with_wm, audios = categorize_formats(info)
                    options = build_options(no_wm, with_wm, audios)
                    target = next((o for o in options if "No-Watermark" in o[2]), None)
                    if not target:
                        target = next((o for o in options if o[0] == "video"), None)
                    if not target:
                        yield _sse({"event": "fatal", "error": "Tidak ada format video yang bisa di-download."})
                        return
                    kind, fmt_id, _label = target
                    job_dir = DOWNLOAD_DIR / f".tmp-{uuid.uuid4().hex[:8]}"
                    do_download(url, kind, fmt_id, job_dir, cookies_browser=cookies_browser)
                    files = [p for p in job_dir.iterdir() if p.is_file()] if job_dir.exists() else []
                    if not files:
                        shutil.rmtree(job_dir, ignore_errors=True)
                        yield _sse({"event": "fatal", "error": "Download selesai tapi file tidak ditemukan."})
                        return
                    src = files[0]
                    final = _unique_path(DOWNLOAD_DIR / src.name)
                    src.rename(final)
                    shutil.rmtree(job_dir, ignore_errors=True)
                    target_path = final
                    sz_mb = final.stat().st_size / 1024 / 1024
                    yield _sse({"event": "status", "step": "download",
                                "msg": f"Download 720p sukses: {final.name} ({sz_mb:.1f} MB)"})
                except yt_dlp.utils.DownloadError as e:
                    yield _sse({"event": "fatal", "error": f"yt-dlp gagal: {e}"})
                    return
                except Exception as e:
                    yield _sse({"event": "fatal", "error": f"Download gagal: {e}"})
                    return

            yield _sse({"event": "status", "step": "upload", "msg": "Mulai upload ke TikTok..."})
            uploaded = False
            for evt in tt_upload_videos([(target_path, caption)], headless=False):
                # Filter event "progress" untuk satu video — redundant di Quick Mode
                if evt.get("event") == "progress":
                    continue
                if evt.get("event") == "ok":
                    uploaded = True
                yield _sse(evt)
                if evt.get("event") in ("fatal",):
                    break

            if uploaded:
                deleted = False
                try:
                    target_path.unlink()
                    deleted = True
                except Exception as e:
                    yield _sse({"event": "status", "step": "cleanup",
                                "msg": f"Gagal hapus file lokal: {e}"})
                yield _sse({
                    "event": "complete",
                    "ok": True,
                    "deleted": deleted,
                    "filename": target_path.name,
                })
            else:
                yield _sse({
                    "event": "complete",
                    "ok": False,
                    "file_kept": str(target_path.resolve()) if target_path else None,
                })
        except Exception as e:
            yield _sse({"event": "fatal", "error": f"Error tak terduga: {e}"})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


def _clone_state_path(username: str) -> Path:
    return CLONE_STATE_DIR / f"{_safe_dirname(username)}.json"


def _load_clone_state(username: str) -> dict:
    """Load state file. Return {uploaded_ids: set[str], ...}. Missing file → empty state."""
    p = _clone_state_path(username)
    if not p.exists():
        return {"username": username, "uploaded_ids": set(), "stats": {}, "last_run": None}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {
            "username": data.get("username", username),
            "uploaded_ids": set(data.get("uploaded_ids") or []),
            "stats": data.get("stats") or {},
            "last_run": data.get("last_run"),
        }
    except (OSError, json.JSONDecodeError):
        # Corrupt state → start fresh, jangan crash run
        return {"username": username, "uploaded_ids": set(), "stats": {}, "last_run": None}


def _save_clone_state(state: dict) -> None:
    """Atomic write: tmp file + rename. Set serialize ke sorted list."""
    CLONE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = _clone_state_path(state["username"])
    payload = {
        "username": state["username"],
        "uploaded_ids": sorted(state["uploaded_ids"]),
        "stats": state.get("stats") or {},
        "last_run": state.get("last_run"),
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _download_one_for_clone(video_url: str, user_dir: Path, username: str,
                            video_id: str, title_hint: str, cookies_browser):
    """Download 1 video ke user_dir. Coba savetik HD dulu, fallback yt-dlp 720p.

    Return (path, tier, info_dict) on success; raise RuntimeError on failure.
    info_dict berisi {description, title, uploader} dari yt-dlp (untuk caption).
    """
    user_dir.mkdir(parents=True, exist_ok=True)
    hd_filename = _make_bulk_filename(username, title_hint, video_id)
    hd_path = _unique_path(user_dir / hd_filename)

    # Step A: savetik HD (best effort)
    try:
        sav = fetch_savetik_hd(video_url, timeout=15)
    except Exception:
        sav = None

    if sav and sav.get("hd_url"):
        if download_savetik_to_file(sav["hd_url"], hd_path, timeout=180):
            return hd_path, "1080p HD"

    # Step B: fallback yt-dlp 720p — kita pakai info-nya untuk caption juga
    try:
        info = fetch_info(video_url, cookies_browser=cookies_browser)
    except yt_dlp.utils.DownloadError as e:
        raise RuntimeError(f"yt-dlp fetch_info gagal: {e}")

    no_wm, with_wm, audios = categorize_formats(info)
    options = build_options(no_wm, with_wm, audios)
    target = next((o for o in options if "No-Watermark" in o[2]), None)
    if not target:
        target = next((o for o in options if o[0] == "video"), None)
    if not target:
        raise RuntimeError("tidak ada format video yang bisa di-download")

    kind, fmt_id, _label = target
    job_dir = user_dir / f".tmp-{uuid.uuid4().hex[:8]}"
    try:
        do_download(video_url, kind, fmt_id, job_dir, cookies_browser=cookies_browser)
        files = [p for p in job_dir.iterdir() if p.is_file()] if job_dir.exists() else []
        if not files:
            raise RuntimeError("file hasil download tidak ditemukan")
        src = files[0]
        final = _unique_path(user_dir / src.name)
        src.rename(final)
        return final, "720p (fallback)"
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


@app.post("/api/clone/run")
def api_clone_run():
    """Clone akun TikTok: bulk-download dari profile + auto-upload ke akun user.

    Body: {"url": "<profile_url>", "browser": "<optional cookie source>", "max_count": <int|null>}
    SSE stream. Per video: skip-if-uploaded → download HD → upload → hapus file → mark uploaded.
    """
    if not UPLOAD_AVAILABLE:
        return jsonify(error=f"Playwright belum terinstall: {UPLOAD_IMPORT_ERROR}"), 500

    data = request.get_json(silent=True) or {}
    profile_url = (data.get("url") or "").strip()
    browser_hint = (data.get("browser") or "").strip() or None
    max_count = data.get("max_count")
    try:
        max_count = int(max_count) if max_count not in (None, "", 0) else None
    except (TypeError, ValueError):
        max_count = None

    if not profile_url:
        return jsonify(error="URL kosong"), 400
    if detect_url_type(profile_url) != "profile":
        return jsonify(error="URL bukan URL profil TikTok"), 400

    @stream_with_context
    def generate():
        try:
            # Step 0: cek login Playwright (target akun upload-an user)
            yield _sse({"event": "status", "msg": "Cek login TikTok (akun upload tujuan)..."})
            try:
                logged_in, dest_username = tt_check_login()
            except Exception as e:
                yield _sse({"event": "fatal", "error": f"Cek login gagal: {e}"})
                return
            if not logged_in:
                yield _sse({
                    "event": "fatal",
                    "error": "Belum login TikTok. Buka halaman utama (/) untuk import login dari browser.",
                })
                return

            # Step 1: deteksi cookies browser untuk yt-dlp (fetch_info & fallback download)
            cookies_browser = browser_hint
            if not cookies_browser:
                try:
                    running = tt_detect_running_browsers()
                    cookies_browser = running[0] if running else None
                except Exception:
                    cookies_browser = None
            cookie_hint = f" (cookies: {cookies_browser})" if cookies_browser else ""

            # Step 2: ambil daftar video dari profile
            yield _sse({"event": "status",
                        "msg": f"Mengambil daftar video dari profil{cookie_hint}..."})
            try:
                profile = fetch_profile_videos(profile_url, cookies_browser=cookies_browser,
                                               max_count=max_count)
            except yt_dlp.utils.DownloadError as e:
                yield _sse({"event": "fatal", "error": f"Gagal ambil profile: {e}",
                            "needs_login": _needs_cookies_retry(e)})
                return
            except Exception as e:
                yield _sse({"event": "fatal", "error": f"Error tak terduga: {e}"})
                return

            videos = profile["videos"]
            username = profile["username"] or "unknown"
            total = len(videos)

            user_dir = DOWNLOAD_DIR / _safe_dirname(username)
            user_dir.mkdir(parents=True, exist_ok=True)

            # Step 3: load state — skip video_id yang sudah di-upload
            state = _load_clone_state(username)
            already = state["uploaded_ids"]
            pending = [v for v in videos if v.get("id") and v["id"] not in already]
            skipped_already = total - len(pending)

            yield _sse({
                "event": "start",
                "username": username,
                "dest_username": dest_username,
                "total": total,
                "pending": len(pending),
                "skipped_already": skipped_already,
                "save_dir": str(user_dir),
            })

            if not pending:
                yield _sse({"event": "done", "uploaded": 0, "failed": 0, "skipped": skipped_already,
                            "username": username})
                return

            uploaded = 0
            failed = 0

            # Step 4: open Playwright session reusable, loop tiap video pending
            try:
                with tt_upload_session(headless=False) as sess:
                    for i, v in enumerate(pending, start=1):
                        video_url = v["url"]
                        video_id = v.get("id") or ""
                        title_hint = v.get("title") or ""

                        yield _sse({
                            "event": "progress",
                            "current": i,
                            "total": len(pending),
                            "video_id": video_id,
                            "url": video_url,
                        })

                        # 4a: download
                        downloaded_path: Path | None = None
                        try:
                            yield _sse({"event": "status",
                                        "msg": f"[{i}/{len(pending)}] Download {video_id}..."})
                            downloaded_path, tier = _download_one_for_clone(
                                video_url, user_dir, username, video_id, title_hint, cookies_browser
                            )
                            yield _sse({"event": "downloaded", "video_id": video_id,
                                        "filename": downloaded_path.name, "tier": tier})
                        except Exception as e:
                            failed += 1
                            yield _sse({"event": "error", "video_id": video_id,
                                        "phase": "download", "reason": str(e)[:300]})
                            continue

                        # 4b: ambil caption asli (description full). Kalau gagal, pakai title.
                        caption = title_hint
                        try:
                            info = fetch_info(video_url, cookies_browser=cookies_browser)
                            caption = (info.get("description") or info.get("title")
                                       or title_hint or "")
                        except Exception:
                            pass

                        # 4c: upload via session yang sama
                        upload_ok = False
                        try:
                            yield _sse({"event": "status",
                                        "msg": f"[{i}/{len(pending)}] Upload {downloaded_path.name}..."})
                            for evt in sess.upload_one(downloaded_path, caption):
                                yield _sse(evt)
                            upload_ok = True
                        except Exception as e:
                            yield _sse({"event": "error", "video_id": video_id,
                                        "phase": "upload", "reason": str(e)[:300]})

                        # 4d: cleanup + state persist
                        if upload_ok:
                            uploaded += 1
                            try:
                                downloaded_path.unlink()
                                deleted = True
                            except Exception:
                                deleted = False
                            state["uploaded_ids"].add(video_id)
                            state["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                            state["stats"]["total_uploaded"] = (
                                state["stats"].get("total_uploaded", 0) + 1
                            )
                            try:
                                _save_clone_state(state)
                            except Exception as e:
                                yield _sse({"event": "status",
                                            "msg": f"Warning: gagal simpan state: {e}"})
                            yield _sse({"event": "ok", "video_id": video_id,
                                        "filename": downloaded_path.name, "deleted": deleted})
                        else:
                            failed += 1
                            # File tetap di disk supaya user bisa retry manual

                        # 4e: jeda antar video supaya tidak rate-limit
                        if i < len(pending):
                            time.sleep(4)

            except Exception as e:
                yield _sse({"event": "fatal", "error": f"Session error: {e}"})
                return

            yield _sse({
                "event": "done",
                "uploaded": uploaded,
                "failed": failed,
                "skipped": skipped_already,
                "username": username,
            })
        except Exception as e:
            yield _sse({"event": "fatal", "error": f"Error tak terduga: {e}"})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


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
