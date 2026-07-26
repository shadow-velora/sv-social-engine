#!/usr/bin/env python3
"""
L'ÉQUIPE ÉDITORIALE — 4 rôles IA qui se réunissent sur le feed comme une vraie
équipe social media : DA, curation de grille, marque, budget.
Sortie : engine/rapport-equipe.json (affiché dans l'onglet ÉQUIPE du Cockpit).
La curatrice réordonne la file de publication elle-même (veto Laurie au-dessus).
"""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE)
import generate as core
import generate_ai as gai
from PIL import Image

RAPPORT = os.path.join(ENGINE, "rapport-equipe.json")


def publish_order():
    items = []
    for state in ("approved", "pending"):
        base = os.path.join(ROOT, "queue", state)
        if not os.path.isdir(base):
            continue
        for it in sorted(os.listdir(base)):
            d = os.path.join(base, it)
            if os.path.isdir(d) and os.path.exists(os.path.join(d, "meta.json")):
                meta = json.load(open(os.path.join(d, "meta.json")))
                media = os.path.join(d, "media.jpg")
                if not os.path.exists(media):
                    slides = sorted(f for f in os.listdir(d) if f.startswith("slide"))
                    media = os.path.join(d, slides[0]) if slides else None
                items.append({"id": it, "state": state, "dir": d,
                              "caption": meta.get("caption", ""), "media": media})
    return items


def grid_montage(items, out_path):
    """La grille telle qu'elle apparaîtra sur Insta (3 col, dernier publié en haut)."""
    grid = list(reversed(items))
    cols, cell_w, cell_h = 3, 300, 375
    rows = (len(grid) + cols - 1) // cols
    im = Image.new("RGB", (cols * cell_w, max(rows, 1) * cell_h), (0, 0, 0))
    from PIL import ImageDraw, ImageFont
    dr = ImageDraw.Draw(im)
    for i, it in enumerate(grid):
        if not it["media"]:
            continue
        tile = Image.open(it["media"]).convert("RGB")
        tile = core.cover(tile, cell_w, cell_h)
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        im.paste(tile, (x, y))
        dr.text((x + 8, y + 6), str(len(items) - grid.index(it)), fill=(255, 255, 255))
    im.save(out_path, quality=88)
    return out_path


