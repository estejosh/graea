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
# Selectors: WebK's DOM/class names are not a public API and change between
# releases. Each role below lists several candidate CSS selectors, tried in
# order; the first that matches >=1 element wins. Keep this list the single
# place to patch when Telegram ships a redesign.
# --------------------------------------------------------------------------

SELECTORS: dict[str, list[str]] = {
    # The scrollable column that holds message bubbles for the open chat.
    "chat_column": [
        ".bubbles",
        ".bubbles-inner",
        "#column-center .scrollable",
        "#column-center",
    ],
    # Individual message bubble elements within the chat column.
    "message_bubble": [
        ".bubble.is-in, .bubble.is-out",
        ".bubble",
        "[class*='bubble']",
    ],
    # Inline keyboard buttons attached to a message bubble.
    "inline_button": [
        ".reply-markup-button",
        ".inline-button",
        "button.reply-markup-button",
        "[class*='reply-markup'] button",
    ],
    # Elements present only on the QR-login screen.
    "login_qr": [
        "#page-signQR canvas",
        "#page-signQR",
        "#page-sign",
        ".auth-image",
        ".qr-container",
        ".login-qr",
        "canvas.qr-canvas",
        "[class*='qr']",
    ],
    # Elements present only when the chat list (i.e. we are logged in) is shown.
    "chat_list": [
        "#page-chats #column-left .chatlist",
        "#column-left .chatlist",
        ".chatlist",
        "#column-left",
    ],
    # The search/open-chat box, used as a login/readiness heuristic fallback.
    "app_root": [
        "#page-chats",
        "#app",
    ],
}


def _username_for_url(bot_username: str) -> str:
    return bot_username[1:] if bot_username.startswith("@") else bot_username


class TelegramWeb:
    """Playwright chromium, persistent context at settings.web_profile."""

    def __init__(self, settings: Settings):
        self.settings = settings
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

    async def _first_visible(self, role: str, timeout_ms: int = 1500):
        """Try each candidate selector for `role`, return the first Locator
        that has >=1 attached element within timeout_ms, else None."""
        page = self._require_page()
        for sel in SELECTORS.get(role, []):
            try:
                locator = page.locator(sel).first
                await locator.wait_for(state="attached", timeout=timeout_ms)
                return locator
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        return None

    # -- auth ---------------------------------------------------------------

    async def is_logged_in(self) -> bool:
        """True if the chat list is visible; False if the login/QR screen is
        showing. Robust to either state being briefly absent: uses short
        timeouts and checks both signals rather than hanging."""
        page = self._require_page()
        # Only navigate if we are not already on the web client: a health check
        # must never pull the page away from an open chat mid-run.
        if not (page.url or "").startswith(self.settings.web_url.rstrip("/")):
            try:
                await page.goto(self.settings.web_url, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass

        chat_list = await self._first_visible("chat_list", timeout_ms=4000)
        if chat_list is not None:
            return True

        login_qr = await self._first_visible("login_qr", timeout_ms=2000)
        if login_qr is not None:
            return False

        # Neither signal found within the timeouts: check page text as a
        # last-resort heuristic rather than hanging indefinitely.
        try:
            text = await page.inner_text("body", timeout=2000)
        except Exception:
            text = ""
        lowered = text.lower()
        if "log in to telegram" in lowered or "scan qr" in lowered or "quick log in" in lowered:
            return False
        return False

    async def login_qr_screenshot(self, path: str) -> str:
        """Navigate to the login screen and screenshot the QR code so the
        CLI can display it to the user. Returns the path written."""
        page = self._require_page()
        await page.goto(self.settings.web_url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(self.settings.web_settle_ms)

        qr = await self._first_visible("login_qr", timeout_ms=8000)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if qr is not None:
            await qr.screenshot(path=path)
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
            chat_list = await self._first_visible("chat_list", timeout_ms=1500)
            if chat_list is not None:
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
        chat_column = await self._first_visible("chat_column", timeout_ms=10000)
        if chat_column is None:
            raise RuntimeError(f"open_chat({bot_username!r}): chat column never appeared")
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
        for sel in SELECTORS["inline_button"]:
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

        try:
            if region == "full":
                await page.screenshot(path=path)
            elif region == "chat":
                chat_column = await self._first_visible("chat_column", timeout_ms=5000)
                if chat_column is None:
                    raise RuntimeError("chat column element not found")
                await chat_column.screenshot(path=path)
            elif region == "last_messages":
                clip = await self._last_messages_bbox(last_n)
                if clip is None:
                    # Fall back to the whole chat column if we can't find bubbles.
                    chat_column = await self._first_visible("chat_column", timeout_ms=5000)
                    if chat_column is None:
                        raise RuntimeError("no message bubbles or chat column found")
                    await chat_column.screenshot(path=path)
                else:
                    await page.screenshot(path=path, clip=clip)
            else:
                raise RuntimeError(f"unknown region: {region!r}")
        except Exception as e:
            raise RuntimeError(f"screenshot(region={region!r}) failed: {e}") from e

        return self._build_screenshot_model(path, region)

    async def _last_messages_bbox(self, last_n: int) -> Optional[dict]:
        """Union bounding box (x, y, width, height) of the last `last_n`
        message bubbles, in page coordinates. None if no bubbles are found."""
        page = self._require_page()
        for sel in SELECTORS["message_bubble"]:
            try:
                locator = page.locator(sel)
                count = await locator.count()
                if count == 0:
                    continue
                start = max(0, count - last_n)
                boxes = []
                for i in range(start, count):
                    box = await locator.nth(i).bounding_box()
                    if box:
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
