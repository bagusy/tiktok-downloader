"""TikTok Downloader CLI.

Usage:
    python tiktok.py <url-tiktok>
    python tiktok.py <url> -q best                       # auto-pilih HD no-watermark
    python tiktok.py <url> -q audio                      # auto-pilih audio MP3
    python tiktok.py <url> --cookies-browser chrome      # pakai cookies browser (untuk video login-only)
"""

import argparse
import base64
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    requests = None  # opsional — hanya dibutuhkan untuk fitur HD via savetik

try:
    import yt_dlp
except ImportError:
    print("[!] yt-dlp belum terinstal.")
    print("    Jalankan: pip install -r requirements.txt")
    sys.exit(1)


SCRIPT_DIR = Path(__file__).parent
DOWNLOAD_DIR = SCRIPT_DIR / "downloads"


class _QuietLogger:
    """Buang semua output internal yt-dlp ke stderr.

    Kita sudah handle error via try/except dan progress via progress_hook,
    jadi yt-dlp tidak perlu print apa-apa sendiri (kalau tidak, terminal jadi noisy
    saat bulk download — error untuk video N+1 tampil di antara progress video N).
    """
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


_LINE_WIDTH = 78  # karakter, untuk wipe residue saat overwrite \r


def progress_hook(d):
    status = d.get("status")
    if status == "downloading":
        pct = (d.get("_percent_str") or "").strip()
        speed = (d.get("_speed_str") or "").strip()
        eta = (d.get("_eta_str") or "").strip()
        msg = f"[*] Mengunduh... {pct}  {speed}  ETA {eta}"
    elif status == "finished":
        msg = "[*] Mengunduh... 100%, memproses file..."
    else:
        return
    sys.stdout.write(f"\r{msg:<{_LINE_WIDTH}}")
    if status == "finished":
        sys.stdout.write("\n")
    sys.stdout.flush()


def _base_opts(cookies_browser):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "logger": _QuietLogger(),
        # Biar yt-dlp internal retry kalau hit anti-bot challenge / network blip.
        "extractor_retries": 3,
        "retries": 3,
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


def fetch_info(url, cookies_browser=None):
    opts = _base_opts(cookies_browser)
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


SAVETIK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def _decode_snapcdn_jwt(jwt: str):
    """Decode JWT payload (no verify) untuk dapat inner URL & filename. None kalau gagal."""
    parts = jwt.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception:
        return None


