#!/usr/bin/env python3
"""Régénère une version corrigée d'un post rejeté, en tenant compte de la raison de Laurie.
Usage : python3 engine/regenerate.py <nom-du-dossier-dans-rejected>"""
import json
import os
import sys

ENGINE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)
import generate as core
import generate_ai as gai


def main(item):
    folder = os.path.join(ROOT, "queue", "rejected", item)
    reason = ""
    mp = os.path.join(folder, "meta.json")
    if os.path.exists(mp):
        reason = json.load(open(mp)).get("rejet", "")
    parts = item.split("_", 2)
    tail = parts[2] if len(parts) == 3 else item
    kind, _, handle = tail.partition("_")
    prods = {p["handle"]: p for p in core.fetch_products() if p.get("images")}
    prod = prods.get(handle)
    if not prod:
        print(f"⚠️ produit introuvable pour {item} — régénération impossible")
        return
    key = gai.api_key()
    captions = core.load_captions()
    state = core.load_state()
    print(f"Régénération de {handle} ({kind}) — correction : {reason or 'aucune raison donnée'}")
    if kind == "ai-bust":
        gai.make_no_face("bust", prod, captions, state, key, correction=reason)
    elif kind == "ai-chaise":
        gai.make_no_face("chaise", prod, captions, state, key, correction=reason)
    else:
        gai.make_model_post(prod, captions, state, key,
                            concept=f"version corrigée ({reason[:60]})" if reason else "version corrigée",
                            correction=reason)
    core.save_state(state)


if __name__ == "__main__":
    main(sys.argv[1])
