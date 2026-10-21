import asyncio, os
from playwright.async_api import async_playwright

PROFILE = "/data/web-profile"
URL = "https://web.telegram.org/k/"
PASS = os.environ.get("GRAEA_2FA_PASSWORD", "")

async def main():
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE, headless=True, viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(5000)
        print("=== BODY ===", flush=True)
        print((await page.inner_text("body"))[:600], flush=True)

        # The visible password field is a div.input-field-password wrapper
        # over a hidden <input class="stealthy">. Click the wrapper, then type.
        field = page.locator(".input-field-password").first
        try:
            await field.click(timeout=8000)
            await page.wait_for_timeout(500)
            await page.keyboard.type(PASS, delay=50)
            print("TYPED", flush=True)
        except Exception as e:
            print("field click/type err:", type(e).__name__, str(e)[:120], flush=True)
            # fallback: click via JS
            try:
                await field.evaluate("(el)=>el.click()")
                await page.keyboard.type(PASS, delay=50)
                print("TYPED (js click)", flush=True)
            except Exception as e2:
                print("js err:", type(e2).__name__, flush=True)

        await page.keyboard.press("Enter")
        print("PRESSED Enter", flush=True)

        await page.wait_for_timeout(6000)
        print("=== BODY AFTER ===", flush=True)
        print((await page.inner_text("body"))[:1000], flush=True)

        await ctx.close()
        print("DONE", flush=True)

asyncio.run(main())
