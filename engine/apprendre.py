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


def main(item, raison):
    if not raison or raison == "sans raison donnée":
        return
    handle = item.split("_", 2)[2].partition("_")[2] if item.count("_") >= 3 else ""
    prompt = ("A founder rejected an AI-generated fashion image. Dress handle: '" + (handle or "unknown") +
              "'. Her rejection reason (French): «" + raison + "». "
              "Distill ONE short imperative English rule (max 25 words) to add to the image-generation prompt so this "
              "exact mistake NEVER happens again. Decide the scope: GLOBAL (applies to all images) or DRESS (only this dress). "
              "If the reason is purely one-off taste with no generalizable rule, answer SKIP. "
              'Answer ONLY in JSON: {"rule": "... or SKIP", "scope": "GLOBAL or DRESS"}')
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key=" + cle_api(),
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
