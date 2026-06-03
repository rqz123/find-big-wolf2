"""
WhatsApp Linked Device notification module.

Uses Selenium to drive Chrome on WhatsApp Web.
The Chrome user-data directory persists the login session across restarts.

First-time setup:
    From tray menu -> "WhatsApp Setup (scan QR to link device)"
    Chrome opens -> scan QR code with your phone:
    Phone: WhatsApp -> Linked Devices -> Link a Device -> Scan QR

Subsequent sends:
    Session is restored automatically from the saved Chrome profile.

config.yaml -> whatsapp -> recipient:
    Phone number in international format, with or without '+'.
    e.g. '+8613012345678' or '8613012345678'
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from typing import Any, Dict, Optional

log = logging.getLogger("smartcam.whatsapp")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SESSION_DIR = os.path.join(_ROOT, "whatsapp_session")
_LOG_DIR = os.path.join(_ROOT, "logs")
_WA_URL = "https://web.whatsapp.com"

# Selenium page-load and element-wait timeouts (seconds)
_PAGE_LOAD_TIMEOUT = 30
_ELEMENT_WAIT = 30
_SESSION_WAIT = 25


def _kill_stale_chrome() -> None:
    """
    Kill any Chrome processes that are still holding the whatsapp_session
    profile lock from a previous run that didn't clean up properly.
    Uses taskkill (Windows) to terminate matching processes.
    """
    try:
        # Find chrome.exe processes whose command line mentions our session dir
        result = subprocess.run(
            ["wmic", "process", "where",
             f'name="chrome.exe" and CommandLine like "%whatsapp_session%"',
             "get", "ProcessId", "/format:value"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [
            line.split("=")[1].strip()
            for line in result.stdout.splitlines()
            if line.startswith("ProcessId=") and line.split("=")[1].strip()
        ]
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid],
                           capture_output=True, timeout=3)
            log.info(f"Killed stale Chrome process PID={pid}")
        if pids:
            time.sleep(1)   # let the OS release the profile lock
    except Exception as exc:
        log.debug(f"_kill_stale_chrome: {exc}")


class WhatsAppNotifier:
    """
    Send image alerts via WhatsApp Web + Selenium (Linked Device).

    Only one Chrome instance is allowed at a time (_send_lock).
    Before every send, stale Chrome processes holding the profile
    lock are terminated so the new session can load cleanly.
    """

    def __init__(self, cfg: Dict[str, Any]) -> None:
        wa = cfg.get("whatsapp", {})
        raw = str(wa.get("recipient", "")).strip().lstrip("+")
        self._recipient: str = raw
        self._enabled: bool = bool(raw and raw not in ("", "REPLACE_ME"))
        os.makedirs(_SESSION_DIR, exist_ok=True)
        os.makedirs(_LOG_DIR, exist_ok=True)
        self._send_lock = threading.Lock()

        if not self._enabled:
            log.warning(
                "WhatsApp recipient not configured — "
                "alert snapshots will be saved locally only."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Open Chrome with WhatsApp Web and wait for QR scan."""
        _kill_stale_chrome()
        log.info("Opening WhatsApp Web for Linked Device setup ...")
        print("\n" + "=" * 55)
        print("WhatsApp Linked Device Setup")
        print("=" * 55)
        print("Scan the QR code with your phone:")
        print("  Phone -> WhatsApp -> Linked Devices -> Link a Device -> Scan QR")
        print("Browser closes automatically once linked.")
        print("=" * 55 + "\n")

        drv = self._make_driver(headless=False)
        try:
            drv.get(_WA_URL)
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.common.by import By
            log.info("Waiting for QR scan ... (120 s timeout)")
            WebDriverWait(drv, 120).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, '[data-testid="chat-list"]')
                )
            )
            log.info("WhatsApp Linked Device setup complete — session saved.")
            print("\nDevice linked!  Future alerts will use this session.\n")
            time.sleep(2)
        except Exception as exc:
            log.error(f"Setup failed: {exc}")
            print(f"\nSetup failed: {exc}\n")
        finally:
            drv.quit()

    def send_alert(self, image_path: str, caption: str = "Motion detected!") -> None:
        """
        Send an image alert (synchronous; call from a background thread).

        Non-blocking lock: if a send is already in progress the new call is
        dropped immediately (not queued), so only one Chrome instance ever runs
        and rapid motion events don't pile up duplicate sends.
        _kill_stale_chrome runs inside the lock so it never kills a live session.
        """
        if not self._enabled:
            log.info(f"[WhatsApp disabled] snapshot: {image_path}")
            return

        # Non-blocking: skip if another send is already running
        if not self._send_lock.acquire(blocking=False):
            log.info("[WhatsApp] Alert skipped — send already in progress")
            return

        try:
            _kill_stale_chrome()   # safe: runs inside the lock
            log.info(f"[WhatsApp] Sending alert to +{self._recipient} ...")
            drv = self._make_driver(headless=False)
            try:
                drv.set_window_size(900, 700)
                drv.set_window_position(200, 50)
                self._do_send(drv, image_path, caption)
                log.info("[WhatsApp] Alert sent successfully")
            except Exception as exc:
                log.exception(f"[WhatsApp] send_alert failed: {exc}")
            finally:
                try:
                    drv.quit()
                    log.info("[WhatsApp] Browser closed")
                except Exception:
                    pass
        finally:
            self._send_lock.release()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _make_driver(self, headless: bool = False):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        opts = Options()
        opts.add_argument(f"--user-data-dir={_SESSION_DIR}")
        opts.add_argument("--profile-directory=Default")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--no-first-run")
        opts.add_argument("--no-default-browser-check")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        if headless:
            opts.add_argument("--headless=new")
            opts.add_argument("--window-size=1920,1080")

        log.info("[WhatsApp] Starting Chrome ...")
        svc = Service(ChromeDriverManager().install())
        drv = webdriver.Chrome(service=svc, options=opts)
        drv.set_page_load_timeout(_PAGE_LOAD_TIMEOUT)
        drv.execute_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        log.info("[WhatsApp] Chrome started")
        return drv

    def _do_send(self, drv, image_path: str, caption: str) -> None:
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By

        abs_path = os.path.abspath(image_path)

        # ── Step 1: verify session ─────────────────────────────────────
        log.info("[WhatsApp] Step 1: loading main page ...")
        drv.get(_WA_URL)
        log.info(f"[WhatsApp] Page title: {drv.title!r}")
        try:
            WebDriverWait(drv, _SESSION_WAIT).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, '[data-testid="chat-list"]')
                )
            )
            log.info("[WhatsApp] Session active")
        except Exception:
            self._save_screenshot(drv, "wa_debug_session.png")
            log.error(f"[WhatsApp] Session not found. Title={drv.title!r}")
            raise RuntimeError("Session expired — run WhatsApp Setup from tray menu.")

        # ── Step 2: search for recipient and open chat ────────────────
        # WhatsApp Business ignores the send?phone= URL redirect;
        # use the search / new-chat flow instead.
        log.info(f"[WhatsApp] Step 2: searching for +{self._recipient} ...")
        wait = WebDriverWait(drv, _ELEMENT_WAIT)

        # Click "New chat" button
        new_chat_clicked = False
        for sel in [
            '[data-testid="new-chat-btn"]',
            'span[data-icon="new-chat-outline"]',
            '[title="New chat"]',
        ]:
            try:
                btn = WebDriverWait(drv, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                btn.click()
                new_chat_clicked = True
                log.info("[WhatsApp] New-chat button clicked")
                break
            except Exception:
                continue
        if not new_chat_clicked:
            self._save_screenshot(drv, "wa_debug_newchat.png")
            raise RuntimeError("Cannot find New-chat button — see logs/wa_debug_newchat.png")

        time.sleep(0.5)

        # Type number in search box
        search_typed = False
        for sel in [
            '[data-testid="chat-list-search"]',
            'input[placeholder*="Search"]',
            'input[title*="Search"]',
            'div[contenteditable="true"][data-tab="3"]',
        ]:
            try:
                search = WebDriverWait(drv, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                search.click()
                search.send_keys(self._recipient)
                search_typed = True
                log.info(f"[WhatsApp] Typed {self._recipient!r} in search box")
                break
            except Exception:
                continue
        if not search_typed:
            self._save_screenshot(drv, "wa_debug_search.png")
            raise RuntimeError("Cannot find search box — see logs/wa_debug_search.png")

        time.sleep(1.5)

        # Click first contact result
        contact_clicked = False
        for sel in [
            '[data-testid="cell-frame-container"]',
            'div[role="listitem"] div[role="button"]',
            'li[data-testid="mi-attach-image"]',   # fallback
        ]:
            try:
                result = WebDriverWait(drv, 8).until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                result.click()
                contact_clicked = True
                log.info("[WhatsApp] Contact selected from search results")
                break
            except Exception:
                continue
        if not contact_clicked:
            self._save_screenshot(drv, "wa_debug_contact.png")
            raise RuntimeError("Cannot find contact in search results — see logs/wa_debug_contact.png")

        time.sleep(1)
        log.info(f"[WhatsApp] Page title: {drv.title!r}  URL: {drv.current_url}")

        # ── Step 3: wait for compose box ──────────────────────────────
        log.info("[WhatsApp] Step 3: waiting for compose box ...")
        compose_found = False
        for sel in [
            '[data-testid="conversation-compose-box-input"]',
            'div[contenteditable="true"][data-tab="10"]',
            'footer div[contenteditable="true"]',
        ]:
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
                log.info(f"[WhatsApp] Compose box found ({sel!r})")
                compose_found = True
                break
            except Exception:
                continue

        if not compose_found:
            self._save_screenshot(drv, "wa_debug_compose.png")
            log.error(f"[WhatsApp] Compose box not found. Title={drv.title!r}")
            raise RuntimeError("Compose box not found — see logs/wa_debug_compose.png")

        time.sleep(1)

        # ── Step 4: attach image ───────────────────────────────────────
        # Strategy A: inject file directly into hidden input via JS
        #   (works without clicking the attach button — avoids Business/Personal selector differences)
        # Strategy B: click attach button first, then find input (fallback)
        log.info("[WhatsApp] Step 4: attaching image ...")

        def _inject_file(path: str) -> bool:
            """Return True if file was successfully sent to a file input."""
            # Make all file inputs visible so Selenium can interact with them
            drv.execute_script("""
                document.querySelectorAll('input[type="file"]').forEach(function(el){
                    el.style.display = 'block';
                    el.style.visibility = 'visible';
                    el.style.opacity = '1';
                    el.style.width = '1px';
                    el.style.height = '1px';
                });
            """)
            inputs = drv.find_elements(By.CSS_SELECTOR, "input[type='file']")
            log.info(f"[WhatsApp] Found {len(inputs)} file input(s) in DOM")
            for inp in inputs:
                try:
                    inp.send_keys(path)
                    log.info("[WhatsApp] Image injected via direct file input")
                    return True
                except Exception:
                    continue
            return False

        attached = _inject_file(abs_path)

        if not attached:
            # Fallback: click the attach button to reveal the file input
            log.info("[WhatsApp] Direct inject failed — trying attach button ...")
            for sel in [
                '[data-testid="attach-btn"]',
                'span[data-icon="attach-menu-plus"]',
                'span[data-icon="clip"]',
                'div[title="Attach"]',
                'button[aria-label="Attach"]',
            ]:
                try:
                    btn = WebDriverWait(drv, 3).until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                    btn.click()
                    log.info(f"[WhatsApp] Attach button clicked ({sel!r})")
                    time.sleep(0.5)
                    attached = _inject_file(abs_path)
                    if attached:
                        break
                except Exception:
                    continue

        if not attached:
            self._save_screenshot(drv, "wa_debug_attach.png")
            raise RuntimeError("Cannot attach image — see logs/wa_debug_attach.png")

        time.sleep(2)

        # ── Step 5: caption (optional, best-effort) ────────────────────
        if caption:
            time.sleep(1)
            for sel in [
                '[data-testid="media-caption-input-container"] [contenteditable="true"]',
                'div[data-testid="photo-caption-input-container"] [contenteditable="true"]',
                'div[contenteditable="true"][data-tab="11"]',
                'div[contenteditable="true"][class*="caption"]',
            ]:
                try:
                    cap_el = WebDriverWait(drv, 3).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                    )
                    cap_el.click()
                    cap_el.send_keys(caption)
                    log.info("[WhatsApp] Caption typed")
                    break
                except Exception:
                    continue

        time.sleep(0.5)

        # ── Step 6: send ──────────────────────────────────────────────
        # The media-preview send button lives in a different React subtree
        # and is unreachable via normal CSS selectors.
        # Pressing Enter is the most reliable way to confirm send in WA Web.
        log.info("[WhatsApp] Step 6: sending via Enter key ...")
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys

        time.sleep(1)   # ensure preview is fully rendered

        sent = False

        # Method 1: ActionChains Enter on document body
        try:
            ActionChains(drv).send_keys(Keys.RETURN).perform()
            log.info("[WhatsApp] Enter key sent via ActionChains")
            sent = True
        except Exception as exc:
            log.debug(f"[WhatsApp] ActionChains Enter failed: {exc}")

        # Method 2: JavaScript click on send button (handles shadow DOM)
        if not sent:
            result = drv.execute_script("""
                var selectors = [
                    '[data-testid="send-btn"]',
                    'span[data-icon="send"]',
                    'div[role="button"][aria-label="Send"]',
                    'button[aria-label="Send"]',
                ];
                for (var sel of selectors) {
                    var el = document.querySelector(sel);
                    if (el) {
                        var btn = el.closest('[role="button"]') || el;
                        btn.click();
                        return 'clicked: ' + sel;
                    }
                }
                // log all buttons for debugging
                var btns = Array.from(document.querySelectorAll('[role="button"],[data-testid]'))
                    .map(b => (b.getAttribute('data-testid') || b.getAttribute('aria-label') || '').trim())
                    .filter(Boolean);
                return 'not found. visible btns: ' + btns.join(' | ');
            """)
            log.info(f"[WhatsApp] JS send result: {result}")
            if result and result.startswith("clicked"):
                sent = True

        if not sent:
            self._save_screenshot(drv, "wa_debug_send.png")
            log.error("[WhatsApp] All send methods failed — see logs/wa_debug_send.png")
            # Don't raise — screenshot saved, continue gracefully
        else:
            time.sleep(3)   # wait for delivery before closing

    def _save_screenshot(self, drv, filename: str) -> None:
        path = os.path.join(_LOG_DIR, filename)
        try:
            drv.save_screenshot(path)
            log.info(f"[WhatsApp] Screenshot -> {path}")
        except Exception as exc:
            log.warning(f"[WhatsApp] Screenshot failed: {exc}")
