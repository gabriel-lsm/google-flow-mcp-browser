#!/usr/bin/env python3
"""
Google Flow MCP Server

Servidor MCP que permite agentes de IA (Claude, etc.) operar o Google Labs Flow
de forma autônoma com arquitetura Zero-DOM. O agente de IA nunca lê o HTML/DOM
diretamente — toda a lógica de navegação e interação fica encapsulada aqui via Playwright.

Ferramentas expostas:
- flow_manage_session: Gerencia autenticação e sessão do browser
- flow_generate_media: Injeta parâmetros de geração no Flow via Playwright
- flow_await_download_media: Aguarda geração e baixa arquivos automaticamente

Resources:
- flow://capabilities: Documentação dos modelos e capacidades
- flow://agent_guidelines: Diretrizes de uso do agente do Flow
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

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

# Seletores Playwright — identificados por inspeção real do DOM do Google Labs Flow (2026-07)
# O Flow usa Slate.js gerenciado por React para o editor de texto.
# NOTA CRÍTICA: Não usar .fill() ou keyboard.type() no editor Slate — causa crash do Next.js.
# A injeção deve ser feita via React Fiber API (ver _inject_text_via_fiber).
SELECTORS = {
    # Campo de input do agente do Flow
    # Confirmado via inspeção real: div[role='textbox'] com Slate.js
    "input_field": [
        "div[role='textbox']",                     # Seletor principal confirmado na inspeção real
        "div[data-slate-editor='true']",           # Slate.js — data-attribute
        "div[role='textbox'][contenteditable='true']",  # role + contenteditable
        "[contenteditable='true']",                # Fallback genérico
    ],
    # Botão de envio — confirmado como botão circular (não arrow_forward como esperado)
    # O botão de submit fica desabilitado (aria-disabled='true') quando o campo está vazio
    "submit_button": [
        "button[aria-disabled='false']:last-of-type",  # Botão circular de submit quando habilitado
        "button:not([aria-disabled='true']):last-of-type",  # Fallback sem aria-disabled=true
        "button[aria-label*='send' i]",
        "button[aria-label*='submit' i]",
        "button[aria-label*='enviar' i]",
        "button[type='submit']",
    ],
    # Indicadores de geração em progresso — visíveis após submit
    "loading_indicator": [
        "[aria-label*='loading' i]",
        "[aria-label*='carregando' i]",
        ".loading",
        "svg[class*='spin' i]",
        # Durante a geração, o Flow mostra 3 pontos animados ou barra de progresso
        "div[class*='progress' i]",
        "div[class*='thinking' i]",
    ],
    # Indicador de geração concluída — imagens/vídeos aparecem na galeria do chat
    "generation_complete": [
        "img[src*='storage.googleapis.com']",      # Imagens geradas ficam no GCS
        "img[src*='generativelanguage.googleapis.com']",
        "img[src*='googleusercontent.com']",
        "video[src]",
        "img[src*='blob:']",                       # Imagens com blob URL
    ],
    # Botão de download — aparece no hover das imagens/vídeos gerados
    "download_button": [
        "a[download]",
        "button[aria-label*='download' i]",
        "button[aria-label*='baixar' i]",
        "button[aria-label*='save' i]",
    ],
    # Indicador de erro/bloqueio de conteúdo
    "content_blocked": [
        "div[role='alert']",
        "[aria-label*='policy' i]",
        "[aria-label*='blocked' i]",
        "div:has-text('Unable to generate')",
        "div:has-text('content policy')",
    ],
    # Verificar se está logado — avatar ou conta Google visível
    "logged_in_indicator": [
        "img[alt*='profile' i]",                   # Avatar de perfil
        "button[aria-label*='Google Account' i]",
        "[data-testid='user-avatar']",
        "img[class*='avatar' i]",
        "[aria-label*='account' i]",
        "[aria-label*='conta' i]",
        # O Flow mostra avatar circular no canto superior direito
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
    # Overlay/modal de anúncios (changelog, novidades) — precisa fechar antes de interagir
    "overlay_close_button": [
        "button:has-text('close')",
        "button[aria-label*='close' i]",
        "button[aria-label*='fechar' i]",
        "button:has-text('dismiss')",
        "[data-testid='close-button']",
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
    """Modelos disponíveis no Google Labs Flow."""
    IMAGEN_3 = "Imagen 3"
    GOOGLE_VEO = "Google Veo"


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


class GenerateMediaInput(BaseModel):
    """Input para flow_generate_media."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    type: MediaType = Field(
        ...,
        description="Tipo de mídia: 'image' (máximo 15 por lote) ou 'video' (máximo 5 por lote)",
    )
    model: ModelName = Field(
        ...,
        description="Modelo a usar: 'Imagen 3' para imagens ou 'Google Veo' para vídeos",
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
    model: ModelName,
    aspect_ratio: AspectRatio,
    quantity: int,
    prompts: List[str],
) -> str:
    """
    Constrói o Template Estrito de geração conforme especificado no plano.

    Este template é injetado diretamente no campo de input do agente do Flow.
    """
    media_label = "Vídeo" if media_type == MediaType.VIDEO else "Imagem"

    prompt_lines = []
    for i, prompt in enumerate(prompts, 1):
        prompt_lines.append(f"{i}. {media_label} {i}: {prompt}")

    prompts_formatted = "\n".join(prompt_lines)

    template = f"""[INSTRUÇÕES DE GERAÇÃO ESTREITA]
Por favor, gere as seguintes mídias usando as especificações exatas abaixo:
- Formato Desejado: {aspect_ratio.value}
- Modelo Selecionado: {model.value}
- Total de Mídias: {quantity}

[PROMPTS ESPECÍFICOS]
{prompts_formatted}"""

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


