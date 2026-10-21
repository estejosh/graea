import asyncio, time
from playwright.async_api import async_playwright

PROFILE = "/data/web-profile"
URL = "https://web.telegram.org/k/"
QR = "/data/login-qr.png"

async def is_logged_in(page):
    try:
        body = (await page.inner_text("body", timeout=3000)).lower()
    except Exception:
        return False
    for m in ["log in by qr", "log in by phone", "scan qr", "quick log in", "log in to telegram"]:
        if m in body:
            return False
    return True

async def main():
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE, headless=True, viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(6000)
        deadline = time.time() + 300
        shot_n = 0
        while time.time() < deadline:
            qr_loc = page.locator("[class*='qr'], [class*='Qr'], canvas").first
            try:
                await qr_loc.wait_for(state="attached", timeout=3000)
                await qr_loc.screenshot(path=QR)
            except Exception:
                await page.screenshot(path=QR)
            shot_n += 1
            if await is_logged_in(page):
                print("LOGGED_IN", flush=True)
                break
            print(f"QR_REFRESH:{shot_n}", flush=True)
            await page.wait_for_timeout(12000)
        await ctx.close()
        print("EXIT", flush=True)

asyncio.run(main())