def ask(role_prompt, montage_path, listing, key):
    parts = [{"text": role_prompt + "\n\nLISTE DES POSTS (ordre de publication, 1 = publié en premier):\n" + listing},
             {"inline_data": {"mime_type": "image/jpeg", "data": gai.b64_of(montage_path)}}]
    resp = gai.gemini(gai.CHECK_MODEL, parts, key)
    try:
        txt = resp["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    except Exception as e:
        return {"erreur": str(e)[:120]}


def reunion():
    key = gai.api_key()
    items = publish_order()
    if len(items) < 2:
        print("pas assez de contenus pour une réunion")
        return
    montage = os.path.join(ROOT, "queue", "__grille.jpg")
    grid_montage(items, montage)
    listing = "\n".join(f"{i+1}. [{it['id']}] {it['caption'].splitlines()[0][:80]}"
                        for i, it in enumerate(items))

    # leçons récentes du journal de Laurie
    fb_path = os.path.join(ENGINE, "feedback.jsonl")
    lecons = ""
    if os.path.exists(fb_path):
        lines = open(fb_path).read().strip().splitlines()[-6:]
        lecons = "\n".join("- " + json.loads(l).get("raison", "") for l in lines)
    robes = ", ".join(sorted({core.first_name(x["title"]) for x in core.fetch_products() if x.get("images")}))

    smm = ask(f"""Tu es le Social Media Manager de Shadow Velora (robes luxe discret type Manière De Voir, "Designed in London"). Tu définis la stratégie et le calendrier. Catalogue disponible : {robes}.
LEÇONS RÉCENTES DE LA FONDATRICE (à respecter absolument) :
{lecons}
L'image jointe = la grille actuelle. Décide le PLAN DU PROCHAIN LOT (2 contenus max, budget serré) : quelles robes mettre en avant (varie par rapport à la grille), quel format (mannequin_pipeline ou buste_produit), quelle ambiance.
Réponds UNIQUEMENT en JSON: {{"strategie": "2 phrases max", "plan_prochain_lot": [{{"robe": "nom exact du catalogue", "format": "mannequin_pipeline ou buste_produit", "ambiance": "1 phrase de brief shooting"}}], "ton_legendes": "1 phrase"}}""",
              montage, listing, key)

    da = ask("""Tu es la directrice artistique de Shadow Velora, marque de robes "luxe discret" inspirée de Manière De Voir. DA verrouillée : palette sable/crème/taupe/espresso, jamais de gris froid ni couleurs criardes, mannequins naturelles (jamais poupée lisse), décors vécus élégants, zéro texte sur les photos (sauf bandes éditoriales assumées). L'image jointe est la grille Instagram prévue.
Réponds UNIQUEMENT en JSON: {"verdicts": [{"numero": n, "da_ok": true/false, "note": "1 phrase"}], "avis_global": "2 phrases max"}""",
             montage, listing, key)

    cura = ask("""Tu es la curatrice de grille Instagram de Shadow Velora (luxe discret type Manière De Voir). Règles d'une belle grille : jamais deux posts visuellement similaires côte à côte (deux mannequins plein pied, deux bandes texte...), alterner les échelles (plein pied / détail / produit / texte), rythme de couleurs harmonieux, la marque doit se présenter en premier (logo/manifeste publiés d'abord). L'image jointe = la grille prévue (3 colonnes, le dernier publié en haut à gauche).
Réponds UNIQUEMENT en JSON: {"ordre_ideal": [liste des numéros actuels dans le NOUVEL ordre de publication souhaité, ex [1,3,2,4,5,6]], "raison": "2 phrases max"}""",
              montage, listing, key)

    marque = ask("""Tu es la responsable de la marque Shadow Velora ("Designed in London", robes pour les moments qui comptent, ton sobre sans emoji, légendes 1 ligne + signature ~). Évalue si cette séquence de posts RACONTE la marque (présentation, matière, produit, femme) ou si c'est un simple empilement. Vérifie les légendes listées.
Réponds UNIQUEMENT en JSON: {"histoire_ok": true/false, "manque": "ce qui manque au récit, 1-2 phrases", "legendes_a_revoir": [{"numero": n, "suggestion": "nouvelle légende EN"}]}""",
               montage, listing, key)

    # budget : calcul local, pas d'IA
    state = core.load_state()
    gen = state.get("gen_calls", 0)
    budget = {
        "images_gemini_ce_mois": f"{gen}/{gai.MAX_GEN_PER_MONTH}",
        "estimation_gemini": f"~{gen * 0.04:.2f} EUR",
        "note": "Plafond dur actif : la machine s'arrete seule au plafond. Passes Magnific ~0,10 EUR/image en credits separes.",
    }

    rapport = {
        "date": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "nb_posts": len(items),
        "social_media_manager": smm,
        "directrice_artistique": da,
        "curatrice_grille": cura,
        "responsable_marque": marque,
        "controleuse_budget": budget,
        "ordre_applique": False,
    }

    # la curatrice applique son ordre (renommage des préfixes) si valide
    ordre = cura.get("ordre_ideal")
    if (isinstance(ordre, list) and sorted(ordre) == list(range(1, len(items) + 1))):
        base_names = [items[n - 1] for n in ordre]
        stamp_base = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H")
        for i, it in enumerate(base_names, start=1):
            rest = it["id"].split("_", 2)[2] if it["id"].count("_") >= 2 else it["id"]
            new = f"{stamp_base}{i:02d}00_{rest}"
            newp = os.path.join(os.path.dirname(it["dir"]), new)
            try:
                if os.path.isdir(it["dir"]) and it["dir"] != newp and not os.path.exists(newp):
                    os.rename(it["dir"], newp)
            except OSError:
                continue
        rapport["ordre_applique"] = True

    if isinstance(smm.get("plan_prochain_lot"), list) and smm["plan_prochain_lot"]:
        json.dump(smm["plan_prochain_lot"], open(os.path.join(ENGINE, "plan-semaine.json"), "w"),
                  indent=2, ensure_ascii=False)
    json.dump(rapport, open(RAPPORT, "w"), indent=2, ensure_ascii=False)
    os.remove(montage)
    print("réunion terminée — rapport écrit", "| ordre appliqué" if rapport["ordre_applique"] else "")
    return rapport


if __name__ == "__main__":
    reunion()
