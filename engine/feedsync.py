#!/usr/bin/env python3
"""
SV Social Engine — Photographie du VRAI feed Instagram.
Récupère les derniers posts du compte (ceux du robot ET ceux publiés à la main
par Laurie depuis son mobile) et les enregistre dans engine/feed-instagram.json
pour que l'aperçu du Cockpit soit toujours synchronisé avec la réalité.

Tourne sur GitHub Actions (inspection quotidienne + après chaque publication).
Secrets attendus : IG_USER_ID, IG_ACCESS_TOKEN.
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH = "https://graph.instagram.com/v21.0"


def main():
    user = os.environ["IG_USER_ID"]
    token = os.environ["IG_ACCESS_TOKEN"]
    fields = "id,caption,media_type,media_url,thumbnail_url,timestamp,permalink"
    url = f"{GRAPH}/{user}/media?" + urllib.parse.urlencode(
        {"fields": fields, "limit": 24, "access_token": token})
    data = json.loads(urllib.request.urlopen(url, timeout=60).read())
    posts = []
    for m in data.get("data", []):
        posts.append({
            "id": m["id"],
            "type": m.get("media_type", "").lower(),   # image / video / carousel_album
            "caption": (m.get("caption") or "").strip()[:220],
            "thumb": m.get("thumbnail_url") or m.get("media_url"),
            "date": m.get("timestamp", "")[:10],
            "lien": m.get("permalink"),
        })
    out = os.path.join(ROOT, "engine", "feed-instagram.json")
    json.dump({"maj": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
               "posts": posts}, open(out, "w"), indent=1, ensure_ascii=False)
    print(f"feed réel enregistré : {len(posts)} posts")


if __name__ == "__main__":
    main()
