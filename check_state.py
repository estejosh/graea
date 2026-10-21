import asyncio
from playwright.async_api import async_playwright

PROFILE = "/data/web-profile"
URL = "https://web.telegram.org/k/"

async def vis(page, sel):
    try:
        el = page.locator(sel).first
        if await el.count():
            r = await el.evaluate("(e)=>{const b=e.getBoundingClientRect();return [b.width,b.height]}")
            return r
    except Exception:
        pass
    return None

async def main():
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE, headless=True, viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(10000)

        has_class = await page.evaluate("() => document.body.classList.contains('has-auth-pages')")
        print("body.has-auth-pages:", has_class, flush=True)
        print("#auth-pages visible:", await vis(page, "#auth-pages"), flush=True)
        print("#column-left visible:", await vis(page, "#column-left"), flush=True)
        print("#chatlist-container visible:", await vis(page, "#chatlist-container"), flush=True)
        print("[data-peer-id] count:", await page.locator("[data-peer-id]").count(), flush=True)
        print("#column-left [data-peer-id] count:", await page.locator("#column-left [data-peer-id]").count(), flush=True)
        print("#chatlist-container [data-peer-id] count:", await page.locator("#chatlist-container [data-peer-id]").count(), flush=True)

        await ctx.close()
        print("DONE", flush=True)

asyncio.run(main())
