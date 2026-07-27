#!/usr/bin/env python3
"""Creative Producer : exécute le plan du Social Media Manager (concepts créatifs inclus)."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_ai as g
import generate as core

ENGINE = os.path.dirname(os.path.abspath(__file__))
key = g.api_key()
captions = core.load_captions()
state = core.load_state()
products = [p for p in core.fetch_products() if p.get("images")]

plan_path = os.path.join(ENGINE, "plan-semaine.json")
plan = json.load(open(plan_path)) if os.path.exists(plan_path) else []

def find_product(nom):
    nom = (nom or "").lower().strip()
    for p in products:
        if nom and nom in p["title"].lower():
            return p
    return None

executed = 0
for brief in plan[:2]:
    p = find_product(brief.get("robe", ""))
    if not p:
        continue
    if brief.get("format") == "buste_produit":
        g.make_no_face("bust", p, captions, state, key)
    elif brief.get("format") == "robe_posee":
        g.make_no_face("chaise", p, captions, state, key)
    else:
        g.make_model_post(p, captions, state, key,
                          scene_text=brief.get("ambiance"), concept=brief.get("concept", ""))
    executed += 1

if executed == 0:
    chosen = core.pick_products(products, state, 2)
    g.make_model_post(chosen[0], captions, state, key)
    g.make_no_face("bust", chosen[1], captions, state, key)

core.save_state(state)
if os.path.exists(plan_path):
    os.remove(plan_path)
print(f"lot produit ({executed} briefs du plan exécutés)")
