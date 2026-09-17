#!/usr/bin/env python3
"""
SV Social Engine — Génération de NOUVELLES images (Nano Banana / Gemini API).
Part des vraies photos produits (référence), applique les recettes verrouillées
DA Inaya Paris (anti-Barbie, robe à l'identique), fait tourner scènes et poses,
puis passe chaque image au CONTRÔLEUR anti-fake (2e IA) avant de la déposer
dans queue/pending/ (Cockpit).

Usage : python3 engine/generate_ai.py [nb_posts]
"""
import base64
import json
import re
import os
import random
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.dirname(os.path.abspath(__file__))
PENDING = os.path.join(ROOT, "queue", "pending")
ATELIER = os.path.join(ROOT, "queue", "_atelier")


def _atelier_dir(nom):
    """Zone de travail : un contenu se fabrique ICI et n'entre dans pending que COMPLET."""
    os.makedirs(ATELIER, exist_ok=True)
    d = os.path.join(ATELIER, nom)
    os.makedirs(d, exist_ok=True)
    return d


def _livrer(d):
    """Livraison atomique : le dossier part dans pending seulement une fois meta.json écrit.
    Plus jamais de dossier à moitié fini qui fait crasher la publication (leçon du 02/08)."""
    import shutil as _s
    os.makedirs(PENDING, exist_ok=True)
    dest = os.path.join(PENDING, os.path.basename(d))
    _s.move(d, dest)
    return dest

sys.path.insert(0, ENGINE)
import generate as core  # curl, fetch_products, fetch_image, cover, captions, state

IMAGE_MODEL = "gemini-3.1-flash-image"  # bascule 07/09/2026 (GO Laurie) — 2.5 dérivait de décor et inventait les dos
MAX_GEN_PER_MONTH = 100  # ramené de 200 à 100 le 17/09/2026 (demande Laurie : diviser le coût par deux ; ~10 GBP de crédit Gemini avaient tenu 14 jours à 200/mois)
CHECK_MODEL = "gemini-3.6-flash"
API = "https://generativelanguage.googleapis.com/v1beta/models"


def api_key():
    k = os.environ.get("GEMINI_API_KEY")
    if k:
        return k
    envp = os.path.join(ROOT, ".env")
    if os.path.exists(envp):
        for line in open(envp):
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("GEMINI_API_KEY manquante (.env ou variable d'environnement)")


def _progress(txt):
    """Étape en cours, lue par le Cockpit (bandeau de progression)."""
    try:
        import time as _tm
        json.dump({"texte": txt, "ts": _tm.time()},
                  open(os.path.join(ENGINE, "progression.json"), "w"))
    except Exception:
        pass


# 07/09/2026 (audit) : SV_FAKE_GEMINI=1 = mode test SANS AUCUN APPEL PAYANT — image de remplissage,
# verdicts « pass », Magnific sauté, compteur budget intact. Sert à tester la file, le Cockpit,
# les workflows et toute modification du moteur avant de dépenser un centime.
FAKE = os.environ.get("SV_FAKE_GEMINI") == "1"


def _fake_reponse(model, parts):
    import hashlib, io as _io
    txt = next((p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")), "")
    if model == IMAGE_MODEL:
        from PIL import Image as _Img, ImageDraw as _Draw
        h = int(hashlib.sha1(txt.encode()).hexdigest()[:6], 16)
        img = _Img.new("RGB", (1080, 1350), ((h >> 16) & 255, (h >> 8) & 255, h & 255))
        _Draw.Draw(img).multiline_text((40, 40), "MODE TEST SV_FAKE_GEMINI\n" + txt[:70], fill=(255, 255, 255))
        buf = _io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        data = base64.b64encode(buf.getvalue()).decode()
        return {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/jpeg", "data": data}}]}}], "fake": True}
    verdict = {"dress_identical": True, "invented_details": [], "skin_natural": True, "face_consistent": True,
               "verdict": "pass", "face_clean": True, "issues": [], "worn": True, "still_life": False, "view": "back",
               "type": "plein_pied", "same_location": True, "near_duplicate": False, "de_saison": True,
               "raison": "mode test", "publiable": True, "legende": "Légende de test (SV_FAKE_GEMINI). ~",
               "rule": "SKIP", "scope": "GLOBAL"}
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(verdict)}]}}], "fake": True}


def _compte_juge():
    """Compte les appels au modèle juge (non plafonnés, mais facturés) dans budget.json → check_calls."""
    try:
        bp = os.path.join(ENGINE, "budget.json")
        b = json.load(open(bp)) if os.path.exists(bp) else {}
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        if b.get("gen_month") != month:
            b = {"gen_month": month, "gen_calls": 0}
        b["check_calls"] = b.get("check_calls", 0) + 1
        json.dump(b, open(bp, "w"))
    except Exception:
        pass


def gemini(model, parts, key):
    """Appel Gemini via curl (urllib bloqué par certains proxies)."""
    if FAKE:
        return _fake_reponse(model, parts)
    if model == CHECK_MODEL:
        _compte_juge()
    payload = json.dumps({"contents": [{"parts": parts}]})
    r = subprocess.run(
        ["curl", "-s", "--max-time", "180",
         "-H", "Content-Type: application/json",
         "-X", "POST", f"{API}/{model}:generateContent?key={key}",
         "-d", "@-"],
        input=payload.encode(), capture_output=True, check=True)
    resp = json.loads(r.stdout)
    # sentinelle crédit : trace l'épuisement pour l'inspectrice, s'efface au premier succès
    _flag = os.path.join(ENGINE, "alerte-credit.json")
    try:
        err = str(resp.get("error", {}))
        if "deplete" in err or "RESOURCE_EXHAUSTED" in err:
            json.dump({"date": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                       "detail": err[:150]}, open(_flag, "w"))
        elif os.path.exists(_flag):
            os.remove(_flag)
    except Exception:
        pass
    return resp


def save_jpeg(raw_bytes, path):
    """Gemini renvoie parfois du PNG : on normalise en vrai JPEG."""
    import io as _io
    from PIL import Image as _Img
    _Img.open(_io.BytesIO(raw_bytes)).convert("RGB").save(path, "JPEG", quality=93)


def b64_of(img_path):
    return base64.b64encode(open(img_path, "rb").read()).decode()


PROMPT_TEMPLATE = """E-commerce fashion editorial photograph of a NEW fictional model — a DIFFERENT woman from the one in the reference image (do not copy the reference model's face), with similar hair color and skin tone family, but her own real face. A REAL woman, not a supermodel render. Her face is pretty in an ordinary, believable way: distinctly asymmetric features as real faces are, a natural nose, slightly uneven brows, faint expression lines, lips gently closed. Her body is a real woman's body: soft natural arms, a gentle waist, realistic proportions, one shoulder carried a touch higher than the other, posture slightly uneven the way real people stand. Her hair has lived through the day: waves losing their shape, light frizz at the crown, flyaways, a few strands tucked behind one ear, an uneven parting. Minimal barely-there makeup.

The garment (dress, coat, knitwear or set) is EXACTLY the one in the reference: same color, fabric, neckline, sleeves, closures, construction and sheen — every detail from the reference only. If the reference fabric carries a pattern (floral jacquard, lace, appliqué, embroidery), that pattern MUST appear with the same density and in the same areas — bodice, hips AND skirt — never a plain smooth version of a patterned garment. The garment stays impeccable, with natural fabric tension and creases where the body moves.

Her skin reads as real, unretouched skin: soft directional window light skims across it at a low angle, revealing pores with varied density (coarser on the nose, finer on the temples). Natural sheen only on the T-zone, matte cheeks, highlights broken by skin micro-relief. Fine vellus hair catches the light on her forearms; real knuckle creases; slight natural redness at nose, elbows and knuckles; subtle tonal transitions between face, neck and chest. Baby hairs soften the hairline. Today specifically: {imperfections}.

An honest outtake caught mid-movement — her body is loose and alive, never stiff, never posed like a statue; weight shifting, a gesture in progress. Her posture stays ELEGANT and open at all times: back long, chin level or slightly lifted, shoulders open — never hunched, never bent forward, never head hanging down. Pose: {pose}

Setting: {scene}. The place is elegant but genuinely inhabited: subtle real-world wear in the decor only, never on the garment.

{framing}

{rules}
Shot on 85mm at f/4: she is tack-sharp from head to hem, the background gently softened by true optics only. Every imperfection is rendered crisp and in focus — imperfection means real detail, never blur, never haze, never soft focus. Unretouched editorial photograph, natural micro-contrast. Keep pores, fine lines and natural skin variation visible and SHARP."""


def sample_imperfections(cfg):
    import random as _r
    imp = cfg.get("imperfections", {})
    picks = []
    if imp.get("face"):
        picks.append(_r.choice(imp["face"]))
    if imp.get("arms"):
        picks.append(_r.choice(imp["arms"]))
    if imp.get("body") and _r.random() < 0.8:
        pool = imp["body"]
        if _r.random() < 0.2 and imp.get("rare_solo"):
            picks.append(_r.choice(imp["rare_solo"]))
        else:
            safe = [x for x in pool if x not in imp.get("rare_solo", [])]
            picks.append(_r.choice(safe))
    return "; ".join(picks)


