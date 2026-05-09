"""TikTok Downloader CLI.

Usage:
    python tiktok.py <url-tiktok>
    python tiktok.py <url> -q best                       # auto-pilih HD no-watermark
    python tiktok.py <url> -q audio                      # auto-pilih audio MP3
    python tiktok.py <url> --cookies-browser chrome      # pakai cookies browser (untuk video login-only)
"""

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    import yt_dlp
except ImportError:
    print("[!] yt-dlp belum terinstal.")
    print("    Jalankan: pip install -r requirements.txt")
    sys.exit(1)


SCRIPT_DIR = Path(__file__).parent
DOWNLOAD_DIR = SCRIPT_DIR / "downloads"


def progress_hook(d):
    if d["status"] == "downloading":
        pct = (d.get("_percent_str") or "").strip()
        speed = (d.get("_speed_str") or "").strip()
        eta = (d.get("_eta_str") or "").strip()
        sys.stdout.write(f"\r[*] Mengunduh... {pct}  {speed}  ETA {eta}     ")
        sys.stdout.flush()
    elif d["status"] == "finished":
        sys.stdout.write("\r[*] Mengunduh... 100%, memproses file...           \n")


def _base_opts(cookies_browser):
    opts = {"quiet": True, "no_warnings": True}
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


def fetch_info(url, cookies_browser=None):
    opts = _base_opts(cookies_browser)
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


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
