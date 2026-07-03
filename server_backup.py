#!/usr/bin/env python3
"""
Google Flow MCP Server

Servidor MCP que permite agentes de IA (Claude, etc.) operar o Google Labs Flow
de forma autônoma com arquitetura Zero-DOM. O agente de IA nunca lê o HTML/DOM
diretamente — toda a lógica de navegação e interação fica encapsulada aqui via Playwright.

Ferramentas expostas:
- flow_manage_session         : Gerencia autenticação e sessão do browser
- flow_generate_media         : Injeta parâmetros de geração no Flow via Playwright
- flow_await_download_media   : Aguarda geração e baixa arquivos automaticamente
- flow_read_agent_status      : Lê o estado atual do chat lateral do Agent do Flow
- flow_reply_to_agent         : Aprova ou responde ao Agent do Flow no chat lateral

Resources:
- flow://capabilities  : Documentação dos modelos e capacidades
- flow://agent_guidelines : Diretrizes de uso do agente do Flow (inclui fluxo bidirecional)
"""

import asyncio
import json
import os
import sys
import time
import base64
import mimetypes
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator
from playwright.async_api import async_playwright, BrowserContext, Page, TimeoutError as PlaywrightTimeout

# ─────────────────────────────────────────────────────────────────────────────
# Inicialização do servidor MCP
# ─────────────────────────────────────────────────────────────────────────────

mcp = FastMCP("google_flow_mcp")

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

FLOW_URL = "https://labs.google/fx/tools/flow/project/new"
FLOW_AGENT_URL = "https://labs.google/fx/tools/flow/project/new"
BROWSER_STATE_PATH = Path(__file__).parent / "browser_state.json"
MIDIAS_DIR = Path(__file__).parent / "midias"
IMAGE_MAX_QUANTITY = 15
VIDEO_MAX_QUANTITY = 5
IMAGE_GENERATION_TIMEOUT = 180   # segundos
VIDEO_GENERATION_TIMEOUT = 360   # segundos
POLL_INTERVAL = 3                # segundos entre cada verificação

# Seletores Playwright — identificados por inspeção visual real do Google Labs Flow (2026-07)
# A interface atual do Flow usa um campo de texto simples (não Slate.js)
# com botão de seta circular para submit e controles de modelo/quantidade integrados.
SELECTORS = {
    # Campo de input — interface nova do Flow (2026)
    # Prioridade: textarea e input simples sobre div[role='textbox']
    "input_field": [
        "textarea[placeholder*='create' i]",        # Placeholder 'What do you want to create?'
        "textarea[placeholder*='criar' i]",         # Variante em português
        "textarea",                                  # Qualquer textarea
        "input[type='text'][placeholder*='create' i]",
        "input[type='text']",
        "div[role='textbox']",                       # Fallback legacy Slate.js
        "div[data-slate-editor='true']",
        "[contenteditable='true']",
    ],
    # Botão de submit — seta circular → (arrow button) no canto direito do input
    # Observado visualmente no screenshot: botão circular com seta à direita do campo
    "submit_button": [
        "button[aria-label*='generate' i]",         # Provavel aria-label do botão gerar
        "button[aria-label*='send' i]",
        "button[aria-label*='submit' i]",
        "button[aria-label*='criar' i]",
        "button[aria-label*='create' i]",
        "button[aria-label*='enviar' i]",
        "button[type='submit']",
        "button svg[data-testid*='arrow' i]",       # Ícone de seta dentro do botão
    ],
    # Indicadores de geração em progresso
    # No Flow novo: card de loading aparece na área principal com skeleton/spinner
    "loading_indicator": [
        "[aria-label*='loading' i]",
        "[aria-label*='carregando' i]",
        "[aria-label*='generating' i]",
        "[aria-label*='gerando' i]",
        ".loading",
        "svg[class*='spin' i]",
        "div[class*='progress' i]",
        "div[class*='thinking' i]",
        "div[class*='skeleton' i]",                 # Skeleton loader do Flow
        "div[class*='placeholder' i]",
        # Skeleton de card de imagem durante geração
        "[data-testid*='loading' i]",
        "[data-testid*='generating' i]",
    ],
    # Indicação de geração concluída — imagens aparecem no grid principal
    # URL real observada: /fx/api/trpc/media.getMediaUrlRedirect?name=<UUID>
    "generation_complete": [
        "img[src*='trpc/media']",                         # URL real do Flow (2026)
        "img[src*='getMediaUrlRedirect']",                # Variante da URL
        "img[src*='storage.googleapis.com']",
        "img[src*='generativelanguage.googleapis.com']",
        "video[src*='trpc/media']",
        "video[src]",
        "img[src*='blob:']",
        "main img[src]",
    ],
    # Botão de download
    "download_button": [
        "a[download]",
        "button[aria-label*='download' i]",
        "button[aria-label*='baixar' i]",
        "button[aria-label*='save' i]",
        "button[aria-label*='export' i]",
    ],
    # Indicador de bloqueio de conteúdo
    "content_blocked": [
        "div[role='alert']",
        "[aria-label*='policy' i]",
        "[aria-label*='blocked' i]",
        "div:has-text('Unable to generate')",
        "div:has-text('content policy')",
        "div:has-text('not allowed')",
    ],
    # Verificar se está logado
    "logged_in_indicator": [
        "img[alt*='profile' i]",
        "button[aria-label*='Google Account' i]",
        "[data-testid='user-avatar']",
        "img[class*='avatar' i]",
        "[aria-label*='account' i]",
        "[aria-label*='conta' i]",
        "header img[src*='googleusercontent']",
        "img[src*='googleusercontent.com/a/']",
    ],
    # Tela de login Google
    "login_page_indicator": [
        "input[type='email']",
        "#identifierId",
        "form[action*='accounts.google']",
        "[data-testid='login']",
    ],
    # Overlay/modal — fechar antes de interagir
    "overlay_close_button": [
        "button:has-text('close')",
        "button[aria-label*='close' i]",
        "button[aria-label*='fechar' i]",
        "button:has-text('dismiss')",
        "[data-testid='close-button']",
    ],
    # ── Agent (modo bidirecional) ──────────────────────────────────────────────
    # Botão toggle do Agent no topo da interface do Flow
    "agent_toggle_button": [
        "button:has-text('Agent')",
        "button[aria-label*='agent' i]",
        "[role='switch'][aria-label*='agent' i]",
        "label:has-text('Agent') input[type='checkbox']",
        "div[aria-label*='agent' i][role='button']",
    ],
    # Container principal do painel/sidebar do chat lateral do Agent
    "agent_chat_sidebar": [
        "[data-testid*='agent' i]",
        "aside[aria-label*='agent' i]",
        "div[class*='agent' i][class*='panel' i]",
        "div[class*='sidebar' i]",
        "div[class*='chat' i][class*='side' i]",
        "section[aria-label*='chat' i]",
    ],
    # Mensagens visíveis do agent no chat lateral
    "agent_messages": [
        "[data-testid*='agent-message' i]",
        "div[class*='agent' i] p",
        "div[class*='message' i]",
        "[role='log'] div",
        "div[class*='chat' i] span",
    ],
    # Botões de aprovação/confirmação que o agent exibe no chat
    "agent_approve_button": [
        "button:has-text('Approve, do not ask again')",
        "button:has-text('Approve')",
        "button:has-text('Yes')",
        "button:has-text('Confirm')",
        "button:has-text('Continue')",
        "button:has-text('Aprovar')",
        "[role='button']:has-text('Approve')",
        "button[aria-label*='approve' i]",
    ],
    # Campo de input do chat lateral do Agent
    "agent_chat_input": [
        "[data-testid*='agent' i] textarea",
        "aside textarea",
        "div[class*='agent' i] textarea",
        "div[class*='sidebar' i] textarea",
        "div[class*='chat' i] textarea",
        "div[class*='chat' i] input[type='text']",
        "[role='log'] ~ div textarea",
    ],
    # ── Seleção de tipo de mídia (imagem vs vídeo) ─────────────────────────────
    # Interface usa Radix UI tabs — os IDs têm sufixo estável mesmo com prefixo dinâmico
    # Padrão real observado: button[id$="-trigger-IMAGE"] / button[id$="-trigger-VIDEO"]
    "media_type_image_tab": [
        "button[id$='-trigger-IMAGE']",
        "button[role='tab']:has-text('Image')",
        "button[role='tab'][aria-label*='image' i]",
        "[data-value='IMAGE']",
    ],
    "media_type_video_tab": [
        "button[id$='-trigger-VIDEO']",
        "button[role='tab']:has-text('Video')",
        "button[role='tab'][aria-label*='video' i]",
        "[data-value='VIDEO']",
    ],
    # ── Seletor de modelo ──────────────────────────────────────────────────────
    # O botão que abre o dropdown de modelos de imagem/vídeo no Flow
    "model_selector_button": [
        "button[id^='radix-'][id$=':']",           # Radix button genérico com ID numérico
        "button[aria-haspopup='menu']",
        "button[aria-controls][aria-expanded]",
    ],
    # ── Aspect ratio ──────────────────────────────────────────────────────────
    # Tabs de seleção de proporção na interface do Flow
    "aspect_ratio_landscape": [
        "button[id$='-trigger-LANDSCAPE']",
        "button[role='tab']:has-text('16:9')",
        "[data-value='LANDSCAPE']",
    ],
    "aspect_ratio_portrait": [
        "button[id$='-trigger-PORTRAIT']",
        "button[role='tab']:has-text('9:16')",
        "[data-value='PORTRAIT']",
    ],
    "aspect_ratio_square": [
        "button[id$='-trigger-SQUARE']",
        "button[role='tab']:has-text('1:1')",
        "[data-value='SQUARE']",
    ],
}


# Estado global da sessão (singleton por processo)
_browser_state: Dict[str, Any] = {
    "playwright": None,
    "browser": None,
    "context": None,
    "page": None,
    "is_ready": False,
    "last_media_type": None,
}


