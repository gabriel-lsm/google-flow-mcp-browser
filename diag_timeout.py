import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir="browser_profile",
            headless=True
        )
        page = await browser.new_page()
        
        print("Indo para Flow...")
        await page.goto("https://labs.google/fx/tools/flow", wait_until="networkidle")
        await asyncio.sleep(5)
        
        # Clicar no primeiro projeto
        links = await page.query_selector_all("a")
        for link in links:
            href = await link.get_attribute("href")
            if href and "project/new" not in href:
                print(f"Clicando no projeto {href}...")
                await link.click()
                await asyncio.sleep(5)
                break
        
        print(f"URL: {page.url}")
        
        # Encontra o textbox
        tb = await page.query_selector("div[role='textbox']")
        if tb:
            print("Limpando e preenchendo...")
            await tb.click()
            await asyncio.sleep(0.5)
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await asyncio.sleep(0.5)
            
            prompt = """[INSTRUÇÕES DE GERAÇÃO ESTREITA]
Por favor, gere as seguintes mídias usando as especificações exatas abaixo:
- Formato Desejado: 1:1
- Modelo Selecionado: Nano Banana 2
- Total de Mídias: 1

Prompt principal: Um gato astronauta flutuando no espaco sideral"""

            await tb.fill(prompt)
            await asyncio.sleep(1)
            
            print("Pressionando Enter...")
            await page.keyboard.press("Enter")
            await asyncio.sleep(1)
            
            js = """
            () => {
                const buttons = Array.from(document.querySelectorAll('button'));
                const submitBtns = buttons.filter(btn => {
                    const text = (btn.innerText || '').toLowerCase();
                    const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                    return text.includes('arrow_forward') || aria.includes('send') || aria.includes('generate');
                });
                if(submitBtns.length) {
                    const b = submitBtns[0];
                    const r = b.getBoundingClientRect();
                    return {x: r.left + r.width/2, y: r.top + r.height/2};
                }
                return null;
            }
            """
            btn = await page.evaluate(js)
            if btn:
                await page.mouse.click(btn['x'], btn['y'])
            
            await asyncio.sleep(1)
            await page.keyboard.press("Control+Enter")
            
            print("Aguardando 15s para a tela de load aparecer...")
            await asyncio.sleep(15)
            await page.screenshot(path="midias/diag_timeout.png")
            print("Screenshot salva.")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
