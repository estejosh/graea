"""Tests for graea.visual.web — offline only. Network-dependent behavior
(actual login, actual web.telegram.org navigation) is not exercised here.

Screenshot/region logic is tested against a local file:// HTML fixture that
mimics the redesigned WebK layout (hashed classes, `data-mid` bubbles
message elements), using a real headless chromium
(PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers).
"""
from __future__ import annotations

import importlib.util

import pytest
from PIL import Image

from graea.config import Settings
from graea.visual.web import SELECTORS, NullWeb, TelegramWeb

PLAYWRIGHT_AVAILABLE = importlib.util.find_spec("playwright") is not None


FIXTURE_HTML = """
<!doctype html>
<html>
<head>
<style>
  body { margin: 0; padding: 0; }
  #column-left { width: 200px; height: 600px; background: #eee; float: left; }
  ._chatlist_ab12_1 { width: 100%; height: 100%; }
  ._bubbles_ab12_3 {
    width: 600px; height: 600px; overflow: auto; background: white;
    position: relative;
  }
  ._bubble_ab12_9 {
    width: 300px; height: 40px; margin: 10px; background: #cde;
    display: block;
  }
</style>
</head>
<body>
  <div id="column-left"><div id="chatlist-container"><ul class="_chatlist_ab12_1"><li data-peer-id="1">chats</li></ul></div></div>
  <div id="column-center">
    <div class="_bubbles_ab12_3">
      <div class="_bubble_ab12_9" data-mid="10" id="b0">message 0</div>
      <div class="_bubble_ab12_9" data-mid="11" id="b1">message 1</div>
      <div class="_bubble_ab12_9" data-mid="12" id="b2">message 2</div>
      <div class="_bubble_ab12_9" data-mid="13" id="b3">message 3</div>
      <div class="_bubble_ab12_9" data-mid="14" id="b4">message 4</div>
      <div class="_bubble_ab12_9" data-mid="15" id="b5">message 5</div>
      <div class="_bubble_ab12_9" data-mid="16" id="b6">message 6</div>
    </div>
  </div>
</body>
</html>
"""


def test_selectors_well_formed():
    required_roles = {
        "chat_column", "message_bubble", "inline_button",
        "login_qr", "chat_list", "auth_page",
    }
    assert required_roles.issubset(SELECTORS.keys())
    for role, candidates in SELECTORS.items():
        assert isinstance(candidates, list)
        assert len(candidates) >= 1
        for sel in candidates:
            assert isinstance(sel, str) and sel.strip()


@pytest.mark.asyncio
async def test_null_web_raises_for_every_method(tmp_path):
    web = NullWeb()
    methods = [
        ("start", ()),
        ("stop", ()),
        ("is_logged_in", ()),
        ("login_qr_screenshot", (str(tmp_path / "x.png"),)),
        ("wait_for_login", (1,)),
        ("open_chat", ("bot",)),
        ("screenshot", (str(tmp_path / "x.png"),)),
        ("scroll_to_bottom", ()),
        ("click_inline_button", ("x",)),
        ("visible_text", ()),
    ]
    for name, args in methods:
        fn = getattr(web, name)
        with pytest.raises(RuntimeError, match="visual eye disabled"):
            await fn(*args)