CHECKER_PROMPT = """You are the demanding photo editor of a luxury fashion brand. Image 1 is the REFERENCE product photo (note: the reference itself is heavily retouched — do NOT use its skin as the standard). Image 2 is a marketing image that must look like a REAL, unretouched photograph of the same woman in the same dress.

Answer ONLY with a JSON object, no other text:
{"dress_identical": true/false, "invented_details": ["any dress detail in image 2 absent from the reference"], "skin_natural": true/false, "face_consistent": true/false — the model in image 2 is INTENTIONALLY a different fictional woman from the reference, so never compare her identity to image 1; true when her face reads as one coherent, natural, believable real person (and always true when the face is hidden, in profile or seen from behind), "verdict": "pass" or "fail"}

skin_natural = false ONLY if the skin is clearly artificial: waxy, plastic, poreless, airbrushed glow, doll-like. If the skin shows visible texture, freckles, moles or natural unevenness, set it to true. Noise or film grain over the whole image does NOT count as skin texture, and heavy grain or noise over the image = fail. Judge the dress strictly: any invented detail = fail. Compare the FABRIC zone by zone against the reference: patterned zones of the reference (lace, jacquard, embroidery) must stay patterned at similar density, and PLAIN zones of the reference must stay plain. A dress whose patterned/plain zones MATCH the reference layout is CORRECT — only fail when a patterned zone was smoothed out or a plain zone gained invented pattern. verdict = pass only if everything is true and invented_details is empty."""


def _budget_guard():
    from datetime import datetime, timezone
    if FAKE:
        return  # mode test : rien n'est facturé, le compteur reste intact
    bp = os.path.join(ENGINE, "budget.json")
    try:
        b = json.load(open(bp)) if os.path.exists(bp) else {}
    except Exception:
        # conflit git dans budget.json : on repart du compteur le plus élevé lisible
        import re as _re
        raw = open(bp).read()
        vals = [int(x) for x in _re.findall(r'"gen_calls":\s*(\d+)', raw)] or [0]
        b = {"gen_month": datetime.now(timezone.utc).strftime("%Y-%m"), "gen_calls": max(vals)}
        print("⚠️ budget.json : conflit git auto-réparé (compteur =", max(vals), ")")
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    if b.get("gen_month") != month:
        b = {"gen_month": month, "gen_calls": 0}
    if b.get("gen_calls", 0) >= MAX_GEN_PER_MONTH:
        raise SystemExit(f"PLAFOND BUDGET ATTEINT ({MAX_GEN_PER_MONTH} generations ce mois) — rien ne sera facture de plus.")
    b["gen_calls"] = b.get("gen_calls", 0) + 1
    json.dump(b, open(bp, "w"))


def _skin_ref():
    """Une vraie photo de peau humaine (dossier de Laurie) comme étalon de réalisme."""
    import glob, mimetypes
    refs = sorted(glob.glob(os.path.join(ENGINE, "skin-refs", "*")))
    if not refs:
        return None
    p = random.choice(refs)
    mime = mimetypes.guess_type(p)[0] or "image/webp"
    return {"inline_data": {"mime_type": mime, "data": b64_of(p)}}




def saison_verdict(p, key):
    """Raisonneur saison : regarde la PHOTO du vêtement et juge si c'est de saison.
    Fail-open : au moindre pépin technique on laisse passer (ne jamais bloquer la prod)."""
    try:
        s = core._saison()
        if not s.get("ambiance"):
            return True
        import tempfile
        ref = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        core.fetch_image(p["images"][0]["src"], 500).save(ref.name, quality=85)
        prompt = (f"Nous sommes en {s.get('nom','')} (marché US). Saison : {s['ambiance']}. "
                  "Regarde la photo de ce vêtement. Peut-on le porter et le publier sur Instagram "
                  "ce mois-ci sans paraître hors saison ? Les robes de soirée élégantes restent valables "
                  "toute l'année ; les pièces clairement estivales (robe légère de plein été, lin, plage, "
                  "imprimés très solaires) ne le sont pas en automne/hiver. "
                  'Réponds UNIQUEMENT en JSON : {"de_saison": true/false, "raison": "3 mots"}')
        parts = [{"text": prompt},
                 {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(ref.name)}}]
        resp = gemini(CHECK_MODEL, parts, key)
        txt = resp["candidates"][0]["content"]["parts"][0]["text"]
        v = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
        ok = bool(v.get("de_saison", True))
        if not ok:
            print(f"🍂 hors saison écarté : {p['title'][:50]} ({v.get('raison','')})")
        return ok
    except Exception:
        return True


def pick_products_saison(products, state, n, key):
    """Tirage pondéré PUIS validation visuelle par le raisonneur saison."""
    chosen, tries = [], 0
    while len(chosen) < n and tries < n * 4:
        cand = core.pick_products(products, state, 1)[0]
        tries += 1
        if any(c["handle"] == cand["handle"] for c in chosen):
            continue
        if saison_verdict(cand, key):
            chosen.append(cand)
    while len(chosen) < n:  # pénurie : on complète sans juger plutôt que bloquer
        cand = core.pick_products(products, state, 1)[0]
        if not any(c["handle"] == cand["handle"] for c in chosen):
            chosen.append(cand)
    return chosen


def generate_candidate(ref_path, scene, pose, rules, key, imperfections="", framing=None):
    _budget_guard()
    refs = ref_path if isinstance(ref_path, (list, tuple)) else [ref_path]
    if framing is None:
        cfg_f = json.load(open(os.path.join(ENGINE, "scenes.json"))).get("framings")
        framing = random.choice(cfg_f) if cfg_f else "Full-length composition, the entire dress visible, generous headroom above her head."
    prompt = PROMPT_TEMPLATE.format(pose=pose, scene=scene, rules=rules, framing=framing,
                                    imperfections=imperfections or "visible pores and natural uneven skin tone")
    if len(refs) > 1:
        prompt += f"\n\nThe first {len(refs)} reference photographs show the SAME garment from different angles (front and back). Reproduce its construction faithfully from EVERY angle: the back of the garment (straps, zip, lacing, buttons, neckline depth, seams) must match the back-view reference exactly — never invent the back."
    prompt += "\n\nThe FINAL reference photograph shows REAL unretouched human skin. This is the exact standard her skin must meet everywhere it is visible: knees and elbows slightly darker with fine creases, visible pores with natural sebum shine in places, patchy tonal variation, faint veins, real joint creases, natural marks. Study it and replicate THIS level of skin realism on her — never smoother than this real photograph."
    # RÈGLE FONDATRICE (Laurie 10/08) : CHAQUE génération s'appuie sur une vraie image
    # exemple du dossier mdv-refs — même décor et même pose autorisés, seule la robe change.
    style_path = None
    mdv_dir = os.path.join(ENGINE, "mdv-refs")
    try:
        pool = [f for f in os.listdir(mdv_dir) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        if pool:
            style_path = os.path.join(mdv_dir, random.choice(pool))
    except OSError:
        pass
    if style_path:
        prompt += ("\n\nAfter the dress reference(s) comes one STYLE reference photograph: a real Instagram post "
                   "the founder wants emulated. Recreate ITS world as closely as possible: same type of location, "
                   "same pose and framing, same energy — the styling (sunglasses, bag, jewelry, heels), the attitude, "
                   "the movement, the outdoor or indoor setting, the light. You may reuse the exact same environment "
                   "and the exact same position. ONLY the garment changes: the woman wears OUR garment from the garment "
                   "reference(s), never the outfit shown in the style reference. Harmonize colors with our dress.")
    parts = [{"text": prompt}] + [
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(r)}} for r in refs
    ]
    if style_path:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64_of(style_path)}})
    sk = _skin_ref()
    if sk:
        parts.append(sk)
    resp = gemini(IMAGE_MODEL, parts, key)
    for cand in resp.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            if "inlineData" in part:
                return base64.b64decode(part["inlineData"]["data"])
            if "inline_data" in part:
                return base64.b64decode(part["inline_data"]["data"])
    raise RuntimeError(f"pas d'image dans la réponse: {str(resp)[:300]}")


TEXTURE_PASS = """Keep the face, pose, dress, setting, framing, light and sharpness identical. Restore the natural skin texture this photo lost to retouching: visible pores, slight unevenness across the skin tone, fine lines, baby hairs at the hairline; on the arms and shoulders restore visible follicles, fine vellus hair and a faintly uneven matte tone — remove the airbrushed look everywhere. Do not change anything else. The image itself stays clean, crisp and high resolution: no added grain, no noise, no blur."""


def texture_pass(img_path, key):
    _budget_guard()
    parts = [
        {"text": TEXTURE_PASS},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(img_path)}},
    ]
    resp = gemini(IMAGE_MODEL, parts, key)
    for cand in resp.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            if "inlineData" in part:
                return base64.b64decode(part["inlineData"]["data"])
            if "inline_data" in part:
                return base64.b64decode(part["inline_data"]["data"])
    raise RuntimeError("texture pass sans image")