def fetch_savetik_hd(video_url: str, timeout: int = 20):
    """Ambil URL 1080p HD lewat savetik.co. Return dict dengan url HD & regular,
    atau None kalau service unavailable / tidak ketemu URL HD.

    Format return:
      {
        "hd_url": str | None,        # snapcdn redirect URL (validasi server-side)
        "hd_size": int | None,       # bytes
        "regular_url": str | None,
        "regular_size": int | None,
        "filename": str | None,      # filename suggestion dari savetik
      }
    """
    if requests is None:
        return None
    try:
        s = requests.Session()
        s.headers["User-Agent"] = SAVETIK_UA
        r = s.post(
            "https://savetik.co/api/ajaxSearch",
            data={"q": video_url, "lang": "en"},
            headers={
                "Origin": "https://savetik.co",
                "Referer": "https://savetik.co/en",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        data_html = r.json().get("data", "")
        if not isinstance(data_html, str) or not data_html:
            return None

        result = {"hd_url": None, "hd_size": None, "regular_url": None,
                  "regular_size": None, "filename": None}

        for full_url, jwt in re.findall(
            r'href="(https://dl\.snapcdn\.app/get\?token=([\w\-.]+))"', data_html
        ):
            obj = _decode_snapcdn_jwt(jwt)
            if not obj:
                continue
            inner = (obj.get("url") or "").lower()
            filename = obj.get("filename") or ""
            # HD = URL berisi "_original.mp4" (TikTok's original-quality endpoint)
            if "_original.mp4" in inner:
                result["hd_url"] = full_url
                result["filename"] = filename
            elif inner.endswith(".mp4") and ".mp3" not in inner and "_original" not in inner:
                if not result["regular_url"]:
                    result["regular_url"] = full_url

        # Probe sizes via HEAD untuk surface info ke UI
        for kind in ("hd", "regular"):
            url = result[f"{kind}_url"]
            if url:
                try:
                    h = s.head(url, allow_redirects=True, timeout=10,
                               headers={"Referer": "https://savetik.co/"})
                    sz = int(h.headers.get("Content-Length") or 0)
                    if sz > 0:
                        result[f"{kind}_size"] = sz
                except Exception:
                    pass

        if not result["hd_url"] and not result["regular_url"]:
            return None
        return result
    except Exception:
        return None


def stream_savetik_url(url: str, timeout: int = 60):
    """Buka stream ke URL savetik (snapcdn). Return tuple (response, filename)
    atau (None, None) kalau gagal. Caller wajib close response."""
    if requests is None:
        return None, None
    try:
        s = requests.Session()
        s.headers["User-Agent"] = SAVETIK_UA
        r = s.get(url, stream=True, timeout=timeout,
                  headers={"Referer": "https://savetik.co/"})
        if r.status_code != 200:
            r.close()
            return None, None
        cd = r.headers.get("Content-Disposition", "")
        m = re.search(r'filename="?([^";]+)"?', cd)
        filename = m.group(1) if m else None
        return r, filename
    except Exception:
        return None, None


def download_savetik_to_file(snapcdn_url: str, output_path: Path,
                             timeout: int = 180) -> bool:
    """Download URL snapcdn langsung ke file di disk. Return True kalau sukses."""
    if requests is None:
        return False
    try:
        s = requests.Session()
        s.headers["User-Agent"] = SAVETIK_UA
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with s.get(snapcdn_url, stream=True, timeout=timeout,
                   headers={"Referer": "https://savetik.co/"}) as r:
            if r.status_code != 200:
                return False
            with output_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
        return True
    except Exception:
        # Bersihkan file parsial kalau ada
        try:
            if output_path.exists():
                output_path.unlink()
        except Exception:
            pass
        return False


def detect_url_type(url: str) -> str:
    """Return 'video', 'profile', atau 'unknown' berdasarkan struktur URL TikTok."""
    try:
        p = urlparse((url or "").strip())
    except Exception:
        return "unknown"
    host = (p.netloc or "").lower()
    path = p.path or ""
    if "tiktok.com" not in host:
        return "unknown"
    if "/video/" in path:
        return "video"
    if "/photo/" in path:
        return "video"  # diperlakukan sebagai post tunggal (yt-dlp handle)
    # /@username atau /@username/ → profil
    if path.startswith("/@") and path.rstrip("/").count("/") == 1:
        return "profile"
    # vt.tiktok.com short links → video
    if host.startswith("vt.") or host.startswith("vm."):
        return "video"
    return "unknown"


def fetch_profile_videos(url, cookies_browser=None, max_count=None):
    """Ambil daftar video dari URL profil TikTok.

    Return dict: {username, video_count, videos: [{id, url, title}, ...]}
    Skip post foto/slideshow (URL berisi /photo/).
    """
    opts = _base_opts(cookies_browser)
    opts["extract_flat"] = "in_playlist"
    if max_count:
        opts["playlistend"] = max_count

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    entries = info.get("entries") or []
    videos = []
    for e in entries:
        if not e:
            continue
        webpage = e.get("webpage_url") or e.get("url") or ""
        if "/photo/" in webpage:
            continue
        if "/video/" not in webpage:
            continue
        videos.append({
            "id": e.get("id") or "",
            "url": webpage,
            "title": e.get("title") or "",
        })

    username = (info.get("uploader") or info.get("uploader_id") or info.get("channel") or "")
    if username.startswith("@"):
        username = username[1:]
    if not username:
        # Fallback: ambil dari path URL (/@username)
        try:
            path = (urlparse(url).path or "").strip("/")
            if path.startswith("@"):
                username = path[1:].split("/")[0]
        except Exception:
            pass

    return {
        "username": username,
        "video_count": len(videos),
        "videos": videos,
    }


def categorize_formats(info):
    """Pisahkan format ke: no-watermark, with-watermark, audio."""
    formats = info.get("formats", []) or []
    no_wm, with_wm, audios = [], [], []

    for f in formats:
        vcodec = f.get("vcodec") or "none"
        acodec = f.get("acodec") or "none"
        note = (f.get("format_note") or "").lower()
        fid = (f.get("format_id") or "").lower()

        if vcodec == "none" and acodec != "none":
            audios.append(f)
            continue
        if vcodec == "none":
            continue

        # TikTok: format_id 'download_addr' / 'play_addr' biasanya berwatermark.
        # 'download' / 'h264_*' biasanya tanpa watermark.
        is_wm = ("watermark" in note) or ("addr" in fid) or ("wm" in note)
        (with_wm if is_wm else no_wm).append(f)

    def qkey(f):
        return (f.get("height") or 0, f.get("tbr") or 0, f.get("filesize") or 0)

    no_wm.sort(key=qkey, reverse=True)
    with_wm.sort(key=qkey, reverse=True)
    audios.sort(key=lambda f: f.get("abr") or 0, reverse=True)
    return no_wm, with_wm, audios


def build_options(no_wm, with_wm, audios):
    """Buat list pilihan untuk ditampilkan ke user."""
    options = []
    if no_wm:
        f = no_wm[0]
        h = f.get("height") or "?"
        options.append(("video", f["format_id"], f"HD No-Watermark ({h}p)"))
    if with_wm:
        f = with_wm[0]
        h = f.get("height") or "?"
        options.append(("video", f["format_id"], f"With Watermark  ({h}p)"))
    if audios:
        f = audios[0]
        abr = f.get("abr") or "?"
        options.append(("audio", f["format_id"], f"Audio MP3       ({abr}kbps)"))
    return options


def auto_pick(options, preset):
    """Pilih opsi otomatis berdasarkan preset CLI."""
    mapping = {
        "best": lambda kind, _: kind == "video",
        "wm":   lambda _, label: "Watermark" in label and "No-" not in label,
        "audio": lambda kind, _: kind == "audio",
    }
    pred = mapping.get(preset)
    if not pred:
        return None
    for kind, fid, label in options:
        if pred(kind, label):
            return kind, fid, label
    return None


def download(url, kind, fmt_id, output_dir, cookies_browser=None):
    output_dir.mkdir(parents=True, exist_ok=True)
    opts = _base_opts(cookies_browser)
    opts.update({
        "format": fmt_id,
        "outtmpl": str(output_dir / "%(uploader)s - %(title).80s [%(id)s].%(ext)s"),
        "progress_hooks": [progress_hook],
        "restrictfilenames": False,
        "windowsfilenames": True,
    })
    if kind == "audio":
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def main():
    parser = argparse.ArgumentParser(description="TikTok Downloader CLI")
    parser.add_argument("url", nargs="?", help="URL video TikTok")
    parser.add_argument("-o", "--output", default=str(DOWNLOAD_DIR),
                        help="Folder output (default: ./downloads)")
    parser.add_argument("-q", "--quality", choices=["best", "wm", "audio"],
                        help="Pilih kualitas otomatis tanpa prompt")
    parser.add_argument("--cookies-browser", dest="cookies_browser",
                        choices=["chrome", "firefox", "edge", "brave", "opera", "vivaldi", "chromium"],
                        help="Ambil cookies dari browser ini (untuk video age-restricted / login-only). "
                             "Browser harus sudah login ke TikTok.")
    args = parser.parse_args()

    url = args.url or input("[?] Paste URL TikTok: ").strip()
    if not url:
        print("[!] URL kosong.")
        sys.exit(1)

    print("[*] Mengambil info video...")
    try:
        info = fetch_info(url, cookies_browser=args.cookies_browser)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        print(f"[!] Gagal mengambil info: {msg}")
        if "Log in" in msg or "cookies" in msg.lower():
            print("[i] Video ini butuh login. Coba ulangi dengan flag:")
            print("    --cookies-browser chrome   (atau: edge, firefox, brave, opera)")
            print("    Pastikan browser tsb sudah login ke TikTok.")
        sys.exit(1)

    print(f"[*] Judul    : {info.get('title') or info.get('id', '?')}")
    print(f"[*] Uploader : {info.get('uploader') or info.get('uploader_id', '?')}")
    if info.get("duration"):
        print(f"[*] Durasi   : {info['duration']}s")

    no_wm, with_wm, audios = categorize_formats(info)
    options = build_options(no_wm, with_wm, audios)

    if not options:
        print("[!] Tidak ada format yang tersedia (mungkin slide foto / akun privat).")
        sys.exit(1)

    print("[*] Kualitas tersedia:")
    for i, (_, _, label) in enumerate(options, start=1):
        print(f"      {i}) {label}")

    if args.quality:
        picked = auto_pick(options, args.quality)
        if not picked:
            print(f"[!] Preset '{args.quality}' tidak tersedia untuk video ini.")
            sys.exit(1)
        kind, fmt_id, label = picked
        print(f"[*] Auto-pilih: {label}")
    else:
        raw = input(f"[?] Pilih [1-{len(options)}]: ").strip()
        try:
            kind, fmt_id, label = options[int(raw) - 1]
        except (ValueError, IndexError):
            print("[!] Pilihan tidak valid.")
            sys.exit(1)

    try:
        download(url, kind, fmt_id, Path(args.output), cookies_browser=args.cookies_browser)
    except yt_dlp.utils.DownloadError as e:
        print(f"\n[!] Gagal download: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[!] Dibatalkan user.")
        sys.exit(130)

    print(f"[OK] Tersimpan di: {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
