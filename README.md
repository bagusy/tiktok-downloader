# TikTok Downloader + Uploader

Aplikasi web lokal untuk **download** video TikTok tanpa watermark dan **upload** balik ke akun TikTok kamu, full otomatis di komputer sendiri — tidak ada server tengah, tidak ada iklan.

Server Flask berjalan di `http://127.0.0.1:5000`, kamu pakai lewat browser yang sudah login TikTok.

## Fitur

- Download video TikTok **1080p HD tanpa watermark** (~10-20 MB) — sama kualitasnya dengan ssstik.io
- Download versi 720p HD tanpa watermark (lebih kecil, ~1-3 MB)
- Download versi original dengan watermark
- Download audio (M4A; MP3 jika ffmpeg terinstall)
- **Bulk download semua video dari satu akun** — paste URL profil, klik 1 tombol, semua video ke-download dengan live progress (post foto/slideshow di-skip)
- **Upload otomatis ke TikTok** — pilih video dari folder `downloads/`, isi caption, klik tombol; Playwright buka browser, upload, dan klik Post otomatis. Bisa multi-pilih untuk batch upload.
- Tampilkan ukuran file di tiap pilihan kualitas
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
6. File tersimpan ke folder `downloads/` di project. UI menampilkan path lengkap setelah selesai.

## Bulk Download (Semua Video dari 1 Akun)

1. Buka profil TikTok di browser, copy URL profilnya — bentuknya:
   `https://www.tiktok.com/@username` (tanpa `/video/...` di belakang)
2. Paste URL itu ke form, klik **Get**
3. Akan muncul panel: `@username — N video ditemukan`
4. Klik **Download Semua Video** — progress bar dan log per video akan tampil
5. File tersimpan ke `downloads/<username>/` — auto-pilih kualitas HD no-watermark untuk tiap video

Catatan:
- Post foto/slideshow di-skip (sesuai permintaan: hanya video)
- Ada jeda kecil (~0.5 detik) antar video untuk menghindari rate-limit TikTok
- Kalau ada video yang butuh login, pilih browser di dropdown "Fallback cookies" sebelum klik Download Semua

## Upload Otomatis ke TikTok

Fitur ini upload video dari folder `downloads/` ke akun TikTok kamu, full otomatis sampai klik tombol Post.

### Setup Pertama Kali

1. Pastikan **Playwright + Chromium** sudah terinstall — `run.bat` melakukannya otomatis. Manual:
   ```powershell
   pip install playwright
   python -m playwright install chromium
   ```
2. **Login TikTok di Firefox** (atau Brave) terlebih dulu di komputer kamu — buka tiktok.com, login pakai akun yang mau dipakai upload.
3. Di UI, scroll ke section **Upload ke TikTok**.
4. Pilih browser di dropdown (rekomendasi: **Firefox**), klik **Import login dari browser**.
5. Cookies TikTok akan di-extract dari browser kamu dan di-inject ke profile Playwright. Badge berubah jadi "Logged in" hijau.

Sesi login disimpan di folder `playwright-profile/` di project. Cukup import **sekali**; selanjutnya bisa langsung upload.

#### Kenapa Firefox?

Chrome 127+ (Sept 2024) pakai App-Bound Encryption yang ngunci cookie file ke binary Chrome itu sendiri. Tools eksternal (termasuk yt-dlp dan browser_cookie3) sering gagal decrypt → error "Failed to decrypt with DPAPI".

Solusi paling reliable: login TikTok di **Firefox** dan import dari sana. Cookies Firefox tidak terenkripsi seperti Chrome modern.

Alternatif lain (kalau Firefox tidak ada):
- **Brave** — Chromium-based, biasanya masih bisa di-decrypt
- **Login manual via Playwright** — tombol "Login manual via Playwright" di UI buka window Chromium kosong; login langsung di sana (catatan: login Google sering ditolak Google, jadi pakai email/password atau nomor HP)

### Upload Video

1. Klik **Refresh daftar video** untuk load semua video di `downloads/` (rekursif, termasuk subfolder per-uploader).
2. Centang video yang mau di-upload (bisa multi-pilih).
3. Isi **caption + hashtag** di kotak masing-masing (opsional — boleh kosong).
4. Klik **Upload Video Terpilih (N)**.
5. Window browser akan terbuka per-batch, video di-upload satu per satu, caption otomatis terisi, dan tombol Post diklik otomatis. Live log per langkah tampil di UI.

### Catatan & Troubleshooting Upload

- **Window browser harus tetap kebuka** selama upload. Jangan tutup window-nya.
- **Captcha** kadang muncul (pertama kali atau saat TikTok curiga otomasi). Selesaikan manual di window browser; otomasi akan tetap lanjut setelah captcha lewat.
- **Rate limit TikTok**: kalau upload banyak sekaligus, TikTok bisa reject sementara. Default ada jeda 4 detik antar video. Untuk upload >10 video, lebih aman dipisah jadi beberapa batch.
- **Selector berubah**: TikTok kadang ubah struktur halaman upload. Kalau upload mendadak gagal di langkah "Tunggu Post button", check `tiktok_upload.py` — kemungkinan butuh update selector di `_locate_caption_editor` / `_locate_post_button`.
- **Reset login**: hapus folder `playwright-profile/` kalau mau ganti akun atau ada masalah.

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
pip install --upgrade --pre "yt-dlp[default]" Flask playwright

# 3. (Opsional, hanya kalau mau pakai fitur upload) install browser Chromium
python -m playwright install chromium

# 4. Jalankan
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
- **Playwright** — otomasi browser untuk fitur upload (Chromium headed, persistent profile)
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
├── app.py               # Flask backend (download + upload endpoints)
├── tiktok.py            # CLI + helper download (di-share dengan app.py)
├── tiktok_upload.py     # Playwright otomasi untuk fitur upload
├── requirements.txt     # daftar dependency
├── templates/
│   └── index.html       # UI
├── static/
│   ├── style.css
│   └── app.js
├── downloads/           # auto-dibuat saat pertama download
└── playwright-profile/  # auto-dibuat saat pertama login TikTok (jangan commit)
```

## Disclaimer

Aplikasi ini dibuat untuk keperluan pribadi (download konten yang kamu sendiri punya hak atau yang bersifat publik). Hormati hak cipta uploader dan ToS TikTok. Jangan distribusi ulang konten orang lain tanpa izin.
