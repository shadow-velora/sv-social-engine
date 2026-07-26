#!/usr/bin/env python3
"""Creative Producer : exécute le plan de la semaine décidé par le Social Media Manager."""
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
    else:
        # mannequin pipeline complet — brief d'ambiance de la productrice en scène
        g.main(1)
    executed += 1

if executed == 0:  # pas de plan → rotation classique
    g.main(1)
    chosen = core.pick_products(products, state, 1)
    g.make_no_face("bust", chosen[0], captions, state, key)

core.save_state(state)
if os.path.exists(plan_path):
    os.remove(plan_path)  # plan consommé
print(f"lot produit selon le plan ({executed} briefs exécutés)")
