import asyncio
import json
import sys
from server import flow_manage_session, flow_generate_media, flow_await_download_media
from server import ManageSessionInput, GenerateMediaInput, AwaitDownloadInput

async def run_tests():
    print('=============================================')
    print('  INICIANDO SESSAO (Aguardando login)')
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
        print(f"Erro ao iniciar a sessão: {r}")
        return
            
    print('\n=============================================')
    print('  TESTE 1: 4 Imagens (Imagen 3)')
    print('=============================================')
    req1_str = await flow_generate_media(GenerateMediaInput(
        type='image',
        quantity=4,
        prompts=[
            'A futuristic cyberpunk city in the rain, neon lights, 4k',
            'A magical forest with glowing mushrooms and fairies',
            'A cute robot reading a book in a library, cinematic lighting',
            'An astronaut riding a horse on Mars'
        ],
        model='Imagen 3',
        aspect_ratio='16:9'
    ))
    req1 = json.loads(req1_str)
    print('Generate Result 1:', req1)
    
    if req1.get('status') == 'generation_started':
        print('Aguardando download...')
        d1_str = await flow_await_download_media(AwaitDownloadInput(timeout_seconds=300))
        d1 = json.loads(d1_str)
        print('Download Result 1:', d1)
        
    print('\n=============================================')
    print('  TESTE 2: 2 Videos (Google Veo)')
    print('=============================================')
    req2_str = await flow_generate_media(GenerateMediaInput(
        type='video',
        quantity=2,
        prompts=[
            'A drone flying through a canyon at sunset',
            'A time lapse of a flower blooming'
        ],
        model='Google Veo',
        aspect_ratio='16:9'
    ))
    req2 = json.loads(req2_str)
    print('Generate Result 2:', req2)
    
    if req2.get('status') == 'generation_started':
        print('Aguardando download...')
        d2_str = await flow_await_download_media(AwaitDownloadInput(timeout_seconds=600))
        d2 = json.loads(d2_str)
        print('Download Result 2:', d2)
        
    print('\n=============================================')
    print('  TESTE 3: 1 Video (Google Veo)')
    print('=============================================')
    req3_str = await flow_generate_media(GenerateMediaInput(
        type='video',
        quantity=1,
        prompts=[
            'A beautiful cinematic shot of a galaxy'
        ],
        model='Google Veo',
        aspect_ratio='16:9'
    ))
    req3 = json.loads(req3_str)
    print('Generate Result 3:', req3)
    
    if req3.get('status') == 'generation_started':
        print('Aguardando download...')
        d3_str = await flow_await_download_media(AwaitDownloadInput(timeout_seconds=600))
        d3 = json.loads(d3_str)
        print('Download Result 3:', d3)

    print('\n=============================================')
    print('  FIM DOS TESTES')
    print('=============================================')

asyncio.run(run_tests())
