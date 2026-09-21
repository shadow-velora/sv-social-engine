#!/usr/bin/env python3
"""Creative Producer : exécute le plan du Social Media Manager (concepts créatifs inclus)."""
import sys, os, json

# ---- PAUSE GLOBALE (Laurie 21/09/2026) : engine/pause.json {"pause": true} → rien ne tourne, même déclenché à la main ----
def _pause_globale():
    try:
        import json as _j, os as _o
        _p = _j.load(open(_o.path.join(_o.path.dirname(_o.path.abspath(__file__)), "pause.json")))
        if _p.get("pause"):
            print(f"EN PAUSE depuis le {_p.get('depuis')} — {_p.get('raison','')[:90]}")
            return True
    except Exception:
        pass
    return False

if __name__ == "__main__" and _pause_globale():
    raise SystemExit(0)
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
    return core.find_product(products, nom)

cible = (os.environ.get("GEN_PRODUIT") or "").strip()
if cible:
    trouves, introuvables, sugg = core.resoudre_produits(products, cible)
    if introuvables:
        # 07/09/2026 (Laurie) : un nom inconnu = on N'INVENTE PAS un autre produit et on ne dépense rien
        msg = "⚠️ introuvable : " + ", ".join(introuvables) + (" (peut-être : " + ", ".join(sugg) + ")" if sugg else "")
        print(msg)
        g._progress(msg)
    for p in trouves:
        print(f"génération ciblée demandée par Laurie : {p['title']}")
        g._progress(f"post demandé — {p['title'][:40]}")
        g.make_model_post(p, captions, state, key)
        core.save_state(state)
        # 07/09/2026 (Laurie) : le mode ciblé fabrique aussi le carrousel « tour du produit » de la pièce demandée
        g._progress(f"carrousel demandé — {p['title'][:40]}")
        g.make_carousel_tour(p, captions, state, key)
        core.save_state(state)
    if not introuvables:
        g._progress("")
    print(f"{len(trouves)} produit(s) ciblé(s) : post + carrousel ✅" if trouves else "rien généré")
    raise SystemExit(0)

ALERTES_PATH = os.path.join(ENGINE, "alertes-generation.json")


def _alertes(sautes, executed):
    """07/09/2026 (audit) : un brief ignoré ne l'est plus en silence — l'inspectrice et le Cockpit le voient."""
    from datetime import datetime, timezone
    if sautes:
        json.dump({"date": datetime.now(timezone.utc).isoformat(timespec="minutes"), "alertes": sautes,
                   "briefs_executes": executed}, open(ALERTES_PATH, "w"), indent=2, ensure_ascii=False)
    elif os.path.exists(ALERTES_PATH):
        os.remove(ALERTES_PATH)


executed = 0
sautes = []
en_file = core.handles_en_file(products=products)
for brief in plan[:2]:  # 21/09/2026 (Laurie) : 2 contenus/semaine
    nom = brief.get("robe", "")
    p = find_product(nom)
    if not p:
        sautes.append(f"brief « {nom} » ({brief.get('format', '?')}) ignoré : robe introuvable dans le catalogue")
        print("⚠️ " + sautes[-1])
        continue
    if not str(brief.get("format", "")).startswith("carrousel"):
        brief["format"] = "carrousel_tour"  # 21/09/2026 (Laurie) : plus de post simple, uniquement des carrousels de 2 images
    kind = "carousel" if str(brief.get("format", "")).startswith("carrousel") else "post"
    if kind in en_file.get(p["handle"], set()):
        sautes.append(f"brief « {nom} » ({brief.get('format', '?')}) ignoré : un {kind} de cette robe est déjà en file (règle d'alternance)")
        print("⚠️ " + sautes[-1])
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
        # 07/09/2026 (Laurie) : plus de natures mortes (fauteuil / à plat) — remplacé par le tour du produit
        g.make_carousel_tour(p, captions, state, key)
    else:
        g.make_model_post(p, captions, state, key,
                          scene_text=brief.get("ambiance"), concept=brief.get("concept", ""))
    executed += 1

if executed == 0:
    # secours : règle du 21/09/2026 — 2 carrousels de 2 images
    if plan:
        sautes.append(f"aucun des {len(plan[:4])} briefs du plan n'a pu être exécuté → lot de secours (2 robes tirées au sort)")
    chosen = g.pick_products_saison(products, state, 2, key)
    g.make_carousel_tour(chosen[0], captions, state, key)  # 21/09/2026 : 2 carrousels de 2 images, aucun post simple
    g.make_carousel_tour(chosen[1], captions, state, key)

core.save_state(state)
_alertes(sautes, executed)
if os.path.exists(plan_path):
    os.remove(plan_path)
g._progress("")
print(f"lot produit ({executed} briefs du plan exécutés)")
