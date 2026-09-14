#!/usr/bin/env python3
"""Placement automatique de la grille Instagram — règle du DAMIER (14/09/2026, demande Laurie).

Idée : dès qu'un post est validé, la file « approved » est réordonnée pour que la grille (3 colonnes,
le plus récent en haut à gauche) reste fluide : jamais la même robe voisine, jamais deux images sombres
côte à côte ni l'une au-dessus de l'autre, alternance des plans (large / moyen / détail) et des types.
Zéro appel payant : tout se mesure sur les images de couverture avec Pillow.

Usage :
  python3 engine/grille.py            → applique le meilleur ordre (renomme les dossiers de queue/approved)
  python3 engine/grille.py --dry      → propose seulement (JSON), ne touche à rien
  python3 engine/grille.py --explique → détail des mesures de chaque post
Réglage : engine/grille-auto.json {"auto": true|false} — si false, le placement ne se fait plus au clic
« valider » (le bouton 🧩 du Cockpit reste disponible).
"""
import itertools
import json
import os
import random
import re
import sys
import urllib.request
from datetime import datetime, timezone

from PIL import Image

ROOT = os.environ.get("SV_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "engine")
APPROVED = os.path.join(ROOT, "queue", "approved")
FEED = os.path.join(ENGINE, "feed-instagram.json")
CACHE = os.path.join(ENGINE, "feed-cache")
NOTE = os.path.join(ENGINE, "grille-note.json")
REGLAGE = os.path.join(ENGINE, "grille-auto.json")
HISTO = os.path.join(ENGINE, "ordre-historique.json")

COLONNES = 3
FIGES_UTILES = 6          # seuls les 6 derniers posts publiés touchent les posts à venir (1 à droite, 3 en dessous)
EXHAUSTIF_MAX = 8         # jusqu'à 8 posts libres on essaie TOUS les ordres (40 320) ; au-delà : glouton + échanges

# ---- pénalités (plus c'est haut, plus on l'évite) ----
P_MEME_ROBE = 10          # même produit voisin (côte à côte ou l'un au-dessus de l'autre)
P_MEME_ROBE_PROCHE = 3    # même produit à moins de deux rangées
P_ROBE_TON_H, P_ROBE_TON_V = 3, 2     # même tonalité de robe (sombre/moyen/clair)
P_FOND_TON_H, P_FOND_TON_V = 2, 1     # même tonalité de fond
P_PLAN_H = 2              # même plan (large / moyen / détail) côte à côte
P_TYPE_H = 1              # même type (carousel / studio / reel) côte à côte
P_COULEUR_H = 1           # même famille de couleur côte à côte
P_TROIS_SOMBRES = 4       # rangée entière sombre
P_DOUBLON = 15            # deux couvertures quasi identiques à moins de deux rangées (même photo vue deux fois)


def auto_actif():
    try:
        return bool(json.load(open(REGLAGE)).get("auto", True))
    except Exception:
        return True


# ---------------------------------------------------------------- mesures
VIDE = {"robe": None, "fond": None, "robe_l": None, "fond_l": None, "sombre": False, "couleur": None, "empreinte": None}


def _doublon(a, b):
    """Deux couvertures quasi identiques (empreinte 8×8 en niveaux de gris, écart moyen < 6/255)."""
    ea, eb = a.get("empreinte"), b.get("empreinte")
    if not ea or not eb:
        return False
    return sum(abs(x - y) for x, y in zip(ea, eb)) / len(ea) < 6


def _proche(a, b, marge=40):
    """1.0 = luminosités identiques, 0.0 = écart ≥ marge (l'œil distingue nettement au-delà)."""
    if a is None or b is None:
        return 0.0
    return max(0.0, 1.0 - abs(a - b) / marge)


def _classe(l):
    return "sombre" if l < 95 else ("clair" if l > 150 else "moyen")


def mesurer_image(path):
    """Tonalité robe (zone centrale) et fond (bords), famille de couleur (chaud / froid / neutre)."""
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return VIDE.copy()
    im = im.resize((60, 75))
    w, h = im.size
    px = im.load()
    centre, bords = [], []
    for y in range(h):
        for x in range(w):
            (centre if (0.30 * w <= x < 0.70 * w and 0.25 * h <= y < 0.92 * h) else bords).append(px[x, y])

    def lum(pts):
        return sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pts) / max(1, len(pts))

    hsv = im.convert("HSV").load()
    hs = [hsv[x, y] for y in range(int(0.25 * h), int(0.92 * h)) for x in range(int(0.30 * w), int(0.70 * w))]
    sat = sum(s for _, s, _ in hs) / max(1, len(hs)) / 255
    # teinte moyenne pondérée par la saturation (0-255 → 0-360)
    tot = sum(s for _, s, _ in hs) or 1
    hue = sum(hh * s for hh, s, _ in hs) / tot * 360 / 255
    if sat < 0.16:
        couleur = "neutre"
    elif hue < 60 or hue >= 320:
        couleur = "chaud"
    elif 150 <= hue < 270:
        couleur = "froid"
    else:
        couleur = "autre"
    lr, lf = lum(centre), lum(bords)
    empreinte = list(im.convert("L").resize((8, 8)).getdata())
    return {"robe": _classe(lr), "fond": _classe(lf), "robe_l": round(lr), "fond_l": round(lf),
            "sombre": (lr * 0.6 + lf * 0.4) < 90, "couleur": couleur, "empreinte": empreinte}


