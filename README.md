# Google Flow MCP Agent

MCP Server para automação do [Google Labs Flow](https://labs.google/flow) com arquitetura **Zero-DOM**.
Permite que agentes de IA (Claude, etc.) gerem imagens e vídeos de forma autônoma sem jamais ler o HTML da página.

## Ferramentas Disponíveis

| Tool | Descrição |
|------|-----------|
| `flow_manage_session` | Inicia/verifica sessão do browser e autenticação Google |
| `flow_generate_media` | Gera imagens ou vídeos com parâmetros estruturados |
| `flow_await_download_media` | Aguarda geração e baixa arquivos para `./midias/` |

## Resources Disponíveis

| URI | Conteúdo |
|-----|---------|
| `flow://capabilities` | Modelos, limites, formatos disponíveis |
| `flow://agent_guidelines` | Diretrizes de uso e Template Estrito |

---

## Instalação

### 1. Pré-requisitos

- Python 3.11+
- pip ou uv

### 2. Instalar dependências

```bash
cd "Google Flow MCP Agent/mcp"
pip install -r requirements.txt
```

### 3. Instalar Chromium para Playwright

```bash
playwright install chromium
```

### 4. Verificar instalação

```bash
python -m py_compile server.py && echo "✅ Sintaxe OK"
python server.py --help
```

---

## Configuração no Claude Desktop

Adicione ao `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "google-flow": {
      "command": "python",
      "args": [
        "C:\\Users\\gabri\\OneDrive\\Documentos\\Google Flow MCP Agent\\mcp\\server.py"
      ]
    }
  }
}
```

**Localização do arquivo de configuração:**
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

---

## Uso Típico (Fluxo Completo)

### 1. Iniciar sessão

```
Chame: flow_manage_session
Parâmetros: { "action": "start" }
```

Se retornar `login_required`, vá ao browser aberto, faça login no Google e chame novamente com `login_confirmed: true`.

### 2. Gerar imagens

```
Chame: flow_generate_media
Parâmetros:
{
  "type": "image",
  "model": "Imagen 3",
  "aspect_ratio": "16:9",
  "quantity": 3,
  "prompts": [
    "Cidade futurista ao pôr do sol, cyberpunk",
    "Floresta mágica com cogumelos bioluminescentes",
    "Robô jogando xadrez com humano idoso, estilo realista"
  ]
}
```

### 3. Aguardar e baixar

```
Chame: flow_await_download_media
Parâmetros: {}
```

Os arquivos serão salvos em `./midias/` com timestamp no nome.

---

## Estrutura de Diretórios

```
mcp/
├── server.py           ← Servidor MCP principal
├── requirements.txt    ← Dependências Python
├── README.md           ← Esta documentação
├── browser_profile/    ← Perfil persistente do Chromium (criado automaticamente)
└── midias/             ← Downloads de imagens e vídeos (criado automaticamente)
```

---

## Códigos de Erro

| Código | Situação | Ação |
|--------|----------|------|
| `LOGIN_REQUIRED` | Não autenticado | Faça login no browser |
| `QUOTA_EXCEEDED` | Excedeu limite por lote | Divida em lotes menores |
| `DOM_SELECTOR_CHANGED` | Layout do Flow mudou | Atualize seletores no server.py |
| `CONTENT_BLOCKED` | Prompt violou políticas | Reescreva o prompt |
| `GENERATION_TIMEOUT` | Geração demorou demais | Tente novamente |
| `MODEL_MISMATCH` | Modelo errado para o tipo | Use Imagen 3 para imagens, Veo para vídeos |

---

## Desenvolvimento

Para testar interativamente:

```bash
# Via MCP Inspector
npx @modelcontextprotocol/inspector python server.py

# Via MCP CLI
python -m mcp dev server.py
```
