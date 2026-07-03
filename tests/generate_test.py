import asyncio
import json
import sys
from server import flow_manage_session, flow_generate_media, flow_await_download_media, flow_reply_to_agent
from server import ManageSessionInput, GenerateMediaInput, AwaitDownloadInput, ReplyToAgentInput

sys.stdout.reconfigure(encoding='utf-8')

async def handle_download(req_json, type_str):
    if req_json.get('status') == 'generation_started':
        print(f'Aguardando download da imagem {type_str}...')
        attempts = 0
        while True:
            d_str = await flow_await_download_media(AwaitDownloadInput(timeout_seconds=300))
            d = json.loads(d_str)
            
            if d.get('status') == 'agent_requires_interaction':
                attempts += 1
                if attempts > 5:
                    print('Limite de tentativas de aprovação excedido. Abortando loop infinito.')
                    break
                print(f'Agent requer interação para a imagem {type_str} (tentativa {attempts}). Aprovando automaticamente...')
                await flow_reply_to_agent(ReplyToAgentInput(approve=True))
                print('Aprovado! Aguardando o download continuar...')
                # Loop will continue and await download again
            else:
                print(f'Resultado do Download {type_str}:', json.dumps(d, indent=2))
                break

async def generate_images():
    print('=============================================')
    print('  INICIANDO SESSÃO (Verificando Login)')
    print('=============================================')
    r_str = await flow_manage_session(ManageSessionInput(action='start', login_confirmed=False))
    r = json.loads(r_str)
    
    if r.get('status') == 'login_required':
        print('==== POR FAVOR FAÇA LOGIN NA JANELA DO BROWSER ====')
        for i in range(24):
            print(f'Aguardando login... {120 - i*5}s restantes')
            await asyncio.sleep(5)
            r_str = await flow_manage_session(ManageSessionInput(action='start', login_confirmed=True))
            r = json.loads(r_str)
            if r.get('status') == 'ready':
                print('Login detectado com sucesso!')
                break
        else:
            print('Tempo esgotado para o login. Abortando.')
            return
    elif r.get('status') != 'ready':
        print(f"Erro ao iniciar a sessão: {json.dumps(r, ensure_ascii=False)}")
        return
            
    print('\n=============================================')
    print('  GERANDO IMAGEM HORIZONTAL (16:9)')
    print('=============================================')
    req1_str = await flow_generate_media(GenerateMediaInput(
        type='image',
        quantity=1,
        prompts=[
            'A futuristic cyberpunk city in the rain, neon lights, highly detailed 4k, horizontal cinematic'
        ],
        model='Nano Banana 2',
        aspect_ratio='16:9'
    ))
    req1 = json.loads(req1_str)
    print('Status Geração Horizontal:', req1.get('status'))
    
    await handle_download(req1, "Horizontal")
        
    print('\n=============================================')
    print('  GERANDO IMAGEM VERTICAL (9:16)')
    print('=============================================')
    req2_str = await flow_generate_media(GenerateMediaInput(
        type='image',
        quantity=1,
        prompts=[
            'A magical ancient tree glowing with ethereal light in a dark enchanted forest, vertical frame'
        ],
        model='Nano Banana 2',
        aspect_ratio='9:16'
    ))
    req2 = json.loads(req2_str)
    print('Status Geração Vertical:', req2.get('status'))
    
    await handle_download(req2, "Vertical")
        
    print('\n=============================================')
    print('  GERAÇÃO FINALIZADA')
    print('=============================================')

asyncio.run(generate_images())
