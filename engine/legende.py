#!/usr/bin/env python3
"""
SV Social Engine — Rédacteur de légendes pour les contenus de Laurie.
Usage : python3 engine/legende.py "réel essayage de la robe Rosalia"
Imprime en dernière ligne un JSON {"legende": "..."} dans le style maison MDV.
"""
import json
import os
import sys

ENGINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE)
import generate as core       # noqa: E402
import generate_ai as gai     # noqa: E402


def main():
    demande = " ".join(sys.argv[1:]).strip()
    if not demande:
        print(json.dumps({"erreur": "décris ton contenu"}))
        return
    key = gai.api_key()
    caps = json.load(open(os.path.join(ENGINE, "captions.json")))
    exemples = caps.get("studio", [])[:8] + caps.get("carousel", [])[:6]
    robes = ""
    try:
        robes = "\n".join(f"- {core.first_name(p['title'])} : {p['title']}"
                          for p in core.fetch_products()[:40])
    except Exception:
        pass
    prompt = f"""Tu écris les légendes Instagram de Shadow Velora (robes luxe discret type Manière De Voir, "Designed in London").
STYLE MAISON (non négociable) : anglais, phrases courtes staccato, le signe ~ comme respiration, minimal et élégant, jamais de vente agressive, jamais d'emojis, jamais de tirets. Option : finir par une question d'engagement discrète. Dernière ligne séparée : #shadowvelora + 2 à 4 hashtags max parmi #eveningdress #quietluxury #parisianstyle #dresslover #londonstyle.
EXEMPLES DU STYLE MAISON : {json.dumps(exemples, ensure_ascii=False)}
CATALOGUE RÉEL (si un nom de robe est cité dans la demande, appuie-toi sur son vrai nom complet — n'invente JAMAIS de détails produit non fournis) :
{robes}
CONTENU DE LA FONDATRICE À LÉGENDER (réel, essayage, coulisses, unboxing...) : {demande}
Réponds UNIQUEMENT en JSON : {{"legende": "la légende complète prête à coller dans Instagram"}}"""
    resp = {}
    import time
    for _ in range(3):
        resp = gai.gemini(gai.CHECK_MODEL, [{"text": prompt}], key)
        if resp.get("candidates"):
            try:
                txt = resp["candidates"][0]["content"]["parts"][0]["text"]
                out = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
                print(json.dumps(out, ensure_ascii=False))
                return
            except Exception:
                break
        time.sleep(4)
    detail = str(resp.get("error", {}).get("message", resp))[:100]
    if "depleted" in detail or "credit" in detail.lower():
        print(json.dumps({"erreur": "⛽ Crédits Google AI Studio épuisés : recharge sur ai.studio/projects"}))
    else:
        print(json.dumps({"erreur": "Gemini est momentanément surchargé — réessaie dans une minute"}))


if __name__ == "__main__":
    main()
