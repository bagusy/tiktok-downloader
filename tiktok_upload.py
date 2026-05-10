"""Otomasi upload video ke TikTok via Playwright.

Strategi:
- Pakai persistent browser context (`playwright-profile/` di project dir) supaya
  cookies + login disimpan dan re-used antar session.
- Login pertama kali: user login manual di window browser yang dibuka,
  dideteksi otomatis ketika URL keluar dari /login.
- Upload: navigate ke tiktokstudio/upload, set file via input[type=file] tersembunyi,
  isi caption di contenteditable, klik Post, tunggu konfirmasi sukses.

Selectors TikTok kadang berubah; kalau upload mendadak gagal, fallback selector
di `_locate_*` bisa di-update tanpa ngubah API publik.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Iterator

from playwright.sync_api import (
    BrowserContext,
    Error as PWError,
    Page,
    TimeoutError as PWTimeout,
    sync_playwright,
)


PROJECT_DIR = Path(__file__).parent
PROFILE_DIR = PROJECT_DIR / "playwright-profile"
UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=creator_center&tab=video"
LOGIN_URL = "https://www.tiktok.com/login"
HOME_URL = "https://www.tiktok.com/"

# UA realistis untuk Chromium 130-an di Windows 11. Diset eksplisit supaya tidak default
# ke "HeadlessChrome" ketika headless=True (mudah dideteksi TikTok).
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

StatusFn = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


def _ensure_profile_dir() -> Path:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILE_DIR


def _launch_context(playwright, headless: bool) -> BrowserContext:
    """Bikin persistent context dengan setting anti-detection ringan."""
    profile = _ensure_profile_dir()
    ctx = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=headless,
        viewport={"width": 1280, "height": 820},
        user_agent=DEFAULT_UA,
        locale="en-US",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )
    # Hapus navigator.webdriver flag sebelum page script jalan
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return ctx


def _is_login_page(url: str) -> bool:
    u = (url or "").lower()
    return "/login" in u or "tiktok.com/signup" in u


def check_login_status(timeout_s: int = 20) -> bool:
    """Cek cepat (headless) apakah profile sudah login.

    Navigate ke upload page; kalau tidak di-redirect ke /login dan input[type=file]
    ada → logged in.
    """
    with sync_playwright() as p:
        ctx = _launch_context(p, headless=True)
        try:
            page = ctx.new_page()
            try:
                page.goto(UPLOAD_URL, timeout=timeout_s * 1000, wait_until="domcontentloaded")
            except PWTimeout:
                pass
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PWTimeout:
                pass
            if _is_login_page(page.url):
                return False
            try:
                page.wait_for_selector('input[type="file"]', timeout=5000, state="attached")
                return True
            except PWTimeout:
                return False
        finally:
            try:
                ctx.close()
            except Exception:
                pass


# Map nama browser (yt-dlp keys) → executable name. Diurutkan by reliability untuk
# extract cookies di Windows: Firefox tidak pakai DPAPI/App-Bound encryption, jadi
# paling konsisten. Chromium-based 127+ sering gagal di-decrypt. Order ini juga jadi
# priority untuk auto_login() saat memilih kandidat cookies.
_BROWSER_PROCESS_MAP: list[tuple[str, str]] = [
    ("firefox", "firefox.exe"),
    ("brave", "brave.exe"),
    ("edge", "msedge.exe"),
    ("chrome", "chrome.exe"),
    ("opera", "opera.exe"),
    ("vivaldi", "vivaldi.exe"),
    ("chromium", "chromium.exe"),
]


def detect_running_browsers() -> list[str]:
    """Deteksi browser yang sedang running. Windows-only (pakai `tasklist`).

    Return list nama browser (yt-dlp keys) urut by extract-cookies reliability.
    Empty list pada platform non-Windows atau bila tasklist gagal — caller
    bisa fallback ke "coba semua".
    """
    if sys.platform != "win32":
        return []
    try:
        kwargs: dict = {"capture_output": True, "text": True, "timeout": 8}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], **kwargs)
    except (subprocess.SubprocessError, OSError):
        return []
    if result.returncode != 0:
        return []
    haystack = result.stdout.lower()
    return [name for name, exe in _BROWSER_PROCESS_MAP if exe in haystack]


def _extract_tt_cookies(browser_name: str) -> tuple[list[dict], str | None]:
    """Extract cookies tiktok.com dari browser, return (pw_cookies, error_msg).

    pw_cookies sudah dalam shape `BrowserContext.add_cookies()` Playwright.
    """
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except ImportError:
        return [], "yt-dlp tidak terinstall"

    try:
        cj = extract_cookies_from_browser(browser_name)
    except Exception as e:
        return [], f"gagal baca cookies: {str(e)[:120]}"

    tt_cookies = [c for c in cj if "tiktok.com" in (c.domain or "")]
    if not tt_cookies:
        return [], "tidak ada cookies tiktok.com"

    pw_cookies: list[dict] = []
    for c in tt_cookies:
        try:
            http_only = bool(c.has_nonstandard_attr("HttpOnly")) if hasattr(c, "has_nonstandard_attr") else False
        except Exception:
            http_only = False
        pw_cookies.append({
            "name": c.name,
            "value": c.value or "",
            "domain": c.domain,
            "path": c.path or "/",
            "expires": int(c.expires) if c.expires else -1,
            "secure": bool(c.secure),
            "httpOnly": http_only,
            "sameSite": "Lax",
        })
    return pw_cookies, None


def auto_login(on_status: StatusFn = _noop) -> tuple[bool, str | None, str | None]:
    """Auto-login pakai cookies dari browser yang sedang running.

    Flow:
    1. Deteksi browser running (Windows tasklist) — fallback "coba semua" kalau
       deteksi tidak available.
    2. Extract cookies tiktok.com dari tiap kandidat. Skip yang gagal/kosong.
    3. Single Playwright headless session: untuk tiap kandidat → clear cookies →
       inject → navigate ke upload page → cek bukan /login + upload form muncul.
       Browser pertama yang sukses verify menang; cookies-nya nempel di profile
       (persistent context auto-save).

    Return (ok, browser_used, error_msg).
    """
    detected = detect_running_browsers()
    if detected:
        on_status(f"Browser running terdeteksi: {', '.join(detected)}")
        candidates = detected
    else:
        on_status("Tidak ada browser running terdeteksi. Mencoba semua browser terinstall...")
        candidates = [name for name, _exe in _BROWSER_PROCESS_MAP]

    extracted: list[tuple[str, list[dict]]] = []
    for br in candidates:
        pw_cookies, err = _extract_tt_cookies(br)
        if pw_cookies:
            on_status(f"  [{br}] {len(pw_cookies)} cookies TikTok ditemukan")
            extracted.append((br, pw_cookies))
        else:
            on_status(f"  [{br}] {err}")

    if not extracted:
        return False, None, (
            "Tidak ada browser yang punya cookies TikTok valid. "
            "Login dulu di salah satu browser (Firefox paling reliable di Windows)."
        )

    with sync_playwright() as p:
        ctx = _launch_context(p, headless=True)
        try:
            page = ctx.new_page()
            for br, pw_cookies in extracted:
                on_status(f"Memverifikasi cookies dari {br}...")
                try:
                    ctx.clear_cookies()
                except Exception:
                    pass
                try:
                    ctx.add_cookies(pw_cookies)
                except PWError as e:
                    on_status(f"  [{br}] gagal inject cookies: {e}")
                    continue
                try:
                    page.goto(UPLOAD_URL, timeout=30_000, wait_until="domcontentloaded")
                except PWTimeout:
                    pass
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except PWTimeout:
                    pass
                if _is_login_page(page.url):
                    on_status(f"  [{br}] cookies tidak valid (TikTok minta login)")
                    continue
                try:
                    page.wait_for_selector('input[type="file"]', timeout=5000, state="attached")
                except PWTimeout:
                    on_status(f"  [{br}] upload form tidak muncul")
                    continue
                on_status(f"Login sukses pakai cookies dari {br}.")
                return True, br, None
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    return False, None, (
        "Cookies ke-extract dari browser tapi tidak valid (mungkin expired/logout). "
        "Login ulang TikTok di browser, lalu refresh."
    )


def login_from_browser(browser_name: str, on_status: StatusFn = _noop) -> tuple[bool, str | None]:
    """Extract cookies TikTok dari browser user (chrome/edge/firefox/brave/dst.) lalu
    inject ke profile Playwright. Pakai yt-dlp's cookie extractor (handle Chrome 127+
    app-bound encryption lebih baik dibanding browser_cookie3).

    Return (ok, error_msg). Browser tidak harus ditutup untuk Firefox; Chrome/Edge
    sebaiknya ditutup karena cookie file kadang lock.
    """
    on_status(f"Membaca cookies TikTok dari {browser_name}...")
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except ImportError:
        return False, "yt-dlp tidak terinstall (dibutuhkan untuk extract cookies)."

    try:
        cj = extract_cookies_from_browser(browser_name)
    except Exception as e:
        return False, f"Gagal baca cookies dari {browser_name}: {e}"

    tt_cookies = [c for c in cj if "tiktok.com" in (c.domain or "")]
    if not tt_cookies:
        return False, (
            f"Tidak ada cookies tiktok.com di {browser_name}. "
            "Pastikan kamu sudah login TikTok di browser tersebut, lalu coba lagi."
        )

    on_status(f"Ditemukan {len(tt_cookies)} cookies. Inject ke profile Playwright...")

    pw_cookies = []
    for c in tt_cookies:
        try:
            http_only = bool(c.has_nonstandard_attr("HttpOnly")) if hasattr(c, "has_nonstandard_attr") else False
        except Exception:
            http_only = False
        pw_cookies.append({
            "name": c.name,
            "value": c.value or "",
            "domain": c.domain,
            "path": c.path or "/",
            "expires": int(c.expires) if c.expires else -1,
            "secure": bool(c.secure),
            "httpOnly": http_only,
            "sameSite": "Lax",
        })

    with sync_playwright() as p:
        ctx = _launch_context(p, headless=True)
        try:
            try:
                ctx.add_cookies(pw_cookies)
            except PWError as e:
                return False, f"Gagal inject cookies ke Playwright: {e}"

            on_status("Memverifikasi login dengan navigate ke TikTok Studio...")
            page = ctx.new_page()
            try:
                page.goto(UPLOAD_URL, timeout=30_000, wait_until="domcontentloaded")
            except PWTimeout:
                pass
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PWTimeout:
                pass

            if _is_login_page(page.url):
                return False, (
                    "Cookies ke-import tapi TikTok masih minta login. "
                    "Coba login ulang di browser tersebut, lalu retry."
                )
            on_status("Login berhasil di-import.")
            return True, None
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def open_login_window(timeout_s: int = 300, on_status: StatusFn = _noop) -> bool:
    """Buka browser headed untuk user login manual.

    Block sampai user keluar dari /login (artinya login sukses) atau timeout.
    Profile auto-disimpan oleh persistent context.

    Return True kalau login sukses terdeteksi, False kalau timeout.
    """
    with sync_playwright() as p:
        ctx = _launch_context(p, headless=False)
        try:
            page = ctx.new_page()
            on_status("Membuka window TikTok login. Silakan login di browser yang baru terbuka...")
            try:
                page.goto(LOGIN_URL, timeout=60_000)
            except PWTimeout:
                on_status("Halaman login lambat respons, tetap dilanjutkan...")

            deadline = time.time() + timeout_s
            last_seen_login = True
            while time.time() < deadline:
                try:
                    cur = page.url
                except PWError:
                    on_status("Window browser ditutup user.")
                    return False

                on_login = _is_login_page(cur)
                if not on_login:
                    if last_seen_login:
                        on_status(f"Login terdeteksi ({cur}). Menunggu cookies tersimpan...")
                    last_seen_login = False
                    # Beri waktu beberapa detik supaya cookies & state stabil
                    time.sleep(3)
                    on_status("Login sukses. Profile tersimpan.")
                    return True

                try:
                    page.wait_for_timeout(1500)
                except PWError:
                    on_status("Window browser ditutup user.")
                    return False

            on_status(f"Timeout {timeout_s}s — login tidak terdeteksi. Coba lagi.")
            return False
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def _locate_caption_editor(page: Page):
    """Cari editor caption di upload page. Coba beberapa selector."""
    candidates = [
        'div[contenteditable="true"][role="combobox"]',
        'div[contenteditable="true"]',
        'div[data-text="true"]',
    ]
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=3000)
            return loc
        except PWTimeout:
            continue
    return page.locator('div[contenteditable="true"]').first


def _locate_post_button(page: Page):
    """Tombol Post biasanya di bawah form, text-nya 'Post'."""
    # Prioritas: button dengan text persis 'Post', lalu 'Posting', lalu data-e2e
    candidates = [
        'button[data-e2e="post_video_button"]',
        'button:has-text("Post"):not(:has-text("Postpone"))',
        'div[data-e2e="post_video_button"] button',
    ]
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=3000)
            return loc
        except PWTimeout:
            continue
    return page.locator('button:has-text("Post")').first


def _wait_post_button_enabled(page: Page, btn, timeout_s: int = 300) -> bool:
    """Tunggu sampai tombol Post bisa diklik (artinya upload selesai diproses)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if btn.is_enabled() and btn.is_visible():
                # Double-check disabled attr (kadang is_enabled() menipu untuk styled button)
                disabled = btn.get_attribute("disabled")
                aria_disabled = btn.get_attribute("aria-disabled")
                if not disabled and aria_disabled != "true":
                    return True
        except PWError:
            pass
        page.wait_for_timeout(2000)
    return False


