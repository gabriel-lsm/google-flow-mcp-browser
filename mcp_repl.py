import sys, json, asyncio, codecs
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())
from server import flow_manage_session, flow_generate_media, flow_await_download_media
from server import ManageSessionInput, GenerateMediaInput, AwaitDownloadInput

async def main():
    print('REPL STARTED')
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line: break
        
        try:
            req = json.loads(line)
            tool = req.get('tool')
            args = req.get('args', {})
            
            if tool == 'flow_manage_session':
                res = await flow_manage_session(ManageSessionInput(**args))
            elif tool == 'flow_generate_media':
                res = await flow_generate_media(GenerateMediaInput(**args))
            elif tool == 'flow_await_download_media':
                res = await flow_await_download_media(AwaitDownloadInput(**args))
            else:
                res = json.dumps({'error': 'unknown tool'})
            print(f'RESULT: {res}', flush=True)
        except Exception as e:
            print(f'ERROR: {str(e)}', flush=True)

asyncio.run(main())
