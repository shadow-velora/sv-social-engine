#!/usr/bin/env python3
"""
Story automatique : reprend un post publié récent pas encore passé en story,
le recadre en 1080×1920 et le publie en story Instagram.
Secrets attendus : IG_USER_ID, IG_ACCESS_TOKEN, ASSET_BASE_URL.
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "engine")
PUBLISHED = os.path.join(ROOT, "queue", "published")
STORIES = os.path.join(ROOT, "queue", "stories")
STATE = os.path.join(ENGINE, "story-state.json")
GRAPH = "https://graph.instagram.com/v21.0"

IG_USER = os.environ["IG_USER_ID"]
TOKEN = os.environ["IG_ACCESS_TOKEN"]
BASE = os.environ["ASSET_BASE_URL"].rstrip("/")


def api(path, params, method="POST"):
    params = {**params, "access_token": TOKEN}
    if method == "GET":
        req = urllib.request.Request(f"{GRAPH}/{path}?" + urllib.parse.urlencode(params))
    else:
        req = urllib.request.Request(f"{GRAPH}/{path}", data=urllib.parse.urlencode(params).encode())
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Graph API {e.code}: {e.read().decode()[:300]}") from e


def sh(*cmd):
    subprocess.run(cmd, cwd=ROOT, check=False, capture_output=True)


def main():
    os.makedirs(STORIES, exist_ok=True)
    state = json.load(open(STATE)) if os.path.exists(STATE) else {"faites": []}
    posts = sorted((d for d in os.listdir(PUBLISHED)
                    if os.path.isdir(os.path.join(PUBLISHED, d))
                    and os.path.exists(os.path.join(PUBLISHED, d, "media.jpg"))), reverse=True)
    cible = next((p for p in posts if p not in state["faites"]), None)
    if not cible:
        state["faites"] = []  # tout a été storié : on recommence le cycle
        cible = posts[0] if posts else None
    if not cible:
        print("aucun post publié — pas de story")
        sys.exit(0)

    from PIL import Image
    img = Image.open(os.path.join(PUBLISHED, cible, "media.jpg")).convert("RGB")
    tw, th = 1080, 1920
    scale = max(tw / img.width, th / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    x, y = (img.width - tw) // 2, (img.height - th) // 3  # cadrage haut : le visage reste visible
    story_dir = os.path.join(STORIES, cible)
    os.makedirs(story_dir, exist_ok=True)
    sp = os.path.join(story_dir, "media.jpg")
    img.crop((x, y, x + tw, y + th)).save(sp, quality=92)

    # l'image doit exister sur GitHub AVANT l'appel API (leçon du 27/07)
    sh("git", "config", "user.name", "sv-engine")
    sh("git", "config", "user.email", "engine@shadowvelora.com")
    sh("git", "add", "queue/stories")
    sh("git", "commit", "-m", f"story {cible}")
    sh("git", "push")
    time.sleep(5)

    rel = f"queue/stories/{urllib.parse.quote(cible)}/media.jpg"
    c = api(f"{IG_USER}/media", {"media_type": "STORIES", "image_url": f"{BASE}/{rel}"})
    for _ in range(40):
        st = api(c["id"], {"fields": "status_code"}, "GET")
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"story container en erreur: {st}")
        time.sleep(5)
    res = api(f"{IG_USER}/media_publish", {"creation_id": c["id"]})
    print("story publiée :", cible, "→", res.get("id"))

    state["faites"].append(cible)
    json.dump(state, open(STATE, "w"), indent=2)
    sh("git", "add", "engine/story-state.json")
    sh("git", "commit", "-m", "story state")
    sh("git", "push")


if __name__ == "__main__":
    main()