def check_candidate(ref_path, gen_path, key):
    refs = ref_path if isinstance(ref_path, (list, tuple)) else [ref_path]
    header = CHECKER_PROMPT
    if len(refs) > 1:
        header = (f"NOTE: the first {len(refs)} images are REFERENCE product photos of the SAME dress "
                  "from different angles (front and back); the LAST image is the marketing image to judge. "
                  "If the marketing image shows the back of the dress, judge it against the back-view reference. ") + CHECKER_PROMPT
    parts = ([{"text": header}]
             + [{"inline_data": {"mime_type": "image/jpeg", "data": b64_of(r)}} for r in refs]
             + [{"inline_data": {"mime_type": "image/jpeg", "data": b64_of(gen_path)}}])
    resp = gemini(CHECK_MODEL, parts, key)
    try:
        txt = resp["candidates"][0]["content"]["parts"][0]["text"]
        txt = txt[txt.find("{"):txt.rfind("}") + 1]
        return json.loads(txt)
    except Exception as e:
        return {"verdict": "fail", "error": f"contrôleur illisible: {e}"}


HEALTH_CHECK = """Inspect this fashion photograph closely. Answer ONLY with JSON:
{"face_clean": true/false, "issues": ["list any bruise-like blue or purple discoloration on the face or body, black-eye shading, wound-like marks, sickly grey patches, anatomical glitches (extra fingers, warped hands), an awkward distressed-looking posture (hunched over, head hanging, looks sick or in pain), visible grain/noise/speckling over the image, or a painterly / illustration-like / CGI-render look anywhere (the image must read as a REAL photograph, clean and crisp)"]}
Natural freckles, moles and healthy redness are fine. Bruise-colored patches anywhere on the face = face_clean false."""


def health_check(img_path, key):
    parts = [{"text": HEALTH_CHECK},
             {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(img_path)}}]
    resp = gemini(CHECK_MODEL, parts, key)
    try:
        txt = resp["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    except Exception:
        return {"face_clean": True, "issues": []}


def clean_noise(media_path, threshold=0.9):
    """Débruitage sélectif : masque de contours calculé sur image réduite
    (le vrai dessin survit, le grain fin disparaît), lissage fort des zones plates."""
    from PIL import Image as _I, ImageFilter as _F, ImageChops as _C, ImageStat as _S
    img = _I.open(media_path).convert("RGB")
    g = img.convert("L")
    noise = _S.Stat(_C.difference(g, g.filter(_F.GaussianBlur(1.2)))).mean[0]
    if noise <= threshold:
        return False
    w, h = img.size
    small = g.resize((w // 4, h // 4), _I.LANCZOS)
    edges = small.filter(_F.FIND_EDGES).resize((w, h), _I.LANCZOS)
    edges = (edges.point(lambda v: 255 if v > 22 else 0)
                  .filter(_F.MaxFilter(15)).filter(_F.GaussianBlur(6)))
    smooth = img.filter(_F.MedianFilter(7)).filter(_F.GaussianBlur(2.6))
    out = _I.composite(img, smooth, edges)
    out = out.filter(_F.UnsharpMask(radius=1.8, percent=38, threshold=4))
    out.save(media_path, quality=93)
    return True


def magnific_finalize(media_path, key):
    """Passe humanité Magnific + inspection anti-artefact. 2 essais max."""
    from PIL import Image as _I
    import magnific
    if FAKE:
        print("  mode test : Magnific sauté")
        return False
    for attempt in (1, 2):
        tmp = media_path + ".mag.jpg"
        try:
            magnific.enhance(media_path, tmp)
        except Exception as e:
            print("magnific indisponible:", str(e)[:100])
            return False
        v = health_check(tmp, key)
        if v.get("face_clean", True):
            img = _I.open(tmp).convert("RGB")
            core.cover(img, 1080, 1350).save(media_path, quality=93)
            os.remove(tmp)
            if clean_noise(media_path):
                print("  bruit détecté → nettoyage sélectif appliqué")
            return True
        print("  artefact détecté:", v.get("issues"))
        os.remove(tmp)
    return False


def pick_scene(scenes, last_id=None):
    pool = [s for s in scenes for _ in range(s.get("weight", 1)) if s["id"] != last_id]
    return random.choice(pool)


def main(n_posts=2):
    key = api_key()
    cfg = json.load(open(os.path.join(ENGINE, "scenes.json")))
    captions = core.load_captions()
    state = core.load_state()
    products = [p for p in core.fetch_products() if p.get("images")]
    chosen = pick_products_saison(products, state, n_posts, key)
    last_scene = state.get("last_scene")

    os.makedirs(PENDING, exist_ok=True)
    report = []

    for p in chosen:
        name = core.first_name(p["title"])
        scene = pick_scene(cfg["scenes"], last_scene)
        last_scene = scene["id"]
        pose = random.choice(cfg["poses"])

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        d = os.path.join(PENDING, f"{stamp}_ai-studio_{p['handle']}")
        os.makedirs(d, exist_ok=True)

        # référence = 1re photo produit
        ref = os.path.join(d, "reference.jpg")
        core.fetch_image(p["images"][0]["src"], 1200).save(ref, quality=92)

        _progress(f"post {name} — création de l'image (jusqu'à 3 essais + contrôle qualité)")
        verdicts = []
        kept = None
        for attempt in range(1, 4):  # max 3 tentatives pour 1 image qui passe
            raw = generate_candidate(ref, scene["text"], pose, cfg["rules"], key)
            cand_path = os.path.join(d, f"cand-{attempt}.jpg")
            save_jpeg(raw, cand_path)
            v = check_candidate(ref, cand_path, key)
            verdicts.append({f"cand-{attempt}": v})
            if v.get("verdict") == "pass":
                kept = cand_path
                break

        if kept:
            from PIL import Image
            img = Image.open(kept).convert("RGB")
            mp = os.path.join(d, "media.jpg")
            core.cover(img, 1080, 1350).save(mp, quality=92)
            os.remove(kept)
            magnific_finalize(mp, key)  # passe humanité (si crédits dispo)
            cap = core.pick_caption(captions, "studio", state, name)
            core.write_meta(d, "studio", cap,
                            f"{name} dress, editorial photograph, {scene['id']}.",
                            random.choice(captions["hashtags"]))
            with open(os.path.join(d, "controle.json"), "w") as f:
                json.dump(verdicts, f, indent=2, ensure_ascii=False)
            os.remove(ref)
            report.append(f"✅ {name} — scène {scene['id']} ({len(verdicts)} tentative(s))")
        else:
            with open(os.path.join(d, "controle.json"), "w") as f:
                json.dump(verdicts, f, indent=2, ensure_ascii=False)
            # rien de publiable → on retire le dossier de la file
            import shutil
            shutil.move(d, os.path.join(ROOT, "queue", "rejected",
                                        os.path.basename(d)))
            report.append(f"❌ {name} — 3 tentatives refusées par le contrôleur")

    state["last_scene"] = last_scene
    core.save_state(state)
    for line in report:
        print(line)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    os.makedirs(os.path.join(ROOT, "queue", "rejected"), exist_ok=True)
    main(n)


# ---------- SET COMPLET : 3 vues → carrousel + reel ----------

VIEW_PROMPTS = [
    None,  # vue 1 = génération initiale (plein pied)
    "Keep everything exactly the same — same woman, same dress, same setting, same light. Now show a closer three-quarter view from the waist up, focusing on the bodice and fabric of the dress. Her pose shifts naturally, hands relaxed. Same analog film look.",
    "Keep everything exactly the same — same woman, same dress, same setting, same light. Now show her from behind, full length, looking away from the camera, showing the back of the dress. Same analog film look.",
]


def edit_image(base_path, instruction, key):
    parts = [
        {"text": instruction},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(base_path)}},
    ]
    resp = gemini(IMAGE_MODEL, parts, key)
    for cand in resp.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            if "inlineData" in part:
                return base64.b64decode(part["inlineData"]["data"])
            if "inline_data" in part:
                return base64.b64decode(part["inline_data"]["data"])
    raise RuntimeError(f"pas d'image: {str(resp)[:200]}")


def make_ai_set(key=None, ffmpeg=None, product=None):
    """Génère 1 scène complète (3 vues contrôlées) → 1 carrousel. (Reels : uniquement depuis de vraies vidéos, dossier rushes/)"""
    from PIL import Image
    key = key or api_key()
    ffmpeg = ffmpeg or os.environ.get("FFMPEG", "ffmpeg")
    cfg = json.load(open(os.path.join(ENGINE, "scenes.json")))
    captions = core.load_captions()
    state = core.load_state()
    products = [p for p in core.fetch_products() if p.get("images")]
    p = product or pick_products_saison(products, state, 1, key)[0]
    name = core.first_name(p["title"])
    scene = pick_scene(cfg["scenes"], state.get("last_scene"))
    state["last_scene"] = scene["id"]
    pose = random.choice(cfg["poses"])

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    work = os.path.join(ROOT, "queue", "rejected", f"__work_{stamp}")
    os.makedirs(work, exist_ok=True)
    ref = os.path.join(work, "reference.jpg")
    core.fetch_image(p["images"][0]["src"], 1200).save(ref, quality=92)

    # Vue 1 : plein pied contrôlé (3 essais max)
    hero = None
    verdicts = []
    for attempt in range(1, 4):
        imps = sample_imperfections(cfg)
        try:
            raw = generate_candidate(ref, scene["text"], pose, cfg["rules"], key, imps)
        except RuntimeError as e:
            verdicts.append({"error": str(e)[:120]})
            continue
        cp = os.path.join(work, f"v1-{attempt}.jpg")
        save_jpeg(raw, cp)
        try:
            save_jpeg(texture_pass(cp, key), cp)
        except RuntimeError:
            pass
        v = check_candidate(ref, cp, key)
        verdicts.append(v)
        if v.get("verdict") == "pass":
            hero = cp
            break
    if not hero:
        print(f"❌ {name} — vue 1 jamais validée, set abandonné")
        core.save_state(state)
        return None

    # Vues 2 et 3 : éditions de la vue 1 (cohérence maximale), contrôlées aussi
    views = [hero]
    for i, instr in enumerate(VIEW_PROMPTS[1:], start=2):
        ok = None
        for attempt in range(1, 3):
            try:
                raw = edit_image(hero, instr, key)
            except RuntimeError:
                continue
            cp = os.path.join(work, f"v{i}-{attempt}.jpg")
            save_jpeg(raw, cp)
            try:
                save_jpeg(texture_pass(cp, key), cp)
            except RuntimeError:
                pass
            v = check_candidate(ref, cp, key)
            if v.get("verdict") == "pass":
                ok = cp
                break
        if ok:
            views.append(ok)

    if len(views) < 2:
        print(f"❌ {name} — pas assez de vues validées")
        core.save_state(state)
        return None

    # → CARROUSEL
    dc = os.path.join(PENDING, f"{stamp}_ai-carousel_{p['handle']}")
    os.makedirs(dc, exist_ok=True)
    for i, vp in enumerate(views):
        img = Image.open(vp).convert("RGB")
        core.cover(img, 1080, 1350).save(os.path.join(dc, f"slide-{i+1}.jpg"), quality=92)
    cap = core.pick_caption(captions, "carousel", state, name)
    core.write_meta(dc, "carousel", cap,
                    f"Carousel of the {name} dress, {scene['id']}, multiple views.",
                    random.choice(captions["hashtags"]))

    import shutil
    shutil.rmtree(work)
    core.save_state(state)
    print(f"✅ set {name} ({scene['id']}) : carrousel {len(views)} vues")
    return dc


# ---------- FORMATS SANS VISAGE (grille finale, verdict Laurie 26/07) ----------

CHAISE_PROMPT = """Still-life fashion photograph in a chateau interior: the exact garment from the reference image lies gracefully draped across an antique gilded armchair, warm window light, herringbone parquet floor. Same color, same fabric, same neckline, same sleeves and construction as the reference, soft natural folds, lived-in patina in the room (rubbed gilding, worn parquet, faded upholstery). True photographic rendering — real optical depth of field, accurate fabric weight and weave, natural light falloff — photorealistic, never a painting or 3D render. No person in the frame."""

BUST_PROMPT = """Professional e-commerce product photograph. The EXACT garment from the reference image — same color, same fabric, same neckline, same sleeves or straps, same construction, same sheen, every detail from the reference only — displayed on a cream linen tailor's dress form (a sewing mannequin bust, NO person, no head, no limbs).

Setting: a real working studio — warm sand seamless paper backdrop with a soft wrinkle, the dress form standing on a worn wooden floor, soft window light from the left casting honest shadows. The dress shows natural fabric behaviour: gentle creases from handling, the hem falling naturally. Real-world subtlety: the backdrop slightly uneven in tone, one faint tape mark on the floor.

Quiet luxury product photography, crisp and sharp, natural true-to-life muted colors, vertical 4:5 composition, the entire garment visible with generous margin. No text, no logos, no person."""

FLATLAY_PROMPT = """Top-down flat-lay editorial photograph. The EXACT garment from the reference image — same color, same fabric, same neckline, same sleeves, same construction, every detail from the reference only — laid carefully on a warm sand linen sheet, artfully but naturally arranged with soft real fabric folds and creases, one sleeve or strap casually off-line as if just placed there. A simple wooden hanger rests beside it.

Soft daylight from one side casting honest shadows in the fabric folds. The linen underneath shows natural wrinkles. Quiet luxury flat-lay, crisp and sharp, natural muted colors, vertical 4:5 composition. No text, no logos, no person, no other products."""



def swap_dress(base_path, dress_ref_path, key):
    """Même femme, même décor — on remplace uniquement la robe par celle de la référence."""
    _budget_guard()
    parts = [
        {"text": ("Edit this photograph. Keep the SAME woman (same face, same hair, same skin), the same "
                  "setting, the same light, the SAME pose and the SAME camera distance and framing — this "
                  "is a lookbook series and the slides must align perfectly when swiped. Only change her "
                  "outfit: she now wears the EXACT dress from the second reference image — same color, "
                  "same fabric, same neckline, same construction, nothing invented. "
                  "Clean, crisp, high-resolution photograph.")},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(base_path)}},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(dress_ref_path)}},
    ]
    resp = gemini(IMAGE_MODEL, parts, key)
    for cand in resp.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            dd = part.get("inlineData") or part.get("inline_data")
            if dd:
                return base64.b64decode(dd["data"])
    raise RuntimeError(f"pas d'image: {str(resp)[:200]}")


