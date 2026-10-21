import asyncio
from playwright.async_api import async_playwright

PROFILE = "/data/web-profile"
URL = "https://web.telegram.org/k/"
BOT = "bullship_bot"

async def main():
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE, headless=True,
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(7000)
        await page.goto(URL.rstrip("/") + "/#@" + BOT, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(7000)

        await page.screenshot(path="/data/inspect-full.png")

        print("=== URL:", page.url)
        try:
            body = await page.inner_text("body")
            print("=== BODY TEXT (first 2500) ===")
            print(body[:2500])
        except Exception as e:
            print("body err", e)

        selectors = [
            ".bubbles", "#column-center", ".chatlist", ".messages-container",
            "[class*='message']", "[class*='Message']", "[class*='bubble']",
            "[class*='Bubble']", ".message", ".Message", ".stacked-messages",
            "#message-compose", ".scrollable", ".bubbles-inner",
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel)
                c = await loc.count()
                print(f"--- {sel}: count={c}")
                if c > 0:
                    h = await loc.first.evaluate("(el)=>el.outerHTML.slice(0,400)")
                    print("    ", h.replace("\n", " ")[:400])
            except Exception as e:
                print(f"--- {sel}: ERR {type(e).__name__}")

        await ctx.close()

asyncio.run(main())
