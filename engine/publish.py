#!/usr/bin/env python3
"""
SV Social Engine — Publieur Instagram (API officielle Meta Graph).
Prend le plus ancien contenu de queue/approved/, le publie sur Instagram,
puis le déplace dans queue/published/.

Secrets attendus (variables d'environnement — GitHub Secrets) :
  IG_USER_ID      id du compte Instagram professionnel
  IG_ACCESS_TOKEN jeton longue durée (auto-prolongé à chaque run)
  ASSET_BASE_URL  URL publique brute du repo (raw.githubusercontent.com/...)
"""
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPROVED = os.path.join(ROOT, "queue", "approved")
PUBLISHED = os.path.join(ROOT, "queue", "published")
GRAPH = "https://graph.instagram.com/v21.0"  # API Instagram Login (jamais graph.facebook.com avec ce token)

IG_USER = os.environ["IG_USER_ID"]
TOKEN = os.environ["IG_ACCESS_TOKEN"]
BASE = os.environ["ASSET_BASE_URL"].rstrip("/")


def api(path, params, method="POST"):
    params = {**params, "access_token": TOKEN}
    data = urllib.parse.urlencode(params).encode()
    url = f"{GRAPH}/{path}"
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=data)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"Graph API {e.code}: {body}") from e


def wait_ready(container_id, tries=30):
    for _ in range(tries):
        st = api(container_id, {"fields": "status_code"}, "GET")
        if st.get("status_code") == "FINISHED":
            return
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"container en erreur: {st}")
        time.sleep(10)
    raise RuntimeError("container jamais prêt")


def publish_item(folder):
    with open(os.path.join(folder, "meta.json")) as f:
        meta = json.load(f)
    rel = os.path.relpath(folder, ROOT).replace(os.sep, "/")
    caption = meta["caption"]
    kind = meta["type"]

    if kind == "reel":
        c = api(f"{IG_USER}/media", {
            "media_type": "REELS",
            "video_url": f"{BASE}/{urllib.parse.quote(rel)}/media.mp4",
            "caption": caption,
        })
        wait_ready(c["id"])
        return api(f"{IG_USER}/media_publish", {"creation_id": c["id"]})

    if kind == "carousel":
        slides = sorted(f for f in os.listdir(folder) if f.startswith("slide-"))
        children = []
        for s in slides:
            c = api(f"{IG_USER}/media", {
                "image_url": f"{BASE}/{urllib.parse.quote(rel)}/{s}",
                "is_carousel_item": "true",
            })
            children.append(c["id"])
        c = api(f"{IG_USER}/media", {
            "media_type": "CAROUSEL",
            "children": ",".join(children),
            "caption": caption,
        })
        return api(f"{IG_USER}/media_publish", {"creation_id": c["id"]})

    # studio / band : image simple
    c = api(f"{IG_USER}/media", {
        "image_url": f"{BASE}/{urllib.parse.quote(rel)}/media.jpg",
        "caption": caption,
    })
    return api(f"{IG_USER}/media_publish", {"creation_id": c["id"]})


def refresh_token():
    """Prolonge le jeton Instagram Login (60 j glissants) à chaque run,
    et met à jour le secret GitHub si GH_PAT est fourni."""
    try:
        r = api("refresh_access_token",
                {"grant_type": "ig_refresh_token"}, "GET")
    except Exception as e:
        print("refresh token: ignoré,", e)
        return
    new_tok = r.get("access_token")
    if not new_tok:
        return
    print("::add-mask::" + new_tok)
    print(f"jeton prolongé ({r.get('expires_in', 0) // 86400} jours)")
    pat, repo = os.environ.get("GH_PAT"), os.environ.get("GITHUB_REPOSITORY")
    if not (pat and repo) or new_tok == TOKEN:
        return
    try:
        import base64
        from nacl import encoding, public
        def gh(method, path, payload=None):
            req = urllib.request.Request(
                f"https://api.github.com{path}",
                data=json.dumps(payload).encode() if payload else None,
                method=method,
                headers={"Authorization": f"Bearer {pat}",
                         "Accept": "application/vnd.github+json"})
            body = urllib.request.urlopen(req, timeout=30).read()
            return json.loads(body) if body else {}
        pk = gh("GET", f"/repos/{repo}/actions/secrets/public-key")
        box = public.SealedBox(public.PublicKey(pk["key"].encode(), encoding.Base64Encoder()))
        enc = base64.b64encode(box.encrypt(new_tok.encode())).decode()
        gh("PUT", f"/repos/{repo}/actions/secrets/IG_ACCESS_TOKEN",
           {"encrypted_value": enc, "key_id": pk["key_id"]})
        print("secret IG_ACCESS_TOKEN mis à jour — rotation automatique OK")
    except Exception as e:
        print("rotation secret: ignorée,", e)


def main():
    os.makedirs(PUBLISHED, exist_ok=True)
    # Modèle VETO : on publie d'abord ce que Laurie a validé ; si rien de
    # validé, on publie le plus ancien contenu en attente (non rejeté).
    # Seul le dossier rejected/ ne part jamais.
    pending = os.path.join(ROOT, "queue", "pending")
    os.makedirs(pending, exist_ok=True)
    source = APPROVED
    items = sorted(d for d in os.listdir(APPROVED)
                   if os.path.isdir(os.path.join(APPROVED, d)))
    if not items:
        source = pending
        items = sorted(d for d in os.listdir(pending)
                       if os.path.isdir(os.path.join(pending, d)))
    if not items:
        print("File vide — rien à publier.")
        sys.exit(0)
    folder = os.path.join(source, items[0])
    print("Publication de", items[0], "(source:", os.path.basename(source) + ")")
    res = publish_item(folder)
    print("OK, media id:", res.get("id"))
    shutil.move(folder, os.path.join(PUBLISHED, items[0]))
    refresh_token()


if __name__ == "__main__":
    main()