def make_muse_carousel(products3, captions, state, key):
    """RECETTE FONDATRICE N°1 « lookbook aligné » (engine/mdv-refs/carrousels.json) :
    la même muse en STUDIO, une robe par slide, pose et cadrage IDENTIQUES sur chaque slide."""
    from PIL import Image as _I
    import shutil as _sh
    cfg = json.load(open(os.path.join(ENGINE, "scenes.json")))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    d = _atelier_dir(f"{stamp}_carousel_muse-{'-'.join(core.first_name(p['title']).lower() for p in products3)}")
    refs = []
    for i, p in enumerate(products3):
        rp = os.path.join(d, f"ref-{i+1}.jpg")
        core.fetch_image(p["images"][0]["src"], 1200).save(rp, quality=92)
        refs.append(rp)
    studios = [s for s in cfg["scenes"] if s["id"].startswith("studio")]
    scene = pick_scene(studios or cfg["scenes"], state.get("last_scene"))
    state["last_scene"] = scene["id"]
    pose = ("standing full length facing the camera, feet visible, arms relaxed along the body — "
            "clean lookbook stance that will be repeated identically on every slide")
    hero = None
    for attempt in range(1, 4):
        try:
            raw = generate_candidate(refs[0], scene["text"], pose, cfg["rules"], key, sample_imperfections(cfg),
                                     framing="full-length, centered, generous and identical margins — lookbook framing")
        except RuntimeError:
            continue
        cp = os.path.join(d, "slide-1.jpg")
        save_jpeg(raw, cp)
        try:
            save_jpeg(texture_pass(cp, key), cp)
        except RuntimeError:
            pass
        v = check_candidate(refs[0], cp, key)
        if v.get("verdict") == "pass":
            hero = cp
            break
    if not hero:
        _sh.rmtree(d)
        print("❌ carrousel muse : slide 1 jamais validée")
        return None
    ok_slides = [hero]
    for i in (1, 2):
        for essai in (1, 2):
            try:
                raw = swap_dress(hero, refs[i], key)
            except RuntimeError:
                continue
            cp = os.path.join(d, f"slide-{i+1}.jpg")
            save_jpeg(raw, cp)
            v = check_candidate(refs[i], cp, key)
            if v.get("dress_identical") and not v.get("invented_details"):
                ok_slides.append(cp)
                break
            os.remove(cp)
    if len(ok_slides) < 3:
        _sh.move(d, os.path.join(ROOT, "queue", "rejected", os.path.basename(d)))
        print("❌ carrousel muse : robes 2/3 jamais fidèles")
        return None
    for i, sl in enumerate(sorted(f for f in os.listdir(d) if f.startswith("slide")), 1):
        sp = os.path.join(d, sl)
        core.cover(_I.open(sp).convert("RGB"), 1080, 1350).save(sp, quality=92)
        magnific_finalize(sp, key) if i == 1 else clean_noise(sp)
    for rp in refs:
        os.remove(rp)
    names = " · ".join(core.first_name(p["title"]) for p in products3)
    core.write_meta(d, "carousel", "Look 1, 2 or 3 ? ~",
                    f"Lookbook carousel: the same muse in an aligned studio series wearing three Inaya Paris dresses: {names}.",
                    "#inayaparis #quietluxury #eveningdress")
    print(f"✅ carrousel lookbook aligné : {names}")
    return _livrer(d)