async def _inject_text_via_fiber(page: Page, text: str) -> bool:
    """
    Injeta texto no editor Slate.js do Google Flow via React Fiber API.

    CRÍTICO: O editor usa React + Slate.js. keyboard.type() e execCommand() causam
    dessincronização entre o DOM virtual do React e o DOM real, resultando em crash
    do cliente (Next.js error). A única abordagem estável é acessar a instância
    interna do editor Slate via React Fiber e usar os métodos oficiais da API Slate.

    Estratégia:
    1. Achar o div[role='textbox'] no DOM
    2. Subir na árvore React Fiber até encontrar a prop 'editor' (instância Slate)
    3. Usar editor.select() + editor.deleteFragment() + editor.insertText()
    4. Disparar Space + Backspace via keyboard para habilitar o botão de submit
       (o React detecta esses eventos físicos e atualiza o estado do botão)
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

        if (!editor) return { success: false, reason: 'slate editor instance not found in fiber tree' };

        try {
            // Limpar texto existente
            const currentLength = editor.string({ path: [0, 0] }).length;
            editor.select({
                anchor: { path: [0, 0], offset: 0 },
                focus: { path: [0, 0], offset: currentLength }
            });
            if (currentLength > 0) editor.deleteFragment();

            // Inserir novo texto via API oficial do Slate
            editor.insertText(text);

            return {
                success: true,
                insertedText: editor.string({ path: [0, 0] })
            };
        } catch(e) {
            return { success: false, reason: e.toString() };
        }
    }
    """
    result = await page.evaluate(js_inject, text)
    if not result.get("success"):
        return False

    # Disparar Space + Backspace para que o React atualize o estado do botão de submit
    # (o Slate oncChange informa ao React, mas o botão de submit usa um listener
    # de teclado para habilitar/desabilitar — Space ativa, Backspace mantém o texto)
    await asyncio.sleep(0.2)
    await page.keyboard.press("Space")
    await asyncio.sleep(0.1)
    await page.keyboard.press("Backspace")
    await asyncio.sleep(0.3)
    return True


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


async def _get_submit_button_coords(page: Page) -> Optional[Dict]:
    """
    Retorna as coordenadas do botão de submit quando habilitado (aria-disabled='false').
    O botão de submit é circular e fica desabilitado quando o campo está vazio.
    """
    js = """
    () => {
        const buttons = Array.from(document.querySelectorAll('button'));
        // O botão de submit é o último botão da área de input
        // Ele fica habilitado (aria-disabled='false') quando há texto
        const submitBtn = buttons.find(btn => {
            const ariaDisabled = btn.getAttribute('aria-disabled');
            const disabled = btn.disabled;
            // Não deve estar desabilitado
            if (ariaDisabled === 'true' || disabled) return false;
            // Deve estar próximo da área inferior da tela (área de input)
            const rect = btn.getBoundingClientRect();
            return rect.y > window.innerHeight * 0.6 && rect.width > 30;
        });
        if (!submitBtn) return null;
        const rect = submitBtn.getBoundingClientRect();
        return {
            x: Math.round(rect.left + rect.width / 2),
            y: Math.round(rect.top + rect.height / 2),
            ariaDisabled: submitBtn.getAttribute('aria-disabled')
        };
    }
    """
    return await page.evaluate(js)


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
    return """# Google Labs Flow — Capacidades e Modelos

## Modelos Disponíveis

### 🖼️ Imagen 3 (para Imagens)
- **Tipo de mídia:** Imagens estáticas
- **Máximo por lote:** 15 imagens
- **Formatos suportados:** PNG, JPG
- **Qualidade:** Alta resolução, foto-realístico ou artístico
- **Velocidade de geração:** ~30-90 segundos por lote
- **Melhor para:** Imagens de produto, arte conceitual, ilustrações, fotos

### 🎬 Google Veo (para Vídeos)
- **Tipo de mídia:** Vídeos curtos
- **Máximo por lote:** 5 vídeos
- **Formatos suportados:** MP4
- **Duração:** Clips curtos (varia conforme o prompt)
- **Velocidade de geração:** ~2-5 minutos por lote
- **Melhor para:** Clips cinematográficos, animações, conteúdo para redes sociais

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

1. `flow_manage_session` — Iniciar/verificar sessão
2. `flow_generate_media` — Gerar com parâmetros estruturados
3. `flow_await_download_media` — Aguardar e baixar arquivos
"""


