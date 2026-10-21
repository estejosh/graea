import asyncio
from playwright.async_api import async_playwright

PROFILE = "/data/web-profile"
URL = "https://web.telegram.org/k/"
QR = "/data/login-qr.png"

async def main():
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE, headless=False, viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(5000)

        # switch to QR screen
        try:
            qrbtn = page.locator("text=LOG IN BY QR CODE").first
            if await qrbtn.count() and await qrbtn.is_visible():
                await qrbtn.click(timeout=5000)
                print("CLICKED_LOG_IN_BY_QR", flush=True)
        except Exception as e:
            print("qrbtn err", type(e).__name__, str(e)[:100], flush=True)

        # wait for QR token to render on the canvas (poll up to ~30s)
        rendered = False
        for i in range(10):
            await page.wait_for_timeout(3000)
            r = await page.evaluate("""() => {
                const c = document.querySelector("[class*='qrContainer'] canvas");
                if (!c) return {canvas:false};
                // sample: draw to a temp and check non-blank
                try {
                    const ctx2 = c.getContext('2d');
                    const d = ctx2.getImageData(0,0,c.width,c.height).data;
                    let dark=0; for (let i=0;i<d.length;i+=4){ if(d[i]<128) dark++; }
                    return {canvas:true, w:c.width, h:c.height, dark:dark, total:(c.width*c.height)};
                } catch(e){ return {canvas:true, err: String(e)}; }
            }""")
            print(f"t+{3*(i+1)}s", r, flush=True)
            if r and r.get("dark") and r["dark"] > 1000:
                rendered = True
                break

        await page.screenshot(path=QR)
        print("SHOT rendered=" + str(rendered), flush=True)
        print("=== BODY ===", flush=True)
        print((await page.inner_text("body"))[:400], flush=True)

        await ctx.close()
        print("DONE", flush=True)

asyncio.run(main())