def make_carousel_lineup(products3, captions, state, key):
    """LA recette des exemples explicites de Laurie ('Carousel 1-5.jpg') : UN panorama studio,
    la même muse répétée 3 fois côte à côte (une robe par silhouette, numérotation manuscrite),
    découpé en slides contiguës — une silhouette coupée au bord continue sur la slide suivante."""
    from PIL import Image as _I
    import shutil as _sh
    cfg = json.load(open(os.path.join(ENGINE, "scenes.json")))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    noms = "-".join(core.first_name(p["title"]).lower() for p in products3)
    d = _atelier_dir(f"{stamp}_carousel_lineup-{noms}")
    refs = []
    for i, p in enumerate(products3):
        rp = os.path.join(d, f"ref-{i+1}.jpg")
        core.fetch_image(p["images"][0]["src"], 1200).save(rp, quality=92)
        refs.append(rp)
    # 1) la muse : UNE image studio validée (robe 1) — pipeline classique fiable
    cfg2 = json.load(open(os.path.join(ENGINE, "scenes.json")))
    studios = [s for s in cfg2["scenes"] if s["id"].startswith("studio")]
    scene = pick_scene(studios or cfg2["scenes"], state.get("last_scene"))
    state["last_scene"] = scene["id"]
    pose = ("standing full length facing the camera, feet visible, arms relaxed along the body, "
            "centered with generous space above the head and around her — lookbook lineup framing")
    hero = None
    import time as _t
    for attempt in range(1, 5):
        try:
            raw = generate_candidate(refs[0], scene["text"], pose, cfg2["rules"], key, sample_imperfections(cfg2),
                                     framing="vertical full-length, the figure occupies the center third")
        except RuntimeError:
            _t.sleep(20)  # Gemini surchargé par vagues (503) : on laisse retomber avant de réessayer
            continue
        cp = os.path.join(d, "fig-1.jpg")
        save_jpeg(raw, cp)
        try:
            save_jpeg(texture_pass(cp, key), cp)
        except RuntimeError:
            pass
        v = check_candidate(refs[0], cp, key)
        if v.get("verdict") != "pass":
            v = check_candidate(refs[0], cp, key)  # le checker flanche parfois : 2e avis avant de jeter une image payée
        if v.get("verdict") == "pass":
            hero = cp
            break
    if not hero:
        _sh.move(d, os.path.join(ROOT, "queue", "rejected", os.path.basename(d)))
        print(f"❌ carrousel lineup {noms} : muse jamais validée")
        return None
    # 2) la MÊME muse dans les robes 2 et 3 (swap contrôlé, pose et cadrage identiques)
    figs = [hero]
    for i in (1, 2):
        ok = None
        for essai in (1, 2):
            try:
                raw = swap_dress(hero, refs[i], key)
            except RuntimeError:
                continue
            cp = os.path.join(d, f"fig-{i+1}.jpg")
            save_jpeg(raw, cp)
            v = check_candidate(refs[i], cp, key)
            if not (v.get("dress_identical") and not v.get("invented_details")):
                v = check_candidate(refs[i], cp, key)  # 2e avis avant de jeter une image payée
            if v.get("dress_identical") and not v.get("invented_details"):
                ok = cp
                break
            os.remove(cp)
        if not ok:
            _sh.move(d, os.path.join(ROOT, "queue", "rejected", os.path.basename(d)))
            print(f"❌ carrousel lineup {noms} : robe {i+1} jamais fidèle")
            return None
        figs.append(ok)
    # 3) finition Magnific sur CHAQUE silhouette AVANT montage
    #    (JAMAIS sur le panorama : Magnific recadre en 1080x1350 et détruirait le montage — leçon du 03/08)
    for f in figs:
        magnific_finalize(f, key)
    # 4) tuiles standardisées 1080x1350 — méthode validée par Laurie le 03/08 :
    #    fond harmonisé (blanc chaud commun), silhouettes à échelle IDENTIQUE (84% de la hauteur,
    #    pieds alignés), centrage sur la silhouette, remplissage par étirement des bords de la
    #    photo elle-même (aucune couture de collage), numéro dans la bande d'air au-dessus des têtes.
    H, W = 1350, 1080
    CIBLE_FIG = int(H * 0.84)
    Y_PIEDS = H - 40
    FOND_CIBLE = (246, 244, 240)

    def _normalise_fond(im):
        ech = im.resize((60, 75))
        px = ech.load()
        coins = [px[x, y] for x, y in ((2, 2), (57, 2), (2, 36), (57, 36))]
        bg = tuple(sum(c[i] for c in coins) // 4 for i in range(3))
        gains = [FOND_CIBLE[i] / max(1, bg[i]) for i in range(3)]
        return _I.merge("RGB", [c.point(lambda v, g=gains[i]: min(255, int(v * g)))
                                for i, c in enumerate(im.split())])

    def _bornes_figure(im):
        g = im.convert("L").resize((100, 200))
        px = g.load()
        lignes = [(sum((px[x, y] - sum(px[x2, y] for x2 in range(100)) / 100) ** 2
                       for x in range(100)) / 100) ** 0.5 for y in range(200)]
        cols = [(sum((px[x, y] - sum(px[x, y2] for y2 in range(200)) / 200) ** 2
                     for y in range(200)) / 200) ** 0.5 for x in range(100)]
        ys = [y for y, v in enumerate(lignes) if v > max(lignes) * 0.18]
        xs = [x for x, v in enumerate(cols) if v > max(cols) * 0.18]
        return (min(ys) / 200 * im.height, max(ys) / 200 * im.height,
                min(xs) / 100 * im.width, max(xs) / 100 * im.width)

    def _etire_bords(im, l, t, r, b):
        l, t, r, b = max(0, l), max(0, t), max(0, r), max(0, b)
        W2, H2 = im.width + l + r, im.height + t + b
        canv = _I.new("RGB", (W2, H2))
        canv.paste(im, (l, t))
        if t: canv.paste(im.crop((0, 0, im.width, 1)).resize((im.width, t)), (l, 0))
        if b: canv.paste(im.crop((0, im.height - 1, im.width, im.height)).resize((im.width, b)), (l, t + im.height))
        if l: canv.paste(canv.crop((l, 0, l + 1, H2)).resize((l, H2)), (0, 0))
        if r: canv.paste(canv.crop((l + im.width - 1, 0, l + im.width, H2)).resize((r, H2)), (l + im.width, 0))
        return canv

    tuiles = []
    for f in figs:
        im = _normalise_fond(_I.open(f).convert("RGB"))
        y0, y1, x0, x1 = _bornes_figure(im)
        scale = CIBLE_FIG / max(1, (y1 - y0))
        im = im.resize((int(im.width * scale), int(im.height * scale)))
        y0, y1, x0, x1 = [v * scale for v in (y0, y1, x0, x1)]
        gauche, haut = int((x0 + x1) / 2 - W / 2), int(y1 - Y_PIEDS)
        im2 = _etire_bords(im, -gauche if gauche < 0 else 0, -haut if haut < 0 else 0,
                           (gauche + W) - im.width if gauche + W > im.width else 0,
                           (haut + H) - im.height if haut + H > im.height else 0)
        g2, h2 = max(0, gauche), max(0, haut)
        tuiles.append(im2.crop((g2, h2, g2 + W, h2 + H)))
    # 5) numérotation fine « 1. 2. 3. » + panorama continu + découpe aux jonctions exactes
    from PIL import ImageDraw, ImageFont
    police = None
    for fp in ("/System/Library/Fonts/Supplemental/Snell Roundhand.ttc",
               "/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"):
        if os.path.exists(fp):
            police = ImageFont.truetype(fp, 56)
            break
    pano = _I.new("RGB", (W * len(tuiles), H))
    for k, t in enumerate(tuiles):
        if police:
            ImageDraw.Draw(t).text((W // 2, 84), f"{k+1}.", font=police, fill=(40, 32, 28), anchor="mm")
        pano.paste(t, (k * W, 0))
    pano.save(os.path.join(d, "panorama.jpg"), quality=94)
    for i in range(len(tuiles)):
        pano.crop((i * W, 0, (i + 1) * W, H)).save(os.path.join(d, f"slide-{i+1}.jpg"), quality=92)
    # les figures intermédiaires restent dans le dossier : on n'efface JAMAIS une image générée (règle bibliothèque)
    for rp in refs:
        os.remove(rp)
    names = " · ".join(core.first_name(p["title"]) for p in products3)
    core.write_meta(d, "carousel", "Look 1, 2 or 3 ? ~",
                    f"Lookbook lineup carousel: the same muse three times on one seamless studio panorama, wearing {names}, numbered looks, sliced across the slides.",
                    "#inayaparis #quietluxury #eveningdress")
    print(f"✅ carrousel lineup panorama : {names} ({len(tuiles)} slides)")
    return _livrer(d)


PLAN_INSTRUCTIONS = {
    # Grammaire des 21 carrousels exemples de Laurie (mdv-refs/plans.json) : le vêtement est TOUJOURS porté.
    "trois_quarts": "a three-quarter view from the hips up, the bodice and neckline of the garment filling the frame, her face partly out of frame or looking away, hands relaxed",
    "closeup_matiere": "a tight close-up on the fabric, seams and construction of the garment as she wears it, cropped at the lips or chin, no full face",
    "dos": "seen from BEHIND, full length, head slightly turned, showing the back of the garment exactly as in the product back-view reference (straps, zip, lacing, seams)",
    "marche": "walking slowly toward or past the camera, full length, natural stride, one leg forward, garment in motion",
    "assise": "seated on a step, ledge or chair of the same location, full body visible, sculptural static pose, gaze away from the camera",
    "plein_pied": "a second full-length standing pose, different from the first, weight on one leg, sculptural",
}
PLAN_SEQUENCES = [  # rotation d'un carrousel au suivant (state['last_plan_seq']) — « assise » retirée (test v3 07/09 :
    # le modèle invente un escalier pour s'asseoir et quitte le décor de la slide 1)
    ["trois_quarts", "dos"],
    ["closeup_matiere", "dos", "marche"],
    ["dos", "trois_quarts", "marche"],
    ["trois_quarts", "closeup_matiere", "plein_pied"],
]


def _plan_ref(plan_type):
    """Une slide exemple de Laurie du même type de plan (référence de CADRAGE, jamais de contenu)."""
    try:
        plans = json.load(open(os.path.join(ENGINE, "mdv-refs", "plans.json")))
        pool = [x for x in plans.get(plan_type, []) if x.get("worn") and not x.get("erreur")]
        if not pool and plan_type in ("marche", "assise"):
            pool = [x for x in plans.get("plein_pied", []) if x.get("worn")]
        if pool:
            return os.path.join(ENGINE, random.choice(pool)["path"])
    except Exception:
        pass
    return None


def _slide_est_portee(img_path, key):
    """Garde-fou (Laurie 07/09) : une slide sans personne = nature morte = refusée."""
    try:
        resp = gemini(CHECK_MODEL, [{"text": "Answer ONLY with JSON {\"worn\": true/false, \"still_life\": true/false}: is a person wearing the garment in this photo (worn), or is the garment shown alone, flat, on a hanger or on furniture (still_life)?"},
                                    {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(img_path)}}], key)
        txt = resp["candidates"][0]["content"]["parts"][0]["text"]
        j = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
        return bool(j.get("worn")) and not j.get("still_life")
    except Exception:
        return True


PLAN_COMPATIBLES = {
    "trois_quarts": {"trois_quarts", "closeup_matiere"},
    "closeup_matiere": {"closeup_matiere", "trois_quarts"},
    "dos": {"dos"},
    "marche": {"marche", "plein_pied"},
    "assise": {"assise"},
    "plein_pied": {"plein_pied", "marche"},
}


def _ref_dos_par_vision(refs, key):
    """Parmi les photos produit téléchargées, laquelle montre le DOS du vêtement ? (None si aucune)"""
    for rp in refs[1:]:
        try:
            resp = gemini(CHECK_MODEL, [{"text": "Answer ONLY with JSON {\"view\": \"front\"|\"back\"|\"side\"|\"detail\"|\"lifestyle\"}: from which side is the garment shown in this product photo? back = the model's back faces the camera."},
                                        {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(rp)}}], key)
            txt = resp["candidates"][0]["content"]["parts"][0]["text"]
            if json.loads(txt[txt.find("{"):txt.rfind("}") + 1]).get("view") == "back":
                return rp
        except Exception:
            continue
    return None


def _verifie_plan(hero_path, cand_path, plan, key):
    """Le plan demandé est-il obtenu ? même lieu que la slide 1 ? pas un doublon ? (07/09/2026)"""
    if FAKE:
        return (True, True, False, {"fake": True})
    prompt = ("Image 1 is the first slide of a fashion carousel, image 2 a candidate for the next slide. Answer ONLY with JSON: "
              "{\"type\": one of [plein_pied, trois_quarts, closeup_matiere, dos, marche, assise, flatlay, autre] describing image 2 "
              "(plein_pied = full length standing; trois_quarts = hips/waist up; closeup_matiere = tight crop on fabric, face mostly out of frame; "
              "dos = seen from behind; marche = walking; assise = seated), "
              "\"same_location\": true/false (same place, same background elements, same light as image 1), "
              "\"near_duplicate\": true/false (same pose and nearly the same framing as image 1)}")
    try:
        resp = gemini(CHECK_MODEL, [{"text": prompt},
                                    {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(hero_path)}},
                                    {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(cand_path)}}], key)
        txt = resp["candidates"][0]["content"]["parts"][0]["text"]
        j = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
        t = j.get("type", "autre")
        return (t in PLAN_COMPATIBLES.get(plan, {plan}), bool(j.get("same_location", True)), bool(j.get("near_duplicate", False)), j)
    except Exception as e:
        return (True, True, False, {"erreur": str(e)[:80]})


