#!/usr/bin/env python3
"""
Passe finale Magnific : ré-injecte du détail RÉEL (peau, matière) dans les images.
Usage module : magnific.enhance(in_path, out_path) — ou CLI : python3 magnific.py in.jpg out.jpg
"""
import base64
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.magnific.com/v1/ai/image-upscaler"

SKIN_PROMPT = ("natural unretouched photograph, real human skin, film photography look, "
               "healthy natural face with even healthy lip color, real fabric texture")


def api_key():
    k = os.environ.get("MAGNIFIC_API_KEY")
    if k:
        return k
    envp = os.path.join(ROOT, ".env")
    if os.path.exists(envp):
        for line in open(envp):
            if line.startswith("MAGNIFIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("MAGNIFIC_API_KEY manquante")


def _curl(method, url, key, payload=None):
    cmd = ["curl", "-s", "--max-time", "120", "-H", f"x-magnific-api-key: {key}"]
    if payload is not None:
        cmd += ["-H", "Content-Type: application/json", "-X", method, "-d", "@-"]
        r = subprocess.run(cmd + [url], input=json.dumps(payload).encode(),
                           capture_output=True, check=True)
    else:
        r = subprocess.run(cmd + [url], capture_output=True, check=True)
    return json.loads(r.stdout)


def enhance(in_path, out_path, creativity=0, hdr=1,
            optimized_for="films_n_photography", prompt=SKIN_PROMPT, timeout_s=600):
    key = api_key()
    payload = {
        "image": base64.b64encode(open(in_path, "rb").read()).decode(),
        "scale_factor": "2x",
        "optimized_for": optimized_for,
        "prompt": prompt,
        "creativity": creativity,
        "hdr": hdr,
    }
    resp = _curl("POST", API, key, payload)
    task = (resp.get("data") or {}).get("task_id") or resp.get("task_id")
    if not task:
        raise RuntimeError(f"Magnific: pas de task_id — {str(resp)[:300]}")
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        time.sleep(10)
        st = _curl("GET", f"{API}/{task}", key)
        data = st.get("data") or st
        status = data.get("status")
        if status in ("COMPLETED", "DONE", "SUCCESS"):
            gen = data.get("generated") or []
            if not gen:
                raise RuntimeError(f"Magnific: terminé sans image — {str(st)[:200]}")
            url = gen[0] if isinstance(gen[0], str) else gen[0].get("url")
            subprocess.run(["curl", "-sL", "--max-time", "300", "-o", out_path, url], check=True)
            return out_path
        if status in ("FAILED", "ERROR"):
            raise RuntimeError(f"Magnific: échec — {str(st)[:300]}")
    raise RuntimeError("Magnific: timeout")


if __name__ == "__main__":
    print(enhance(sys.argv[1], sys.argv[2]))
