import asyncio
import random
from playwright.async_api import async_playwright

async def human_delay(min_sec: float = 1.0, max_sec: float = 3.0):
    """Applies a random delay to emulate human decision/reaction time."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))

async def smooth_scroll(page, steps: int = 4):
    """Scrolls down the page incrementally instead of instantly jumping."""
    for _ in range(steps):
        scroll_amount = random.randint(200, 450)
        await page.mouse.wheel(0, scroll_amount)
        await asyncio.sleep(random.uniform(0.4, 0.9))

async def run_automation_session():
    async with async_playwright() as p:
        # Directory where browser state (cookies, local storage, cache) is saved
        user_data_dir = "./browser_profile"

        # Launch persistent context to reuse session state across runs
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,  # Headful mode displays the GUI and behaves like a regular browser
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            )
        )

        page = context.pages[0] if context.pages else await context.new_page()

        try:
            print("Navigating to web page...")
            await page.goto("https://example.com", wait_until="domcontentloaded")
            await human_delay(2.0, 4.0)

            # Simulate gradual mouse movement
            await page.mouse.move(
                random.randint(100, 400), 
                random.randint(100, 400), 
                steps=15
            )
            await human_delay(1.0, 2.0)

            # Perform incremental scrolling
            await smooth_scroll(page, steps=3)
            print("Automation routine finished successfully.")

        except Exception as err:
            print(f"Execution encountered an error: {err}")

        finally:
            await context.close()

if __name__ == "__main__":
    asyncio.run(run_automation_session())