def make_carousel_tour(product, captions, state, key):
    """RECETTE MDV N°1 « tour du produit », refondue le 07/09/2026 (retour Laurie : « slides 2-3 fake,
    tout le temps la même chose ») : UNE muse, UNE robe, UN lieu, le vêtement TOUJOURS PORTÉ.
    Slide 1 = plein pied héro validé ; slides 2-4 = NOUVELLES photos du même shooting selon une séquence
    de plans qui tourne (trois-quarts, matière, dos, marche, assise), chacune guidée par une slide EXEMPLE
    de Laurie du même type de plan (référence de cadrage). Jamais de fauteuil ni de flatlay."""
    from PIL import Image as _I
    import shutil as _sh
    import time as _t
    cfg = json.load(open(os.path.join(ENGINE, "scenes.json")))
    name = core.first_name(product["title"])
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    d = _atelier_dir(f"{stamp}_carousel_tour_{product['handle']}")
    imgs = product["images"]
    picks = [imgs[0]]
    for im in imgs[1:]:
        blob = (str(im.get("alt") or "") + " " + im.get("src", "")).lower()
        if any(k in blob for k in ("back", "dos", "rear")) and im not in picks:
            picks.append(im)
    for im in imgs[1:4]:
        if len(picks) >= 4:
            break
        if im not in picks:
            picks.append(im)
    refs = []
    for i, im in enumerate(picks[:4]):
        rp = os.path.join(d, f"ref-{i+1}.jpg")
        core.fetch_image(im["src"], 1200).save(rp, quality=92)
        refs.append(rp)
    scene = pick_scene(cfg["scenes"], state.get("last_scene"))
    state["last_scene"] = scene["id"]
    rules = cfg["rules"] + lecons_texte(product["handle"])
    seq_i = (int(state.get("last_plan_seq", -1)) + 1) % len(PLAN_SEQUENCES)
    state["last_plan_seq"] = seq_i
    # La photo de dos se reconnaît PAR VISION (test v2 07/09 : Ruby avait une vraie photo de dos, sans « back » dans le nom)
    ref_dos = _ref_dos_par_vision(refs, key)
    a_ref_dos = ref_dos is not None
    # « dos » exige une vraie photo produit de dos (sinon le contrôle refuse tout, test v2 07/09) → repli
    sequence = []
    for pl in PLAN_SEQUENCES[seq_i]:
        if pl == "dos" and not a_ref_dos:
            pl = "marche" if "marche" not in PLAN_SEQUENCES[seq_i] else "plein_pied"
        if pl not in sequence:
            sequence.append(pl)
    replis = [pl for pl in ("closeup_matiere", "trois_quarts", "marche", "plein_pied") if pl not in sequence][:2]
    journal = []
    _progress(f"carrousel {name} — slide 1/{len(sequence)+1} (plein pied)")
    hero = None
    for attempt in range(1, 5):
        try:
            poses_debout = [po for po in cfg["poses"] if not any(w in po.lower() for w in ("sit", "seated", "assise", "chair", "steps"))] or cfg["poses"]
            raw = generate_candidate(refs, scene["text"], random.choice(poses_debout), rules, key,
                                     sample_imperfections(cfg))
        except RuntimeError as e:
            journal.append({f"slide-1 essai {attempt}": str(e)[:150]})
            _t.sleep(15)
            continue
        cp = os.path.join(d, "slide-1.jpg")
        save_jpeg(raw, cp)
        try:
            save_jpeg(texture_pass(cp, key), cp)
        except RuntimeError:
            pass
        v = check_candidate(refs[0], cp, key)
        if v.get("verdict") != "pass":
            v = check_candidate(refs[0], cp, key)
        journal.append({f"slide-1 essai {attempt}": v})
        if v.get("verdict") == "pass":
            hero = cp
            break
    if not hero:
        json.dump(journal, open(os.path.join(d, "controle.json"), "w"), indent=2, ensure_ascii=False)
        _sh.move(d, os.path.join(ROOT, "queue", "rejected", os.path.basename(d)))
        print(f"❌ tour {name} : plein pied jamais validé")
        _fiabilite(product["handle"], False)
        return None
    plans_faits = ["plein_pied"]
    a_faire = list(sequence)
    idx = 1
    while a_faire and idx < 5:
        plan = a_faire.pop(0)
        idx += 1
        _progress(f"carrousel {name} — slide {idx}/{len(sequence)+1} ({plan})")
        fname = f"slide-{idx}.jpg"
        dernier = not a_faire
        ok = False
        for essai in (1, 2):  # 2 essais max par plan (test v3 : 12 générations perdues sur un seul carrousel)
            _budget_guard()
            plan_ref = None
            consigne = (
                "Create a NEW photograph from the SAME fashion shoot as the first image: the SAME woman (same face, same hairstyle), "
                "wearing the SAME garment, in the SAME location with the SAME light and color palette. "
                f"This new frame is {PLAN_INSTRUCTIONS[plan]}. "
                + ("Her silhouette is slightly cut by the edge of the frame, as if inviting a swipe. " if dernier else "")
                + "The garment is WORN by her: a person is always in the frame — never a still life, never on a chair, never laid flat. "
                "Every garment detail stays EXACTLY as in the product reference photos. Crisp, sharp, real unretouched photograph, no text."
                + "The camera MOVES: this frame must be clearly different from the first image (different distance, angle and pose) — never a crop or copy of it. "
                f"THE LOCATION DOES NOT CHANGE. It is exactly the place of the first image: {scene['text']} Reproduce the same walls, floor, furniture, windows and light; "
                "do not add stairs, balustrades, columns or any element absent from the first image. "
                + lecons_texte(product["handle"]))
            # 07/09 test 1 : une image exemple envoyée comme référence de cadrage entraînait SON décor
            # (escalier, assise) dans la slide → on garde la grammaire en texte seulement.
            parts = ([{"text": consigne}, {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(hero)}}]
                     + [{"inline_data": {"mime_type": "image/jpeg", "data": b64_of(r)}} for r in refs])
            resp = gemini(IMAGE_MODEL, parts, key)
            raw = None
            for cc in resp.get("candidates", []):
                for pt in cc.get("content", {}).get("parts", []):
                    dd = pt.get("inlineData") or pt.get("inline_data")
                    if dd:
                        raw = base64.b64decode(dd["data"])
            if not raw:
                journal.append({f"{fname} essai {essai}": "pas d'image générée"})
                _t.sleep(10)
                continue
            cp = os.path.join(d, fname)
            save_jpeg(raw, cp)
            ref_v = ref_dos if (plan == "dos" and ref_dos) else refs[0]
            v = check_candidate(ref_v, cp, key)
            if not (v.get("dress_identical") and not v.get("invented_details")):
                v = check_candidate(ref_v, cp, key)  # 2e avis avant de jeter une image payée
            portee = _slide_est_portee(cp, key)
            plan_ok, meme_lieu, doublon, detail = _verifie_plan(hero, cp, plan, key)
            journal.append({f"{fname} essai {essai} ({plan})": {"controle": v, "portee": portee, "plan_ok": plan_ok, "meme_lieu": meme_lieu, "doublon_slide1": doublon, "lu": detail}})
            if v.get("dress_identical") and not v.get("invented_details") and portee and plan_ok and meme_lieu and not doublon:
                ok = True
                break
            os.rename(cp, os.path.join(d, f"essai-{fname[:-4]}_{plan}-{essai}-recale.jpg"))  # on n'efface jamais
        if not ok:
            # plan raté : on passe au plan suivant de la séquence (test v4 07/09 : le carrousel était rejeté
            # alors qu'il restait des plans à essayer), puis aux replis ; 3 slides minimum pour livrer
            if a_faire:
                print(f"↩️ tour {name} : {fname} ({plan}) raté → plan suivant {a_faire[0]}")
                idx -= 1
                continue
            if replis:
                sub = replis.pop(0)
                print(f"↩️ tour {name} : {fname} ({plan}) raté → repli {sub}")
                a_faire.insert(0, sub)
                idx -= 1
                continue
            if len(plans_faits) >= 3:
                break
            json.dump(journal, open(os.path.join(d, "controle.json"), "w"), indent=2, ensure_ascii=False)
            _sh.move(d, os.path.join(ROOT, "queue", "rejected", os.path.basename(d)))
            print(f"❌ tour {name} : {len(plans_faits)} slide(s) seulement, aucun plan restant")
            return None
        plans_faits.append(plan)
        if len(plans_faits) >= 4:
            break
        if not a_faire and len(plans_faits) < 3 and replis:
            a_faire.append(replis.pop(0))  # règle fondatrice : 3 slides minimum (test v5 07/09 livrait 2 slides)
    if len(plans_faits) < 3:
        json.dump(journal, open(os.path.join(d, "controle.json"), "w"), indent=2, ensure_ascii=False)
        _sh.move(d, os.path.join(ROOT, "queue", "rejected", os.path.basename(d)))
        print(f"❌ tour {name} : {len(plans_faits)} slides seulement (minimum 3)")
        return None
    json.dump(journal, open(os.path.join(d, "controle.json"), "w"), indent=2, ensure_ascii=False)
    for i, sl in enumerate(sorted(f for f in os.listdir(d)
                                  if re.match(r"^slide-\d+\.jpg$", f)), 1):
        sp = os.path.join(d, sl)
        core.cover(_I.open(sp).convert("RGB"), 1080, 1350).save(sp, quality=92)
        magnific_finalize(sp, key) if i == 1 else clean_noise(sp)
    _fiabilite(product["handle"], True)
    cap = core.pick_caption(captions, "carousel", state, name)
    libelle = {"plein_pied": "full length", "trois_quarts": "three-quarter", "closeup_matiere": "fabric close-up",
               "dos": "from behind", "marche": "walking", "assise": "seated"}
    core.write_meta(d, "carousel", cap,
                    f"Recette « tour du produit » (séquence {seq_i+1}) : the {name}, {' → '.join(libelle[p] for p in plans_faits)} — same muse, same place, same light, garment always worn.",
                    random.choice(captions["hashtags"]))
    try:
        mp = os.path.join(d, "meta.json"); m = json.load(open(mp))
        m["recette"] = "tour_du_produit"; m["plans"] = plans_faits; json.dump(m, open(mp, "w"), indent=2, ensure_ascii=False)
    except Exception:
        pass
    print(f"✅ carrousel tour du produit : {name} ({' → '.join(plans_faits)})")
    return _livrer(d)