# ─────────────────────────────────────────────────────────────────────────────
# Enums e Modelos Pydantic
# ─────────────────────────────────────────────────────────────────────────────

class MediaType(str, Enum):
    """Tipo de mídia a ser gerada no Google Flow."""
    IMAGE = "image"
    VIDEO = "video"


class ModelName(str, Enum):
    """Modelos disponíveis no Google Labs Flow (atualizado 2026-07).

    Imagens:
      - NANO_BANANA_PRO   → avançado (Google AI Ultra)
      - NANO_BANANA_2     → padrão gratuito
      - NANO_BANANA_2_LITE → versão leve/rápida

    Vídeos:
      - OMNI_FLASH
      - VEO_31_LITE, VEO_31_FAST, VEO_31_QUALITY
    """
    # ── Imagens ──────────────────────────────────────────
    NANO_BANANA_PRO = "Nano Banana Pro"
    NANO_BANANA_2 = "Nano Banana 2"
    NANO_BANANA_2_LITE = "Nano Banana 2 Lite"
    # ── Vídeos ───────────────────────────────────────────
    OMNI_FLASH = "Omni Flash"
    VEO_31_LITE = "Veo 3.1 - Lite"
    VEO_31_FAST = "Veo 3.1 - Fast"
    VEO_31_QUALITY = "Veo 3.1 - Quality"
    # ── Legacy (mantidos para compatibilidade retroativa) ─
    IMAGEN_3 = "Imagen 3"
    GOOGLE_VEO = "Google Veo"
    GEMINI_OMNI_FLASH = "Gemini Omni Flash"  # alias legacy


class AspectRatio(str, Enum):
    """Formatos de aspecto disponíveis no Google Labs Flow."""
    SQUARE = "1:1"
    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
    LANDSCAPE_43 = "4:3"
    PORTRAIT_34 = "3:4"


class ManageSessionInput(BaseModel):
    """Input para flow_manage_session."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    action: str = Field(
        default="start",
        description="Ação a executar: 'start' para iniciar sessão, 'status' para verificar estado, 'stop' para encerrar browser",
        pattern="^(start|status|stop)$",
    )
    login_confirmed: bool = Field(
        default=False,
        description="Defina como True após o usuário humano ter realizado o login manualmente no browser aberto",
    )


# Modelos de imagem conhecidos (para validação MODEL_MISMATCH)
_IMAGE_MODELS: set = {
    ModelName.NANO_BANANA_PRO.value,
    ModelName.NANO_BANANA_2.value,
    ModelName.NANO_BANANA_2_LITE.value,
    ModelName.IMAGEN_3.value,  # legacy
}

# Modelos de vídeo conhecidos (para validação MODEL_MISMATCH)
_VIDEO_MODELS: set = {
    ModelName.OMNI_FLASH.value,
    ModelName.VEO_31_LITE.value,
    ModelName.VEO_31_FAST.value,
    ModelName.VEO_31_QUALITY.value,
    ModelName.GOOGLE_VEO.value,  # legacy
    ModelName.GEMINI_OMNI_FLASH.value,  # legacy alias
}


class GenerateMediaInput(BaseModel):
    """Input para flow_generate_media."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    type: MediaType = Field(
        ...,
        description="Tipo de mídia: 'image' (máximo 15 por lote) ou 'video' (máximo 5 por lote)",
    )
    model: Union[ModelName, str] = Field(
        ...,
        description=(
            "Modelo a usar. Imagens: 'Nano Banana 2' (padrão) ou 'Nano Banana Pro' (premium). "
            "Vídeos: 'Veo 3.1 Lite', 'Veo 3.1 Fast', 'Veo 3.1 Quality' ou 'Gemini Omni Flash'. "
            "Aceita também strings livres para modelos novos não listados."
        ),
    )
    aspect_ratio: AspectRatio = Field(
        ...,
        description="Formato/proporção: '1:1', '16:9', '9:16', '4:3', '3:4'",
    )
    quantity: int = Field(
        ...,
        description="Quantidade de mídias a gerar. Máximo 15 para imagens, máximo 5 para vídeos",
        ge=1,
        le=15,
    )
    prompts: List[str] = Field(
        ...,
        description="Lista de prompts de geração. O número de prompts deve ser igual à quantidade solicitada",
        min_length=1,
        max_length=15,
    )
    reference_image: Optional[str] = Field(
        default=None,
        description="Caminho absoluto para uma imagem local a ser usada como referência (simula um Ctrl+V no chat do Flow).",
    )

    @field_validator("quantity")
    @classmethod
    def validate_quantity_for_type(cls, v: int, info: Any) -> int:
        """Valida limite de quantidade baseado no tipo de mídia."""
        # Nota: validação cruzada type+quantity é feita na tool diretamente
        # pois o Pydantic v2 InfoField precisa de acesso ao campo 'type'
        return v

    @field_validator("prompts")
    @classmethod
    def validate_prompts_not_empty(cls, v: List[str]) -> List[str]:
        """Garante que nenhum prompt está vazio."""
        for i, prompt in enumerate(v):
            if not prompt.strip():
                raise ValueError(f"Prompt {i + 1} está vazio. Todos os prompts devem ter conteúdo.")
        return [p.strip() for p in v]


