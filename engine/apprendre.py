#!/usr/bin/env python3
"""
APPRENTISSAGE IMMÉDIAT — transforme un rejet de Laurie en règle PERMANENTE de génération.
Usage : python3 engine/apprendre.py "<item>" "<raison>"
Écrit engine/lecons.json — relu par TOUS les prompts de génération à chaque image.
"""
import json
import os
import sys
import urllib.request

ENGINE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE)
LECONS = os.path.join(ENGINE, "lecons.json")


def cle_api():
    k = os.environ.get("GEMINI_API_KEY")
    if k:
        return k
    for line in open(os.path.join(ROOT, ".env")):
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("clé manquante")


def autre_robe_citee(raison, handle, products):
    """Prénom d'une AUTRE robe du catalogue citée dans la raison, sinon None.
    07/09/2026 (bug Vespéra) : « J'ai demandé la vespéra dress » sur un post Bourgeoise avait gravé, sur la robe
    Bourgeoise, l'ordre de dessiner la Vespéra — contamination durable de l'apprentissage par un incident ponctuel."""
    import re
    sys.path.insert(0, ENGINE)
    import generate as core
    r = " " + core.norm_nom(raison) + " "
    mienne = next((p for p in products if p.get("handle") == handle), None)
    mon_nom = core.norm_nom(core.first_name(mienne["title"])) if mienne else ""
    if not mon_nom:
        # 07/09/2026 (Laurie/Claude) : dossier dont le handle a disparu du catalogue (produit renommé) —
        # on ne sait pas quelle robe c'est, donc on ne bloque pas l'apprentissage.
        return None
    # prénoms du catalogue qui sont aussi des mots courants : exigés précédés d'un article pour compter comme citation
    GENERIQUES = {"muse", "rouge", "petite", "paris", "jolie", "luxury", "madame", "ivory", "charm", "fleur",
                  "dune", "berry", "cerise", "opale", "aura", "essence", "angels", "breeze", "cocoon", "celestial"}
    ARTICLE = r"(?:la|le|les|une|un|the|a|robe|dress|gown|de|du|ma|sa) "
    for p in products:
        n = core.norm_nom(core.first_name(p.get("title", "")))
        if not n or len(n) < 3 or n == mon_nom:
            continue
        motif = (ARTICLE if n in GENERIQUES else r"(?<![a-z0-9])") + re.escape(n) + r"(?![a-z0-9])"
        if re.search(motif, r):
            return core.first_name(p["title"])
    return None


def main(item, raison):
    if not raison or raison == "sans raison donnée":
        return
    try:
        sys.path.insert(0, ENGINE)
        import generate as core
        produits = core.fetch_products()
    except Exception:
        produits = []
    # 07/09/2026 (audit) : « carousel_tour_<handle> » donnait le handle « tour_<handle> » — on passe par le catalogue
    import generate as core
    handle = core.handle_du_dossier(item, produits)
    autre = autre_robe_citee(raison, handle, produits)
    if autre:
        print(f"leçon NON gravée : la raison parle d'une autre robe (« {autre} ») que celle rejetée ({handle or '?'}) — "
              "incident ponctuel, pas une règle de génération")
        return
    prompt = ("A founder rejected an AI-generated fashion image. Dress handle: '" + (handle or "unknown") +
              "'. Her rejection reason (French): «" + raison + "». "
              "Distill ONE short imperative English rule (max 25 words) to add to the image-generation prompt so this "
              "exact mistake NEVER happens again. Decide the scope: GLOBAL (applies to all images) or DRESS (only this dress). "
              "If the reason is purely one-off taste with no generalizable rule, or if it names a DIFFERENT product than this dress, answer SKIP. "
              'Answer ONLY in JSON: {"rule": "... or SKIP", "scope": "GLOBAL or DRESS"}')
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key=" + cle_api(),
        data=body, headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=60).read())
    txt = r["candidates"][0]["content"]["parts"][0]["text"]
    v = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    rule = (v.get("rule") or "").strip()
    if not rule or rule.upper() == "SKIP":
        print("leçon non généralisable — ignorée")
        return
    lec = json.load(open(LECONS)) if os.path.exists(LECONS) else {"global": [], "par_robe": {}}
    if v.get("scope") == "DRESS" and handle:
        cible = lec["par_robe"].setdefault(handle, [])
    else:
        cible = lec["global"]
    if rule not in cible:
        cible.append(rule)
        del cible[:-12]  # garder les 12 plus récentes par liste
    json.dump(lec, open(LECONS, "w"), indent=2, ensure_ascii=False)
    print("leçon gravée:", rule)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
