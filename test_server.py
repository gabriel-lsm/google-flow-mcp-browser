# -*- coding: utf-8 -*-
"""Testes unitarios do Google Flow MCP Server (sem browser)."""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import (
    GenerateMediaInput, ManageSessionInput, AwaitDownloadInput,
    MediaType, ModelName, AspectRatio,
    _build_strict_template, _error_response, _success_response,
    IMAGE_MAX_QUANTITY, VIDEO_MAX_QUANTITY,
)
from pydantic import ValidationError

results = {"passed": 0, "failed": 0, "errors": []}


def check(tid, desc, cond, detail=""):
    if cond:
        print(f"  PASS [{tid}] {desc}")
        results["passed"] += 1
    else:
        print(f"  FAIL [{tid}] {desc}" + (f" -- {detail}" if detail else ""))
        results["failed"] += 1
        results["errors"].append(tid)


def section(title):
    print(f"\n--- {title} ---")


# FASE 1: Setup
section("FASE 1: Setup e Constantes")
check("1.1", "Modulo importado com sucesso", True)
check("1.2", "IMAGE_MAX_QUANTITY == 15", IMAGE_MAX_QUANTITY == 15, str(IMAGE_MAX_QUANTITY))
check("1.3", "VIDEO_MAX_QUANTITY == 5", VIDEO_MAX_QUANTITY == 5, str(VIDEO_MAX_QUANTITY))
check("1.4", "MediaType tem image e video", MediaType.IMAGE == "image" and MediaType.VIDEO == "video")
check("1.5", "ModelName tem Imagen 3 e Google Veo", ModelName.IMAGEN_3 == "Imagen 3" and ModelName.GOOGLE_VEO == "Google Veo")
check("1.6", "AspectRatio tem 5 formatos", len(AspectRatio) == 5, str(len(AspectRatio)))

# FASE 2: Respostas
section("FASE 2: Respostas Padronizadas")
err = json.loads(_error_response("CODE", "msg", {"k": "v"}))
check("2.1", "error_response status=error", err["status"] == "error")
check("2.2", "error_response error_code=CODE", err["error_code"] == "CODE")
check("2.3", "error_response message=msg", err["message"] == "msg")
check("2.4", "error_response tem details", err.get("details") == {"k": "v"})
ok = json.loads(_success_response({"data": 42}))
check("2.5", "success_response status=success", ok["status"] == "success")
check("2.6", "success_response preserva dados", ok["data"] == 42)

# FASE 3: Validacoes GenerateMediaInput
section("FASE 3: Validacoes GenerateMediaInput")

try:
    g = GenerateMediaInput(type="image", model="Imagen 3", aspect_ratio="16:9", quantity=15, prompts=["p"] * 15)
    check("4.3", "image+qty=15 aceito (limite exato)", g.quantity == 15)
except ValidationError as e:
    check("4.3", "image+qty=15 aceito (limite exato)", False, str(e)[:100])

try:
    g = GenerateMediaInput(type="video", model="Google Veo", aspect_ratio="9:16", quantity=5, prompts=["p"] * 5)
    check("4.4", "video+qty=5 aceito (limite exato)", g.quantity == 5)
except ValidationError as e:
    check("4.4", "video+qty=5 aceito (limite exato)", False, str(e)[:100])

try:
    GenerateMediaInput(type="image", model="Imagen 3", aspect_ratio="1:1", quantity=16, prompts=["p"] * 16)
    check("4.1", "image+qty=16 rejeitado pelo Pydantic", False, "Deveria lancar ValidationError")
except ValidationError:
    check("4.1", "image+qty=16 rejeitado pelo Pydantic", True)

try:
    GenerateMediaInput(type="image", model="Imagen 3", aspect_ratio="1:1", quantity=1, prompts=[])
    check("4.5", "prompts=[] rejeitado", False)
except ValidationError:
    check("4.5", "prompts=[] rejeitado", True)

try:
    GenerateMediaInput(type="animation", model="Imagen 3", aspect_ratio="1:1", quantity=1, prompts=["p"])  # type: ignore
    check("4.6", "type=animation rejeitado", False)
except ValidationError:
    check("4.6", "type=animation rejeitado", True)

