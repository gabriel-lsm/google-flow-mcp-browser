# -*- coding: utf-8 -*-
"""Teste das 3 midias restantes: 1 imagem + 2 videos"""
import asyncio, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from server import (
    flow_manage_session, flow_generate_media, flow_await_download_media,
    flow_read_agent_status, flow_reply_to_agent,
    ManageSessionInput, GenerateMediaInput, AwaitDownloadInput, ReplyToAgentInput
)

OUT = Path(__file__).parent.parent / "test_outputs"
OUT.mkdir(parents=True, exist_ok=True)
MIDIAS = Path(__file__).parent / "midias"
MIDIAS.mkdir(parents=True, exist_ok=True)

RELATORIO = {
    "testes": [],
    "problemas": [],
    "melhorias": [],
    "inicio": time.strftime("%Y-%m-%d %H:%M:%S")
}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def salvar():
    with open(OUT / "relatorio_completo.json", "w", encoding="utf-8") as f:
        json.dump(RELATORIO, f, ensure_ascii=False, indent=2)

async def aguardar_download_com_interacao(timeout=300):
    """Aguarda download tratando interacoes do agent"""
    interaction_count = 0
    while interaction_count < 5:
        d_str = await flow_await_download_media(AwaitDownloadInput(timeout_seconds=timeout))
        d = json.loads(d_str)

        if d.get("status") == "agent_requires_interaction":
            interaction_count += 1
            log(f"🤖 Agent pediu interacao #{interaction_count}")
            try:
                s_str = await flow_read_agent_status()
                s = json.loads(s_str)
                log(f"   Agent diz: {json.dumps(s, ensure_ascii=False)[:200]}")
                await flow_reply_to_agent(ReplyToAgentInput(approve=True))
                log(f"   ✅ Aprovado! Retomando...")
            except Exception as e:
                log(f"   ⚠️ Erro na interacao: {e}")
                return d
        else:
            return d

    log("⚠️ Muitas interacoes seguidas. Abortando.")
    return d

async def gerar_midia(tipo, quantity, prompts, model, aspect_ratio, nome_teste):
    """Gera uma midia e retorna o resultado"""
    log(f"\n{'='*60}")
    log(f"📌 {nome_teste}")
    log(f"{'='*60}")
    log(f"   Tipo: {tipo} | Modelo: {model} | Ratio: {aspect_ratio}")
    log(f"   Prompt: {prompts[0][:80]}...")

    teste = {
        "nome": nome_teste,
        "tipo": tipo,
        "modelo": model,
        "aspect_ratio": aspect_ratio,
        "prompt": prompts[0],
        "status": "pendente"
    }
    RELATORIO["testes"].append(teste)

    # GERAR
    try:
        req_str = await flow_generate_media(GenerateMediaInput(
            type=tipo, quantity=quantity,
            prompts=prompts,
            model=model,
            aspect_ratio=aspect_ratio
        ))
        req = json.loads(req_str)
        teste["generate_response"] = req
        log(f"   ✅ Generate: {req.get('status')}")

        if req.get("status") != "generation_started":
            teste["status"] = "falha_geracao"
            log(f"   ❌ Falha: {req}")
            salvar()
            return teste
    except Exception as e:
        teste["status"] = "erro"
        teste["erro"] = str(e)
        log(f"   ❌ Erro: {e}")
        RELATORIO["problemas"].append(f"Erro ao gerar {nome_teste}: {e}")
        salvar()
        return teste

    # AGUARDAR DOWNLOAD
    log(f"   ⏳ Aguardando geracao...")
    try:
        d = await aguardar_download_com_interacao(timeout=300)
        teste["download_response"] = d

        if d.get("status") == "download_complete":
            files = d.get("files", [])
            teste["status"] = "sucesso"
            teste["arquivos"] = []
            for f in files:
                p = Path(f)
                sz = p.stat().st_size if p.exists() else 0
                teste["arquivos"].append({"path": f, "bytes": sz})

            log(f"   ✅ Download COMPLETO! {len(files)} arquivo(s):")
            for f in files:
                log(f"      📁 {Path(f).name}")

            # Verificar discrepancia quantity vs downloaded
            if quantity != len(files):
                msg = f"Quantidade solicitada ({quantity}) != arquivos baixados ({len(files)}) em '{nome_teste}'"
                RELATORIO["problemas"].append(msg)
                RELATORIO["melhorias"].append(f"BUG: {msg} - O MCP deveria baixar exatamente {quantity} arquivo(s), mas baixou {len(files)}")
                log(f"   ⚠️ Discrepancia: pedido {quantity}, baixado {len(files)}")

        elif d.get("status") == "generation_complete_no_files":
            teste["status"] = "sem_arquivos"
            log(f"   ⚠️ Geracao concluida mas sem arquivos extraidos")
        else:
            teste["status"] = f"status_inesperado: {d.get('status')}"
            log(f"   ⚠️ Status: {d.get('status')}")

    except Exception as e:
        teste["status"] = "erro"
        teste["erro"] = str(e)
        log(f"   ❌ Erro no download: {e}")
        RELATORIO["problemas"].append(f"Erro download {nome_teste}: {e}")

    salvar()
    return teste

