"""Probe every model /v1/models lists, through a plain chat completion.

Cheap: 8 completion tokens each. Records the real status per model so the
difference between 'retired', 'rate limited' and 'not a chat model' is visible
rather than collapsed into one failure.
"""
import asyncio
import json
import os
import re
import sys

import httpx

SP = os.path.dirname(os.path.abspath(__file__))
URL = "https://integrate.api.nvidia.com/v1/chat/completions"

# Not chat endpoints - embeddings, rerankers, safety classifiers, OCR, vision-only.
SKIP = re.compile(
    r"embed|rerank|nvclip|nemotron-parse|riva-translate|guard|content-safety|"
    r"topic-control|reward|video-detector|kosmos|deplot|fuyu|neva|vila|"
    r"ising-calibration|nemoretriever"
)


async def probe(client: httpx.AsyncClient, key: str, model: str) -> dict:
    payload = {"model": model, "messages": [{"role": "user", "content": "Say ok."}],
               "max_tokens": 8}
    try:
        r = await client.post(URL, json=payload,
                              headers={"Authorization": f"Bearer {key}"}, timeout=90)
    except Exception as e:
        return {"model": model, "status": "transport", "detail": str(e)[:70]}
    if r.status_code == 200:
        body = r.json()
        text = body["choices"][0]["message"].get("content") or ""
        return {"model": model, "status": "ok", "detail": text.strip()[:30]}
    detail = r.text[:110].replace("\n", " ")
    return {"model": model, "status": str(r.status_code), "detail": detail}


async def main() -> None:
    key = open(os.path.join(os.path.dirname(SP), "key.txt")).read().strip() \
        if os.path.exists(os.path.join(os.path.dirname(SP), "key.txt")) else sys.argv[1]
    ids = [m["id"] for m in json.load(open(f"{SP}/models-listed-2026-08-26.json"))["data"]]
    chat = [m for m in ids if not SKIP.search(m)]
    print(f"{len(ids)} listed, probing {len(chat)} chat candidates "
          f"({len(ids) - len(chat)} skipped as non-chat)\n", flush=True)

    sem = asyncio.Semaphore(4)
    results = []

    async with httpx.AsyncClient() as client:
        async def one(m: str) -> None:
            async with sem:
                res = await probe(client, key, m)
                results.append(res)
                print(f"  {res['status']:10} {m:48} {res['detail'][:50]}", flush=True)

        await asyncio.gather(*(one(m) for m in chat))

    json.dump(results, open(f"{SP}/model-liveness-latest.json", "w"), indent=2)
    ok = [r["model"] for r in results if r["status"] == "ok"]
    print(f"\n=== {len(ok)} callable of {len(chat)} probed ===")


asyncio.run(main())
