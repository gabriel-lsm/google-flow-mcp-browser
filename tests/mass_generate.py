import asyncio
import json
import os
import shutil
import time

from server import flow_manage_session, flow_generate_media, flow_await_download_media
from server import flow_read_agent_status, flow_reply_to_agent
from server import ManageSessionInput, GenerateMediaInput, AwaitDownloadInput
from server import ReplyToAgentInput

target_dir = r"C:\Users\gabri\OneDrive\Área de Trabalho\teste 1"
os.makedirs(target_dir, exist_ok=True)

def log(msg):
    print(msg, flush=True)
    with open("mass_generate_log.txt", "a", encoding="utf-8") as f:
        f.write(msg + "\n")

async def wait_and_download(timeout):
    attempts = 0
    while True:
        log("Waiting for download...")
        d_str = await flow_await_download_media(AwaitDownloadInput(timeout_seconds=timeout))
        d = json.loads(d_str)
        if d.get('status') == 'agent_requires_interaction':
            attempts += 1
            if attempts > 5:
                log("Max interaction attempts reached. Breaking loop.")
                return []
            
            log("Agent requires interaction! Reading status...")
            status_str = await flow_read_agent_status()
            log(f"Agent status: {status_str}")
            
            log("Approving interaction...")
            reply_str = await flow_reply_to_agent(ReplyToAgentInput(approve=True))
            log(f"Reply response: {reply_str}")
            
            await asyncio.sleep(5)
            continue
        elif d.get('status') == 'download_complete':
            log(f"Download complete: {len(d.get('files', []))} files.")
            return d.get('files', [])
        else:
            log(f"Error or timeout: {d}")
            return []

async def ensure_session():
    r_str = await flow_manage_session(ManageSessionInput(action='start', login_confirmed=False))
    r = json.loads(r_str)
    
    if r.get('status') == 'login_required':
        log('==== POR FAVOR FAÇA LOGIN NA JANELA DO BROWSER ====')
        for i in range(24):
            log(f'Aguardando login... {120 - i*5}s restantes')
            await asyncio.sleep(5)
            r_str = await flow_manage_session(ManageSessionInput(action='start', login_confirmed=True))
            r = json.loads(r_str)
            if r.get('status') == 'ready':
                log('Login detectado com sucesso!')
                return True
        else:
            log('Tempo esgotado para o login. Abortando.')
            return False
    elif r.get('status') == 'ready':
        return True
    return False

async def run():
    open("mass_generate_log.txt", "w", encoding="utf-8").close() # clear log
    log("Starting session...")
    
    if not await ensure_session():
        return
            
    log("Session ready. Starting image generation.")
    prompts_img = [
        "A beautiful landscape with mountains and a river, digital art",
        "A futuristic city with flying cars at night",
        "A cute puppy playing with a ball in a park",
        "A majestic dragon flying over a castle",
        "A cozy cabin in the woods during winter",
        "An astronaut floating in space with Earth in background",
        "A detailed portrait of a cyberpunk character",
        "A magical forest with glowing trees and fairies",
        "A steam-punk train crossing a bridge",
        "A serene beach at sunset with palm trees",
        "A hyper-realistic sports car on a race track",
        "A fantasy warrior holding a glowing sword",
        "A space station orbiting a red planet",
        "A underwater city with submarines and glowing fish",
        "A vintage photography of a 1920s street",
        "A surreal painting of floating islands",
        "A realistic painting of a bowl of fruit",
        "A robot cooking dinner in a modern kitchen",
        "A wizard casting a spell in a dark cave",
        "A bustling market in an ancient middle eastern city"
    ]
    
    all_files = []
    
    for i in range(0, 20, 4):
        batch = prompts_img[i:i+4]
        log(f"Generating images batch {i//4 + 1}/5...")
        req_str = await flow_generate_media(GenerateMediaInput(
            type='image',
            quantity=len(batch),
            prompts=batch,
            model='Nano Banana 2',
            aspect_ratio='16:9'
        ))
        req = json.loads(req_str)
        if req.get('status') == 'generation_started':
            log("Generation started, awaiting download...")
            files = await wait_and_download(300)
            if not files:
                log("Batch failed or returned 0 files. Attempting to recover session.")
                await ensure_session()
            all_files.extend(files)
        else:
            log(f"Failed to start generation: {req}")
            await ensure_session()
        
    prompts_vid = [
        "A cinematic pan over a futuristic city at sunset",
        "A time lapse of clouds moving over a mountain",
        "A slow motion shot of a water drop falling",
        "A dog running through a field of flowers",
        "A spaceship taking off from a landing pad"
    ]
    
    for i in range(0, 5, 2):
        batch = prompts_vid[i:i+2]
        log(f"Generating videos batch {i//2 + 1}/3...")
        req_str = await flow_generate_media(GenerateMediaInput(
            type='video',
            quantity=len(batch),
            prompts=batch,
            model='Veo 3.1 - Fast',
            aspect_ratio='16:9'
        ))
        req = json.loads(req_str)
        if req.get('status') == 'generation_started':
            log("Generation started, awaiting download...")
            files = await wait_and_download(600)
            if not files:
                log("Batch failed or returned 0 files. Attempting to recover session.")
                await ensure_session()
            all_files.extend(files)
        else:
            log(f"Failed to start generation: {req}")
            await ensure_session()
            
    log("Moving files to target directory...")
    moved_count = 0
    for src in all_files:
        if os.path.exists(src):
            fname = os.path.basename(src)
            dst = os.path.join(target_dir, fname)
            shutil.copy2(src, dst)
            log(f"Copied {fname}")
            moved_count += 1
    
    log(f"Done! Moved {moved_count} files in total.")

if __name__ == '__main__':
    asyncio.run(run())
