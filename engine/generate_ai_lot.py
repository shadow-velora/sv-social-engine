#!/usr/bin/env python3
"""Creative Producer : exécute le plan du Social Media Manager (concepts créatifs inclus)."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_ai as g
import cerveau
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
for brief in plan[:4]:
    p = find_product(brief.get("robe", ""))
    if not p:
        continue
    if brief.get("format") == "buste_produit":
        g.make_no_face("bust", p, captions, state, key)
    elif brief.get("format") == "robe_posee":
        g.make_no_face("chaise", p, captions, state, key)
    elif brief.get("format") in ("carrousel_muse", "carrousel_lookbook"):
        autres = [x for x in g.pick_products_saison(products, state, 3, key) if x["handle"] != p["handle"]][:2]
        g.make_muse_carousel([p] + autres, captions, state, key)
    elif brief.get("format") == "carrousel_lineup":
        autres = [x for x in g.pick_products_saison(products, state, 3, key) if x["handle"] != p["handle"]][:2]
        g.make_carousel_lineup([p] + autres, captions, state, key)
    elif brief.get("format") == "carrousel_tour":
        g.make_carousel_tour(p, captions, state, key)
    elif brief.get("format") == "carrousel_porte_pose":
        g.make_carousel_porte_pose(p, captions, state, key)
    else:
        g.make_model_post(p, captions, state, key,
                          scene_text=brief.get("ambiance"), concept=brief.get("concept", ""))
    executed += 1

if executed == 0:
    # secours : la règle fondatrice 03/08 reste respectée — 1 post simple + au moins 1 carrousel
    chosen = g.pick_products_saison(products, state, 2, key)
    g.make_model_post(chosen[0], captions, state, key)
    g.make_carousel_porte_pose(chosen[1], captions, state, key)

core.save_state(state)
if os.path.exists(plan_path):
    os.remove(plan_path)
g._progress("")
print(f"lot produit ({executed} briefs du plan exécutés)")
