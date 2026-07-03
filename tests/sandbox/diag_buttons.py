import asyncio
from playwright.async_api import async_playwright
import time

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir="browser_profile_test",
            headless=False,
            args=["--window-size=1280,720"]
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()
        print("Abrindo projeto existente...")
        await page.goto("https://labs.google/fx/tools/flow/project/bb6c9962-b8cd-4346-96b2-031c41c4ede4")
        
        print("Esperando 15s... Por favor, passe o mouse sobre um VIDEO gerado para revelar os botoes ocultos!")
        await asyncio.sleep(15)
        
        print("Dumping botoes...")
        buttons = await page.evaluate('''() => {
            const btns = document.querySelectorAll('button, a, [role="button"], span');
            return Array.from(btns).filter(b => b.innerText.includes('download') || b.innerHTML.includes('download') || b.getAttribute('aria-label') || b.title || b.innerHTML.includes('svg')).map(b => {
                return {
                    tag: b.tagName,
                    ariaLabel: b.getAttribute('aria-label'),
                    title: b.getAttribute('title'),
                    html: b.innerHTML.slice(0, 150),
                    className: b.className,
                    text: b.innerText
                }
            });
        }''')
        
        with open("buttons_log.txt", "w", encoding="utf-8") as f:
            for i, b in enumerate(buttons):
                f.write(f"[{i}] {b['tag']} | aria: {b['ariaLabel']} | title: {b['title']} | cls: {b['className']}\n")
                f.write(f"   html: {b['html']}\n")
        print("Salvo em buttons_log.txt")
        await browser.close()

asyncio.run(run())