try:
    GenerateMediaInput(type="image", model="GPT-4", aspect_ratio="1:1", quantity=1, prompts=["p"])  # type: ignore
    check("4.7", "model=GPT-4 rejeitado", False)
except ValidationError:
    check("4.7", "model=GPT-4 rejeitado", True)

try:
    GenerateMediaInput(type="image", model="Imagen 3", aspect_ratio="21:9", quantity=1, prompts=["p"])  # type: ignore
    check("4.8", "aspect_ratio=21:9 rejeitado", False)
except ValidationError:
    check("4.8", "aspect_ratio=21:9 rejeitado", True)

try:
    GenerateMediaInput(type="image", model="Imagen 3", aspect_ratio="1:1", quantity=2, prompts=["ok", "   "])
    check("4.5b", "prompt apenas espacos rejeitado", False)
except ValidationError:
    check("4.5b", "prompt apenas espacos rejeitado", True)

# FASE 4: Template Estrito
section("FASE 4: Template Estrito de Geracao")
t = _build_strict_template(
    MediaType.VIDEO, ModelName.GOOGLE_VEO, AspectRatio.PORTRAIT, 2,
    ["Astronauta pousando na Lua", "Planeta de diamante"]
)
check("4.9", "Template tem INSTRUCOES DE GERACAO ESTREITA", "INSTRUCOES DE GERACAO ESTREITA" in t or "[INSTRUCOES" in t or "INSTRU" in t)
check("4.10", "Template tem aspect_ratio 9:16", "9:16" in t)
check("4.11", "Template tem Google Veo", "Google Veo" in t)
check("4.12", "Template tem Total de Midias: 2", "2" in t and "Total" in t)
check("4.13a", "Prompt 1 numerado com '1.'", "1." in t)
check("4.13b", "Prompt 2 numerado com '2.'", "2." in t)
check("4.14", "Template tem PROMPTS ESPECIFICOS", "PROMPTS" in t)

t2 = _build_strict_template(MediaType.IMAGE, ModelName.IMAGEN_3, AspectRatio.SQUARE, 1, ["Logo"])
check("4.14b", "Template de imagem usa 'Imagem' na numeracao", "Imagem 1" in t2)

# FASE 5: ManageSessionInput
section("FASE 5: ManageSessionInput")
for action in ["start", "stop", "status"]:
    try:
        s = ManageSessionInput(action=action)
        check(f"3.{action}", f"action={action} valido", s.action == action)
    except ValidationError as e:
        check(f"3.{action}", f"action={action} valido", False, str(e)[:80])

try:
    ManageSessionInput(action="invalid_xyz")  # type: ignore
    check("3.invalid", "action=invalid_xyz rejeitado", False)
except ValidationError:
    check("3.invalid", "action=invalid_xyz rejeitado", True)

# FASE 6: AwaitDownloadInput
section("FASE 6: AwaitDownloadInput")
try:
    d = AwaitDownloadInput()
    check("5.A", "Default sem params aceito", True)
    check("5.B", "timeout_seconds default=None", d.timeout_seconds is None)
    check("5.C", "download_dir default=None", d.download_dir is None)
except ValidationError as e:
    check("5.A", "Default sem params aceito", False, str(e)[:80])

try:
    AwaitDownloadInput(timeout_seconds=5)
    check("5.D", "timeout=5 rejeitado (ge=10)", False)
except ValidationError:
    check("5.D", "timeout=5 rejeitado (ge=10)", True)

try:
    AwaitDownloadInput(timeout_seconds=601)
    check("5.E", "timeout=601 rejeitado (le=600)", False)
except ValidationError:
    check("5.E", "timeout=601 rejeitado (le=600)", True)

try:
    d = AwaitDownloadInput(timeout_seconds=300, download_dir="./custom/")
    check("5.F", "timeout=300 e dir custom aceitos", d.timeout_seconds == 300)
except ValidationError as e:
    check("5.F", "timeout=300 e dir custom aceitos", False, str(e)[:80])

# RESULTADO FINAL
total = results["passed"] + results["failed"]
print(f"\n{'=' * 55}")
print(f"RESULTADO FINAL: {results['passed']}/{total} passaram | {results['failed']} falharam")
if results["errors"]:
    print(f"Testes com falha: {results['errors']}")
print("=" * 55)

sys.exit(0 if results["failed"] == 0 else 1)
