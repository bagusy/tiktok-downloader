# TikTok Downloader

Aplikasi web lokal untuk download video TikTok tanpa watermark, dengan kualitas terbaik yang TikTok berikan. Mirip ssstik.io tapi jalan **di komputer kamu sendiri** — tidak ada server tengah, tidak ada iklan.

Server Flask berjalan di `http://127.0.0.1:5000`, kamu pakai lewat browser yang sudah login TikTok.

## Fitur

- Download video TikTok HD tanpa watermark
- Download versi original dengan watermark
- Download audio (M4A; MP3 jika ffmpeg terinstall)
- Auto-detect kualitas tertinggi yang tersedia
- Otomatis pakai cookies dari browser kalau video butuh login (age-restricted)
- One-click setup di Windows lewat `run.bat`

## Quick Start (Windows)

### Opsi A — Download ZIP

1. Klik tombol hijau **Code** di repo ini → **Download ZIP**
2. Extract ZIP-nya
3. Dobel klik **`run.bat`**
4. Tunggu sampai browser terbuka otomatis ke `http://127.0.0.1:5000`

### Opsi B — Pakai git

```powershell
git clone https://github.com/bagusy/tiktok-downloader.git
cd tiktok-downloader
.\run.bat
```

`run.bat` akan otomatis:
- Cek Python — install via `winget` kalau belum ada
- Cek `yt-dlp` + `Flask` — install via `pip` kalau belum ada
- Start server Flask + buka browser

## Cara Pakai

1. Buka video TikTok yang ingin di-download di aplikasi/browser TikTok
2. Klik tombol **Share** → **Copy Link**
3. Paste URL ke form di `http://127.0.0.1:5000`
4. Klik **Get** → akan muncul thumbnail + judul + pilihan kualitas
5. Klik tombol **Download** di kualitas yang diinginkan
6. Browser akan kasih dialog "Save as", file juga ter-copy ke folder `downloads/`

## Untuk Video Age-Restricted

Beberapa video TikTok butuh login untuk diakses (yang biasanya muncul tulisan *"This post may not be comfortable for some audiences"*). Untuk video seperti ini:

1. **Login ke TikTok** di salah satu browser (Chrome/Edge/Firefox/Brave/dst.)
2. Di form aplikasi, pilih browser tersebut di dropdown **Fallback cookies dari browser**
3. Klik **Get** seperti biasa — aplikasi akan otomatis pakai cookies dari browser tersebut kalau video butuh login

> **Catatan:** Untuk Chrome/Edge, browser-nya disarankan **ditutup dulu** sebelum klik Download, karena browser tsb mengunci file cookies-nya. Firefox tidak mengunci, jadi paling aman.

Kalau video tidak butuh login, dropdown ini bisa dibiarkan apa adanya — cookies hanya dipakai sebagai fallback otomatis.

## Manual Install (Tanpa run.bat)

Kalau kamu lebih suka kontrol manual atau di OS lain:

```bash
# 1. Pastikan Python 3.10+ terinstall
python --version

# 2. Install dependencies (pakai --pre supaya yt-dlp dapat extractor TikTok terbaru)
pip install --upgrade --pre "yt-dlp[default]" Flask

# 3. Jalankan
python app.py
```

Lalu buka `http://127.0.0.1:5000` di browser.

## CLI (Bonus)

Selain web UI, ada juga versi CLI `tiktok.py` kalau kamu lebih suka terminal:

```bash
# Mode interaktif
python tiktok.py

# Langsung dari URL
python tiktok.py "https://www.tiktok.com/@user/video/1234567890"

# Auto-pilih HD no-watermark, no prompt
python tiktok.py "<url>" -q best

# Audio MP3 saja (butuh ffmpeg)
python tiktok.py "<url>" -q audio

# Video age-restricted, ambil cookies dari Chrome
python tiktok.py "<url>" --cookies-browser chrome
```

## Tech Stack

- **Python 3.10+** — runtime
- **Flask** — web server lokal
- **yt-dlp** — extractor TikTok (versi nightly/pre supaya up-to-date dengan perubahan TikTok)
- **HTML + CSS + vanilla JS** — frontend (no build step)

## Troubleshooting

| Masalah | Solusi |
|---------|--------|
| `'python' is not recognized` setelah install via run.bat | Tutup PowerShell/CMD, buka jendela baru |
| `Unexpected response from webpage request` | Jalankan ulang dengan `pip install --upgrade --pre "yt-dlp[default]"` — extractor TikTok perlu fresh |
| `This post may not be comfortable for some audiences` | Pilih browser di dropdown "Fallback cookies", lihat section Age-Restricted di atas |
| Audio tidak jadi MP3, tapi M4A | Install ffmpeg: `winget install Gyan.FFmpeg`, lalu coba lagi |
| `winget tidak tersedia` di Windows 10 lama | Install Python manual dari https://www.python.org/downloads/ (centang "Add Python to PATH") |
| Browser tidak otomatis terbuka | Manual buka `http://127.0.0.1:5000` |

## Struktur Project

```
tiktok-downloader/
├── run.bat              # one-click installer + launcher (Windows)
├── app.py               # Flask backend
├── tiktok.py            # CLI + helper functions (di-share dengan app.py)
├── requirements.txt     # daftar dependency
├── templates/
│   └── index.html       # UI
├── static/
│   ├── style.css
│   └── app.js
└── downloads/           # auto-dibuat saat pertama download
```

## Disclaimer

Aplikasi ini dibuat untuk keperluan pribadi (download konten yang kamu sendiri punya hak atau yang bersifat publik). Hormati hak cipta uploader dan ToS TikTok. Jangan distribusi ulang konten orang lain tanpa izin.
