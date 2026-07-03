import asyncio
import json
import os
import random
import glob

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import (
    flow_manage_session, flow_list_projects, flow_check_status,
    flow_generate_media, flow_await_download_media,
    flow_read_agent_status, flow_reply_to_agent,
    ManageSessionInput, GenerateMediaInput, AwaitDownloadInput, ReplyToAgentInput
)

from pathlib import Path

# Configurações
DOWNLOAD_DIR = Path(__file__).parent / "test_outputs" / "final_test_bulletproof"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

def log(msg):
    print(msg, flush=True)

async def wait_and_download(timeout, file_prefix=None):
    attempts = 0
    while True:
        log("Aguardando download...")
        input_args = AwaitDownloadInput(
            timeout_seconds=timeout, 
            download_dir=str(DOWNLOAD_DIR), 
            file_prefix=file_prefix
        )
        d_str = await flow_await_download_media(input_args)
        d = json.loads(d_str)
        
        status = d.get('status')
        if status == 'agent_requires_interaction':
            attempts += 1
            if attempts > 5:
                log("Max interações atingido.")
                return []
            
            log("Agent requisitou interação, aprovando...")
            await flow_reply_to_agent(ReplyToAgentInput(approve=True))
            await asyncio.sleep(5)
            continue
        elif status == 'download_complete':
            files = d.get('files', [])
            log(f"Download completo: {len(files)} arquivos.")
            return files
        elif status == 'generation_complete_no_files':
            log("Geração completa, mas arquivos não detectados.")
            return []
        else:
            log(f"Timeout ou erro: {d}")
            return []

async def run_tests():
    log("=== Iniciando Testes Finais do MCP ===")
    
    # 1. Gerenciamento de Sessão Inteligente
    log("Iniciando sessão...")
    r_str = await flow_manage_session(ManageSessionInput(action='start', login_confirmed=False, project_url="new"))
    r = json.loads(r_str)
    
    if r.get('status') == 'login_required':
        log("Login requerido. Por favor, faça o login no navegador aberto.")
        for i in range(12):
            await asyncio.sleep(5)
            r_str = await flow_manage_session(ManageSessionInput(action='start', login_confirmed=True))
            r = json.loads(r_str)
            if r.get('status') == 'ready':
                break
        else:
            log("Falha ao detectar login.")
            return

    log("Testando flow_list_projects...")
    proj_str = await flow_list_projects()
    log(f"Projetos: {proj_str}")
    
    # Abrir o primeiro projeto retornado para continuar, ou criar um novo
    target_project = "new"
    try:
        proj_data = json.loads(proj_str)
        if proj_data.get("projects"):
            target_project = proj_data["projects"][0]["url"]
    except Exception:
        pass
        
    log(f"Abrindo projeto para os testes: {target_project}")
    await flow_manage_session(ManageSessionInput(action='start', login_confirmed=True, project_url=target_project))
    
    # Tarefa 1: 4 imagens, Nano Banana 2, vertical, gato fofo, renomeados para gatinhos_fofos_X.png
    log("\\n--- TAREFA 1: 4 Imagens Gatinhos Fofos ---")
    prompts_gatos = [
        "Um gatinho muito fofo dormindo num cesto",
        "Um gatinho laranja brincando com lã",
        "Um gatinho filhote olhando para as estrelas",
        "Um gatinho persa elegante com laço"
    ]
    req_str = await flow_generate_media(GenerateMediaInput(
        type='image',
        model='Nano Banana 2',
        aspect_ratio='9:16',
        quantity=4,
        prompts=prompts_gatos
    ))
    if json.loads(req_str).get('status') == 'generation_started':
        files_gatos = await wait_and_download(300, file_prefix='gatinhos_fofos')
    else:
        log(f"Erro ao iniciar Tarefa 1: {req_str}")
        
    # Tarefa 2: 2 vídeos, Veo 3.1 Lite, vertical, panda na neve
    log("\\n--- TAREFA 2: 2 Vídeos Panda na Neve ---")
    prompts_pandas = [
        "Um panda correndo feliz na neve grossa",
        "Panda filhote rolando na neve numa montanha"
    ]
    req_str = await flow_generate_media(GenerateMediaInput(
        type='video',
        model='Veo 3.1 - Lite',
        aspect_ratio='9:16',
        quantity=2,
        prompts=prompts_pandas
    ))
    if json.loads(req_str).get('status') == 'generation_started':
        await wait_and_download(400, file_prefix='panda_neve')
    else:
        log(f"Erro ao iniciar Tarefa 2: {req_str}")

    # Tarefa 3: 20 imagens mistas
    log("\\n--- TAREFA 3: 20 Imagens Diversas (5 batches) ---")
    aspects = ['1:1', '16:9', '9:16', '4:3', '3:4']
    models = ['Nano Banana 2', 'Nano Banana Pro']
    all_mixed_files = []
    
    for i in range(5):
        log(f"Batch {i+1}/5...")
        prompts_batch = [f"Paisagem incrível variação {i*4 + j}" for j in range(4)]
        req_str = await flow_generate_media(GenerateMediaInput(
            type='image',
            model=random.choice(models),
            aspect_ratio=random.choice(aspects),
            quantity=4,
            prompts=prompts_batch
        ))
        if json.loads(req_str).get('status') == 'generation_started':
            batch_files = await wait_and_download(300, file_prefix=f'mista_batch_{i}')
            all_mixed_files.extend(batch_files)

    # Tarefa 4: 1 vídeo com imagem de referência
    log("\\n--- TAREFA 4: Vídeo com Imagem de Referência ---")
    if all_mixed_files:
        ref_image = random.choice(all_mixed_files)
    else:
        # fallback case
        images_found = glob.glob(os.path.join(str(DOWNLOAD_DIR), "*.png"))
        ref_image = images_found[0] if images_found else None

    if ref_image:
        log(f"Usando imagem {ref_image} como referência.")
        req_str = await flow_generate_media(GenerateMediaInput(
            type='video',
            model='Veo 3.1 - Lite',
            aspect_ratio='16:9',
            quantity=1,
            prompts=["Fazer um zoom lento e cinematográfico desta cena"],
            reference_image=ref_image
        ))
        if json.loads(req_str).get('status') == 'generation_started':
            await wait_and_download(400, file_prefix='video_com_referencia')
    else:
        log("Nenhuma imagem gerada para servir de referência.")

    log("\\n=== TESTES CONCLUÍDOS ===")

if __name__ == "__main__":
    asyncio.run(run_tests())
