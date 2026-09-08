"""Visual eye: Playwright driving Telegram Web (web.telegram.org/k/).

Public API:
    class TelegramWeb — async, persistent chromium context at settings.web_profile
        start() / stop()
        is_logged_in() -> bool
        login_qr_screenshot(path) -> str
        wait_for_login(timeout_s) -> bool
        open_chat(bot_username) -> None
        screenshot(path, region="chat"|"last_messages"|"full", last_n=5) -> Screenshot
        scroll_to_bottom() -> None
        click_inline_button(text) -> bool
        visible_text() -> str
    class NullWeb — same interface, every method raises RuntimeError("visual eye disabled")

Selectors for the WebK client change over releases; several candidates per
role are tried in order (see SELECTORS below). The exact URL scheme verified
against the WebK source/docs: `https://web.telegram.org/k/#@BotFather` opens
a direct chat with that username. See docs/dev/agent-reports/visual.md for which
selectors are guesses that still need checking against a live login.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal, Optional

from PIL import Image

try:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        TimeoutError as PlaywrightTimeoutError,
        async_playwright,
    )
except ImportError:  # pragma: no cover - playwright always installed per env
    Browser = BrowserContext = Page = object  # type: ignore
    PlaywrightTimeoutError = Exception  # type: ignore
    async_playwright = None  # type: ignore

from graea.config import Settings
from graea.models import Screenshot

Region = Literal["chat", "last_messages", "full"]

# --------------------------------------------------------------------------
# Selectors. Telegram Web K (2026 redesign) uses CSS-module hashed class names
# (e.g. `_pageSignQR_1b0yp_208`), so class-substring patterns and the few
# stable ids are the anchors; never bare `.bubble`-style names. Verified
# against a live login screen on 2026-09-07:
#   body.has-auth-pages                 -> logged OUT
#   #auth-pages (visible)               -> logged OUT (QR / phone pages)
#   [class*="qrContainer"]              -> the QR code itself
#   #column-left / #chatlist-container  -> present always; VISIBLE (w>0) only when logged in
#   #column-center                      -> chat column (visible only when a chat is open)
# Post-login bubble/keyboard selectors are substring patterns over the
# hashed names plus WebK's `data-mid` attribute on message elements. Every
# role can be overridden without code changes: GRAEA_SELECTORS_FILE points at
# a JSON {role: [selectors...]} whose entries are tried FIRST. `graea web-probe`
# dumps what the live DOM actually contains so a field patch is quick.
# --------------------------------------------------------------------------

SELECTORS: dict[str, list[str]] = {
    # Present (and visible) only while logged out.
    "auth_page": [
        "#auth-pages",
        "[class*='pageSign']",
        "[class*='authPage']",
    ],
    # The QR code element on the login screen.
    "login_qr": [
        "[class*='qrContainer']",
        "[class*='pageSignQR'] canvas",
        "#auth-pages canvas",
        "[class*='pageSignQR']",
    ],
    # A positive logged-in signal: real chat rows in the left column.
    "chat_list": [
        "#chatlist-container [data-peer-id]",
        "#column-left [data-peer-id]",
        "#chatlist-container ul li",
        "#column-left [class*='chatlist'] li",
        "[class*='chatlist-chat']",
    ],
    # The column that holds the open chat's messages.
    "chat_column": [
        "#column-center [class*='bubbles']",
        "#column-center [class*='messages']",
        "#column-center .scrollable",
        "#column-center",
    ],
    # Individual message elements within the chat column.
    "message_bubble": [
        "#column-center [data-mid]",
        "#column-center [class*='_bubble_']",
        "#column-center [class*='bubble']",
        "#column-center [class*='_message_']",
        "#column-center [class*='Message']",
        "[data-mid]",
    ],
    # Inline keyboard buttons attached to a message.
    "inline_button": [
        "#column-center [class*='reply-markup'] button",
        "#column-center [class*='replyMarkup'] button",
        "#column-center [class*='reply-markup-button']",
        "#column-center [class*='keyboard'] button",
        "#column-center [class*='inline'] button",
    ],
}


def _merge_selector_overrides(settings: "Settings") -> dict[str, list[str]]:
    """SELECTORS with any GRAEA_SELECTORS_FILE entries prepended per role."""
    merged = {k: list(v) for k, v in SELECTORS.items()}
    path = getattr(settings, "selectors_file", None)
    if not path:
        return merged
    try:
        import json

        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for role, sels in data.items():
            if isinstance(sels, str):
                sels = [sels]
            merged[role] = [str(x) for x in sels] + merged.get(role, [])
    except Exception:
        pass
    return merged


def _username_for_url(bot_username: str) -> str:
    return bot_username[1:] if bot_username.startswith("@") else bot_username


class TelegramWeb:
    """Playwright chromium, persistent context at settings.web_profile."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.selectors = _merge_selector_overrides(settings)
        self.last_qr_rendered: Optional[bool] = None
        self._pw = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Launch (or attach to) the persistent chromium profile and open a page."""
        if async_playwright is None:
            raise RuntimeError("playwright is not installed")
        self._pw = await async_playwright().start()
        self.settings.web_profile.mkdir(parents=True, exist_ok=True)
        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.settings.web_profile),
            headless=self.settings.web_headless,
            viewport={
                "width": self.settings.web_viewport_width,
                "height": self.settings.web_viewport_height,
            },
        )
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

    async def stop(self) -> None:
        """Close the browser context and stop the Playwright driver."""
        try:
            if self._context is not None:
                await self._context.close()
        finally:
            self._context = None
            self._page = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("TelegramWeb not started; call start() first")
        return self._page

    async def _first_visible(self, role: str, timeout_ms: int = 1500, state: str = "visible"):
        """Try each candidate selector for `role`; return the first Locator
        with >=1 element in `state` ("visible" by default — an attached but
        hidden element is useless for screenshots) within timeout_ms, else None."""
        page = self._require_page()
        for sel in self.selectors.get(role, []):
            try:
                locator = page.locator(sel).first
                await locator.wait_for(state=state, timeout=timeout_ms)
                return locator
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        return None

    async def _is_visible(self, selector: str) -> bool:
        """True if the first match of `selector` exists and has a non-empty box."""
        page = self._require_page()
        try:
            return bool(await page.evaluate(
                """(sel) => { const e = document.querySelector(sel); if (!e) return false;
                       const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }""",
                selector,
            ))
        except Exception:
            return False

    async def _auth_page_showing(self) -> bool:
        """The redesigned WebK marks a logged-out client with body.has-auth-pages
        and a visible #auth-pages container; both are checked."""
        page = self._require_page()
        try:
            has_class = await page.evaluate("() => document.body.classList.contains('has-auth-pages')")
        except Exception:
            has_class = False
        if has_class:
            return True
        for sel in self.selectors.get("auth_page", []):
            if await self._is_visible(sel):
                return True
        return False

    # -- auth ---------------------------------------------------------------

    async def is_logged_in(self) -> bool:
        """True only on a positive logged-in signal (visible left column with
        chat rows) AND no auth page showing. The QR/phone login screen always
        yields False — `#column-left` alone is never trusted because it exists
        (hidden) on the login screen too."""
        page = self._require_page()
        # Only navigate if we are not already on the web client: a health check
        # must never pull the page away from an open chat mid-run.
        if not (page.url or "").startswith(self.settings.web_url.rstrip("/")):
            try:
                await page.goto(self.settings.web_url, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
        # give the SPA a moment to decide which page it is showing
        for _ in range(10):
            if await self._auth_page_showing():
                return False
            if await self._is_visible("#column-left") or await self._is_visible("#chatlist-container"):
                return True
            if await self._first_visible("chat_list", timeout_ms=300) is not None:
                return True
            await page.wait_for_timeout(500)
        return False

    async def _click_text_button(self, text: str) -> bool:
        """Click a visible button/link whose text matches `text` (case-insensitive).
        Returns True if something was clicked."""
        page = self._require_page()
        for sel in (f"button:has-text('{text}')", f"text={text}", f"[role=button]:has-text('{text}')"):
            try:
                btn = page.locator(sel).first
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=5000)
                    return True
            except Exception:
                continue
        return False

    QR_CANVAS = "[class*='qrContainer'] canvas, #auth-pages canvas"

    async def _qr_pixels(self) -> tuple[int, int]:
        """(dark, light) opaque pixel counts on the QR canvas. A never-drawn
        canvas is fully transparent (reads as 0,0,0,0 — which a naive "dark"
        count would mistake for a rendered code), so alpha is required."""
        page = self._require_page()
        try:
            res = await page.evaluate(
                """(sel) => { const c = document.querySelector(sel); if (!c || !c.width) return [0, 0];
                     try { const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                           let dark = 0, light = 0;
                           for (let i = 0; i < d.length; i += 4) { if (d[i + 3] < 128) continue;
                             if (d[i] < 128) dark++; else light++; }
                           return [dark, light]; }
                     catch (e) { return [-1, -1]; } }""",
                self.QR_CANVAS,
            )
            return int(res[0]), int(res[1])
        except Exception:
            return -1, -1

    async def _wait_qr_rendered(self, timeout_s: int = 20) -> bool:
        """Wait for the QR canvas to become non-blank (Telegram actually drew
        the login token). False if it stays blank: Telegram rate-limited or
        refused the token, and the canvas is a preloader spinner."""
        page = self._require_page()
        for _ in range(max(1, timeout_s)):
            dark, light = await self._qr_pixels()
            if dark > 500 and light > 500:  # a real QR has both; a blank or solid canvas has not
                return True
            await page.wait_for_timeout(1000)
        return False

    async def login_qr_screenshot(self, path: str) -> str:
        """Get to the QR login screen and screenshot the QR so the CLI can
        hand it to a human. Returns the path written; sets
        `self.last_qr_rendered` (False when the canvas stayed blank, i.e.
        Telegram did not issue a login token — the caller should warn).

        Telegram Web K now lands on the phone-number form by default, so if
        no QR is visible we click "Log in by QR code" first, then wait for the
        canvas to actually render instead of screenshotting the preloader."""
        page = self._require_page()
        await page.goto(self.settings.web_url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(self.settings.web_settle_ms)

        if await self._first_visible("login_qr", timeout_ms=2000) is None:
            for label in ("LOG IN BY QR CODE", "Log in by QR code", "QR"):
                if await self._click_text_button(label):
                    break
            await page.wait_for_timeout(self.settings.web_settle_ms)

        rendered = await self._wait_qr_rendered(timeout_s=20)
        self.last_qr_rendered = rendered
        Path(path).parent.mkdir(parents=True, exist_ok=True)

        if rendered:
            canvas = page.locator(self.QR_CANVAS).first
            try:
                if await canvas.count():
                    await canvas.screenshot(path=path)
                    return path
            except Exception:
                pass

        qr = await self._first_visible("login_qr", timeout_ms=4000)
        if qr is not None:
            card = await self._first_visible("auth_page", timeout_ms=1000)
            await (card or qr).screenshot(path=path)
        else:
            await page.screenshot(path=path)
        return path

    async def wait_for_login(self, timeout_s: int) -> bool:
        """Poll is_logged_in() until True or timeout_s elapses. Returns
        whether login completed in time."""
        page = self._require_page()
        deadline_ms = timeout_s * 1000
        poll_ms = 2000
        waited = 0
        while waited <= deadline_ms:
            if not await self._auth_page_showing() and (
                await self._is_visible("#column-left") or await self._is_visible("#chatlist-container")
            ):
                return True
            await page.wait_for_timeout(poll_ms)
            waited += poll_ms
        return await self.is_logged_in()

    # -- navigation -----------------------------------------------------------

    async def open_chat(self, bot_username: str) -> None:
        """Navigate to `{web_url}#@username` (WebK's direct-chat-open URL
        scheme, e.g. https://web.telegram.org/k/#@BotFather) and wait for the
        message column to appear."""
        page = self._require_page()
        uname = _username_for_url(bot_username)
        url = self.settings.web_url.rstrip("/") + f"/#@{uname}"
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        if await self._auth_page_showing():
            raise RuntimeError(f"open_chat({bot_username!r}): web client is not logged in (run `graea login-web`)")
        chat_column = await self._first_visible("chat_column", timeout_ms=10000)
        if chat_column is None:
            raise RuntimeError(f"open_chat({bot_username!r}): chat column never became visible")
        await page.wait_for_timeout(self.settings.web_settle_ms)

    # -- reading --------------------------------------------------------------

    async def scroll_to_bottom(self) -> None:
        """Scroll the chat column to the newest message."""
        page = self._require_page()
        chat_column = await self._first_visible("chat_column", timeout_ms=3000)
        if chat_column is None:
            return
        try:
            await chat_column.evaluate("(el) => { el.scrollTop = el.scrollHeight; }")
        except Exception:
            pass
        await page.wait_for_timeout(200)

    async def visible_text(self) -> str:
        """Cheap non-vision fallback: innerText of the message column."""
        chat_column = await self._first_visible("chat_column", timeout_ms=3000)
        if chat_column is None:
            return ""
        try:
            return await chat_column.inner_text()
        except Exception:
            return ""

    async def click_inline_button(self, text: str) -> bool:
        """Visually click an inline keyboard button matching `text` (exact or
        substring, case-insensitive) among the last messages. Returns True if
        a matching, clickable button was found and clicked."""
        page = self._require_page()
        for sel in self.selectors["inline_button"]:
            try:
                locator = page.locator(sel).filter(has_text=text)
                count = await locator.count()
                if count == 0:
                    continue
                await locator.first.click(timeout=3000)
                await page.wait_for_timeout(self.settings.web_settle_ms)
                return True
            except Exception:
                continue
        return False

    # -- screenshots ------------------------------------------------------------

    async def screenshot(self, path: str, region: Region = "chat", last_n: int = 5) -> Screenshot:
        """Screenshot the given region, wait settings.web_settle_ms first.

        region="chat": bounding box of the chat column element.
        region="last_messages": clip to the union bbox of the last `last_n`
            message bubbles.
        region="full": full viewport.
        Computes sha256 + width/height via Pillow. Raises RuntimeError with a
        clear message on failure (caller sets StepResult.screenshot_error).
        """
        page = self._require_page()
        await page.wait_for_timeout(self.settings.web_settle_ms)
        Path(path).parent.mkdir(parents=True, exist_ok=True)

        fallback: Optional[str] = None
        try:
            if region == "full":
                await page.screenshot(path=path)
            elif region == "chat":
                chat_column = await self._first_visible("chat_column", timeout_ms=5000)
                if chat_column is None:
                    fallback = "chat column not visible; captured the viewport instead"
                    await page.screenshot(path=path)
                else:
                    await chat_column.screenshot(path=path, timeout=10000)
            elif region == "last_messages":
                clip = await self._last_messages_bbox(last_n)
                if clip is not None:
                    await page.screenshot(path=path, clip=clip)
                else:
                    chat_column = await self._first_visible("chat_column", timeout_ms=5000)
                    if chat_column is not None:
                        fallback = "no message bubbles matched; captured the chat column instead"
                        await chat_column.screenshot(path=path, timeout=10000)
                    else:
                        fallback = "no bubbles and no visible chat column; captured the viewport instead"
                        await page.screenshot(path=path)
            else:
                raise RuntimeError(f"unknown region: {region!r}")
        except Exception as e:
            # Last resort: a viewport capture is always better than no eye at all.
            try:
                await page.screenshot(path=path)
                fallback = f"{region} capture failed ({type(e).__name__}); captured the viewport instead"
            except Exception as e2:
                raise RuntimeError(f"screenshot(region={region!r}) failed: {e2}") from e2

        shot = self._build_screenshot_model(path, region)
        shot.fallback = fallback
        return shot

    async def _last_messages_bbox(self, last_n: int) -> Optional[dict]:
        """Union bounding box (x, y, width, height) of the last `last_n`
        message bubbles, in page coordinates. None if no bubbles are found."""
        page = self._require_page()
        for sel in self.selectors["message_bubble"]:
            try:
                locator = page.locator(sel)
                count = await locator.count()
                if count == 0:
                    continue
                start = max(0, count - last_n)
                boxes = []
                for i in range(start, count):
                    box = await locator.nth(i).bounding_box()
                    if box and box["width"] > 0 and box["height"] > 0:
                        boxes.append(box)
                if not boxes:
                    continue
                x0 = min(b["x"] for b in boxes)
                y0 = min(b["y"] for b in boxes)
                x1 = max(b["x"] + b["width"] for b in boxes)
                y1 = max(b["y"] + b["height"] for b in boxes)
                return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
            except Exception:
                continue
        return None

    async def probe_dom(self, screenshot_path: Optional[str] = None) -> dict:
        """Dump what the live DOM contains so selectors can be fixed in the
        field: logged-in verdict, per-role/per-selector match + visible
        counts, and a compact tree of visible elements under #column-center
        (or the auth page). Never raises; errors land in the dict."""
        page = self._require_page()
        out: dict = {"url": page.url}
        try:
            out["body_class"] = await page.evaluate("() => document.body.className")
            out["auth_page_showing"] = await self._auth_page_showing()
            out["logged_in"] = await self.is_logged_in()
            counts: dict[str, dict[str, dict[str, int]]] = {}
            for role, sels in self.selectors.items():
                counts[role] = {}
                for sel in sels:
                    try:
                        res = await page.evaluate(
                            """(sel) => { const els = [...document.querySelectorAll(sel)];
                                 const vis = els.filter(e => { const r = e.getBoundingClientRect();
                                   return r.width > 0 && r.height > 0; });
                                 return {count: els.length, visible: vis.length}; }""",
                            sel,
                        )
                    except Exception as e:
                        res = {"error": str(e)[:80]}
                    counts[role][sel] = res
            out["selector_counts"] = counts
            out["tree"] = await page.evaluate(
                """() => { const out = []; const roots = ['#column-center', '#auth-pages', '#column-left'];
                   const walk = (e, d) => { if (d > 7 || !(e instanceof Element)) return;
                     const r = e.getBoundingClientRect(); if (r.width > 0 && r.height > 0) {
                       const cls = String(e.getAttribute('class') || '').trim().split(/\s+/).join('.').slice(0, 120);
                       const mid = e.getAttribute('data-mid') ? ' data-mid=' + e.getAttribute('data-mid') : '';
                       out.push('  '.repeat(d) + e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (cls ? '.' + cls : '') + mid + ' [' + Math.round(r.width) + 'x' + Math.round(r.height) + ']'); }
                     [...e.children].forEach(c => walk(c, d + 1)); };
                   for (const sel of roots) { const el = document.querySelector(sel); if (el) { out.push('ROOT ' + sel); walk(el, 0); } }
                   return out.slice(0, 400); }"""
            )
            out["text"] = (await page.inner_text("body"))[:600]
            if screenshot_path:
                Path(screenshot_path).parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=screenshot_path)
                out["screenshot"] = screenshot_path
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
        return out

    def _build_screenshot_model(self, path: str, region: Region) -> Screenshot:
        data = Path(path).read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        with Image.open(path) as im:
            width, height = im.size
        return Screenshot(path=path, sha256=sha256, width=width, height=height, region=region)


class NullWeb:
    """Same interface as TelegramWeb; every method raises. Used when the
    web profile isn't logged in and the caller wants a visual-eye-disabled
    stand-in rather than special-casing None everywhere."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def _disabled(self):
        raise RuntimeError("visual eye disabled")

    async def start(self) -> None:
        self._disabled()

    async def stop(self) -> None:
        self._disabled()

    async def is_logged_in(self) -> bool:
        self._disabled()

    async def login_qr_screenshot(self, path: str) -> str:
        self._disabled()

    async def wait_for_login(self, timeout_s: int) -> bool:
        self._disabled()

    async def open_chat(self, bot_username: str) -> None:
        self._disabled()

    async def screenshot(self, path: str, region: Region = "chat", last_n: int = 5) -> Screenshot:
        self._disabled()

    async def scroll_to_bottom(self) -> None:
        self._disabled()

    async def click_inline_button(self, text: str) -> bool:
        self._disabled()

    async def visible_text(self) -> str:
        self._disabled()
