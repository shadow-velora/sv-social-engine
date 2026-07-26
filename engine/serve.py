#!/usr/bin/env python3
"""
SV Cockpit — interface locale de contrôle de l'usine sociale.
Lancement : double-clic sur "SV Cockpit.command" (ou python3 engine/serve.py)
puis http://localhost:8765
"""
import json
import os
import shutil
import subprocess
import threading
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "engine")
Q = lambda name: os.path.join(ROOT, "queue", name)
for name in ("pending", "approved", "published", "rejected"):
    os.makedirs(Q(name), exist_ok=True)

PORT = 8765
_gen_lock = threading.Lock()
_gen_running = False


def list_state(state):
    out = []
    base = Q(state)
    for it in sorted(os.listdir(base)):
        d = os.path.join(base, it)
        mp = os.path.join(d, "meta.json")
        if not os.path.isdir(d) or not os.path.exists(mp):
            continue
        meta = json.load(open(mp))
        media = sorted(f for f in os.listdir(d)
                       if f.startswith(("media", "slide")) or f.startswith("cand"))
        out.append({
            "id": it, "state": state, "type": meta.get("type", "?"),
            "caption": meta.get("caption", ""),
            "media": [f"/queue/{state}/{it}/{m}" for m in media],
        })
    return out


def run_generate():
    global _gen_running
    with _gen_lock:
        if _gen_running:
            return
        _gen_running = True
    try:
        env = dict(os.environ)
        if "FFMPEG" not in env:
            for cand in (os.path.join(ROOT, "bin", "ffmpeg"),
                         shutil.which("ffmpeg") or "", "/opt/homebrew/bin/ffmpeg"):
                if cand and os.path.exists(cand):
                    env["FFMPEG"] = cand
                    break
        script = "generate_ai_lot.py" if getattr(run_generate, "ai_mode", False) else "generate.py"
        subprocess.run(["python3", os.path.join(ENGINE, script)],
                       env=env, cwd=ROOT, timeout=3600)
    finally:
        _gen_running = False


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self.path = "/engine/cockpit.html"
            return super().do_GET()
        if self.path == "/api/queue":
            return self._json({
                "generating": _gen_running,
                "pending": list_state("pending"),
                "approved": list_state("approved"),
                "published": list_state("published"),
            })
        return super().do_GET()

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(ln) or b"{}")

        if self.path == "/api/generate":
            run_generate.ai_mode = False
            threading.Thread(target=run_generate, daemon=True).start()
            return self._json({"ok": True})

        if self.path == "/api/generate_ai":
            run_generate.ai_mode = True
            threading.Thread(target=run_generate, daemon=True).start()
            return self._json({"ok": True})

        if self.path == "/api/action":
            state, item = data.get("state"), data.get("id")
            src = os.path.join(Q(state), item)
            if not os.path.isdir(src) or "/" in item or ".." in item:
                return self._json({"error": "introuvable"}, 404)

            action = data.get("action")
            if action == "approve":
                shutil.move(src, os.path.join(Q("approved"), item))
            elif action == "reject":
                shutil.move(src, os.path.join(Q("rejected"), item))
            elif action == "save":
                mp = os.path.join(src, "meta.json")
                meta = json.load(open(mp))
                meta["caption"] = data.get("caption", meta["caption"])
                json.dump(meta, open(mp, "w"), indent=2, ensure_ascii=False)
            else:
                return self._json({"error": "action inconnue"}, 400)
            return self._json({"ok": True})

        return self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    print(f"SV Cockpit → http://localhost:{PORT}")
    subprocess.Popen(["open", f"http://localhost:{PORT}"])
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
