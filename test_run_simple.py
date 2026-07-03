# -*- coding: utf-8 -*-
"""
Teste manual do Google Flow MCP Agent
Criado por Claude para testar o MCP sem modificar arquivos existentes
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from server import (
    flow_manage_session, flow_generate_media, flow_await_download_media,
    flow_read_agent_status, flow_reply_to_agent,
    ManageSessionInput, GenerateMediaInput, AwaitDownloadInput,
    ReplyToAgentInput,
)

def log(msg):
    print(f"[{__import__('datetime').datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

async def run():
    log("=" * 60)
    log("INICIANDO TESTE DO GOOGLE FLOW MCP")
    log("=" * 60)

    # ────────────────────── PASSO 1: SESSÃO ──────────────────────
    log("\n📌 [PASSO 1] Iniciando sessão...")
    log("    Chamando flow_manage_session(action='start')")
    log("    Browser será aberto em modo headed (visível)")

    r_str = await flow_manage_session(ManageSessionInput(action='start', login_confirmed=False))
    r = json.loads(r_str)
    log(f"    ✅ Resposta: status={r.get('status')}")

    if r.get('status') == 'login_required':
        log("\n⚠️  LOGIN REQUERIDO!")
        log("    O browser foi aberto. Faça login manualmente na janela do Google.")
        log("    Aguardando até 2 minutos...")

        for i in range(24):
            log(f"    ⏳ Aguardando login... {120 - i*5}s restantes")
            await asyncio.sleep(5)
            r_str = await flow_manage_session(ManageSessionInput(action='start', login_confirmed=True))
            r = json.loads(r_str)
            if r.get('status') == 'ready':
                log("    ✅ Login detectado com sucesso!")
                break
        else:
            log("    ❌ Tempo esgotado para login. Abortando.")
            return

    elif r.get('status') != 'ready':
        log(f"    ❌ Erro inesperado: {json.dumps(r, ensure_ascii=False)}")
        return

    session_url = r.get('url', 'N/A')
    log(f"    🌐 URL atual: {session_url}")
    log("✅ SESSÃO PRONTA!")

    # ────────────────────── PASSO 2: IMAGEM 16:9 ──────────────────────
    log("\n" + "=" * 60)
    log("📌 [PASSO 2] Gerando IMAGEM HORIZONTAL (16:9)")
    log("=" * 60)
    log("    Modelo: Nano Banana 2")
    log("    Prompt: Paisagem cyberpunk futurista")

    req1_str = await flow_generate_media(GenerateMediaInput(
        type='image',
        quantity=1,
        prompts=['Uma paisagem cyberpunk futurista com chuva e luzes de neon, altamente detalhada, estilo cinematográfico 4K'],
        model='Nano Banana 2',
        aspect_ratio='16:9'
    ))
    req1 = json.loads(req1_str)
    log(f"    Resposta: status={req1.get('status')}")

    if req1.get('status') == 'generation_started':
        log(f"    ✅ Template injetado: {req1.get('template_injected', 'N/A')[:100]}...")
        log(f"\n    ⏳ Aguardando geração e download (timeout: 180s)...")

        d1_str = await flow_await_download_media(AwaitDownloadInput(timeout_seconds=180))
        d1 = json.loads(d1_str)
        log(f"    ✅ Download response: status={d1.get('status')}")

        if d1.get('status') == 'download_complete':
            files1 = d1.get('files', [])
            log(f"    📁 Arquivos baixados ({len(files1)}):")
            for f in files1:
                size = Path(f).stat().st_size if Path(f).exists() else 0
                log(f"       - {f} ({size} bytes)")
        elif d1.get('status') == 'agent_requires_interaction':
            log(f"    🤖 Agent pediu interação!")
            log(f"    Lendo status do agent...")
            status_str = await flow_read_agent_status()
            log(f"    Agent: {status_str[:200]}")
            log(f"    Aprovando...")
            await flow_reply_to_agent(ReplyToAgentInput(approve=True))
            log(f"    Aprovado! Aguardando download novamente...")
        else:
            log(f"    ⚠️ Status inesperado: {json.dumps(d1, ensure_ascii=False)}")
    else:
        log(f"    ❌ Falha na geração: {json.dumps(req1, ensure_ascii=False)}")

    # ────────────────────── PASSO 3: IMAGEM 9:16 ──────────────────────
    log("\n" + "=" * 60)
    log("📌 [PASSO 3] Gerando IMAGEM VERTICAL (9:16)")
    log("=" * 60)
    log("    Modelo: Nano Banana 2")
    log("    Prompt: Árvore mágica na floresta encantada")

    req2_str = await flow_generate_media(GenerateMediaInput(
        type='image',
        quantity=1,
        prompts=['Uma árvore antiga e mágica brilhando com luz etérea em uma floresta escura e encantada, estilo fantasia'],
        model='Nano Banana 2',
        aspect_ratio='9:16'
    ))
    req2 = json.loads(req2_str)
    log(f"    Resposta: status={req2.get('status')}")

    if req2.get('status') == 'generation_started':
        log(f"    ✅ Template injetado: {req2.get('template_injected', 'N/A')[:100]}...")
        log(f"\n    ⏳ Aguardando geração e download (timeout: 180s)...")

        d2_str = await flow_await_download_media(AwaitDownloadInput(timeout_seconds=180))
        d2 = json.loads(d2_str)
        log(f"    ✅ Download response: status={d2.get('status')}")

        if d2.get('status') == 'download_complete':
            files2 = d2.get('files', [])
            log(f"    📁 Arquivos baixados ({len(files2)}):")
            for f in files2:
                size = Path(f).stat().st_size if Path(f).exists() else 0
                log(f"       - {f} ({size} bytes)")
        elif d2.get('status') == 'agent_requires_interaction':
            log(f"    🤖 Agent pediu interação!")
            log(f"    Aprovando...")
            await flow_reply_to_agent(ReplyToAgentInput(approve=True))
            log(f"    Aprovado! Aguardando download novamente...")
        else:
            log(f"    ⚠️ Status inesperado: {json.dumps(d2, ensure_ascii=False)}")
    else:
        log(f"    ❌ Falha na geração: {json.dumps(req2, ensure_ascii=False)}")

    # ────────────────────── FINAL ──────────────────────
    log("\n" + "=" * 60)
    log("🏁 TESTE CONCLUÍDO")
    log("=" * 60)

    # Fechar browser?
    log("\n    Browser permanece aberto para inspeção manual.")

asyncio.run(run())