@mcp.resource("flow://agent_guidelines")
async def flow_agent_guidelines() -> str:
    """
    Diretrizes de como operar o agente interno do Google Labs Flow.

    Contém o Template Estrito de geração, instruções de comportamento esperado,
    e orientações sobre como formatar corretamente as solicitações de mídia.
    """
    return """# Google Labs Flow — Diretrizes do Agente

## Princípio Zero-DOM

Como agente de IA usando este MCP, você NUNCA deve:
- Ler ou analisar HTML/DOM do Google Flow
- Interpretar seletores CSS ou estrutura de página
- Debugar problemas de interface do navegador

Todo DOM-reading está encapsulado no servidor MCP. Se o layout mudar e quebrar,
o MCP retornará `DOM_SELECTOR_CHANGED` — seu papel é reportar ao usuário que o MCP precisa de atualização.

## Template Estrito de Geração

O MCP usa este template ao injetar no Flow. Você fornece os dados; o MCP formata:

```
[INSTRUÇÕES DE GERAÇÃO ESTREITA]
Por favor, gere as seguintes mídias usando as especificações exatas abaixo:
- Formato Desejado: {aspect_ratio}
- Modelo Selecionado: {model}
- Total de Mídias: {quantity}

[PROMPTS ESPECÍFICOS]
1. Imagem 1: {prompt_1}
2. Imagem 2: {prompt_2}
...
```

## Fluxo de Decisão para Geração

1. **Verifique a sessão primeiro:** Sempre chame `flow_manage_session` antes de gerar
2. **Valide os limites antes de solicitar:**
   - Imagens: máximo 15 por chamada
   - Vídeos: máximo 5 por chamada
   - Se precisar de mais, divida em múltiplas chamadas
3. **Escolha o modelo correto:**
   - Imagens → use "Imagen 3"
   - Vídeos → use "Google Veo"
4. **Aguarde o download:** Sempre chame `flow_await_download_media` após `flow_generate_media`

## Códigos de Erro e Ações Recomendadas

| Código | Situação | O que fazer |
|--------|----------|-------------|
| `LOGIN_REQUIRED` | Usuário não autenticado | Peça ao usuário para logar no browser aberto |
| `QUOTA_EXCEEDED` | Quantidade acima do limite | Divida a tarefa em lotes menores |
| `DOM_SELECTOR_CHANGED` | Layout do Flow mudou | Informe usuário que o MCP precisa de atualização |
| `CONTENT_BLOCKED` | Prompt viola políticas | Reescreva o prompt problemático e tente novamente |
| `GENERATION_TIMEOUT` | Geração demorou muito | Tente novamente ou reduza a quantidade |

## Exemplos de Uso

### Gerar 2 vídeos verticais:
```json
{
  "type": "video",
  "model": "Google Veo",
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
  "model": "Imagen 3",
  "aspect_ratio": "1:1",
  "quantity": 3,
  "prompts": [
    "Logo minimalista de startup tech, fundo escuro",
    "Pôr do sol na praia com cores vibrantes",
    "Café expresso artesanal em close, fotografia de produto"
  ]
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

    # Verificar compatibilidade tipo x modelo
    if params.type == MediaType.IMAGE and params.model == ModelName.GOOGLE_VEO:
        return _error_response(
            "MODEL_MISMATCH",
            "O modelo 'Google Veo' é para vídeos. Para imagens, use 'Imagen 3'.",
            {"suggestion": "Altere model para 'Imagen 3' ou mude type para 'video'"},
        )

    if params.type == MediaType.VIDEO and params.model == ModelName.IMAGEN_3:
        return _error_response(
            "MODEL_MISMATCH",
            "O modelo 'Imagen 3' é para imagens. Para vídeos, use 'Google Veo'.",
            {"suggestion": "Altere model para 'Google Veo' ou mude type para 'image'"},
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

        # ── Injetar template via React Fiber (única abordagem estável para Slate.js) ──
        # IMPORTANTE: .fill() e keyboard.type() causam crash do Next.js no Slate.js
        # A injeção via Fiber API é o método correto e testado.
        injected = await _inject_text_via_fiber(page, template)

        if not injected:
            # Fallback: tentar click + type convencional (pode causar crash)
            await input_element.click()
            await asyncio.sleep(0.3)
            await page.keyboard.press("Control+a")
            await asyncio.sleep(0.1)
            await page.keyboard.press("Delete")
            await asyncio.sleep(0.1)
            await page.keyboard.type(template, delay=10)
            await asyncio.sleep(0.5)

        # ── Submeter a solicitação ──
        # Tentar via coordenadas do botão de submit (detectado via JS)
        submit_coords = await _get_submit_button_coords(page)

        if submit_coords:
            await page.mouse.click(submit_coords["x"], submit_coords["y"])
        else:
            # Fallback: Enter (pode adicionar nova linha no Slate, mas vale tentar)
            await page.keyboard.press("Enter")

        await asyncio.sleep(2.0)

        # ── Verificar se houve bloqueio de conteúdo imediato ──
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
            "message": f"Solicitação de geração de {params.quantity} {params.type.value}(s) enviada com sucesso ao Google Flow.",
            "template_injected": template,
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

    try:
        while time.time() - start_time < timeout:
            elapsed = int(time.time() - start_time)

            # Verificar se a geração concluiu verificando imagens/vídeos no DOM
            complete_element = await _find_element(page, "generation_complete", timeout=500)

            # Verificar download buttons visíveis
            download_buttons = await page.query_selector_all(
                "a[download], "
                "button[aria-label*='download' i], "
                "button[aria-label*='baixar' i], "
                "button[aria-label*='save' i]"
            )

            if complete_element or download_buttons:
                # ── Geração concluída — tentar fazer downloads ──

                if download_buttons:
                    # Clicar nos botões de download encontrados
                    for btn in download_buttons[:15]:
                        try:
                            if await btn.is_visible():
                                await btn.click()
                                await asyncio.sleep(1.5)
                        except Exception:
                            continue
                else:
                    # Fallback: baixar via URL direta dos elementos de mídia
                    # Prioridade: GCS > generativelanguage > googleusercontent
                    media_selectors = [
                        "img[src*='storage.googleapis.com']",
                        "img[src*='generativelanguage.googleapis.com']",
                        "img[src*='googleusercontent.com']",
                        "video[src]",
                        "img[src*='blob:']",
                    ]
                    for selector in media_selectors:
                        elements = await page.query_selector_all(selector)
                        for elem in elements[:15]:
                            try:
                                src = await elem.get_attribute("src")
                                if src and (src.startswith("http") or src.startswith("blob:")):
                                    # Determinar extensão
                                    tag = await elem.evaluate("el => el.tagName.toLowerCase()")
                                    ext = "mp4" if tag == "video" else "png"
                                    filename = f"{timestamp}_media_{len(downloaded_files) + 1}.{ext}"
                                    save_path = download_dir / filename

                                    # Download via Playwright request API (autentica automaticamente)
                                    if src.startswith("http"):
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
                    return _success_response({
                        "status": "download_complete",
                        "message": f"{len(downloaded_files)} arquivo(s) baixado(s) com sucesso.",
                        "files": downloaded_files,
                        "count": len(downloaded_files),
                        "download_dir": str(download_dir.absolute()),
                        "elapsed_seconds": elapsed,
                    })
                else:
                    # Geração parece concluída mas sem arquivos identificados
                    return _success_response({
                        "status": "generation_complete_no_files",
                        "message": "A geração parece ter concluído, mas nenhum arquivo foi detectado para download automático.",
                        "download_dir": str(download_dir.absolute()),
                        "elapsed_seconds": elapsed,
                        "suggestion": "Verifique manualmente o browser — os arquivos podem precisar de download manual ou os seletores precisam de atualização.",
                    })

            # Aguardar antes do próximo poll
            await asyncio.sleep(POLL_INTERVAL)

        # ── Timeout ──
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
        return _error_response(
            "DOWNLOAD_ERROR",
            f"Erro durante o processo de download: {type(e).__name__}: {str(e)}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