@pytest.fixture
def fixture_html_path(tmp_path):
    p = tmp_path / "fixture.html"
    p.write_text(FIXTURE_HTML)
    return p


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")
@pytest.mark.asyncio
async def test_screenshot_region_logic_against_fixture(tmp_path, fixture_html_path):
    """Drive a real headless chromium against a local file:// page that
    matches the hashed-class / data-mid selectors TelegramWeb looks for,
    and check region="chat" vs region="last_messages" vs region="full"
    produce differently-sized, valid PNGs."""
    settings = Settings(
        web_profile=tmp_path / "profile",
        web_headless=True,
        web_viewport_width=800,
        web_viewport_height=700,
        web_settle_ms=50,
    )
    web = TelegramWeb(settings)
    await web.start()
    try:
        page = web._page
        await page.goto(f"file://{fixture_html_path}")
        await page.wait_for_timeout(100)

        full_path = tmp_path / "full.png"
        chat_path = tmp_path / "chat.png"
        last_path = tmp_path / "last.png"

        full_shot = await web.screenshot(str(full_path), region="full")
        chat_shot = await web.screenshot(str(chat_path), region="chat")
        last_shot = await web.screenshot(str(last_path), region="last_messages", last_n=2)

        assert full_shot.region == "full"
        assert chat_shot.region == "chat"
        assert last_shot.region == "last_messages"

        for shot, path in ((full_shot, full_path), (chat_shot, chat_path), (last_shot, last_path)):
            assert path.exists()
            with Image.open(path) as im:
                assert im.size[0] > 0 and im.size[1] > 0
            assert shot.sha256 and len(shot.sha256) == 64

        # full viewport should be the widest (includes #column-left).
        assert full_shot.width >= chat_shot.width
        # last_messages (2 bubbles ~ 2*(40+20) tall) should be much shorter
        # than the whole chat column (700px tall).
        assert last_shot.height < chat_shot.height

        # visible_text / scroll_to_bottom should not raise.
        text = await web.visible_text()
        assert "message" in text
        await web.scroll_to_bottom()

        # click_inline_button: no matching button in the fixture -> False, no raise.
        clicked = await web.click_inline_button("Does Not Exist")
        assert clicked is False
    finally:
        await web.stop()


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")
@pytest.mark.asyncio
async def test_click_inline_button_against_fixture(tmp_path):
    html = FIXTURE_HTML.replace(
        '<div class="_bubble_ab12_9" data-mid="16" id="b6">message 6</div>',
        '<div class="_bubble_ab12_9" data-mid="16" id="b6">message 6'
        '<div class="_replyMarkup_ab12_4"><button>Click Me</button></div></div>',
    )
    fixture_path = tmp_path / "fixture_btn.html"
    fixture_path.write_text(html)

    settings = Settings(web_profile=tmp_path / "profile2", web_headless=True, web_settle_ms=50)
    web = TelegramWeb(settings)
    await web.start()
    try:
        page = web._page
        await page.goto(f"file://{fixture_path}")
        await page.wait_for_timeout(100)
        clicked = await web.click_inline_button("Click Me")
        assert clicked is True
    finally:
        await web.stop()


LOGIN_HTML = """<!doctype html><html><body class="animation-level-2 has-auth-pages">
<div id="column-left" style="width:0;height:0"><div id="chatlist-container"></div></div>
<div id="column-center" style="width:0;height:0"></div>
<div id="auth-pages" class="whole _host_1b0yp_9" style="width:800px;height:600px">
 <div class="_card_1b0yp_73 _pageSignQR_1b0yp_208" style="width:392px;height:572px">
  <div class="_sticker_1ixcd_28 _qrContainer_1b0yp_179" style="width:240px;height:240px;background:#000"></div>
  <div>Log in by QR Code</div></div></div></body></html>"""


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")
@pytest.mark.asyncio
async def test_login_screen_is_not_logged_in_and_screenshot_never_fails(tmp_path):
    """Regression for the field report: #column-left exists on the QR screen,
    so a bare #column-left must never count as logged in; and screenshots on
    a page with no bubbles fall back to a wider capture instead of raising."""
    fixture = tmp_path / "login.html"
    fixture.write_text(LOGIN_HTML)
    settings = Settings(web_profile=tmp_path / "p3", web_headless=True, web_settle_ms=50,
                        web_url=f"file://{fixture}")
    web = TelegramWeb(settings)
    await web.start()
    try:
        await web._page.goto(f"file://{fixture}")
        assert await web.is_logged_in() is False
        qr = await web.login_qr_screenshot(str(tmp_path / "qr.png"))
        assert (tmp_path / "qr.png").exists() and qr
        shot = await web.screenshot(str(tmp_path / "s.png"), region="last_messages")
        assert shot.fallback and "viewport" in shot.fallback
        probe = await web.probe_dom()
        assert probe["auth_page_showing"] is True and probe["logged_in"] is False
        assert probe["selector_counts"]["login_qr"]["[class*='qrContainer']"]["visible"] == 1
    finally:
        await web.stop()


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")
@pytest.mark.asyncio
async def test_logged_in_fixture_and_selector_overrides(tmp_path, fixture_html_path):
    override = tmp_path / "sel.json"
    override.write_text('{"message_bubble": ["#b6"]}')
    settings = Settings(web_profile=tmp_path / "p4", web_headless=True, web_settle_ms=50,
                        web_url=f"file://{fixture_html_path}", selectors_file=override)
    web = TelegramWeb(settings)
    assert web.selectors["message_bubble"][0] == "#b6"
    await web.start()
    try:
        await web._page.goto(f"file://{fixture_html_path}")
        assert await web.is_logged_in() is True
        shot = await web.screenshot(str(tmp_path / "one.png"), region="last_messages", last_n=5)
        assert shot.fallback is None
        assert shot.height <= 60  # the override narrowed capture to a single 40px bubble
    finally:
        await web.stop()