def _focus_caption_editor(page: Page, editor) -> None:
    """Coba focus editor caption pakai 3 strategi (Draft.js editor TikTok kadang
    bandel ke .click() biasa karena ada overlay/animasi). Tidak raise — best effort."""
    # Strategi 1: focus() langsung — tidak ada actionability check
    try:
        editor.focus(timeout=5000)
        return
    except (PWError, PWTimeout):
        pass
    # Strategi 2: click dengan force=True — bypass overlay check
    try:
        editor.click(force=True, timeout=5000)
        return
    except (PWError, PWTimeout):
        pass
    # Strategi 3: dispatch focus via JS
    try:
        editor.evaluate("el => el.focus()")
    except Exception:
        pass


def _confirm_post_dialog_if_present(page: Page) -> bool:
    """Setelah klik Post, TikTok kadang munculkan dialog konfirmasi.
    Coba cari tombol confirm/post/yes di dalam dialog dan klik.
    Return True kalau ada dialog yang ke-confirm."""
    dialog_button_texts = ["Post anyway", "Confirm", "Continue", "Post", "Yes"]
    for txt in dialog_button_texts:
        try:
            # Cari button di dalam dialog/modal yang baru muncul
            loc = page.locator(
                f'div[role="dialog"] button:has-text("{txt}"), '
                f'div[class*="modal"] button:has-text("{txt}")'
            ).first
            if loc.is_visible(timeout=500):
                try:
                    loc.click(timeout=3000)
                    page.wait_for_timeout(1000)
                    return True
                except (PWError, PWTimeout):
                    try:
                        loc.click(force=True, timeout=3000)
                        page.wait_for_timeout(1000)
                        return True
                    except (PWError, PWTimeout):
                        continue
        except (PWError, PWTimeout):
            continue
    return False