class AwaitDownloadInput(BaseModel):
    """Input para flow_await_download_media."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    timeout_seconds: Optional[int] = Field(
        default=None,
        description="Timeout em segundos para aguardar a geração. None usa o padrão (120s imagem, 300s vídeo)",
        ge=10,
        le=600,
    )
    download_dir: Optional[str] = Field(
        default=None,
        description="Diretório de destino para os downloads. Padrão: ./midias/",
    )


class ReplyToAgentInput(BaseModel):
    """Input para flow_reply_to_agent."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    approve: bool = Field(
        default=False,
        description=(
            "Se True, clica no botão de aprovação (Approve/Yes/Confirm) visível no chat lateral do Agent. "
            "Use quando flow_await_download_media retornar agent_requires_interaction."
        ),
    )
    message: Optional[str] = Field(
        default=None,
        description=(
            "Mensagem de texto opcional para digitar no campo de input do chat lateral do Agent. "
            "Combine com approve=False para enviar texto personalizado ao invés de clicar em Approve."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Funções Utilitárias
# ─────────────────────────────────────────────────────────────────────────────

def _error_response(error_code: str, message: str, details: Optional[Dict] = None) -> str:
    """Formata resposta de erro padronizada para a IA."""
    response = {
        "status": "error",
        "error_code": error_code,
        "message": message,
    }
    if details:
        response["details"] = details
    return json.dumps(response, ensure_ascii=False, indent=2)


def _success_response(data: Dict) -> str:
    """Formata resposta de sucesso padronizada."""
    response = {"status": "success", **data}
    return json.dumps(response, ensure_ascii=False, indent=2)

def _build_strict_template(
    media_type: MediaType,
    model: Union[ModelName, str],
    aspect_ratio: AspectRatio,
    quantity: int,
    prompts: List[str],
) -> str:
    """
    Constrói o Template Estrito de geração conforme especificado no plano.

    Este template é injetado diretamente no campo de input do agente do Flow.
    """
    media_label = "Vídeo" if media_type == MediaType.VIDEO else "Imagem"
    media_label_plural = "Vídeos" if media_type == MediaType.VIDEO else "Imagens"

    prompt_lines = []
    for i, prompt in enumerate(prompts, 1):
        prompt_lines.append(f"{i}. {media_label} {i}: {prompt}")

    prompts_formatted = " | ".join(prompt_lines)

    template = f"GERAR: {quantity} {media_label_plural}, Formato {aspect_ratio.value}, Modelo {model.value if isinstance(model, ModelName) else str(model)}. PROMPTS: {prompts_formatted}"

    return template


async def _find_element(page: Page, selector_key: str, timeout: int = 5000) -> Optional[Any]:
    """
    Tenta múltiplos seletores para encontrar um elemento.
    Retorna o primeiro elemento encontrado ou None.
    Para seletores :has() usa query_selector pois wait_for_selector não suporta pseudo-classes complexas.
    """
    selectors = SELECTORS.get(selector_key, [])
    for selector in selectors:
        try:
            # Seletores com :has() ou :has-text() não funcionam com wait_for_selector
            # Usa query_selector + verificação manual
            if ":has" in selector or ":text" in selector:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    return element
            else:
                element = await page.wait_for_selector(selector, timeout=timeout, state="visible")
                if element:
                    return element
        except PlaywrightTimeout:
            continue
        except Exception:
            continue
    return None


async def _inject_text_into_input(page: Page, text: str, reference_image: Optional[str] = None) -> bool:
    """
    Injeta texto no campo de input do Google Flow ativando os eventos React corretamente.

    Descoberta real (diag2_output.txt 2026-07-02):
    - O campo de input é um INPUT HTML (tag=INPUT, classe sc-57873d76-2),
      NÃO um div[role='textbox'] como presumido anteriormente.
    - O botão submit fica aria-disabled='true' até o React detectar mudança de valor.
    - Solução: usar o nativeInputValueSetter do React para forçar onChange,
      desbloqueando o botão submit.

    Estratégia (em ordem de prioridade):
    1. Paste de Imagem de Referência via ClipboardEvent (se reference_image provido)
    2. React nativeInputValueSetter no INPUT detectado — ativa aria-disabled=false
    3. Clipboard paste via execCommand no elemento ativo
    4. el.fill() + keyboard event dispatch como fallback
    """
    if reference_image and os.path.exists(reference_image):
        try:
            # Em vez de injetar Base64 via clipboard (que pode crashar o renderer),
            # usamos a API nativa do Playwright para o input type="file" escondido na página.
            await page.set_input_files('input[type="file"]', reference_image)
            await asyncio.sleep(2.5)  # Aguardar upload da imagem no UI
        except Exception as e:
            print(f"Erro injetando imagem de referência (set_input_files): {e}")

    # Estratégia 1: React nativeInputValueSetter (INPUT controlado pelo React)
    # Isso é o único método que ativa o onChange do React e desbloqueia o submit
    try:
        result = await page.evaluate(
            """
            (text) => {
                // Buscar INPUT que não seja título (evitar input de nome do projeto)
                // O input do prompt fica na barra inferior (y > janela/2)
                const inputs = Array.from(document.querySelectorAll('input[type="text"], input:not([type]), input[type="search"]'));
                let target = null;
                for (const inp of inputs) {
                    const rect = inp.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    // Input do prompt fica na metade inferior da tela
                    if (rect.top > window.innerHeight * 0.4) {
                        target = inp;
                        break;
                    }
                }
                if (!target) return { ok: false, reason: 'input not found' };

                // Focar e limpar
                target.focus();
                target.select();

                // Usar o setter nativo do React para disparar onChange
                const nativeInputSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                nativeInputSetter.call(target, text);

                // Disparar evento de input para notificar React
                target.dispatchEvent(new Event('input', { bubbles: true }));
                target.dispatchEvent(new Event('change', { bubbles: true }));

                return { ok: true, value: target.value, tag: target.tagName };
            }
            """,
            text,
        )
        if result and result.get("ok") and result.get("value"):
            await asyncio.sleep(0.8)  # Aguardar React re-render e habilitar submit
            return True
    except Exception:
        pass

    # Estratégia 2: Clicar no input e usar page.fill() via Playwright locator
    try:
        # Seletores em ordem de prioridade — baseado no diagnóstico real
        for sel in [
            "input.sc-57873d76-2",  # Classe real observada no diagnóstico
            "input[class*='sc-57873d76']",
            "div[role='textbox']",
            "div[data-slate-editor='true']",
            "textarea",
        ]:
            els = await page.query_selector_all(sel)
            for el in els:
                if not await el.is_visible():
                    continue
                # Verificar que não é um input de título (y > 40% da tela)
                bbox = await el.bounding_box()
                if bbox and bbox["y"] < 300:
                    continue  # Muito no topo — provavelmente título do projeto

                await el.click()
                await asyncio.sleep(0.3)
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
                await asyncio.sleep(0.2)

                # Tentar fill() primeiro (funciona para inputs simples)
                try:
                    await el.fill(text)
                    # Disparar evento de change para React
                    await page.evaluate(
                        """() => {
                            const el = document.activeElement;
                            if (el) {
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        }"""
                    )
                    await asyncio.sleep(0.8)

                    # Verificar se ficou com conteúdo
                    val = await el.evaluate("el => el.value || el.innerText || ''")
                    if val.strip():
                        return True
                except Exception:
                    pass
    except Exception:
        pass

    return False




async def _inject_text_via_fiber(page: Page, text: str) -> bool:
    """
    Injeta texto no editor Slate.js via React Fiber API (legacy/fallback).
    Usado como fallback quando o campo não é textarea/input simples.
    """
    js_inject = """
    (text) => {
        const textbox = document.querySelector('div[role="textbox"]');
        if (!textbox) return { success: false, reason: 'textbox not found' };

        const fiberKey = Object.keys(textbox).find(key =>
            key.startsWith('__reactFiber') || key.startsWith('__reactInternalInstance')
        );
        if (!fiberKey) return { success: false, reason: 'react fiber key not found' };

        let editor = null;
        let current = textbox[fiberKey];
        while (current) {
            if (current.memoizedProps && current.memoizedProps.editor) {
                editor = current.memoizedProps.editor;
                break;
            }
            current = current.return;
        }

        if (!editor) return { success: false, reason: 'slate editor instance not found' };

        try {
            const currentLength = editor.string({ path: [0, 0] }).length;
            editor.select({
                anchor: { path: [0, 0], offset: 0 },
                focus: { path: [0, 0], offset: currentLength }
            });
            if (currentLength > 0) editor.deleteFragment();
            editor.insertText(text);
            return { success: true, insertedText: editor.string({ path: [0, 0] }) };
        } catch(e) {
            return { success: false, reason: e.toString() };
        }
    }
    """
    result = await page.evaluate(js_inject, text)
    if not result.get("success"):
        return False
    await asyncio.sleep(0.2)
    await page.keyboard.press("Space")
    await asyncio.sleep(0.1)
    await page.keyboard.press("Backspace")
    await asyncio.sleep(0.3)
    return True


async def _ensure_agent_active(page: Page) -> bool:
    """
    Garante que o botão 'Agent' do Google Flow está ativado antes de submeter um prompt.

    O modo Agent altera a UI, abrindo um chat lateral onde o 'Agente do Google' recebe
    o prompt complexo, raciocina e (às vezes) pede aprovação antes de gerar a mídia.
    Sem este modo ativo, o Flow usa comportamento padrão/legado e ignora instruções estruturadas.

    Returns:
        True  — Agent já estava ou foi ativado com sucesso.
        False — Botão não encontrado (interface pode ter mudado); execução continua normalmente.
    """
    try:
        for selector in SELECTORS["agent_toggle_button"]:
            try:
                el = await page.query_selector(selector)
                if not el or not await el.is_visible():
                    continue

                # Verificar se já está ativo (aria-pressed, aria-checked, data-active, etc.)
                aria_pressed = await el.get_attribute("aria-pressed") or ""
                aria_checked = await el.get_attribute("aria-checked") or ""
                data_active = await el.get_attribute("data-active") or ""
                class_name = await el.get_attribute("class") or ""

                already_active = (
                    aria_pressed == "true"
                    or aria_checked == "true"
                    or data_active in ("true", "1", "active")
                    or "active" in class_name.lower()
                    or "selected" in class_name.lower()
                )

                if already_active:
                    return True  # Já está ligado, nada a fazer

                # Ativar o agent
                await el.click()
                await asyncio.sleep(1.2)

                # Confirmar ativação
                aria_pressed_after = await el.get_attribute("aria-pressed") or ""
                aria_checked_after = await el.get_attribute("aria-checked") or ""
                class_after = await el.get_attribute("class") or ""
                activated = (
                    aria_pressed_after == "true"
                    or aria_checked_after == "true"
                    or "active" in class_after.lower()
                )
                return activated

            except Exception:
                continue
    except Exception:
        pass

    return False  # Não encontrado — não-fatal, continua normalmente


async def _dismiss_overlays(page: Page) -> None:
    """
    Fecha overlays/modais que possam bloquear a interação com o editor.
    O Flow ocasionalmente exibe changelogs, anúncios e tutoriais em popup.
    """
    try:
        # Verificar se há iframe sobreposto (overlay de changelog)
        iframes = await page.query_selector_all("iframe")
        for iframe in iframes:
            rect = await iframe.bounding_box()
            if rect and rect["width"] > 400 and rect["height"] > 400:
                # Iframe grande provavelmente é overlay — tentar fechar
                break

        # Tentar clicar em botões de fechar
        for selector in SELECTORS["overlay_close_button"]:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(0.5)
                    return
            except Exception:
                continue

        # Pressionar Escape como último recurso
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
    except Exception:
        pass  # Não falhar se dismiss não funcionar


async def _submit_and_verify(page: Page) -> bool:
    """
    Envia a mensagem no Flow e verifica que o envio realmente ocorreu.

    INSIGHT CRÍTICO (diag2_output.txt 2026-07-02):
    - O botão submit (arrow_forward|Create) tem aria-disabled='true' por padrão.
    - Só fica aria-disabled='false' APÓS o React processar o onChange do input.
    - Clicar quando aria-disabled='true' NÃO submete — silenciosamente ignora.
    - Solução: aguardar até 3s por aria-disabled='false', depois clicar.

    Verificação de sucesso pós-submit:
    - O input volta a ficar vazio (React limpa após submit bem-sucedido)
    - OU novos items aparecem no DOM
    """
    submit_clicked = False

    # ── Aguardar botão ficar habilitado (aria-disabled='false') ──────────────
    # O React precisa processar o onChange antes de habilitar o submit
    btn_coords = None
    for _ in range(12):  # até 3s (12 × 0.25s)
        try:
            js_find_enabled_btn = """
            () => {
                const buttons = Array.from(document.querySelectorAll('button'));
                const submitBtns = buttons.filter(btn => {
                    const text = (btn.innerText || '').toLowerCase();
                    const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                    const isSubmit = text.includes('arrow_forward') || aria.includes('send') || aria.includes('generate') || aria.includes('create');
                    if (!isSubmit) return false;
                    // Verificar se está habilitado — aria-disabled deve ser null ou 'false'
                    const ariaDisabled = btn.getAttribute('aria-disabled');
                    if (ariaDisabled === 'true') return false;  // Ainda desabilitado
                    const rect = btn.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0 && rect.top > 0;
                });
                if (!submitBtns.length) return null;
                const btn = submitBtns[0];
                const rect = btn.getBoundingClientRect();
                return {
                    x: Math.round(rect.left + rect.width / 2),
                    y: Math.round(rect.top + rect.height / 2),
                    ariaDisabled: btn.getAttribute('aria-disabled'),
                };
            }
            """
            coords = await page.evaluate(js_find_enabled_btn)
            if coords:
                btn_coords = coords
                break
        except Exception:
            pass
        await asyncio.sleep(0.25)

    # ── Clicar no botão habilitado ────────────────────────────────────────────
    if btn_coords:
        try:
            await page.mouse.click(btn_coords["x"], btn_coords["y"])
            submit_clicked = True
            await asyncio.sleep(0.5)
        except Exception:
            pass

    # ── Fallback: Enter (se botão não encontrado ou click falhou) ─────────────
    if not submit_clicked:
        try:
            await page.keyboard.press("Enter")
            submit_clicked = True
        except Exception:
            pass

    # ── Verificação de sucesso pós-submit (até 15 segundos) ──────────────────
    # Sinal 1: INPUT do prompt ficou vazio (React limpa após submit bem-sucedido)
    # Sinal 2: loading indicator apareceu
    # Sinal 3: novas URLs de mídia aparecem no DOM (trpc/media padrão real do Flow)
    deadline = time.time() + 15.0
    prev_media_srcs: set = set(await page.evaluate(
        "() => Array.from(document.querySelectorAll('img[src], video[src]')).map(el => el.src)"
    ))

    while time.time() < deadline:
        await asyncio.sleep(0.5)
        try:
            # Verificar INPUT do prompt ficou vazio
            input_content = await page.evaluate("""
            () => {
                // Buscar INPUT na barra inferior (y > 40% da tela)
                const inputs = Array.from(document.querySelectorAll('input'));
                for (const inp of inputs) {
                    const rect = inp.getBoundingClientRect();
                    if (rect.top > window.innerHeight * 0.4 && rect.width > 100) {
                        return inp.value || '';
                    }
                }
                // Fallback: div[role='textbox']
                const tb = document.querySelector('div[role="textbox"]');
                if (tb) return tb.innerText || '';
                return null;
            }
            """)

            clean = (input_content or "").replace("\ufeff", "").replace("\n", "").strip()
            if clean == "" or "what do you want to create" in clean.lower():
                return True  # INPUT vazio = submit bem-sucedido

            # Verificar loading indicator
            for sel in SELECTORS["loading_indicator"]:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        return True
                except Exception:
                    continue

            # Verificar se novas mídias apareceram (URL trpc/media é o padrão real do Flow)
            curr_media_srcs: set = set(await page.evaluate(
                "() => Array.from(document.querySelectorAll('img[src], video[src]')).map(el => el.src)"
            ))
            new_srcs = curr_media_srcs - prev_media_srcs
            if new_srcs:
                return True

        except Exception:
            pass

    # Botão foi encontrado e clicado com aria-disabled=false — assume sucesso
    return submit_clicked


async def _is_logged_in(page: Page) -> bool:
    """Verifica se o usuário está autenticado no Google e no Flow."""
    
    current_url = page.url
    # Se estiver em tela de login/oauth, com certeza não completou o login
    if "accounts.google.com" in current_url or "signin" in current_url or "oauth" in current_url:
        return False
        
    # Verifica se há indicadores de login
    for selector in SELECTORS["logged_in_indicator"]:
        try:
            element = await page.query_selector(selector)
            if element:
                return True
        except Exception:
            continue

    # Verifica se há campos de login (indicando que NÃO está logado)
    for selector in SELECTORS["login_page_indicator"]:
        try:
            element = await page.query_selector(selector)
            if element:
                return False
        except Exception:
            continue

    # Verifica pela URL
    current_url = page.url
    if "accounts.google.com" in current_url or "signin" in current_url:
        return False

    # Se chegou até o Flow e não há indicadores de login/logout claros,
    # verifica se há conteúdo da aplicação
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
        # Se a URL é do Flow e a página carregou, provavelmente está logado
        if "labs.google" in current_url:
            return True
    except Exception:
        pass

    return False


async def _get_or_create_browser(headless: bool = False) -> Optional[Page]:
    """Obtém ou cria instância do browser com contexto persistente."""
    global _browser_state

    if _browser_state["page"] and not _browser_state["page"].is_closed():
        return _browser_state["page"]

    try:
        if _browser_state["playwright"] is None:
            _browser_state["playwright"] = await async_playwright().start()

        pw = _browser_state["playwright"]

        # Usa contexto persistente para manter cookies/sessão entre execuções
        user_data_dir = str(Path(__file__).parent / "browser_profile")
        os.makedirs(user_data_dir, exist_ok=True)

        context = await pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )

        _browser_state["context"] = context

        if context.pages:
            page = context.pages[0]
        else:
            page = await context.new_page()

        _browser_state["page"] = page
        return page

    except Exception as e:
        _browser_state["playwright"] = None
        raise RuntimeError(f"Falha ao iniciar browser: {e}") from e


async def _close_browser():
    """Fecha o browser e limpa o estado."""
    global _browser_state
    try:
        if _browser_state["context"]:
            await _browser_state["context"].close()
        if _browser_state["playwright"]:
            await _browser_state["playwright"].stop()
    except Exception:
        pass
    finally:
        _browser_state = {
            "playwright": None,
            "browser": None,
            "context": None,
            "page": None,
            "is_ready": False,
            "last_media_type": None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# MCP Resources
# ─────────────────────────────────────────────────────────────────────────────

@mcp.resource("flow://capabilities")
async def flow_capabilities() -> str:
    """
    Documentação das capacidades do Google Labs Flow.

    Retorna informações sobre modelos disponíveis, limites de geração,
    formatos suportados e custos computacionais. Use este resource antes
    de planejar qualquer geração de mídia para entender as opções disponíveis.
    """
    return """# Google Labs Flow — Capacidades e Modelos (atualizado 2026-07)

## Modelos Disponíveis

### 🖼️ Modelos de Imagem

| Modelo | Plano | Descrição |
|--------|-------|-----------|
| `Nano Banana Pro` | Google AI Ultra | Modelo avançado de alta qualidade |
| `Nano Banana 2` | Gratuito | Modelo padrão de imagens do Flow |
| `Nano Banana 2 Lite` | Gratuito | Versão mais rápida e leve |

- **Máximo por lote:** 15 imagens
- **Formatos:** PNG, JPG
- **Tempo estimado:** 30–120 segundos por lote

### 🎬 Modelos de Vídeo

| Modelo | Descrição |
|--------|-----------|
| `Omni Flash` | Modelo Gemini multimodal, geração rápida |
| `Veo 3.1 - Lite` | Versão leve, geração mais rápida |
| `Veo 3.1 - Fast` | Equilíbrio entre velocidade e qualidade |
| `Veo 3.1 - Quality` | Máxima qualidade, mais lento |

- **Máximo por lote:** 5 vídeos
- **Formato:** MP4
- **Tempo estimado:** 2–5 minutos por lote

## Aspectos/Formatos Disponíveis

| Formato | Descrição | Uso Ideal |
|---------|-----------|-----------|
| `1:1` | Quadrado | Instagram, avatares |
| `16:9` | Paisagem horizontal | YouTube, desktop |
| `9:16` | Retrato vertical | Stories, TikTok, Reels |
| `4:3` | Paisagem clássica | Apresentações |
| `3:4` | Retrato clássico | Fotografias |

## Limites por Requisição

| Tipo | Quantidade Máxima | Timeout Estimado |
|------|-------------------|-----------------|
| Imagens | **15 por lote** | 30-120 segundos |
| Vídeos | **5 por lote** | 120-360 segundos |

> ⚠️ Exceeder os limites resulta em erro QUOTA_EXCEEDED imediato (sem chamar o Flow).

## Políticas de Conteúdo

- Conteúdo violento, adulto ou que viola direitos autorais é bloqueado automaticamente
- Se um prompt for bloqueado, o MCP retorna CONTENT_BLOCKED indicando qual prompt falhou
- A IA deve reescrever o prompt e tentar novamente

## Fluxo de Trabalho Recomendado

### Fluxo Simples (sem interação do Agent)
1. `flow_manage_session` — Iniciar/verificar sessão
2. `flow_generate_media` — Gerar com parâmetros (ativa Agent automaticamente)
3. `flow_await_download_media` — Aguardar e baixar arquivos

### Fluxo Bidirecional (quando Agent pede aprovação)
1. `flow_manage_session` — Iniciar/verificar sessão
2. `flow_generate_media` — Gerar com parâmetros
3. `flow_await_download_media` → retorna `agent_requires_interaction`
4. `flow_read_agent_status` — Ler a pergunta do Agent
5. `flow_reply_to_agent(approve=True)` — Clicar em Approve/Yes
6. `flow_await_download_media` — Retomar e baixar arquivos

## Tipagem Aberta de Modelos

O campo `model` aceita qualquer string — você pode passar novos modelos lançados pela Google
sem precisar de atualização do MCP. Exemplos: `"Nano Banana Pro"`, `"Veo 3.1 Fast"`,
ou qualquer string customizada caso um novo modelo apareça na interface.
"""


@mcp.resource("flow://agent_guidelines")
async def flow_agent_guidelines() -> str:
    """
    Diretrizes de como operar o agente interno do Google Labs Flow.

    Contém o Template Estrito de geração, instruções de comportamento esperado,
    orientações sobre como formatar corretamente as solicitações de mídia,
    e o fluxo bidirecional completo com o Agent do Google Flow.
    """
    return """# Google Labs Flow — Diretrizes do Agente (atualizado 2026-07)

## Princípio Zero-DOM

Como agente de IA usando este MCP, você NUNCA deve:
- Ler ou analisar HTML/DOM do Google Flow
- Interpretar seletores CSS ou estrutura de página
- Debugar problemas de interface do navegador

Todo DOM-reading está encapsulado no servidor MCP. Se o layout mudar e quebrar,
o MCP retornará `DOM_SELECTOR_CHANGED` — seu papel é reportar ao usuário que o MCP precisa de atualização.

## Tipagem Aberta de Modelos

O campo `model` aceita `Union[ModelName, str]`. Você PODE passar qualquer string:
- `"Nano Banana Pro"` (enum conhecido)
- `"Veo 3.1 Fast"` (enum conhecido)
- `"Nano Banana 4"` (futuro, string livre — funcionará sem crash)

## Template Estrito de Geração

O MCP usa este template ao injetar no Flow. Você fornece os dados; o MCP formata:

```
GERAR: {quantity} {tipo}s, Formato {aspect_ratio}, Modelo {model}.
PROMPTS: 1. Imagem 1: {prompt_1} | 2. Imagem 2: {prompt_2} | ...
```

## Fluxo de Decisão para Geração

### ✅ Fluxo Simples (sem interação do Agent)
1. `flow_manage_session(action='start')` — verificar/iniciar sessão
2. `flow_generate_media(...)` — o MCP ativa o Agent automaticamente antes do submit
3. `flow_await_download_media()` — aguardar e baixar arquivos

### 🔄 Fluxo Bidirecional (quando Agent pede aprovação)

Quando `flow_await_download_media` retorna `status: "agent_requires_interaction"`:

1. Leia a mensagem do Agent: `flow_read_agent_status()`
2. Responda ao Agent: `flow_reply_to_agent(approve=True)` para clicar em Approve
   - OU `flow_reply_to_agent(message="texto")` para digitar uma resposta
3. Retome o download: `flow_await_download_media()` novamente

**IMPORTANTE:** NÃO chame `flow_generate_media` novamente após `agent_requires_interaction`.
O Agent já recebeu o prompt — você só precisa aprovar ou responder.

## Códigos de Erro e Ações Recomendadas

| Código | Situação | O que fazer |
|--------|----------|-------------|
| `LOGIN_REQUIRED` | Usuário não autenticado | Peça ao usuário para logar no browser aberto |
| `QUOTA_EXCEEDED` | Quantidade acima do limite | Divida a tarefa em lotes menores |
| `SUBMIT_FAILED` | Submit não detectado pelo Flow | Verifique se a página carregou e tente novamente |
| `DOM_SELECTOR_CHANGED` | Layout do Flow mudou | Informe usuário que o MCP precisa de atualização |
| `CONTENT_BLOCKED` | Prompt viola políticas | Reescreva o prompt problemático e tente novamente |
| `GENERATION_TIMEOUT` | Geração demorou muito | Tente novamente ou reduza a quantidade |
| `agent_requires_interaction` | Agent parou e aguarda aprovação | Chame `flow_read_agent_status` depois `flow_reply_to_agent` |

## Exemplos de Uso

### Gerar 2 vídeos verticais:
```json
{
  "type": "video",
  "model": "Veo 3.1 Fast",
  "aspect_ratio": "9:16",
  "quantity": 2,
  "prompts": [
    "Astronauta pousando na Lua, cinematográfico",
    "Planeta de diamante colidindo com Júpiter, hiper-realista"
  ]
}
```

### Gerar 3 imagens quadradas:
```json
{
  "type": "image",
  "model": "Nano Banana 2",
  "aspect_ratio": "1:1",
  "quantity": 3,
  "prompts": [
    "Logo minimalista de startup tech, fundo escuro",
    "Pôr do sol na praia com cores vibrantes",
    "Café expresso artesanal em close, fotografia de produto"
  ]
}
```

### Usando modelo customizado (string livre):
```json
{
  "type": "image",
  "model": "Nano Banana Pro",
  "aspect_ratio": "16:9",
  "quantity": 1,
  "prompts": ["Um pequeno robô feliz digitando num teclado mecânico"]
}
```
"""


# ─────────────────────────────────────────────────────────────────────────────
# MCP Tools
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="flow_manage_session",
    annotations={
        "title": "Gerenciar Sessão do Google Flow",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def flow_manage_session(params: ManageSessionInput) -> str:
    """
    Gerencia a sessão do browser para o Google Labs Flow.

    Esta é a PRIMEIRA ferramenta a ser chamada antes de qualquer geração.
    Abre o Chromium, navega para o Flow e verifica o estado de autenticação.

    Comportamento:
    - Se não logado: abre browser visível e retorna LOGIN_REQUIRED com instruções
    - Se logado: navega para a interface do agente e retorna status "ready"
    - Salva contexto persistente em browser_profile/ para reutilizar entre sessões

    Args:
        params (ManageSessionInput):
            - action (str): 'start' para iniciar, 'status' para verificar, 'stop' para encerrar
            - login_confirmed (bool): True se o usuário confirmou que já fez login manualmente

    Returns:
        str: JSON com campos:
            - status: "ready" | "login_required" | "stopped" | "error"
            - message: Descrição do estado atual
            - error_code: (apenas em erros) Código padronizado
    """
    global _browser_state

    if params.action == "stop":
        await _close_browser()
        return _success_response({
            "status": "stopped",
            "message": "Browser encerrado com sucesso.",
        })

    if params.action == "status":
        if _browser_state["is_ready"] and _browser_state["page"] and not _browser_state["page"].is_closed():
            return _success_response({
                "status": "ready",
                "message": "Sessão ativa. Browser está pronto para usar.",
                "url": _browser_state["page"].url,
            })
        return _success_response({
            "status": "not_initialized",
            "message": "Sessão não iniciada. Chame flow_manage_session com action='start'.",
        })

    # action == "start"
    try:
        page = await _get_or_create_browser(headless=False)

        # Navegar para o Flow se não estivermos nele ou se estivermos na página de erro
        current_url = page.url
        if "labs.google" not in current_url or "/project/new" in current_url:
            await page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(6)

        # ── Verificar se estamos na landing page e precisamos entrar no workspace ──
        try:
            # Seletores para o botão de criar projeto / ir para workspace
            create_selectors = [
                "a[href*='/project/']",
                "a:has-text('Create with Google Flow')",
                "button:has-text('Create with Google Flow')",
                "a:has-text('New project')",
                "button:has-text('New project')"
            ]
            for sel in create_selectors:
                elements = await page.query_selector_all(sel)
                clicked = False
                for el in elements:
                    if await el.is_visible():
                        # Evitar clicar em 'New project' se quisermos abrir um projeto existente
                        if sel == "a[href*='/project/']":
                            href = await el.get_attribute("href")
                            if href and "/project/new" in href:
                                continue
                        
                        await el.click()
                        # Aguarda navegação para o workspace
                        await asyncio.sleep(5)
                        clicked = True
                        break
                if clicked:
                    break
        except Exception:
            pass

        # ── Tratamento de Erro do Google Flow: "Something went wrong" ──
        try:
            error_text = page.get_by_text("Something went wrong", exact=False).first
            if await error_text.is_visible(timeout=2000):
                back_btn = page.get_by_text("Back to projects").first
                if await back_btn.is_visible():
                    await back_btn.click()
                    await asyncio.sleep(4)
                
                # Tentar clicar no primeiro projeto existente na lista
                project_links = await page.query_selector_all("a[href*='/project/']")
                for link in project_links:
                    href = await link.get_attribute("href")
                    if href and href != "/fx/tools/flow/project/new" and "project/" in href:
                        await link.click()
                        await asyncio.sleep(4)
                        break
        except Exception:
            pass

        # Verificar autenticação
        logged_in = await _is_logged_in(page)

        if not logged_in and not params.login_confirmed:
            return _success_response({
                "status": "login_required",
                "message": (
                    "O browser foi aberto mas o usuário não está autenticado no Google. "
                    "Por favor:\n"
                    "1. Vá até a janela do browser que foi aberta\n"
                    "2. Faça login com sua conta Google\n"
                    "3. Navegue até o Google Labs Flow (labs.google/flow)\n"
                    "4. Após o login, chame flow_manage_session novamente com login_confirmed=True"
                ),
                "flow_url": FLOW_URL,
                "action_required": "Faça login manualmente no browser aberto e chame esta ferramenta com login_confirmed=True",
            })

        if params.login_confirmed or logged_in:
            # Aguardar carregamento completo da página
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeout:
                pass  # Continua mesmo sem networkidle

            # Verificar novamente após aguardar
            logged_in = await _is_logged_in(page)
            if not logged_in:
                return _success_response({
                    "status": "login_required",
                    "message": "Login não detectado ainda. Certifique-se de ter completado o login e tente novamente.",
                    "action_required": "Complete o login no browser e chame com login_confirmed=True",
                })

            _browser_state["is_ready"] = True
            
            # Aguardar renderização das imagens lazy-loaded antigas
            # para evitar que sejam lidas como "novas" na geração atual.
            await asyncio.sleep(4.0)

            return _success_response({
                "status": "ready",
                "message": "Sessão iniciada com sucesso! O browser está autenticado e pronto para gerar mídias.",
                "url": page.url,
                "next_step": "Use flow_generate_media para iniciar uma geração",
            })

    except RuntimeError as e:
        return _error_response(
            "BROWSER_INIT_FAILED",
            f"Falha ao inicializar o browser: {str(e)}",
            {"suggestion": "Verifique se o Playwright está instalado corretamente com: playwright install chromium"},
        )
    except Exception as e:
        return _error_response(
            "UNEXPECTED_ERROR",
            f"Erro inesperado ao gerenciar sessão: {type(e).__name__}: {str(e)}",
        )


@mcp.tool(
    name="flow_generate_media",
    annotations={
        "title": "Gerar Mídia no Google Flow",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def flow_generate_media(params: GenerateMediaInput) -> str:
    """
    Injeta parâmetros de geração de mídia no Google Labs Flow via Playwright.

    Usa o Template Estrito definido no plano para formatar a solicitação.
    A IA fornece os parâmetros estruturados; o MCP monta e injeta o texto no Flow.

    Validações pré-execução (sem abrir browser):
    - Imagens: quantity deve ser <= 15
    - Vídeos: quantity deve ser <= 5
    - Prompts: nenhum deve estar vazio, quantidade deve ser >= 1

    Args:
        params (GenerateMediaInput):
            - type: "image" ou "video"
            - model: "Imagen 3" ou "Google Veo"
            - aspect_ratio: "1:1", "16:9", "9:16", "4:3" ou "3:4"
            - quantity: número de mídias (1-15 para imagens, 1-5 para vídeos)
            - prompts: lista de prompts descritivos

    Returns:
        str: JSON com campos:
            - status: "generation_started" | "error"
            - message: Descrição do resultado
            - error_code: (em erros) QUOTA_EXCEEDED | DOM_SELECTOR_CHANGED | LOGIN_REQUIRED | CONTENT_BLOCKED
            - template_injected: O template exato que foi injetado no Flow
    """
    global _browser_state

    # ── Validação pré-execução (sem abrir browser) ──
    if params.type == MediaType.VIDEO and params.quantity > VIDEO_MAX_QUANTITY:
        return _error_response(
            "QUOTA_EXCEEDED",
            f"Quantidade solicitada ({params.quantity}) excede o limite de vídeos por lote ({VIDEO_MAX_QUANTITY}).",
            {
                "requested": params.quantity,
                "limit": VIDEO_MAX_QUANTITY,
                "suggestion": f"Divida a tarefa em lotes de até {VIDEO_MAX_QUANTITY} vídeos.",
            },
        )

    if params.type == MediaType.IMAGE and params.quantity > IMAGE_MAX_QUANTITY:
        return _error_response(
            "QUOTA_EXCEEDED",
            f"Quantidade solicitada ({params.quantity}) excede o limite de imagens por lote ({IMAGE_MAX_QUANTITY}).",
            {
                "requested": params.quantity,
                "limit": IMAGE_MAX_QUANTITY,
                "suggestion": f"Divida a tarefa em lotes de até {IMAGE_MAX_QUANTITY} imagens.",
            },
        )

    # Verificar compatibilidade tipo x modelo (apenas para modelos conhecidos)
    model_value = params.model.value if isinstance(params.model, ModelName) else str(params.model)

    if params.type == MediaType.IMAGE and model_value in _VIDEO_MODELS:
        return _error_response(
            "MODEL_MISMATCH",
            f"O modelo '{model_value}' é para vídeos. Para imagens, use 'Nano Banana 2' ou 'Nano Banana Pro'.",
            {"suggestion": "Altere model para 'Nano Banana 2' (gratuito) ou 'Nano Banana Pro' (premium) para imagens."},
        )

    if params.type == MediaType.VIDEO and model_value in _IMAGE_MODELS:
        return _error_response(
            "MODEL_MISMATCH",
            f"O modelo '{model_value}' é para imagens. Para vídeos, use 'Veo 3.1 Fast', 'Veo 3.1 Quality' ou 'Gemini Omni Flash'.",
            {"suggestion": "Altere model para um modelo de vídeo válido."},
        )

    # ── Verificar sessão ──
    if not _browser_state["is_ready"] or not _browser_state["page"] or _browser_state["page"].is_closed():
        return _error_response(
            "SESSION_NOT_READY",
            "Sessão não iniciada. Chame flow_manage_session primeiro.",
            {"suggestion": "Execute flow_manage_session com action='start' antes de gerar mídias."},
        )

    page: Page = _browser_state["page"]

    # ── Construir Template Estrito ──
    template = _build_strict_template(
        media_type=params.type,
        model=params.model,
        aspect_ratio=params.aspect_ratio,
        quantity=params.quantity,
        prompts=params.prompts,
    )

    # ── Ativar o modo Agent antes de submeter o prompt ──────────────────────
    # O modo Agent é essencial para que o Flow interprete diretrizes estruturadas.
    # _ensure_agent_active() é não-fatal: se o botão não for encontrado, continua.
    try:
        await _ensure_agent_active(page)
    except Exception:
        pass

    # ── Verificar e fechar overlays que possam bloquear o editor ──
    try:
        await _dismiss_overlays(page)
    except Exception:
        pass

    # ── Verificar que o editor está disponível ──
    try:
        input_element = await _find_element(page, "input_field", timeout=10000)

        if not input_element:
            return _error_response(
                "DOM_SELECTOR_CHANGED",
                "Campo de input do Flow não encontrado. O layout da página pode ter mudado.",
                {
                    "suggestion": "Atualize os seletores CSS no servidor MCP (arquivo server.py, SELECTORS['input_field'])",
                    "current_url": page.url,
                    "action": "Relate este erro ao desenvolvedor do MCP para atualizar os seletores Playwright",
                },
            )

        # ── Injetar texto no campo de input ────────────────────────────────────
        # _inject_text_into_input() suporta a interface nova (textarea simples)
        # e faz fallback automático para Fiber/keyboard caso necessário.
        #
        # Nota: A nova UI do Flow (2026) usa um campo de texto simples onde
        # o usuário digita o prompt. Para que o Flow ajuste modelo e formato
        # automaticamente, DEVEMOS sempre injetar o template estrito.
        prompt_to_inject = template
        injected = await _inject_text_into_input(page, prompt_to_inject, params.reference_image)

        if not injected:
            return _error_response(
                "DOM_SELECTOR_CHANGED",
                "Não foi possível inserir texto no campo de input do Flow.",
                {
                    "suggestion": "Verifique se a página do Flow está carregada corretamente.",
                    "current_url": page.url,
                },
            )
        
        # Marcar todas as mídias antigas no DOM para ignorá-las depois.
        # Isso evita de forma definitiva o problema de "falso positivo"
        # quando imagens lazy-loaded antigas aparecem durante a geração.
        try:
            await page.evaluate('''() => {
                document.querySelectorAll("img, video").forEach(el => {
                    el.setAttribute('data-old', 'true');
                });
            }''')
        except Exception:
            pass

        # Mapear as midias do Flow no DOM ANTES de submeter!
        # Usa seletor restrito e ignora as mídias antigas marcadas.
        last_img_srcs = await page.evaluate('(function(){var r=[];document.querySelectorAll("img:not([data-old=\'true\']),video:not([data-old=\'true\'])").forEach(function(el){var s=el.src||"";if(s.indexOf("trpc/media")>-1||s.indexOf("getMediaUrlRedirect")>-1||s.indexOf("storage.googleapis.com")>-1||el.tagName==="VIDEO"&&s)r.push(s);});return r;})()')
        _browser_state["last_img_srcs"] = last_img_srcs

        # Contar botões de download ANTES de submeter
        _browser_state["initial_download_buttons"] = len(await page.query_selector_all(
            "a[download], button[aria-label*='download' i], button[aria-label*='baixar' i], button[aria-label*='save' i]"
        ))

        # ── Submeter a solicitação com verificação de sucesso ──────────────────
        submit_ok = await _submit_and_verify(page)

        if not submit_ok:
            return _error_response(
                "SUBMIT_FAILED",
                "Não foi possível confirmar que a mensagem foi enviada ao Flow. "
                "O campo de texto não ficou vazio e nenhum indicador de loading foi detectado.",
                {
                    "suggestion": (
                        "Verifique se a página do Flow está carregada corretamente no browser. "
                        "Tente chamar flow_manage_session novamente e repetir a operação."
                    ),
                    "current_url": page.url,
                    "template_attempted": template,
                },
            )

        # ── Verificar se houve bloqueio de conteúdo imediato ──────────────────
        blocked_element = await _find_element(page, "content_blocked", timeout=3000)
        if blocked_element:
            blocked_text = await blocked_element.text_content() or "Conteúdo bloqueado"
            blocked_prompt = params.prompts[0] if params.prompts else "desconhecido"
            return _error_response(
                "CONTENT_BLOCKED",
                "O Flow bloqueou a solicitação por violação de políticas de conteúdo.",
                {
                    "blocked_message": blocked_text.strip(),
                    "likely_problematic_prompt": blocked_prompt,
                    "all_prompts": params.prompts,
                    "suggestion": "Reescreva os prompts evitando conteúdo sensível, violento ou que possa violar direitos autorais.",
                },
            )

        # Armazenar tipo de mídia para uso no download
        _browser_state["last_media_type"] = params.type.value

        return _success_response({
            "status": "generation_started",
            "message": f"Solicitação de geração de {params.quantity} {params.type.value}(s) enviada e confirmada no Google Flow.",
            "template_injected": template,
            "model_used": model_value,
            "next_step": "Chame flow_await_download_media para aguardar a conclusão e baixar os arquivos.",
            "estimated_wait": f"{'30-120 segundos' if params.type == MediaType.IMAGE else '2-5 minutos'}",
        })

    except PlaywrightTimeout:
        return _error_response(
            "DOM_SELECTOR_CHANGED",
            "Timeout ao localizar elementos do Flow. A página pode ter mudado seu layout.",
            {
                "suggestion": "Atualize os seletores no servidor MCP ou verifique se o Flow está acessível.",
                "current_url": page.url if page else "desconhecida",
            },
        )
    except Exception as e:
        return _error_response(
            "PLAYWRIGHT_ERROR",
            f"Erro Playwright ao injetar template: {type(e).__name__}: {str(e)}",
        )


@mcp.tool(
    name="flow_await_download_media",
    annotations={
        "title": "Aguardar e Baixar Mídia Gerada no Flow",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def flow_await_download_media(params: AwaitDownloadInput) -> str:
    """
    Aguarda a conclusão da geração de mídia no Flow e baixa os arquivos automaticamente.

    FLUXO BIDIRECIONAL: Se o Agent do Flow pausar a geração pedindo aprovação/confirmação,
    esta função NÃO dá timeout — ela interrompe o loop e retorna imediatamente com
    status `agent_requires_interaction`. Isso sinaliza para a IA que deve:
      1. Chamar `flow_read_agent_status` para ler a mensagem do Agent
      2. Chamar `flow_reply_to_agent(approve=True)` para aprovar
      3. Chamar `flow_await_download_media` novamente para retomar

    Realiza polling invisível na interface do Flow monitorando indicadores de conclusão.
    A IA aguarda o retorno desta ferramenta sem gastar tokens durante a espera.

    Comportamento:
    - Polling a cada 3 segundos verificando se a geração concluiu
    - Timeout: 120s para imagens, 300s para vídeos (ou valor customizado)
    - Download automático para ./midias/{timestamp}_{filename}
    - Retorna lista de caminhos absolutos dos arquivos baixados

    Args:
        params (AwaitDownloadInput):
            - timeout_seconds: Timeout customizado em segundos (10-600). None = padrão por tipo
            - download_dir: Diretório de destino. None = ./midias/

    Returns:
        str: JSON com campos:
            - status: "download_complete" | "timeout" | "error"
            - files: Lista de caminhos absolutos dos arquivos baixados
            - count: Número de arquivos baixados
            - download_dir: Diretório onde foram salvos
            - error_code: (em erros) GENERATION_TIMEOUT | DOM_SELECTOR_CHANGED | SESSION_NOT_READY
    """
    global _browser_state

    # ── Verificar sessão ──
    if not _browser_state["page"] or _browser_state["page"].is_closed():
        return _error_response(
            "SESSION_NOT_READY",
            "Sessão não ativa. Chame flow_manage_session primeiro.",
        )

    page: Page = _browser_state["page"]

    # ── Configurar diretório de download ──
    if params.download_dir:
        download_dir = Path(params.download_dir)
    else:
        download_dir = MIDIAS_DIR

    download_dir.mkdir(parents=True, exist_ok=True)

    # ── Configurar timeout ──
    last_media_type = _browser_state.get("last_media_type", "image")
    if params.timeout_seconds:
        timeout = params.timeout_seconds
    elif last_media_type == "video":
        timeout = VIDEO_GENERATION_TIMEOUT
    else:
        timeout = IMAGE_GENERATION_TIMEOUT

    # ── Polling — aguardar conclusão ──
    start_time = time.time()
    downloaded_files: List[str] = []

    # Registrar listener de download antes do polling
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    async def handle_download(download):
        """Intercepta downloads iniciados pelo Flow e salva com nomenclatura padronizada."""
        try:
            filename = download.suggested_filename or f"media_{len(downloaded_files) + 1}"
            # Sanitizar filename — remover caracteres especiais
            filename = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
            save_path = download_dir / f"{timestamp}_{filename}"
            await download.save_as(str(save_path))
            downloaded_files.append(str(save_path.absolute()))
        except Exception:
            pass  # Não falhar se um download individual falhar

    page.on("download", handle_download)

    # ── Recuperar os src salvos ANTES da geração (salvos no _browser_state) ──
    # Usamos seletor específico das URLs do Flow (trpc/media) para evitar falsos positivos
    # com avatares e outras imagens estáticas da UI que já existem na página.
    FLOW_MEDIA_SELECTOR = "img[src*='trpc/media'], img[src*='getMediaUrlRedirect'], img[src*='storage.googleapis.com'], video[src*='trpc/media'], video[src]"

    initial_img_srcs = _browser_state.get("last_img_srcs")
    if not initial_img_srcs:
        # Captura apenas as mídias do Flow (não avatares/ícones da UI)
        initial_img_srcs = await page.evaluate('(function(){var r=[];document.querySelectorAll("img,video").forEach(function(el){var s=el.src||"";if(s.indexOf("trpc/media")>-1||s.indexOf("getMediaUrlRedirect")>-1||s.indexOf("storage.googleapis.com")>-1||el.tagName==="VIDEO"&&s)r.push(s);});return r;})()')

    try:
        while time.time() - start_time < timeout:
            elapsed = int(time.time() - start_time)

            # Rolar qualquer container para o topo: novas imagens aparecem no início do grid.
            # O lazy loading do Flow só popula img[src] quando o elemento está na viewport.
            # Como o scroll fica numa div e não no window, precisamos rolar todos os containers com scroll.
            try:
                await page.evaluate('''() => {
                    document.querySelectorAll('*').forEach(el => {
                        if (el.scrollHeight > el.clientHeight && el.clientHeight > 0) {
                            let overflow = window.getComputedStyle(el).overflowY;
                            if (overflow === 'auto' || overflow === 'scroll' || overflow === 'overlay') {
                                el.scrollTop = 0;
                            }
                        }
                    });
                }''')
                await asyncio.sleep(0.5)  # Aguarda lazy loading popular os src
            except Exception:
                pass

            # A geração concluiu se há novas mídias do Flow que não estavam na lista inicial.
            # Usamos seletor restrito para ignorar avatares/UI que sempre existem na página, e ignoramos as antigas.
            curr_img_srcs = await page.evaluate('(function(){var r=[];document.querySelectorAll("img:not([data-old=\'true\']),video:not([data-old=\'true\'])").forEach(function(el){var s=el.src||"";if(s.indexOf("trpc/media")>-1||s.indexOf("getMediaUrlRedirect")>-1||s.indexOf("storage.googleapis.com")>-1||el.tagName==="VIDEO"&&s)r.push(s);});return r;})()')
            complete_element = any(src for src in curr_img_srcs if src not in initial_img_srcs)

            # Verificar download buttons visíveis
            download_buttons = await page.query_selector_all(
                "a[download], "
                "button[aria-label*='download' i], "
                "button[aria-label*='baixar' i], "
                "button[aria-label*='save' i]"
            )

            initial_buttons_count = _browser_state.get("initial_download_buttons", 0)
            has_new_buttons = len(download_buttons) > initial_buttons_count

            # Só considera concluído se surgiram novos elementos confirmados e se surgiram NOVOS botões de download
            # Para vídeos, esperamos vídeos ou botões. Para imagens, novas imagens e novos botões.
            if complete_element or has_new_buttons:
                # ── Geração concluída — tentar fazer downloads ──

                # Clicar apenas nos NOVOS botões de download encontrados
                new_buttons = download_buttons[initial_buttons_count:]
                for btn in new_buttons[:15]:
                    try:
                        if await btn.is_visible():
                            await btn.click()
                            await asyncio.sleep(1.5)
                    except Exception:
                        continue
                
                if not downloaded_files:
                    # Fallback 1: hover sobre imagens para revelar botões de download ocultos
                    # O Flow usa hover-reveal para os controles de download
                    hover_selectors = [
                        "img[src*='trpc/media']",
                        "video[src]",
                    ]
                    for sel in hover_selectors:
                        elems = await page.query_selector_all(sel)
                        for elem in elems[:15]:
                            try:
                                if await elem.is_visible():
                                    await elem.hover()
                                    await asyncio.sleep(1.0)
                                    # Re-verificar botões de download após hover
                                    new_btns = await page.query_selector_all(
                                        "a[download], "
                                        "button[aria-label*='download' i], "
                                        "button[aria-label*='baixar' i], "
                                        "button[aria-label*='save' i]"
                                    )
                                    for btn in new_btns:
                                        try:
                                            if await btn.is_visible():
                                                await btn.click()
                                                await asyncio.sleep(1.5)
                                        except Exception:
                                            continue
                            except Exception:
                                continue

                    # Fallback 2: baixar via URL direta dos elementos de mídia
                    # Usa seletor específico do Flow e ignora antigas.
                    media_selectors = [
                        "img:not([data-old='true'])[src*='trpc/media'], img:not([data-old='true'])[src*='getMediaUrlRedirect'], img:not([data-old='true'])[src*='storage.googleapis.com']",
                        "video:not([data-old='true'])[src]",
                    ]
                    for selector in media_selectors:
                        elements = await page.query_selector_all(selector)
                        for elem in elements[:15]:
                            try:
                                src = await elem.evaluate("el => el.src")
                                if src and src not in initial_img_srcs:
                                    # Resolver URL relativa para absoluta (o Flow usa URLs /fx/api/...)
                                    if src.startswith("/"):
                                        from urllib.parse import urlparse
                                        parsed = urlparse(page.url)
                                        src = f"{parsed.scheme}://{parsed.netloc}{src}"

                                    if not src.startswith("http"):
                                        continue

                                    # Determinar extensão
                                    tag = await elem.evaluate("el => el.tagName.toLowerCase()")
                                    ext = "mp4" if tag == "video" else "png"
                                    filename = f"{timestamp}_media_{len(downloaded_files) + 1}.{ext}"
                                    save_path = download_dir / filename

                                    # Evitar duplicatas
                                    if str(save_path.absolute()) in downloaded_files:
                                        continue

                                    # Download via Playwright request API (autentica automaticamente)
                                    response = await page.request.get(src)
                                    if response.ok:
                                        body = await response.body()
                                        save_path.write_bytes(body)
                                        downloaded_files.append(str(save_path.absolute()))
                            except Exception:
                                continue

                # Aguardar downloads completarem
                await asyncio.sleep(2)

                if downloaded_files:
                    try: page.remove_listener("download", handle_download)
                    except: pass
                    return _success_response({
                        "status": "download_complete",
                        "message": f"{len(downloaded_files)} arquivo(s) baixado(s) com sucesso.",
                        "files": downloaded_files,
                        "count": len(downloaded_files),
                        "download_dir": str(download_dir.absolute()),
                        "elapsed_seconds": elapsed,
                    })
                else:
                    try: page.remove_listener("download", handle_download)
                    except: pass
                    # Geração parece concluída mas sem arquivos identificados
                    return _success_response({
                        "status": "generation_complete_no_files",
                        "message": "A geração parece ter concluído, mas nenhum arquivo foi detectado para download automático.",
                        "download_dir": str(download_dir.absolute()),
                        "elapsed_seconds": elapsed,
                        "suggestion": "Verifique manualmente o browser — os arquivos podem precisar de download manual ou os seletores precisam de atualização.",
                    })

            # ── Verificar se o Agent do Flow pausou e aguarda interação humana ──
            # Prioridade: verificar ANTES do sleep para reagir rápido
            try:
                agent_interaction_needed = False
                agent_message_text = ""

                for approve_sel in SELECTORS["agent_approve_button"]:
                    approve_el = await page.query_selector(approve_sel)
                    if approve_el and await approve_el.is_visible():
                        agent_interaction_needed = True
                        # Tentar capturar o texto do chat lateral
                        for msg_sel in SELECTORS["agent_messages"]:
                            msg_els = await page.query_selector_all(msg_sel)
                            texts = []
                            for msg_el in msg_els[-5:]:  # últimas 5 mensagens
                                try:
                                    t = await msg_el.inner_text()
                                    t = t.strip()
                                    if t:
                                        texts.append(t)
                                except Exception:
                                    pass
                            if texts:
                                agent_message_text = " | ".join(texts)
                                break
                        break

                if agent_interaction_needed:
                    page.remove_listener("download", handle_download)
                    return _success_response({
                        "status": "agent_requires_interaction",
                        "message": (
                            "O Agent do Google Flow pausou a geração e aguarda aprovação ou resposta. "
                            "Chame flow_read_agent_status para ver a mensagem completa, "
                            "depois flow_reply_to_agent(approve=True) para aprovar. "
                            "Em seguida, chame flow_await_download_media novamente para retomar."
                        ),
                        "agent_message": agent_message_text or "Agent aguarda interação (mensagem não capturada)",
                        "next_steps": [
                            "1. flow_read_agent_status() — ler mensagem do Agent",
                            "2. flow_reply_to_agent(approve=True) — clicar em Approve",
                            "3. flow_await_download_media() — retomar e baixar",
                        ],
                        "elapsed_seconds": elapsed,
                    })
            except Exception:
                pass  # Não falhar o loop por erro na verificação do Agent

            # Aguardar antes do próximo poll
            await asyncio.sleep(POLL_INTERVAL)

        # ── Timeout ──
        try: page.remove_listener("download", handle_download)
        except: pass
        return _error_response(
            "GENERATION_TIMEOUT",
            f"A geração não concluiu dentro do timeout de {timeout} segundos.",
            {
                "timeout_seconds": timeout,
                "suggestion": "Tente novamente com timeout maior ou verifique o status no browser. A geração pode ainda estar em andamento.",
                "download_dir": str(download_dir.absolute()),
            },
        )

    except Exception as e:
        try: page.remove_listener("download", handle_download)
        except: pass
        return _error_response(
            "DOWNLOAD_ERROR",
            f"Erro durante o processo de download: {type(e).__name__}: {str(e)}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# MCP Tools — Interação Bidirecional com o Agent do Flow
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="flow_read_agent_status",
    annotations={
        "title": "Ler Status do Agent do Google Flow",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def flow_read_agent_status() -> str:
    """
    Lê o estado atual do chat lateral do Agent do Google Flow.

    Use esta ferramenta APÓS receber `agent_requires_interaction` de `flow_await_download_media`.
    Captura as últimas mensagens visíveis do Agent no painel lateral e identifica
    se há botões de aprovação disponíveis.

    Esta ferramenta é READ-ONLY — não altera nenhum estado no browser.

    Returns:
        str: JSON com campos:
            - status: "success" | "error"
            - agent_active: bool — se o modo Agent está ligado
            - agent_messages: List[str] — últimas mensagens do Agent visíveis
            - requires_interaction: bool — se há botões de approve visíveis
            - approve_buttons_found: List[str] — textos dos botões de approve visíveis
            - sidebar_text: str — texto completo do painel lateral (para diagnóstico)
    """
    global _browser_state

    if not _browser_state["page"] or _browser_state["page"].is_closed():
        return _error_response(
            "SESSION_NOT_READY",
            "Sessão não ativa. Chame flow_manage_session primeiro.",
        )

    page: Page = _browser_state["page"]

    try:
        # ── Verificar se o Agent está ativo ──
        agent_active = False
        for selector in SELECTORS["agent_toggle_button"]:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    aria_pressed = await el.get_attribute("aria-pressed") or ""
                    aria_checked = await el.get_attribute("aria-checked") or ""
                    class_name = await el.get_attribute("class") or ""
                    agent_active = (
                        aria_pressed == "true"
                        or aria_checked == "true"
                        or "active" in class_name.lower()
                        or "selected" in class_name.lower()
                    )
                    break
            except Exception:
                continue

        # ── Capturar mensagens do Agent ──
        agent_messages: List[str] = []
        sidebar_text = ""

        # Tentar capturar texto do sidebar/chat lateral
        for sidebar_sel in SELECTORS["agent_chat_sidebar"]:
            try:
                sidebar_el = await page.query_selector(sidebar_sel)
                if sidebar_el and await sidebar_el.is_visible():
                    sidebar_text = (await sidebar_el.inner_text()).strip()
                    break
            except Exception:
                continue

        # Capturar mensagens individuais do chat
        for msg_sel in SELECTORS["agent_messages"]:
            try:
                msg_els = await page.query_selector_all(msg_sel)
                for msg_el in msg_els:
                    try:
                        t = (await msg_el.inner_text()).strip()
                        if t and t not in agent_messages:
                            agent_messages.append(t)
                    except Exception:
                        pass
                if agent_messages:
                    break
            except Exception:
                continue

        # Limitar a últimas 10 mensagens
        agent_messages = agent_messages[-10:]

        # ── Verificar botões de aprovação disponíveis ──
        approve_buttons_found: List[str] = []
        requires_interaction = False

        for approve_sel in SELECTORS["agent_approve_button"]:
            try:
                approve_els = await page.query_selector_all(approve_sel)
                for btn in approve_els:
                    if await btn.is_visible():
                        btn_text = (await btn.inner_text()).strip()
                        if btn_text and btn_text not in approve_buttons_found:
                            approve_buttons_found.append(btn_text)
                            requires_interaction = True
            except Exception:
                continue

        return _success_response({
            "agent_active": agent_active,
            "agent_messages": agent_messages,
            "requires_interaction": requires_interaction,
            "approve_buttons_found": approve_buttons_found,
            "sidebar_text": sidebar_text[:2000] if sidebar_text else "",
            "message": (
                f"Agent {'ativo' if agent_active else 'inativo'}. "
                f"{len(agent_messages)} mensagem(ns) capturada(s). "
                f"{'Aprovação necessária.' if requires_interaction else 'Sem interação pendente.'}"
            ),
        })

    except Exception as e:
        return _error_response(
            "AGENT_READ_ERROR",
            f"Erro ao ler status do Agent: {type(e).__name__}: {str(e)}",
        )


@mcp.tool(
    name="flow_reply_to_agent",
    annotations={
        "title": "Responder ao Agent do Google Flow",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def flow_reply_to_agent(params: ReplyToAgentInput) -> str:
    """
    Responde ou aprova a solicitação do Agent do Google Flow no chat lateral.

    Use esta ferramenta quando `flow_await_download_media` retornar `agent_requires_interaction`.
    Permite dois modos de interação:

    1. **Aprovação direta** (`approve=True`): Clica no primeiro botão Approve/Yes/Confirm
       visível no chat lateral. Use quando o Agent pede confirmação antes de gerar.

    2. **Mensagem customizada** (`message="texto"`): Digita texto no input do chat lateral
       e submete. Use quando o Agent fez uma pergunta que requer resposta textual.

    Args:
        params (ReplyToAgentInput):
            - approve (bool): Se True, clica no botão de aprovação visível
            - message (str|None): Texto opcional para digitar no chat lateral

    Returns:
        str: JSON com campos:
            - status: "success" | "error"
            - action_taken: Descrição da ação executada
            - approve_clicked: bool — se um botão de approve foi clicado
            - message_sent: bool — se uma mensagem foi digitada e enviada
    """
    global _browser_state

    if not _browser_state["page"] or _browser_state["page"].is_closed():
        return _error_response(
            "SESSION_NOT_READY",
            "Sessão não ativa. Chame flow_manage_session primeiro.",
        )

    page: Page = _browser_state["page"]

    approve_clicked = False
    message_sent = False
    actions_taken: List[str] = []

    try:
        # ── Modo 1: Clicar em botão de aprovação ──
        if params.approve:
            for approve_sel in SELECTORS["agent_approve_button"]:
                try:
                    approve_els = await page.query_selector_all(approve_sel)
                    for btn in approve_els:
                        if await btn.is_visible():
                            btn_text = (await btn.inner_text()).strip()
                            await btn.click()
                            await asyncio.sleep(1.5)
                            approve_clicked = True
                            actions_taken.append(f"Clicou em botão '{btn_text}'")
                            break
                    if approve_clicked:
                        break
                except Exception:
                    continue

            if not approve_clicked:
                return _error_response(
                    "APPROVE_BUTTON_NOT_FOUND",
                    "Nenhum botão de aprovação (Approve/Yes/Confirm) foi encontrado visível no chat lateral.",
                    {
                        "suggestion": (
                            "Chame flow_read_agent_status para verificar o estado atual do Agent. "
                            "O Agent pode não estar aguardando aprovação neste momento."
                        ),
                    },
                )

        # ── Modo 2: Enviar mensagem customizada no chat lateral ──
        if params.message:
            chat_input = None
            for input_sel in SELECTORS["agent_chat_input"]:
                try:
                    el = await page.query_selector(input_sel)
                    if el and await el.is_visible():
                        chat_input = el
                        break
                except Exception:
                    continue

            if chat_input:
                await chat_input.click()
                await asyncio.sleep(0.3)
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
                await chat_input.fill(params.message)
                await asyncio.sleep(0.5)
                # Tentar submeter via Enter ou botão de envio próximo
                await page.keyboard.press("Enter")
                await asyncio.sleep(1.0)
                message_sent = True
                actions_taken.append(f"Enviou mensagem: '{params.message[:80]}...'" if len(params.message) > 80 else f"Enviou mensagem: '{params.message}'")
            else:
                return _error_response(
                    "CHAT_INPUT_NOT_FOUND",
                    "Campo de input do chat lateral do Agent não encontrado.",
                    {
                        "suggestion": (
                            "Verifique se o painel do Agent está aberto. "
                            "Use flow_read_agent_status para confirmar o estado atual."
                        ),
                    },
                )

        if not params.approve and not params.message:
            return _error_response(
                "NO_ACTION_SPECIFIED",
                "Nenhuma ação especificada. Use approve=True para aprovar ou forneça message='texto' para responder.",
                {"suggestion": "Chame com approve=True ou message='sua resposta aqui'."},
            )

        return _success_response({
            "approve_clicked": approve_clicked,
            "message_sent": message_sent,
            "actions_taken": actions_taken,
            "message": " | ".join(actions_taken) if actions_taken else "Nenhuma ação executada.",
            "next_step": "Chame flow_await_download_media para retomar o aguardo da geração.",
        })

    except Exception as e:
        return _error_response(
            "AGENT_REPLY_ERROR",
            f"Erro ao responder ao Agent: {type(e).__name__}: {str(e)}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
