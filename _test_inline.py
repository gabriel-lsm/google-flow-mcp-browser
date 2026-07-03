# -*- coding: utf-8 -*-
"""Teste inline - gera 3 midias sequenciais"""
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
LOG = {
    "steps": [],
    "started": time.strftime("%H:%M:%S"),
    "problemas": [],
    "melhorias": []
}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def save():
    with open(OUT / "log_completo.json", "w", encoding="utf-8") as f:
        json.dump(LOG, f, ensure_ascii=False, indent=2)

async def gera(tipo, qtd, prompts, modelo, ratio, nome):
    log(f"\n--- {nome} ---")
    s = {"test": nome, "type": tipo, "model": modelo, "ratio": ratio, "prompt": prompts[0][:80]}
    try:
        r = json.loads(await flow_generate_media(GenerateMediaInput(
            type=tipo, quantity=qtd, prompts=prompts, model=modelo, aspect_ratio=ratio
        )))
        s["generate"] = r
        log(f"Generate: {r.get('status')}")

        if r.get("status") == "generation_started":
            timeout = 300 if tipo == "video" else 180
            log(f"Aguardando download (timeout: {timeout}s)...")
            d = json.loads(await flow_await_download_media(AwaitDownloadInput(timeout_seconds=timeout)))
            s["download"] = d

            if d.get("status") == "download_complete":
                files = d.get("files", [])
                s["files"] = files
                log(f"✅ {len(files)} arquivo(s):")
                for f in files:
                    p = Path(f)
                    log(f"   📁 {p.name}")
                if len(files) != qtd:
                    msg = f"'{nome}': solicitado {qtd}, baixado {len(files)}"
                    LOG["problemas"].append(msg)
                    LOG["melhorias"].append(f"BUG: {msg}")

            elif d.get("status") == "agent_requires_interaction":
                log("🤖 Agent interaction...")
                for _ in range(5):
                    as_ = json.loads(await flow_read_agent_status())
                    log(f"Agent: {json.dumps(as_, ensure_ascii=False)[:200]}")
                    await flow_reply_to_agent(ReplyToAgentInput(approve=True))
                    log("Approved! Retrying...")
                    d2 = json.loads(await flow_await_download_media(AwaitDownloadInput(timeout_seconds=timeout)))
                    if d2.get("status") == "download_complete":
                        s["files"] = d2.get("files", [])
                        log(f"✅ {len(s['files'])} arquivo(s) apos interacao!")
                        break
                    elif d2.get("status") != "agent_requires_interaction":
                        s["download_retry"] = d2
                        break
                else:
                    LOG["problemas"].append(f"'{nome}': muitas interacoes")
            else:
                LOG["problemas"].append(f"'{nome}': status inesperado {d.get('status')}")
        else:
            LOG["problemas"].append(f"'{nome}': falha geracao - {json.dumps(r, ensure_ascii=False)[:200]}")
    except Exception as e:
        s["error"] = str(e)
        LOG["problemas"].append(f"'{nome}': {e}")
    LOG["steps"].append(s)
    save()

async def main():
    log("INICIANDO TESTE 3 MIDIAS")

    # Session
    r = json.loads(await flow_manage_session(ManageSessionInput(action="start")))
    log(f"Sessao: {r.get('status')}")
    if r.get("status") != "ready":
        log(f"ERRO: {r}")
        return
    LOG["session_url"] = r.get("url")
    save()

    # 1 - Imagem Cyberpunk 16:9
    await gera("image", 1,
        ["A futuristic neon-lit cyberpunk street market at night, heavy rain on neon signs, reflections on wet pavement, flying drones, concept art style, highly detailed, cinematic lighting"],
        "Nano Banana 2", "16:9", "Imagem Cyberpunk 16:9")

    # 2 - Video Golden Retriever 1:1
    await gera("video", 1,
        ["A cute golden retriever puppy playing with a red ball in a sunny green park, slow motion, happy expression, warm sunlight, shallow depth of field, cinematic quality"],
        "Veo 3.1 - Fast", "1:1", "Video Golden Retriever 1:1")

    # 3 - Video Waves 16:9
    await gera("video", 1,
        ["Cinematic aerial drone shot of powerful waves crashing against dramatic ocean cliffs at golden hour, spray catching the warm light, epic nature documentary style"],
        "Veo 3.1 - Fast", "16:9", "Video Waves 16:9")

    ok = sum(1 for s in LOG["steps"] if "files" in s)
    log(f"\n🏁 CONCLUIDO - {ok}/3 com arquivos")
    if LOG["problemas"]:
        log("⚠️ Problemas:")
        for p in LOG["problemas"]:
            log(f"  • {p}")
    if LOG["melhorias"]:
        log("🔧 Melhorias sugeridas:")
        for m in LOG["melhorias"]:
            log(f"  • {m}")
    LOG["ended"] = time.strftime("%H:%M:%S")
    save()
    log(f"📄 {OUT}/log_completo.json")

asyncio.run(main())