@echo off
setlocal enabledelayedexpansion
title TikTok Clone Account
cd /d "%~dp0"

echo ============================================================
echo                 TikTok Clone Account
echo       Bulk Download Profile + Auto Upload ke akun kamu
echo ============================================================
echo.

REM ========== [1/3] Cari atau install Python ==========
echo [1/3] Mencari Python...

set "PYTHON_EXE="

where python >nul 2>&1 && set "PYTHON_EXE=python"
if not defined PYTHON_EXE where py >nul 2>&1 && set "PYTHON_EXE=py"
if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYTHON_EXE if exist "C:\Python312\python.exe" set "PYTHON_EXE=C:\Python312\python.exe"
if not defined PYTHON_EXE if exist "C:\Python311\python.exe" set "PYTHON_EXE=C:\Python311\python.exe"

if defined PYTHON_EXE (
    echo       Ditemukan: !PYTHON_EXE!
    "!PYTHON_EXE!" --version
    goto :deps
)

echo       Python tidak ditemukan. Akan diinstall otomatis...
echo.

REM Cari winget
set "WINGET="
where winget >nul 2>&1 && set "WINGET=winget"
if not defined WINGET if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe" set "WINGET=%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe"

if not defined WINGET (
    echo [ERROR] winget tidak tersedia di sistem ini.
    echo.
    echo Silakan install Python 3.12 manual dari:
    echo     https://www.python.org/downloads/
    echo.
    echo Saat install, CENTANG opsi "Add Python to PATH".
    echo Lalu jalankan ulang script ini.
    echo.
    pause
    exit /b 1
)

echo       Menginstall Python 3.12 via winget (mungkin perlu konfirmasi UAC)...
"!WINGET!" install --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements --scope user --silent
if errorlevel 1 (
    echo.
    echo [ERROR] Gagal install Python via winget.
    echo Coba install manual dari https://www.python.org/downloads/
    pause
    exit /b 1
)

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
) else if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
) else (
    echo [ERROR] Python terinstall tapi binary tidak ditemukan di lokasi standar.
    pause
    exit /b 1
)

echo       Python terinstall: !PYTHON_EXE!
"!PYTHON_EXE!" --version

:deps
echo.

REM ========== [2/3] Cek dan install dependencies ==========
echo [2/3] Memeriksa dependencies (Flask, yt-dlp, playwright)...

"!PYTHON_EXE!" -c "import yt_dlp, flask, playwright" >nul 2>&1
if errorlevel 1 (
    echo       Belum lengkap. Menginstall dari requirements.txt...
    echo       (yt-dlp dipakai versi nightly supaya extractor TikTok up-to-date^)
    "!PYTHON_EXE!" -m pip install --upgrade pip
    "!PYTHON_EXE!" -m pip install --upgrade --pre "yt-dlp[default]" Flask playwright
    if errorlevel 1 (
        echo.
        echo [ERROR] Gagal install dependencies.
        pause
        exit /b 1
    )
    echo       Dependencies terinstall.
) else (
    echo       Sudah terinstall.
)

REM Pastikan browser Chromium untuk Playwright sudah ke-download
echo       Memeriksa / install browser Chromium untuk Playwright...
"!PYTHON_EXE!" -m playwright install chromium
if errorlevel 1 (
    echo.
    echo [WARNING] Gagal install Chromium. Fitur upload TikTok belum bisa dipakai.
    echo Manual install: python -m playwright install chromium
    echo Lanjut tanpa fitur upload...
)

echo.

REM ========== [3/3] Start server + buka browser ke /clone ==========
echo [3/3] Menjalankan server (mode CLONE ACCOUNT)...
echo.
echo ============================================================
echo   Server: http://127.0.0.1:5000/clone
echo   Mode: Bulk download profile + auto upload ke akun kamu
echo   Browser akan terbuka otomatis dalam 3 detik.
echo   Tekan Ctrl+C di window ini untuk STOP server.
echo ============================================================
echo.
echo   Catatan: pastikan kamu sudah Import login dari browser
echo   di halaman utama (http://127.0.0.1:5000) sebelumnya.
echo   Progress resume tersimpan di clone-state/^<username^>.json
echo   (aman di-stop & lanjut tanpa double-upload).
echo.

REM Buka browser ke /clone
start "" /b cmd /c "ping -n 4 127.0.0.1 >nul && start """" http://127.0.0.1:5000/clone"

REM Run server (foreground - blocks sampai user Ctrl+C)
"!PYTHON_EXE!" app.py

echo.
echo Server berhenti.
pause
