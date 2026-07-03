import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir="browser_profile_test",
            headless=False,
            args=["--window-size=1280,720"]
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()
        print("Abrindo pagina...")
        await page.goto("https://labs.google/fx/tools/flow")
        await page.wait_for_load_state("networkidle")
        print("Aguardando o usuario gerar um video e fechar o navegador...")
        
        async def on_download(download):
            print(f"Intercepted download! {download.suggested_filename}")
            await download.save_as("test_video_download.mp4")
            
        page.on("download", on_download)
        
        while not page.is_closed():
            await asyncio.sleep(1)

asyncio.run(run())