def _read_error_toast(page: Page) -> str:
    """Baca error toast/notification kalau ada. Return text-nya, atau '' kalau tidak ada."""
    selectors = [
        '[role="alert"]',
        'div[class*="toast"][class*="error"]',
        'div[class*="Toast"][class*="error"]',
        'div[class*="notice-error"]',
        'div[data-e2e*="error"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=300):
                txt = (loc.text_content() or "").strip()
                if txt:
                    return txt[:300]
        except (PWError, PWTimeout):
            continue
    return ""


def _robust_click(page: Page, locator) -> str:
    """Click yang anti-overlay: scroll-into-view → hover → normal click → force click → JS click.
    Return nama strategi yang berhasil. Raise RuntimeError kalau semua strategi gagal."""
    last_err: Exception | None = None
    try:
        locator.scroll_into_view_if_needed(timeout=3000)
    except (PWError, PWTimeout):
        pass
    # Hover dulu — banyak React app reset internal state saat hover, plus simulate user lebih natural
    try:
        locator.hover(timeout=3000)
        page.wait_for_timeout(250)
    except (PWError, PWTimeout):
        pass

    # Strategi 1: click normal — produces trusted event via CDP
    try:
        locator.click(timeout=5000)
        return "normal"
    except (PWError, PWTimeout) as e:
        last_err = e

    # Strategi 2: force click — bypass actionability tapi tetap pakai CDP (trusted)
    try:
        locator.click(force=True, timeout=5000)
        return "force"
    except (PWError, PWTimeout) as e:
        last_err = e

    # Strategi 3: dispatch_event — bukan trusted, tapi handled via Playwright (lebih reliable dari JS .click())
    try:
        locator.dispatch_event("click")
        return "dispatch_event"
    except Exception as e:
        last_err = e

    # Strategi 4 (last resort): JS .click() — untrusted, kemungkinan diabaikan React
    try:
        locator.evaluate("el => el.click()")
        return "js"
    except Exception as e:
        last_err = e

    raise RuntimeError(f"Semua strategi click gagal: {last_err}")


def _dismiss_suggestion_popup(page: Page) -> None:
    """Tutup popup saran hashtag/mention kalau lagi terbuka.

    TikTok pop-up saran hashtag akan overlay area Post button — bikin click ke Post
    landing di dropdown, bukan di tombol-nya. Wajib dismiss sebelum click Post.
    """
    # Press Escape — cara paling andal untuk close popup di Draft.js editor
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    except (PWError, PWTimeout):
        pass
    # Klik area aman (heading "Details") untuk blur editor + close popup yang masih bandel
    safe_targets = ['h2:has-text("Details")', 'h1', 'header', 'body']
    for sel in safe_targets:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=500):
                loc.click(force=True, position={"x": 5, "y": 5}, timeout=2000)
                page.wait_for_timeout(200)
                return
        except (PWError, PWTimeout):
            continue


