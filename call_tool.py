import asyncio
import json
import sys
import codecs
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

from server import flow_manage_session, flow_generate_media, flow_await_download_media
from server import ManageSessionInput, GenerateMediaInput, AwaitDownloadInput

async def main():
    tool = sys.argv[1]
    args_file = sys.argv[2]
    
    with open(args_file, 'r', encoding='utf-8') as f:
        args = json.load(f)
        
    try:
        if tool == 'flow_manage_session':
            res = await flow_manage_session(ManageSessionInput(**args))
        elif tool == 'flow_generate_media':
            res = await flow_generate_media(GenerateMediaInput(**args))
        elif tool == 'flow_await_download_media':
            res = await flow_await_download_media(AwaitDownloadInput(**args))
        else:
            res = json.dumps({"error": "tool not found"})
            
        with open('tool_output.json', 'w', encoding='utf-8') as f:
            f.write(res)
    except Exception as e:
        with open('tool_output.json', 'w', encoding='utf-8') as f:
            json.dump({"status": "error", "message": str(e)}, f)

asyncio.run(main())
