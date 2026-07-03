# -*- coding: utf-8 -*-
"""Testes de logica das tools do MCP sem necessidade de browser aberto."""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import (
    flow_generate_media, flow_manage_session,
    GenerateMediaInput, ManageSessionInput,
)

results = {"passed": 0, "failed": 0}


def check(tid, desc, cond, detail=""):
    if cond:
        print(f"  PASS [{tid}] {desc}")
        results["passed"] += 1
    else:
        print(f"  FAIL [{tid}] {desc}" + (f" -- {detail}" if detail else ""))
        results["failed"] += 1


async def run_tests():
    print("\n--- FASE TOOLS: Logica pre-browser ---")

    # 4.2: video + qty=6 deve retornar QUOTA_EXCEEDED (tool verifica antes do browser)
    params = GenerateMediaInput(
        type="video", model="Google Veo",
        aspect_ratio="9:16", quantity=6, prompts=["p"] * 6
    )
    r = json.loads(await flow_generate_media(params))
    check("4.2", "video+qty=6 -> QUOTA_EXCEEDED", r.get("error_code") == "QUOTA_EXCEEDED", str(r)[:120])

    # MODEL_MISMATCH: image + Google Veo
    params = GenerateMediaInput(
        type="image", model="Google Veo",
        aspect_ratio="1:1", quantity=1, prompts=["p"]
    )
    r = json.loads(await flow_generate_media(params))
    check("4.M1", "image+Veo -> MODEL_MISMATCH", r.get("error_code") == "MODEL_MISMATCH", str(r)[:120])

    # MODEL_MISMATCH: video + Imagen 3
    params = GenerateMediaInput(
        type="video", model="Imagen 3",
        aspect_ratio="9:16", quantity=1, prompts=["p"]
    )
    r = json.loads(await flow_generate_media(params))
    check("4.M2", "video+Imagen3 -> MODEL_MISMATCH", r.get("error_code") == "MODEL_MISMATCH", str(r)[:120])

    # SESSION_NOT_READY: gerar sem sessao iniciada
    params = GenerateMediaInput(
        type="image", model="Imagen 3",
        aspect_ratio="1:1", quantity=1, prompts=["test"]
    )
    r = json.loads(await flow_generate_media(params))
    check("4.21", "generate sem sessao -> SESSION_NOT_READY", r.get("error_code") == "SESSION_NOT_READY", str(r)[:120])

    # manage_session status sem sessao = not_initialized
    s = ManageSessionInput(action="status")
    r = json.loads(await flow_manage_session(s))
    check("3.status_cold", "status sem sessao = not_initialized", r.get("status") == "not_initialized", str(r)[:120])

    # manage_session stop sem browser = deve retornar stopped sem crash
    s = ManageSessionInput(action="stop")
    r = json.loads(await flow_manage_session(s))
    check("3.stop_clean", "stop sem browser = stopped", r.get("status") == "stopped", str(r)[:120])

    total = results["passed"] + results["failed"]
    print(f"\nRESULTADO: {results['passed']}/{total} passaram | {results['failed']} falharam")
    return results["failed"]


if __name__ == "__main__":
    failed = asyncio.run(run_tests())
    sys.exit(failed)
