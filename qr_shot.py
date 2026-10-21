import asyncio
from playwright.async_api import async_playwright

PROFILE = "/data/web-profile"
URL = "https://web.telegram.org/k/"
QR = "/data/login-qr.png"

async def main():
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE, headless=True, viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(6000)
        qr_loc = page.locator("[class*='qr'], [class*='Qr'], canvas").first
        try:
            await qr_loc.wait_for(state="attached", timeout=5000)
            await qr_loc.screenshot(path=QR)
            print("QR_ELEMENT_SHOT", flush=True)
        except Exception:
            await page.screenshot(path=QR)
            print("QR_FULLPAGE_SHOT", flush=True)
        await ctx.close()
        print("DONE", flush=True)

asyncio.run(main())