PHONE_FORM_HTML = """<!doctype html><html><body class="has-auth-pages">
<div id="auth-pages" style="width:800px;height:600px">
 <div id="phone" class="_card_x _pageSign_x" style="width:392px;height:400px">
   <div>Sign in to Telegram</div><input placeholder="Phone number">
   <button id="toqr" onclick="document.getElementById('phone').style.display='none';document.getElementById('qr').style.display='block';setTimeout(draw, %d)">LOG IN BY QR CODE</button>
 </div>
 <div id="qr" class="_card_x _pageSignQR_x" style="display:none;width:392px;height:572px">
   <div class="_qrContainer_x" style="width:240px;height:240px"><canvas width="240" height="240"></canvas></div>
   <div>Log in by QR Code</div>
 </div>
</div>
<script>
function draw(){ const c=document.querySelector('canvas').getContext('2d'); c.fillStyle='#fff'; c.fillRect(0,0,240,240);
  c.fillStyle='#000'; for(let y=0;y<240;y+=20) for(let x=0;x<240;x+=20) if((x/20+y/20)%%2==0) c.fillRect(x,y,20,20); }
</script></body></html>"""


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")
@pytest.mark.asyncio
async def test_login_qr_clicks_through_phone_form_and_waits_for_render(tmp_path):
    fixture = tmp_path / "phone.html"
    fixture.write_text(PHONE_FORM_HTML % 1500)  # QR draws 1.5s after the click
    settings = Settings(web_profile=tmp_path / "p5", web_headless=True, web_settle_ms=50,
                        web_url=f"file://{fixture}")
    web = TelegramWeb(settings)
    await web.start()
    try:
        out = tmp_path / "qr.png"
        await web.login_qr_screenshot(str(out))
        assert web.last_qr_rendered is True
        with Image.open(out) as im:
            assert im.size[0] <= 260 and im.size[1] <= 260  # tight canvas capture, not the form
            px = im.convert("L").getdata()
            dark = sum(1 for v in px if v < 128) / len(px)
            assert 0.3 < dark < 0.7  # checkerboard ~50% dark: a drawn QR, not a blank canvas
    finally:
        await web.stop()


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")
@pytest.mark.asyncio
async def test_login_qr_reports_blank_canvas(tmp_path, monkeypatch):
    fixture = tmp_path / "blank.html"
    fixture.write_text(PHONE_FORM_HTML % 999999)  # QR never draws
    settings = Settings(web_profile=tmp_path / "p6", web_headless=True, web_settle_ms=50,
                        web_url=f"file://{fixture}")
    web = TelegramWeb(settings)
    monkeypatch.setattr(web, "_wait_qr_rendered", lambda timeout_s=20: web._wait_qr_rendered_fast())
    web._wait_qr_rendered_fast = lambda: _false()
    await web.start()
    try:
        out = tmp_path / "qr.png"
        await web.login_qr_screenshot(str(out))
        assert web.last_qr_rendered is False
        assert out.exists()
    finally:
        await web.stop()


async def _false():
    return False