def _fill_caption(page: Page, caption: str) -> None:
    if not caption:
        return
    editor = _locate_caption_editor(page)
    _focus_caption_editor(page, editor)
    page.wait_for_timeout(300)
    # Bersihkan caption default (TikTok kadang isi otomatis pakai filename)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.wait_for_timeout(150)
    # Type pelan supaya hashtag/mention dropdown sempat ter-trigger natural
    page.keyboard.type(caption, delay=15)
    page.wait_for_timeout(500)
    # Tutup popup saran hashtag/mention yang muncul saat ngetik #/@
    _dismiss_suggestion_popup(page)


def _detect_captcha(page: Page) -> bool:
    selectors = [
        'div[id*="captcha"]',
        'iframe[src*="captcha"]',
        'div[class*="captcha"]',
    ]
    for sel in selectors:
        try:
            if page.locator(sel).first.is_visible(timeout=500):
                return True
        except (PWTimeout, PWError):
            continue
    return False


def _upload_in_page_iter(
    page: Page,
    video_path: Path,
    caption: str,
) -> Iterator[dict]:
    """Generator: lakukan upload satu video, yield status events.

    Yield {"event": "status", "msg": str} pada tiap langkah.
    Raise RuntimeError/PWError on failure (caller harus catch).
    """
    yield {"event": "status", "msg": f"Buka halaman upload untuk {video_path.name}"}
    page.goto(UPLOAD_URL, timeout=60_000, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except PWTimeout:
        pass

    if _is_login_page(page.url):
        raise RuntimeError("Tidak login di TikTok. Klik 'Login TikTok' dulu.")

    if _detect_captcha(page):
        raise RuntimeError("Captcha terdeteksi. Selesaikan manual di window browser.")

    yield {"event": "status", "msg": "Memilih file video..."}
    file_input = page.locator('input[type="file"]').first
    file_input.wait_for(state="attached", timeout=30_000)
    file_input.set_input_files(str(video_path))

    yield {"event": "status", "msg": "Menunggu TikTok memproses video (bisa 30-90 detik)..."}
    deadline = time.time() + 180
    editor_ready = False
    while time.time() < deadline:
        try:
            ed = _locate_caption_editor(page)
            ed.wait_for(state="visible", timeout=3000)
            editor_ready = True
            break
        except PWTimeout:
            continue
    if not editor_ready:
        raise RuntimeError("Form upload tidak muncul setelah 3 menit.")

    if caption:
        snippet = caption[:60] + ("..." if len(caption) > 60 else "")
        yield {"event": "status", "msg": f'Mengisi caption: "{snippet}"'}
        _fill_caption(page, caption)

    yield {"event": "status", "msg": "Menunggu video selesai di-upload (Post button enabled)..."}
    btn = _locate_post_button(page)
    if not _wait_post_button_enabled(page, btn, timeout_s=600):
        raise RuntimeError("Timeout 10 menit: Post button tidak enabled (upload mungkin gagal).")

    url_before_post = page.url

    # Wajib dismiss popup saran hashtag yang mungkin masih kebuka — kalau tidak,
    # popup akan intercept click ke Post button.
    _dismiss_suggestion_popup(page)

    # Diagnostic: hitung berapa Post button match, ambil yang VISIBLE + enabled
    try:
        all_btns = page.locator('button[data-e2e="post_video_button"]')
        count = all_btns.count()
        yield {"event": "status", "msg": f"Diagnostic: ditemukan {count} kandidat Post button"}
        if count > 1:
            # Pilih yang visible+enabled, bukan yang first
            for idx in range(count):
                cand = all_btns.nth(idx)
                try:
                    if cand.is_visible(timeout=300) and cand.is_enabled(timeout=300):
                        btn = cand
                        yield {"event": "status", "msg": f"Pilih kandidat #{idx+1} (visible+enabled)"}
                        break
                except (PWError, PWTimeout):
                    continue
    except (PWError, PWTimeout):
        pass

    # Screenshot before click untuk diagnosis
    debug_dir = PROJECT_DIR / "playwright-debug"
    debug_dir.mkdir(exist_ok=True)
    ts = int(time.time())
    try:
        page.screenshot(path=str(debug_dir / f"post_before_{ts}.png"), full_page=False)
    except Exception:
        pass

    yield {"event": "status", "msg": "Klik tombol Post..."}
    try:
        strategy = _robust_click(page, btn)
        yield {"event": "status", "msg": f"Click berhasil pakai strategi: {strategy}"}
        if strategy in ("dispatch_event", "js"):
            yield {"event": "status", "msg": "PERINGATAN: click pakai event programmatic — TikTok mungkin abaikan (anti-bot)."}
    except (RuntimeError, PWError) as e:
        raise RuntimeError(f"Gagal klik Post: {e}")

    # Setelah klik, kadang muncul dialog konfirmasi ("Are you sure...?" atau "Post anyway")
    # — coba klik tombol konfirm-nya kalau ada (best effort, jangan blok lama)
    page.wait_for_timeout(1500)
    _confirm_post_dialog_if_present(page)

    yield {"event": "status", "msg": "Menunggu konfirmasi sukses (URL berubah ke /content)..."}
    success = False
    deadline = time.time() + 180  # 3 menit max
    while time.time() < deadline:
        cur = page.url.lower()
        # Kriteria sukses STRICT: URL pindah ke halaman content / manage
        if "tiktokstudio/content" in cur or "tiktokstudio/post" in cur:
            success = True
            break
        # Kalau URL keluar dari upload page tapi bukan content (misal foryou, profile),
        # juga dianggap sukses
        if "tiktokstudio/upload" not in cur and "/upload" not in cur:
            success = True
            break
        # Cek error toast kalau masih di upload page
        err_msg = _read_error_toast(page)
        if err_msg:
            raise RuntimeError(f"TikTok error: {err_msg}")
        page.wait_for_timeout(2500)

    if not success:
        # Upaya terakhir: cek error toast sekali lagi
        err_msg = _read_error_toast(page)
        if err_msg:
            raise RuntimeError(f"TikTok error: {err_msg}")

        # Screenshot final state untuk diagnosis
        try:
            page.screenshot(path=str(debug_dir / f"post_after_{ts}.png"), full_page=True)
        except Exception:
            pass

        raise RuntimeError(
            f"Klik Post tidak menghasilkan navigasi dalam 3 menit. URL masih: {page.url}. "
            f"Screenshot disimpan di playwright-debug/post_*_{ts}.png — buka untuk lihat "
            "apakah ada dialog konfirmasi atau error toast yang tidak terdeteksi."
        )


def upload_videos(
    items: Iterable[tuple[Path, str]],
    headless: bool = False,
    inter_delay_s: float = 4.0,
) -> Iterator[dict]:
    """Generator: upload list (path, caption). Yield dict event tiap update.

    Events:
      {"event": "status", "msg": str}
      {"event": "progress", "current": int, "total": int, "filename": str}
      {"event": "ok", "filename": str}
      {"event": "error", "filename": str, "reason": str}
      {"event": "done", "success": int, "failed": int}
      {"event": "fatal", "error": str}  -- error yang stop seluruh batch
    """
    items = list(items)
    total = len(items)
    if total == 0:
        yield {"event": "done", "success": 0, "failed": 0}
        return

    success = 0
    failed = 0

    try:
        with sync_playwright() as p:
            ctx = _launch_context(p, headless=headless)
            try:
                # Re-use 1 page untuk seluruh batch supaya tidak boros
                page = ctx.new_page()

                # Quick sanity check: navigate dulu ke home buat cek login
                yield {"event": "status", "msg": "Mengecek status login TikTok..."}
                try:
                    page.goto(HOME_URL, timeout=30_000, wait_until="domcontentloaded")
                except PWTimeout:
                    pass

                for i, (path, caption) in enumerate(items, start=1):
                    yield {
                        "event": "progress",
                        "current": i,
                        "total": total,
                        "filename": path.name,
                    }
                    if not path.exists():
                        failed += 1
                        yield {"event": "error", "filename": path.name, "reason": "File tidak ditemukan"}
                        continue

                    try:
                        for evt in _upload_in_page_iter(page, path, caption):
                            yield evt
                        success += 1
                        yield {"event": "ok", "filename": path.name}
                    except (RuntimeError, PWError, PWTimeout) as e:
                        failed += 1
                        yield {
                            "event": "error",
                            "filename": path.name,
                            "reason": str(e)[:300],
                        }

                    # Jeda antar upload supaya tidak rate-limit
                    if i < total:
                        time.sleep(inter_delay_s)

                yield {"event": "done", "success": success, "failed": failed}
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    except PWError as e:
        yield {"event": "fatal", "error": f"Playwright error: {e}"}
    except Exception as e:
        yield {"event": "fatal", "error": f"Error tak terduga: {e}"}
