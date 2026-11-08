"""Full-page screenshots of the running UI via headless Chrome (DevTools protocol). Used for docs and visual checks.

    python streamlit-ui/scripts/screenshot.py "<url>" out.png [--width 1500] [--wait 12] [--click "text to click"]

Needs Google Chrome and the `websockets` package. Streamlit renders over a websocket, so this waits in real time.
"""
import argparse
import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ap = argparse.ArgumentParser()
ap.add_argument("url")
ap.add_argument("out")
ap.add_argument("--width", type=int, default=1500)
ap.add_argument("--wait", type=float, default=12)
ap.add_argument("--click", action="append", default=[], help="visible text of a tab/button to click before capturing (repeatable)")
ap.add_argument("--port", type=int, default=9333)
ap.add_argument("--header", action="append", default=[], help="extra request header, e.g. 'Authorization: Basic ...' (repeatable)")
args = ap.parse_args()


async def main() -> None:
    profile = tempfile.mkdtemp()
    proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", f"--remote-debugging-port={args.port}", f"--user-data-dir={profile}",
                             f"--window-size={args.width},1000", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = None
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://localhost:{args.port}/json"))
                ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
            n = 0

            async def send(method, **params):
                nonlocal n
                n += 1
                await ws.send(json.dumps({"id": n, "method": method, "params": params}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == n:
                        return msg.get("result", {})

            await send("Emulation.setDeviceMetricsOverride", width=args.width, height=1000, deviceScaleFactor=1, mobile=False)
            if args.header:
                await send("Network.enable")
                await send("Network.setExtraHTTPHeaders", headers=dict(h.split(": ", 1) for h in args.header))
            await send("Page.navigate", url=args.url)
            await asyncio.sleep(args.wait)
            for text in args.click:
                js = ("(() => { const els=[...document.querySelectorAll('button,[role=tab],[role=radio],label')];"
                      f"const e=els.find(x=>x.innerText && x.innerText.includes({json.dumps(text)})); if(e){{e.click();return true}} return false }})()")
                res = await send("Runtime.evaluate", expression=js, returnByValue=True)
                if not res.get("result", {}).get("value"):
                    print(f"warning: nothing to click for {text!r}", file=sys.stderr)
                await asyncio.sleep(3)
            res = await send("Runtime.evaluate", returnByValue=True, expression=(
                "Math.max(document.documentElement.scrollHeight, (document.querySelector('[data-testid=stMain]')||{}).scrollHeight||0,"
                " (document.querySelector('section.main')||{}).scrollHeight||0)"))
            h = max(1000, min(int(res.get("result", {}).get("value") or 1000), 6000))
            await send("Emulation.setDeviceMetricsOverride", width=args.width, height=h, deviceScaleFactor=1, mobile=False)
            await asyncio.sleep(1.5)
            shot = await send("Page.captureScreenshot", format="png")
            open(args.out, "wb").write(base64.b64decode(shot["data"]))
            print(f"saved {args.out} ({args.width}x{h})")
    finally:
        proc.terminate()

asyncio.run(main())