async def run():
    log("INICIANDO TESTE DAS 3 MIDIAS RESTANTES")
    log("="*60)

    # PASSO 1: Sessao
    log("\n📌 [PASSO 1] Iniciando sessao...")
    try:
        r_str = await flow_manage_session(ManageSessionInput(action='start'))
        r = json.loads(r_str)
        log(f"   Sessao: {r.get('status')} - {r.get('message', '')[:100]}")

        if r.get("status") == "login_required":
            log("   ⚠️ Login necessario! Abra o Chrome e faca login...")
            for i in range(30):
                await asyncio.sleep(5)
                r_str = await flow_manage_session(ManageSessionInput(action='start', login_confirmed=True))
                r = json.loads(r_str)
                if r.get("status") == "ready":
                    log(f"   ✅ Login confirmado!")
                    break
            else:
                log("   ❌ Timeout login")
                return
        elif r.get("status") != "ready":
            log(f"   ❌ Erro sessao: {r}")
            return
    except Exception as e:
        log(f"   ❌ Erro critico sessao: {e}")
        return

    RELATORIO["sessao_url"] = r.get("url", "N/A")
    log(f"   URL: {RELATORIO['sessao_url']}")
    salvar()

    # ── TESTE 1: Imagem Cyberpunk 16:9 ──
    await gerar_midia(
        tipo='image', quantity=1,
        prompts=['A futuristic neon-lit cyberpunk street market at night, heavy rain on neon signs, reflections on wet pavement, flying drones, concept art style, highly detailed, cinematic lighting'],
        model='Nano Banana 2',
        aspect_ratio='16:9',
        nome_teste='1-Imagem Cyberpunk 16:9'
    )

    # ── TESTE 2: Video Golden Retriever 1:1 ──
    await gerar_midia(
        tipo='video', quantity=1,
        prompts=['A cute golden retriever puppy playing with a red ball in a sunny green park, slow motion, happy expression, warm sunlight, shallow depth of field, cinematic quality'],
        model='Veo 3.1 - Fast',
        aspect_ratio='1:1',
        nome_teste='2-Video Golden Retriever 1:1'
    )

    # ── TESTE 3: Video Waves 16:9 ──
    await gerar_midia(
        tipo='video', quantity=1,
        prompts=['Cinematic aerial drone shot of powerful waves crashing against dramatic ocean cliffs at golden hour, spray catching the warm light, epic nature documentary style'],
        model='Veo 3.1 - Fast',
        aspect_ratio='16:9',
        nome_teste='3-Video Waves 16:9'
    )

    # ── FINAL ──
    RELATORIO["fim"] = time.strftime("%Y-%m-%d %H:%M:%S")
    log(f"\n{'='*60}")
    log("🏁 TESTE CONCLUIDO!")
    log(f"{'='*60}")

    # Resumo
    sucessos = sum(1 for t in RELATORIO["testes"] if t["status"] == "sucesso")
    falhas = sum(1 for t in RELATORIO["testes"] if t["status"] != "sucesso")
    log(f"\n✅ Sucessos: {sucessos}/{len(RELATORIO['testes'])}")
    if RELATORIO["problemas"]:
        log(f"\n⚠️ Problemas encontrados:")
        for p in RELATORIO["problemas"]:
            log(f"   • {p}")
    if RELATORIO["melhorias"]:
        log(f"\n🔧 Sugestoes de melhoria:")
        for m in RELATORIO["melhorias"]:
            log(f"   • {m}")

    salvar()
    log(f"\n📄 Relatorio salvo em: {OUT / 'relatorio_completo.json'}")

asyncio.run(run())