def _produit_depuis_nom(nom):
    reste = nom.split("_", 2)[2] if nom.count("_") >= 2 else nom
    return reste.split("_")[-1].strip().lower()


def _plan_depuis_meta(meta, nom):
    plans = meta.get("plans") or []
    p = str(plans[0]).lower() if plans else ""
    if "closeup" in p or "detail" in p or "matiere" in p:
        return "detail"
    if p in ("plein_pied", "marche", "dos", "trois_quarts") or "pied" in p:
        return "large"
    if p:
        return "moyen"
    if "ai-studio" in nom or "_studio_" in nom:
        return "moyen"
    return None


def _type_depuis_nom(nom):
    if "_reel_" in nom or nom.endswith("_reel"):
        return "reel"
    return "carousel" if "carousel" in nom else "studio"


def _couverture(d):
    m = os.path.join(d, "media.jpg")
    if os.path.exists(m):
        return m
    sl = sorted(f for f in os.listdir(d) if re.match(r"^slide-\d+\.jpg$", f))
    return os.path.join(d, sl[0]) if sl else None


def posts_libres():
    """Les posts à venir (queue/approved), dans l'ordre de publication actuel."""
    out = []
    if not os.path.isdir(APPROVED):
        return out
    for nom in sorted(os.listdir(APPROVED)):
        d = os.path.join(APPROVED, nom)
        if not os.path.isdir(d) or nom.count("_") < 2:
            continue
        try:
            meta = json.load(open(os.path.join(d, "meta.json")))
        except Exception:
            meta = {}
        cov = _couverture(d)
        f = mesurer_image(cov) if cov else VIDE.copy()
        f.update({"id": nom, "produit": _produit_depuis_nom(nom), "plan": _plan_depuis_meta(meta, nom),
                  "type": _type_depuis_nom(nom), "libre": True})
        out.append(f)
    return out


def posts_figes(produits_connus):
    """Les derniers posts déjà en ligne (feed réel), du plus ancien au plus récent."""
    try:
        posts = json.load(open(FEED)).get("posts", [])
    except Exception:
        return []
    posts = list(reversed(posts))[-FIGES_UTILES:]
    os.makedirs(CACHE, exist_ok=True)
    out = []
    for p in posts:
        pid = str(p.get("id", ""))
        cache = os.path.join(CACHE, f"{pid}.jpg")
        if pid and not os.path.exists(cache) and p.get("thumb") and not os.environ.get("SV_GRILLE_HORS_LIGNE"):
            try:
                urllib.request.urlretrieve(p["thumb"], cache)
            except Exception:
                pass
        f = mesurer_image(cache) if os.path.exists(cache) else VIDE.copy()
        cap = (p.get("caption") or "").lower()
        produit = None
        for pr in produits_connus:
            tete = pr.split("-")[0]
            if len(tete) >= 4 and re.search(r"\b" + re.escape(tete) + r"\b", cap):
                produit = pr
                break
        f.update({"id": pid, "produit": produit, "plan": None,
                  "type": "carousel" if "carousel" in str(p.get("type", "")) else ("reel" if "video" in str(p.get("type", "")) else "studio"),
                  "libre": False})
        out.append(f)
    return out