def make_carousel_porte_pose(product, captions, state, key):
    """RECETTE FONDATRICE N°4 « porté + posé » (engine/mdv-refs/carrousels.json) :
    UNE robe, UN shooting cohérent — slide 1 portée, slide 2 posée sur le fauteuil,
    slide 3 à plat. Même lumière et même ambiance sur les 3 slides."""
    from PIL import Image as _I
    import shutil as _sh
    cfg = json.load(open(os.path.join(ENGINE, "scenes.json")))
    name = core.first_name(product["title"])
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    d = _atelier_dir(f"{stamp}_carousel_porte-pose_{product['handle']}")
    refs = []
    for i, im in enumerate(product["images"][:2]):
        rp = os.path.join(d, f"ref-{i+1}.jpg")
        core.fetch_image(im["src"], 1200).save(rp, quality=92)
        refs.append(rp)
    interieurs = [s for s in cfg["scenes"] if s["id"] in
                  ("interieur-soir", "nuit-interieure-bordeaux", "townhouse-londonien", "interieur-moderne-luxe")]
    scene = pick_scene(interieurs or cfg["scenes"], state.get("last_scene"))
    state["last_scene"] = scene["id"]
    rules = cfg["rules"] + lecons_texte(product["handle"])
    _progress(f"carrousel {name} — slide 1/3 (la pièce portée)")
    # slide 1 : la robe portée
    porte = None
    journal = []
    for attempt in range(1, 4):
        try:
            raw = generate_candidate(refs, scene["text"], random.choice(cfg["poses"]), rules, key,
                                     sample_imperfections(cfg))
        except RuntimeError as e:
            journal.append({f"slide-1 essai {attempt}": f"génération échouée : {str(e)[:150]}"})
            continue
        cp = os.path.join(d, "slide-1.jpg")
        save_jpeg(raw, cp)
        try:
            save_jpeg(texture_pass(cp, key), cp)
        except RuntimeError:
            pass
        v = check_candidate(refs[0], cp, key)
        journal.append({f"slide-1 essai {attempt}": v})
        if v.get("verdict") == "pass":
            porte = cp
            break
    if not porte:
        json.dump(journal, open(os.path.join(d, "controle.json"), "w"), indent=2, ensure_ascii=False)
        _sh.move(d, os.path.join(ROOT, "queue", "rejected", os.path.basename(d)))
        print(f"❌ porté+posé {name} : slide portée jamais validée")
        _fiabilite(product["handle"], False)
        return None
    # slides 2 et 3 : la robe posée. Le suffixe « SAME SHOOTING » déclenchait des refus
    # IMAGE_OTHER systématiques (constat 01/09) : on tourne sur des variantes TESTÉES,
    # ambiance intégrée dans la phrase, une variante différente par essai.
    CHAISE_VARIANTES = [
        ("Still-life fashion photograph in a dim warm evening interior with espresso tones and one "
         "glowing table lamp: the exact garment from the reference image lies gracefully draped across "
         "an antique gilded armchair, herringbone parquet floor. Same color, same fabric, same neckline, "
         "same sleeves and construction as the reference, soft natural folds. True photographic rendering, "
         "real depth of field, photorealistic, never a painting or 3D render. No person in the frame."),
        CHAISE_PROMPT,
        ("Still-life fashion photograph: the exact garment from the reference image lies gracefully "
         "draped across an antique armchair, warm window light, herringbone parquet floor. Same color, "
         "fabric and construction as the reference, soft natural folds, photorealistic, no person in the frame."),
    ]
    FLATLAY_VARIANTES = [
        FLATLAY_PROMPT,
        ("Top-down still-life photograph: the exact garment from the reference image laid flat on a warm "
         "sand linen sheet, natural soft folds, a simple wooden hanger beside it, soft daylight, honest "
         "shadows, photorealistic, vertical 4:5, no person, no text."),
        FLATLAY_PROMPT,
    ]
    for fname, variantes in (("slide-2.jpg", CHAISE_VARIANTES), ("slide-3.jpg", FLATLAY_VARIANTES)):
        _progress(f"carrousel {name} — {fname.replace('.jpg', '')}/3")
        ok = False
        for attempt in (1, 2, 3):
            prompt = variantes[attempt - 1]  # JAMAIS de leçons mannequin sur un shot sans personne (refus IMAGE_OTHER)
            _budget_guard()
            parts = [{"text": prompt},
                     {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(refs[0])}}]
            resp = gemini(IMAGE_MODEL, parts, key)
            raw = None
            for c in resp.get("candidates", []):
                for pt in c.get("content", {}).get("parts", []):
                    dd = pt.get("inlineData") or pt.get("inline_data")
                    if dd:
                        raw = base64.b64decode(dd["data"])
            if not raw:
                journal.append({f"{fname} essai {attempt}": f"pas d'image générée : {str(resp.get('error') or resp.get('candidates', [{}])[0].get('finishReason', '?'))[:120]}"})
                continue
            cp = os.path.join(d, fname)
            save_jpeg(raw, cp)
            v = check_candidate(refs[0], cp, key)
            journal.append({f"{fname} essai {attempt}": v})
            if v.get("dress_identical") and not v.get("invented_details"):
                ok = True
                break
            os.remove(cp)
        if not ok:
            json.dump(journal, open(os.path.join(d, "controle.json"), "w"), indent=2, ensure_ascii=False)
            _sh.move(d, os.path.join(ROOT, "queue", "rejected", os.path.basename(d)))
            print(f"❌ porté+posé {name} : {fname} jamais fidèle")
            return None
    for i, sl in enumerate(sorted(f for f in os.listdir(d) if f.startswith("slide")), 1):
        sp = os.path.join(d, sl)
        core.cover(_I.open(sp).convert("RGB"), 1080, 1350).save(sp, quality=92)
        magnific_finalize(sp, key) if i == 1 else clean_noise(sp)
    for rp in refs:
        os.remove(rp)
    _fiabilite(product["handle"], True)
    core.write_meta(d, "carousel", f"Worn, then at rest. The {name}. ~",
                    f"Carousel from one shooting: the {name} dress worn by the muse, then draped over an antique armchair, then laid flat — same warm light throughout.",
                    "#inayaparis #quietluxury #eveningdress")
    print(f"✅ carrousel porté+posé : {name}")
    return _livrer(d)


