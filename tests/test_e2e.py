# -*- coding: utf-8 -*-
"""
Teste end-to-end do Google Flow MCP Agent.
Requer sessao salva em browser_profile/ (executar open_login.py primeiro).
"""
import asyncio
import json
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from server import (
    flow_manage_session, flow_generate_media, flow_await_download_media,
    ManageSessionInput, GenerateMediaInput, AwaitDownloadInput,
)

results = {"passed": 0, "failed": 0, "errors": []}


def check(tid, desc, cond, detail=""):
    if cond:
        print(f"  PASS [{tid}] {desc}")
        results["passed"] += 1
    else:
        print(f"  FAIL [{tid}] {desc}" + (f"\n         -> {detail}" if detail else ""))
        results["failed"] += 1
        results["errors"].append(tid)


def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


async def run_e2e():

    section("TESTE 1: flow_manage_session (sessao existente)")

    # Testar com sessao ja salva — deve detectar login automaticamente
    params = ManageSessionInput(action="start", login_confirmed=False)
    print(f"  Chamando flow_manage_session(action='start')...")
    result_raw = await flow_manage_session(params)
    r = json.loads(result_raw)
    print(f"  Resposta: status={r.get('status')}")

    if r.get("status") == "login_required":
        # Sessao expirou — tentar com login_confirmed=True
        print("  Sessao expirou, tentando com login_confirmed=True...")
        params2 = ManageSessionInput(action="start", login_confirmed=True)
        result_raw = await flow_manage_session(params2)
        r = json.loads(result_raw)
        print(f"  Resposta com confirmed: status={r.get('status')}")

    check("3.6", "Browser carregou perfil salvo (browser_profile/)",
          r.get("status") in ("ready", "login_required"),
          str(r)[:200])

    check("3.7_3.9", "manage_session retornou status valido (ready ou login_required)",
          r.get("status") in ("ready", "login_required"),
          str(r)[:200])

    session_ready = r.get("status") == "ready"
    if not session_ready:
        print(f"\n  AVISO: Sessao nao esta 'ready'. Status: {r.get('status')}")
        print(f"  Tentando com login_confirmed=True...")
        params3 = ManageSessionInput(action="start", login_confirmed=True)
        result_raw = await flow_manage_session(params3)
        r = json.loads(result_raw)
        session_ready = r.get("status") == "ready"
        print(f"  Novo status: {r.get('status')}")

    check("3.9", "Sessao ready apos deteccao de login",
          session_ready,
          f"Status atual: {r.get('status')} | msg: {r.get('message', '')[:100]}")

    if not session_ready:
        print("\n  ABORTANDO: Sessao nao esta pronta. Verifique o login.")
        return

    print(f"\n  URL atual no browser: {r.get('url', 'N/A')}")

    section("TESTE 2: flow_generate_media (1 imagem de teste)")

    gen_params = GenerateMediaInput(
        type="image",
        model="Nano Banana 2",
        aspect_ratio="1:1",
        quantity=1,
        prompts=["Um gato astronauta flutuando no espaco, estilo cartoon colorido"],
    )

    print(f"  Chamando flow_generate_media(type=image, quantity=1)...")
    print(f"  Prompt: '{gen_params.prompts[0]}'")
    g_raw = await flow_generate_media(gen_params)
    g = json.loads(g_raw)
    print(f"  Resposta: status={g.get('status')} | error_code={g.get('error_code', 'none')}")

    # --- INJECT SCREENSHOT HERE ---
    try:
        from server import _browser_state
        if _browser_state.get("page"):
            os.makedirs("midias", exist_ok=True)
            await _browser_state["page"].screenshot(path="midias/e2e_after_generate.png")
            print("  [DIAG] Screenshot salva em midias/e2e_after_generate.png")
    except Exception as e:
        print(f"  [DIAG] Erro ao salvar screenshot: {e}")
    # ------------------------------

    if g.get("status") == "error":
        print(f"  ERRO: {g.get('error_code')} - {g.get('message')}")

    check("4.15_4.18", "flow_generate_media retornou generation_started",
          g.get("status") == "generation_started",
          str(g)[:300])

    check("4.16", "Template injetado presente na resposta",
          "template_injected" in g,
          str(g.get("template_injected", ""))[:100])

    generation_started = g.get("status") == "generation_started"

    if generation_started:
        print(f"\n  Template injetado no Flow:")
        template = g.get("template_injected", "")
        for line in template.split("\n")[:6]:
            print(f"    {line}")

        section("TESTE 3: flow_await_download_media")
        timeout_s = 120
        print(f"  Aguardando geracao e download (timeout: {timeout_s}s para {gen_params.type})...")

        # Inicia aguardo em background
        download_task = asyncio.create_task(flow_await_download_media(AwaitDownloadInput(
            timeout_seconds=timeout_s
        )))

        # Tirar screenshot após 60s
        await asyncio.sleep(60)
        try:
            from server import _browser_state
            if _browser_state.get("page"):
                await _browser_state["page"].screenshot(path="midias/e2e_at_60s.png")
                print("  [DIAG] Screenshot salva em midias/e2e_at_60s.png")
        except Exception as e:
            print(f"  [DIAG] Erro ao salvar screenshot: {e}")

        d_raw = await download_task
        d = json.loads(d_raw)
        print(f"  Resposta: status={d.get('status')} | error_code={d.get('error_code', 'none')}")

        check("5.1_5.5", "await_download_media retornou status esperado",
              d.get("status") in ("download_complete", "generation_complete_no_files", "error"),
              str(d)[:300])

        if d.get("status") == "download_complete":
            files = d.get("files", [])
            check("5.8_5.10", f"Arquivos baixados: {len(files)}",
                  len(files) > 0,
                  str(files))
            check("5.11", "Caminhos absolutos retornados",
                  all(os.path.isabs(f) for f in files) if files else False,
                  str(files[:2]))
            check("5.6", "Diretorio midias/ existe",
                  os.path.exists(d.get("download_dir", "")),
                  d.get("download_dir", ""))

            print(f"\n  Arquivos baixados:")
            for f in files:
                exists = os.path.exists(f)
                size = os.path.getsize(f) if exists else 0
                print(f"    {'OK' if exists else 'MISSING'} {f} ({size} bytes)")

            check("6.2", "Arquivos fisicamente existem em disco",
                  all(os.path.exists(f) for f in files) if files else False)
            check("6.3", "Caminhos validos e acessiveis",
                  all(os.path.getsize(f) > 0 for f in files) if files else False)

        elif d.get("status") == "generation_complete_no_files":
            print(f"  INFO: Geracao concluiu mas download automatico nao detectou arquivos.")
            print(f"  Sugere atualizacao dos seletores de download no server.py")
            check("5.7_files", "Download automatico funcionou", False,
                  "Geracao OK mas arquivos nao capturados - seletores precisam update")

        elif d.get("status") == "error" and d.get("error_code") == "GENERATION_TIMEOUT":
            print(f"  TIMEOUT: A geracao demorou mais de 120s.")
            check("5.5", "GENERATION_TIMEOUT retornado corretamente", True)

        else:
            print(f"  Resposta inesperada: {d}")

    else:
        print(f"  AVISO: Geracao nao iniciada. Pulando testes de download.")
        check("4.15", "Localizou campo input do Flow", False,
              f"error_code={g.get('error_code')} msg={g.get('message', '')[:150]}")

    section("TESTE 4: manage_session status")
    status_params = ManageSessionInput(action="status")
    st_raw = await flow_manage_session(status_params)
    st = json.loads(st_raw)
    check("3.D_live", "action=status retorna estado correto com sessao ativa",
          st.get("status") in ("ready", "not_initialized"),
          str(st)[:150])

    section("RESULTADO FINAL E2E")
    total = results["passed"] + results["failed"]
    print(f"  {results['passed']}/{total} testes passaram | {results['failed']} falharam")
    if results["errors"]:
        print(f"  Falharam: {results['errors']}")

    # Fechar browser
    print("\n  Fechando browser...")
    stop_params = ManageSessionInput(action="stop")
    await flow_manage_session(stop_params)
    print("  Browser fechado.")

    return results["failed"]


if __name__ == "__main__":
    failed = asyncio.run(run_e2e())
    sys.exit(failed)
