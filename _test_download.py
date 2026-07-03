# -*- coding: utf-8 -*-
"""Teste - apenas aguarda download da geracao ja iniciada"""
import asyncio, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from server import flow_await_download_media, AwaitDownloadInput

OUT = Path(__file__).parent.parent / "test_outputs"

async def run():
    print("[WAIT] Aguardando geracao e download... (timeout: 240s)", flush=True)
    try:
        d_str = await flow_await_download_media(AwaitDownloadInput(timeout_seconds=240))
        d = json.loads(d_str)
        print(f"[RESULT] {json.dumps(d, ensure_ascii=False)}", flush=True)
        with open(OUT / "download_result.json", "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] {e}", flush=True)

asyncio.run(run())