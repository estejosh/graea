import asyncio, os, time
from playwright.async_api import async_playwright

PROFILE = "/data/web-profile"
URL = "https://web.telegram.org/k/"
CODE_FILE = "/data/web-code.txt"
PHONE = os.environ.get("GRAEA_PHONE", "").lstrip("+")
LOCAL = PHONE[1:] if PHONE.startswith("1") else PHONE
PASS = os.environ.get("GRAEA_2FA_PASSWORD", "")


def log(*a):
    print(*a, flush=True)


async def body(page, n=900):
    try:
        return (await page.inner_text("body"))[:n]
    except Exception as e:
        return f"<body err {type(e).__name__}>"


async def click_country_us(page):
    ce = page.locator("[contenteditable='true']")
    clicked = False
    for i in range(await ce.count()):
        el = ce.nth(i)
        txt = (await el.inner_text()).strip()
        if txt in ("El Salvador", "United States", ""):
            await el.click(timeout=5000)
            clicked = True
            break
    if not clicked:
        await ce.first.click(timeout=5000)
    await page.wait_for_timeout(1200)

    # scroll the largest scrollable container (the country list) to bottom
    scrolled = await page.evaluate("""() => {
        const cands = Array.from(document.querySelectorAll('*')).filter(el => el.scrollHeight > el.clientHeight + 50);
        cands.sort((a,b) => b.scrollHeight - a.scrollHeight);
        if (!cands.length) return null;
        const c = cands[0];
        c.scrollTop = c.scrollHeight;
        return {cls: (c.className||'').toString().slice(0,80), sh: c.scrollHeight};
    }""")
    log("SCROLLED", scrolled)
    await page.wait_for_timeout(1500)

    for pat in ["United States +1", "United States"]:
        try:
            li = page.locator("li").filter(has_text=pat).first
            await li.wait_for(state="visible", timeout=4000)
            await li.click(timeout=5000)
            log("COUNTRY_SELECTED", pat)
            return True
        except Exception as e:
            log("COUNTRY_CLICK_FAIL", pat, type(e).__name__)
    return False


async def fill_phone(page):
    field = page.locator("div[contenteditable='true'][inputmode='decimal']").first
    try:
        await field.click(timeout=5000)
        await page.wait_for_timeout(500)
        await page.keyboard.type(LOCAL, delay=30)
        log("PHONE_TYPED", LOCAL)
        return True
    except Exception as e:
        log("PHONE_FILL_FAIL", type(e).__name__, str(e)[:120])
        return False


async def click_next(page):
    try:
        btn = page.locator("button", has_text="NEXT").first
        await btn.click(timeout=5000)
        log("NEXT_CLICKED")
        return True
    except Exception as e:
        log("NEXT_FAIL", type(e).__name__, str(e)[:120])
        return False


async def enter_code(page, code):
    try:
        inp = page.locator("input")
        n = await inp.count()
        if n == 1:
            await inp.first.fill(code)
            return True
        if n >= len(code):
            for i, d in enumerate(code):
                await inp.nth(i).fill(d)
            return True
    except Exception:
        pass
    try:
        ce = page.locator("[contenteditable='true']")
        if await ce.count() >= 1:
            await ce.first.click()
            await page.keyboard.type(code, delay=50)
            return True
    except Exception as e:
        log("CODE_ENTER_ERR", type(e).__name__)
    return False


async def main():
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE, headless=True, viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(5000)

        try:
            e = page.locator("text=LOG IN BY PHONE NUMBER").first
            if await e.count() and await e.is_visible():
                await e.click(timeout=5000)
                await page.wait_for_timeout(3000)
        except Exception:
            pass

        if not await click_country_us(page):
            log("=== BODY (country fail) ==="); log(await body(page))
        await page.wait_for_timeout(800)

        if not await fill_phone(page):
            log("=== BODY (phone fail) ==="); log(await body(page))
        await page.wait_for_timeout(500)

        if not await click_next(page):
            log("=== BODY (next fail) ==="); log(await body(page))

        await page.wait_for_timeout(3500)
        log("=== BODY after NEXT ==="); log(await body(page))
        log("=== inputs after NEXT ===")
        try:
            for i in range(await page.locator("input").count()):
                a = await page.locator("input").nth(i).evaluate(
                    "(el)=>({type:el.type,ph:el.placeholder,aria:el.getAttribute('aria-label')})")
                log(" ", a)
        except Exception as e:
            log("input dump err", e)

        log("WAITING_FOR_CODE")
        code = None
        deadline = time.time() + 240
        while time.time() < deadline:
            if os.path.exists(CODE_FILE):
                try:
                    code = open(CODE_FILE).read().strip()
                    if code:
                        os.remove(CODE_FILE)
                        break
                except Exception:
                    pass
            await page.wait_for_timeout(2000)

        if not code:
            log("CODE_TIMEOUT")
            await ctx.close()
            return

        ok = await enter_code(page, code)
        log("CODE_ENTERED", code, "ok" if ok else "FAILED")
        await page.wait_for_timeout(3500)
        log("=== BODY after code ==="); log(await body(page))

        b = (await body(page)).lower()
        if "password" in b and ("two" in b or "cloud" in b or "enter your password" in b):
            log("TWOFA_REQUIRED")
            if PASS:
                try:
                    ce = page.locator("[contenteditable='true'], input").first
                    await ce.click()
                    await page.keyboard.type(PASS, delay=40)
                    await page.wait_for_timeout(500)
                    for label in ["NEXT", "Done", "Submit", "Log in"]:
                        btn = page.locator("button", has_text=label).first
                        if await btn.count() and await btn.is_visible():
                            await btn.click(timeout=4000)
                            log("TWOFA_SUBMIT_CLICKED", label)
                            break
                except Exception as e:
                    log("TWOFA_ERR", type(e).__name__, str(e)[:120])
            else:
                log("TWOFA_NO_PASSWORD")

        await page.wait_for_timeout(4000)
        b = (await body(page)).lower()
        logged = not any(m in b for m in ["log in by qr", "log in by phone", "sign in to telegram", "enter your phone"])
        log("LOGGED_IN" if logged else "STILL_LOGIN_SCREEN")
        log("=== FINAL BODY ==="); log(await body(page))

        await ctx.close()
        log("DONE")


asyncio.run(main())