# ---------------------------------------------------------------- score
def _meme_robe(a, b):
    if not a["produit"] or not b["produit"]:
        return False
    ta, tb = a["produit"].split("-")[0], b["produit"].split("-")[0]
    return a["produit"] == b["produit"] or (len(ta) >= 4 and ta == tb)


def penalite_paire(a, b, vertical):
    p = 0
    if _meme_robe(a, b):
        p += P_MEME_ROBE
    p += (P_ROBE_TON_V if vertical else P_ROBE_TON_H) * _proche(a["robe_l"], b["robe_l"])
    p += (P_FOND_TON_V if vertical else P_FOND_TON_H) * _proche(a["fond_l"], b["fond_l"])
    if not vertical:
        if a["plan"] and a["plan"] == b["plan"]:
            p += P_PLAN_H
        if a["type"] == b["type"]:
            p += P_TYPE_H
        if a["couleur"] and a["couleur"] == b["couleur"] and a["couleur"] != "neutre":
            p += P_COULEUR_H
    return p


def score(seq, fautes=None):
    """seq = posts dans l'ordre CHRONOLOGIQUE (le dernier de la liste sera en haut à gauche)."""
    n = len(seq)
    rang = lambda i: (n - 1 - i) // COLONNES
    total = 0
    for i in range(n):
        a = seq[i]
        j = i + 1
        if j < n and rang(i) == rang(j) and (a["libre"] or seq[j]["libre"]):
            p = penalite_paire(a, seq[j], False)
            total += p
            if fautes is not None and p >= 2:
                fautes.append((a["id"], seq[j]["id"], "côte à côte", round(p, 1)))
        j = i + COLONNES
        if j < n and (a["libre"] or seq[j]["libre"]):
            p = penalite_paire(a, seq[j], True)
            total += p
            if fautes is not None and p >= 2:
                fautes.append((a["id"], seq[j]["id"], "l'un au-dessus de l'autre", round(p, 1)))
    # même robe à moins de deux rangées d'écart (pas voisines, mais l'œil la voit deux fois) ; même photo = pire
    for i in range(n):
        for j in range(i + 1, min(n, i + 7)):
            if not (seq[i]["libre"] or seq[j]["libre"]):
                continue
            if _doublon(seq[i], seq[j]):
                total += P_DOUBLON
                if fautes is not None:
                    fautes.append((seq[i]["id"], seq[j]["id"], "MÊME PHOTO vue deux fois", P_DOUBLON))
            elif j - i < 5 and _meme_robe(seq[i], seq[j]) and j - i not in (1, COLONNES):
                total += P_MEME_ROBE_PROCHE
                if fautes is not None:
                    fautes.append((seq[i]["id"], seq[j]["id"], "même robe à moins de deux rangées", P_MEME_ROBE_PROCHE))
    # rangées entièrement sombres
    for r in range(0, n, COLONNES):
        ligne = [seq[k] for k in range(n) if rang(k) == r // COLONNES]
        if len(ligne) == COLONNES and all(x["sombre"] for x in ligne) and any(x["libre"] for x in ligne):
            total += P_TROIS_SOMBRES
            if fautes is not None:
                fautes.append((ligne[0]["id"], ligne[-1]["id"], "rangée entière sombre", P_TROIS_SOMBRES))
    return round(total, 2)


def meilleur_ordre(figes, libres):
    """Renvoie la liste des posts libres dans le meilleur ordre de publication."""
    if len(libres) < 2:
        return list(libres)
    if len(libres) <= EXHAUSTIF_MAX:
        best, best_s = None, None
        for perm in itertools.permutations(range(len(libres))):
            s = score(figes + [libres[i] for i in perm])
            if best_s is None or s < best_s - 1e-9 or (abs(s - best_s) < 1e-9 and perm < best):
                best, best_s = perm, s
        return [libres[i] for i in best]
    # glouton + échanges par paires, départs multiples déterministes
    rnd = random.Random(14092026)
    best, best_s = None, None
    for essai in range(12):
        ordre = list(libres)
        if essai:
            rnd.shuffle(ordre)
        cur = score(figes + ordre)
        amelio = True
        while amelio:
            amelio = False
            for i in range(len(ordre)):
                for j in range(i + 1, len(ordre)):
                    ordre[i], ordre[j] = ordre[j], ordre[i]
                    s = score(figes + ordre)
                    if s < cur:
                        cur, amelio = s, True
                    else:
                        ordre[i], ordre[j] = ordre[j], ordre[i]
        if best_s is None or cur < best_s:
            best, best_s = list(ordre), cur
    return best


# ---------------------------------------------------------------- application
def _snapshot(libres):
    try:
        hist = json.load(open(HISTO)) if os.path.exists(HISTO) else []
    except Exception:
        hist = []
    order = [it["id"].split("_", 2)[2] for it in libres]
    if order and (not hist or hist[-1]["order"] != order):
        hist.append({"date": datetime.now(timezone.utc).isoformat(timespec="minutes"), "order": order})
        json.dump(hist[-10:], open(HISTO, "w"), indent=2, ensure_ascii=False)


def appliquer(ordre):
    """Renomme les dossiers de queue/approved pour suivre `ordre` (même convention que /api/reorder)."""
    if not ordre:
        return {}
    date = ordre[0]["id"].split("_", 1)[0]
    tmp = {}
    for i, it in enumerate(ordre):
        reste = re.sub(r" \d+$", "", it["id"].split("_", 2)[2].strip())
        t = os.path.join(APPROVED, f"__grille_{i}_{reste}")
        os.rename(os.path.join(APPROVED, it["id"]), t)
        tmp[i] = (t, reste)
    ren = {}
    for i, (t, reste) in tmp.items():
        new = f"{date}_{i * 100 + 100100:06d}_{reste}"
        os.rename(t, os.path.join(APPROVED, new))
        ren[ordre[i]["id"]] = new
    return ren


def placer(dry=False):
    libres = posts_libres()
    figes = posts_figes([p["produit"] for p in libres])
    if len(libres) < 2:
        res = {"applique": False, "raison": "moins de deux posts à venir : rien à placer"}
        print(json.dumps(res, ensure_ascii=False))
        return res
    fautes_avant = []
    avant = score(figes + libres, fautes_avant)
    ordre = meilleur_ordre(figes, libres)
    fautes_apres = []
    apres = score(figes + ordre, fautes_apres)
    change = [x["id"] for x in ordre] != [x["id"] for x in libres]
    lisible = lambda x: re.sub(r"^\d{4}-\d{2}-\d{2}_\d+_", "", x)
    res = {
        "applique": False,
        "score_avant": avant, "score_apres": apres,
        "ordre": [x["id"] for x in ordre],
        "ordre_lisible": [lisible(x["id"]) for x in ordre],
        "fautes_restantes": [(lisible(a), lisible(b), k, p) for a, b, k, p in fautes_apres],
        "raison": (f"Règle du damier : {avant} → {apres} points de friction"
                   + (" (ordre inchangé, déjà optimal)" if not change else "")
                   + (f" ; il reste {len(fautes_apres)} voisinage(s) imparfait(s), inévitable(s) avec ces images" if fautes_apres else " ; aucune faute de voisinage")),
        "date": datetime.now(timezone.utc).isoformat(timespec="minutes"),
    }
    if not dry and change:
        _snapshot(libres)
        ren = appliquer(ordre)
        res["applique"] = True
        res["renommes"] = ren
    if not dry:
        json.dump(res, open(NOTE, "w"), indent=2, ensure_ascii=False)
    print(json.dumps(res, ensure_ascii=False))
    return res


if __name__ == "__main__":
    if "--explique" in sys.argv:
        libres = posts_libres()
        for p in posts_figes([x["produit"] for x in libres]) + libres:
            print(json.dumps({k: v for k, v in p.items() if k != "empreinte"}, ensure_ascii=False))
        sys.exit(0)
    placer(dry="--dry" in sys.argv)