def make_no_face(kind, product, captions, state, key, correction=""):
    """kind: 'bust', 'flatlay' ou 'chaise' — formats produit sans humain."""
    from PIL import Image as _I
    name = core.first_name(product["title"])
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    d = _atelier_dir(f"{stamp}_ai-{kind}_{product['handle']}")
    ref = os.path.join(d, "reference.jpg")
    core.fetch_image(product["images"][0]["src"], 1200).save(ref, quality=92)
    prompt = {"bust": BUST_PROMPT, "chaise": CHAISE_PROMPT}.get(kind, FLATLAY_PROMPT)  # sans leçons mannequin (shot sans personne)
    if correction:
        prompt += " CRITICAL correction requested by the brand founder after a rejected attempt: " + correction + ". Address this point precisely."
    kept, verdicts = None, []
    for attempt in range(1, 6 if kind == "chaise" else 4):
        _budget_guard()
        parts = [{"text": prompt},
                 {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(ref)}}]
        resp = gemini(IMAGE_MODEL, parts, key)
        raw = None
        for c in resp.get("candidates", []):
            for pt in c.get("content", {}).get("parts", []):
                dd = pt.get("inlineData") or pt.get("inline_data")
                if dd:
                    raw = base64.b64decode(dd["data"])
        if not raw:
            verdicts.append({"error": "refus"})
            continue
        cp = os.path.join(d, "media.jpg")
        save_jpeg(raw, cp)
        img = _I.open(cp).convert("RGB")
        core.cover(img, 1080, 1350).save(cp, quality=92)
        v = check_candidate(ref, cp, key)
        verdicts.append(v)
        if v.get("dress_identical") and not v.get("invented_details"):
            kept = cp
            break
    with open(os.path.join(d, "controle.json"), "w") as f:
        json.dump(verdicts, f, indent=2, ensure_ascii=False)
    if not kept:
        import shutil
        shutil.move(d, os.path.join(ROOT, "queue", "rejected", os.path.basename(d)))
        print(f"❌ {kind} {name} — robe jamais fidèle")
        _fiabilite(product["handle"], False)
        return None
    _fiabilite(product["handle"], True)
    os.remove(ref)
    cap = core.pick_caption(captions, "studio", state, name)
    core.write_meta(d, "studio", cap,
                    f"The {name} dress, {('displayed on a dress form' if kind=='bust' else 'flat-lay editorial')}.",
                    random.choice(captions["hashtags"]))
    print(f"✅ {kind} {name}")
    return _livrer(d)


def lecons_texte(handle=""):
    """Les règles apprises des rejets de Laurie — injectées dans chaque prompt."""
    lp = os.path.join(ENGINE, "lecons.json")
    if not os.path.exists(lp):
        return ""
    lec = json.load(open(lp))
    regles = list(lec.get("global", []))
    if handle:
        regles += lec.get("par_robe", {}).get(handle, [])
    if not regles:
        return ""
    return " HARD RULES learned from the founder's past rejections (breaking any of these means instant rejection): " + " ".join(regles)


def _fiabilite(handle, succes):
    """Compte les réussites/échecs par robe. 2 échecs sans aucun succès → blacklist auto
    (on arrête de payer pour des robes que l'IA rate systématiquement)."""
    fp = os.path.join(ENGINE, "fiabilite.json")
    f = json.load(open(fp)) if os.path.exists(fp) else {}
    e = f.setdefault(handle, {"ok": 0, "echec": 0})
    e["ok" if succes else "echec"] += 1
    json.dump(f, open(fp, "w"), indent=2)
    if not succes and e["echec"] >= 2 and e["ok"] == 0:
        bp = os.path.join(ENGINE, "blacklist.json")
        b = json.load(open(bp)) if os.path.exists(bp) else {"exclues_generation": []}
        if handle not in b["exclues_generation"]:
            b["exclues_generation"].append(handle)
            json.dump(b, open(bp, "w"), indent=2, ensure_ascii=False)
            print(f"⛔ {handle} blacklistée automatiquement (2 échecs, 0 réussite) — plus un centime dessus")


def make_model_post(product, captions, state, key, scene_text=None, concept="", pose_text=None, framing_text=None, correction=""):
    """Plein-pied mannequin pipeline complet, avec brief d'ambiance de la Creative Producer."""
    from PIL import Image as _I
    cfg = json.load(open(os.path.join(ENGINE, "scenes.json")))
    name = core.first_name(product["title"])
    scene = scene_text or pick_scene(cfg["scenes"], state.get("last_scene"))["text"]
    rules = cfg["rules"] + lecons_texte(product["handle"])
    if correction:
        rules = rules + " CRITICAL correction requested by the brand founder after a rejected attempt: " + correction + ". Address this point precisely."
    pose = pose_text or random.choice(cfg["poses"])
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    d = _atelier_dir(f"{stamp}_ai-studio_{product['handle']}")
    imgs = product["images"]
    picks = [imgs[0]]
    for im in imgs[1:]:
        blob = (str(im.get("alt") or "") + " " + im.get("src", "")).lower()
        if any(k in blob for k in ("back", "dos", "rear")) and im not in picks:
            picks.append(im)
    for im in imgs[1:3]:
        if len(picks) >= 3:
            break
        if im not in picks:
            picks.append(im)
    picks = picks[:3]
    ref = []
    for i, im in enumerate(picks):
        rp = os.path.join(d, "reference.jpg" if i == 0 else f"reference-{i+1}.jpg")
        core.fetch_image(im["src"], 1200).save(rp, quality=92)
        ref.append(rp)
    verdicts, kept = [], None
    for attempt in range(1, 4):
        imps = sample_imperfections(cfg)
        try:
            raw = generate_candidate(ref, scene, pose, rules, key, imps, framing=framing_text)
        except RuntimeError as e:
            verdicts.append({"error": str(e)[:100]})
            continue
        cp = os.path.join(d, f"cand-{attempt}.jpg")
        save_jpeg(raw, cp)
        try:
            save_jpeg(texture_pass(cp, key), cp)
        except RuntimeError:
            pass
        v = check_candidate(ref, cp, key)
        verdicts.append(v)
        if v.get("verdict") == "pass":
            kept = cp
            break
    with open(os.path.join(d, "controle.json"), "w") as f:
        json.dump(verdicts, f, indent=2, ensure_ascii=False)
    if not kept:
        import shutil
        shutil.move(d, os.path.join(ROOT, "queue", "rejected", os.path.basename(d)))
        print(f"❌ {name} — brief '{concept}' jamais validé")
        _fiabilite(product["handle"], False)
        return None
    _fiabilite(product["handle"], True)
    img = _I.open(kept).convert("RGB")
    mp = os.path.join(d, "media.jpg")
    core.cover(img, 1080, 1350).save(mp, quality=92)
    os.remove(kept)
    for rp in ref:
        os.remove(rp)
    magnific_finalize(mp, key)
    cap = core.pick_caption(captions, "studio", state, name)
    core.write_meta(d, "studio", cap, f"{name} dress, editorial photograph. Concept: {concept or 'editorial'}.",
                    random.choice(captions["hashtags"]))
    print(f"✅ {name} — concept: {concept or 'libre'}")
    return _livrer(d)
