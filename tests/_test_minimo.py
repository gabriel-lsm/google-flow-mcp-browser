# -*- coding: utf-8 -*-
"""Teste minimo do MCP - executa passo a passo salvando JSON"""
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
RESULT = {"steps": [], "final_status": "unknown"}

def save():
    with open(OUT / "test_result.json", "w", encoding="utf-8") as f:
        json.dump(RESULT, f, ensure_ascii=False, indent=2)

async def run():
    global RESULT
    RESULT["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    # PASSO 1
    step = {"name": "manage_session", "status": "running"}
    RESULT["steps"].append(step)
    try:
        r_str = await flow_manage_session(ManageSessionInput(action='start'))
        r = json.loads(r_str)
        step["status"] = r.get("status")
        step["response"] = r
        print(f"[OK] Sessao: {r.get('status')} - {json.dumps(r.get('message',''))[:100]}")
        save()
    except Exception as e:
        step["status"] = "error"
        step["error"] = str(e)
        print(f"[ERRO] manage_session: {e}")
        save()
        return
    save()

    if r.get("status") == "ready":
        print("Sessao pronta! Browser autenticado.")
    elif r.get("status") == "login_required":
        print("LOGIN REQUERIDO. Abra o Chrome e faca login. Esperando...")
        for i in range(24):
            await asyncio.sleep(5)
            try:
                r_str = await flow_manage_session(ManageSessionInput(action='start', login_confirmed=True))
                r = json.loads(r_str)
                if r.get("status") == "ready":
                    print(f"Login confirmado apos {i*5}s!")
                    break
            except: pass
        else:
            print("Timeout no login.")
            RESULT["final_status"] = "login_timeout"
            save()
            return

    # PASSO 2 - GERAR IMAGEM
    step = {"name": "generate_media", "status": "running"}
    RESULT["steps"].append(step)
    print("\n--- GERANDO IMAGEM ---")
    try:
        req_str = await flow_generate_media(GenerateMediaInput(
            type='image', quantity=1,
            prompts=['A serene Japanese zen garden in autumn, red maple leaves reflecting in a pond, koi fish swimming, soft sunlight filtering through trees, highly detailed, photorealistic, peaceful atmosphere'],
            model='Nano Banana 2',
            aspect_ratio='1:1'
        ))
        req = json.loads(req_str)
        step["status"] = req.get("status")
        step["response"] = req
        print(f"[OK] Generate: {req.get('status')}")
        save()
    except Exception as e:
        step["status"] = "error"
        step["error"] = str(e)
        print(f"[ERRO] generate: {e}")
        save()
        return
    save()

    if req.get("status") != "generation_started":
        RESULT["final_status"] = "generate_failed"
        print(f"Falha: {req}")
        save()
        return

    # PASSO 3 - AGUARDAR DOWNLOAD
    step = {"name": "await_download", "status": "running"}
    RESULT["steps"].append(step)
    print("\n--- AGUARDANDO GERACAO E DOWNLOAD ---")
    try:
        d_str = await flow_await_download_media(AwaitDownloadInput(timeout_seconds=240))
        d = json.loads(d_str)
        step["status"] = d.get("status")
        step["response"] = d
        print(f"[OK] Download: {d.get('status')}")
        save()
    except Exception as e:
        step["status"] = "error"
        step["error"] = str(e)
        print(f"[ERRO] await_download: {e}")
        save()
        return

    # Tratar interacoes
    interaction_count = 0
    while d.get("status") == "agent_requires_interaction" and interaction_count < 5:
        interaction_count += 1
        step = {"name": f"interaction_{interaction_count}", "status": "running"}
        RESULT["steps"].append(step)
        print(f"\n--- INTERACAO DO AGENT #{interaction_count} ---")
        try:
            s_str = await flow_read_agent_status()
            s = json.loads(s_str)
            step["agent_status"] = s
            print(f"Agent: {json.dumps(s, ensure_ascii=False)[:200]}")
            await flow_reply_to_agent(ReplyToAgentInput(approve=True))
            step["status"] = "approved"
            print("Aprovado! Aguardando download...")
            save()
        except Exception as e:
            step["status"] = "error"
            step["error"] = str(e)
            print(f"[ERRO] interacao: {e}")
            break

        try:
            d_str = await flow_await_download_media(AwaitDownloadInput(timeout_seconds=240))
            d = json.loads(d_str)
            print(f"Download apos interacao: {d.get('status')}")
            save()
        except Exception as e:
            print(f"[ERRO] await_download apos interacao: {e}")
            break

    if d.get("status") == "download_complete":
        files = d.get("files", [])
        print(f"\n🎉 DOWNLOAD COMPLETO! {len(files)} arquivo(s)")
        for f in files:
            p = Path(f)
            sz = p.stat().st_size if p.exists() else 0
            print(f"   📁 {f} ({sz} bytes)")
        RESULT["final_status"] = "success"
        RESULT["downloaded_files"] = files
    elif d.get("status") == "generation_complete_no_files":
        print("\n⚠️ Geracao concluida mas sem arquivos extraidos")
        RESULT["final_status"] = "no_files"
    else:
        print(f"\nStatus final inesperado: {d}")
        RESULT["final_status"] = "unexpected"

    RESULT["ended_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()
    print(f"\nResultado salvo em: {OUT / 'test_result.json'}")

asyncio.run(run())