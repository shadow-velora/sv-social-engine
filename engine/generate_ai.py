#!/usr/bin/env python3
"""
SV Social Engine — Génération de NOUVELLES images (Nano Banana / Gemini API).
Part des vraies photos produits (référence), applique les recettes verrouillées
DA Shadow (anti-Barbie, robe à l'identique), fait tourner scènes et poses,
puis passe chaque image au CONTRÔLEUR anti-fake (2e IA) avant de la déposer
dans queue/pending/ (Cockpit).

Usage : python3 engine/generate_ai.py [nb_posts]
"""
import base64
import json
import os
import random
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.dirname(os.path.abspath(__file__))
PENDING = os.path.join(ROOT, "queue", "pending")

sys.path.insert(0, ENGINE)
import generate as core  # curl, fetch_products, fetch_image, cover, captions, state

IMAGE_MODEL = "gemini-2.5-flash-image"
MAX_GEN_PER_MONTH = 45  # plafond dur : ~2 EUR/mois d'images, budget 10 GBP = 2 mois garanti
CHECK_MODEL = "gemini-flash-latest"
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


def gemini(model, parts, key):
    """Appel Gemini via curl (urllib bloqué par certains proxies)."""
    payload = json.dumps({"contents": [{"parts": parts}]})
    r = subprocess.run(
        ["curl", "-s", "--max-time", "180",
         "-H", "Content-Type: application/json",
         "-X", "POST", f"{API}/{model}:generateContent?key={key}",
         "-d", "@-"],
        input=payload.encode(), capture_output=True, check=True)
    return json.loads(r.stdout)


def save_jpeg(raw_bytes, path):
    """Gemini renvoie parfois du PNG : on normalise en vrai JPEG."""
    import io as _io
    from PIL import Image as _Img
    _Img.open(_io.BytesIO(raw_bytes)).convert("RGB").save(path, "JPEG", quality=93)


def b64_of(img_path):
    return base64.b64encode(open(img_path, "rb").read()).decode()


PROMPT_TEMPLATE = """E-commerce fashion editorial photograph of a fictional model of the same type as the reference image — same hair color and length, same skin tone — but a REAL woman, not a supermodel render. Her face is pretty in an ordinary, believable way: distinctly asymmetric features as real faces are, a natural nose, slightly uneven brows, faint expression lines, lips gently closed. Her body is a real woman's body: soft natural arms, a gentle waist, realistic proportions, one shoulder carried a touch higher than the other, posture slightly uneven the way real people stand. Her hair has lived through the day: waves losing their shape, light frizz at the crown, flyaways, a few strands tucked behind one ear, an uneven parting. Minimal barely-there makeup.

The dress is EXACTLY the one in the reference: same color, fabric, neckline, straps, construction and sheen — every detail from the reference only. The dress stays impeccable, with natural fabric tension and creases where the body moves.

Her skin reads as real, unretouched skin: soft directional window light skims across it at a low angle, revealing pores with varied density (coarser on the nose, finer on the temples). Natural sheen only on the T-zone, matte cheeks, highlights broken by skin micro-relief. Fine vellus hair catches the light on her forearms; real knuckle creases; slight natural redness at nose, elbows and knuckles; subtle tonal transitions between face, neck and chest. Baby hairs soften the hairline. Today specifically: {imperfections}.

An honest outtake caught mid-movement — her body is loose and alive, never stiff, never posed like a statue; weight shifting, a gesture in progress. Her posture stays ELEGANT and open at all times: back long, chin level or slightly lifted, shoulders open — never hunched, never bent forward, never head hanging down. Pose: {pose}

Setting: {scene}. The place is elegant but genuinely inhabited: subtle real-world wear in the decor only, never on the dress.

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
{"dress_identical": true/false, "invented_details": ["any dress detail in image 2 absent from the reference"], "skin_natural": true/false, "face_consistent": true/false (true if the face is not visible in image 2), "verdict": "pass" or "fail"}

skin_natural = false ONLY if the skin is clearly artificial: waxy, plastic, poreless, airbrushed glow, doll-like. If the skin shows visible texture, freckles, moles or natural unevenness, set it to true. Noise or film grain over the whole image does NOT count as skin texture, and heavy grain or noise over the image = fail. Judge the dress strictly: any invented detail = fail. verdict = pass only if everything is true and invented_details is empty."""


def _budget_guard():
    from datetime import datetime, timezone
    state = core.load_state()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    if state.get("gen_month") != month:
        state["gen_month"] = month
        state["gen_calls"] = 0
    if state.get("gen_calls", 0) >= MAX_GEN_PER_MONTH:
        raise SystemExit(f"PLAFOND BUDGET ATTEINT ({MAX_GEN_PER_MONTH} generations ce mois) — rien ne sera facture de plus.")
    state["gen_calls"] = state.get("gen_calls", 0) + 1
    core.save_state(state)


def _skin_ref():
    """Une vraie photo de peau humaine (dossier de Laurie) comme étalon de réalisme."""
    import glob, mimetypes
    refs = sorted(glob.glob(os.path.join(ENGINE, "skin-refs", "*")))
    if not refs:
        return None
    p = random.choice(refs)
    mime = mimetypes.guess_type(p)[0] or "image/webp"
    return {"inline_data": {"mime_type": mime, "data": b64_of(p)}}


def generate_candidate(ref_path, scene, pose, rules, key, imperfections="", framing=None):
    _budget_guard()
    if framing is None:
        cfg_f = json.load(open(os.path.join(ENGINE, "scenes.json"))).get("framings")
        framing = random.choice(cfg_f) if cfg_f else "Full-length composition, the entire dress visible, generous headroom above her head."
    prompt = PROMPT_TEMPLATE.format(pose=pose, scene=scene, rules=rules, framing=framing,
                                    imperfections=imperfections or "visible pores and natural uneven skin tone")
    prompt += "\n\nThe SECOND reference photograph shows REAL unretouched human skin. This is the exact standard her skin must meet everywhere it is visible: knees and elbows slightly darker with fine creases, visible pores with natural sebum shine in places, patchy tonal variation, faint veins, real joint creases, natural marks. Study it and replicate THIS level of skin realism on her — never smoother than this real photograph."
    parts = [
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(ref_path)}},
    ]
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
    parts = [
        {"text": CHECKER_PROMPT},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(ref_path)}},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(gen_path)}},
    ]
    resp = gemini(CHECK_MODEL, parts, key)
    try:
        txt = resp["candidates"][0]["content"]["parts"][0]["text"]
        txt = txt[txt.find("{"):txt.rfind("}") + 1]
        return json.loads(txt)
    except Exception as e:
        return {"verdict": "fail", "error": f"contrôleur illisible: {e}"}


HEALTH_CHECK = """Inspect this fashion photograph closely. Answer ONLY with JSON:
{"face_clean": true/false, "issues": ["list any bruise-like blue or purple discoloration on the face or body, black-eye shading, wound-like marks, sickly grey patches, anatomical glitches (extra fingers, warped hands), or an awkward distressed-looking posture (hunched over, head hanging, looks sick or in pain)"]}
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


def magnific_finalize(media_path, key):
    """Passe humanité Magnific + inspection anti-artefact. 2 essais max."""
    from PIL import Image as _I
    import magnific
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
            return True
        print("  artefact détecté:", v.get("issues"))
        os.remove(tmp)
    return False


def pick_scene(scenes, last_id=None):
    pool = [s for s in scenes for _ in range(s["weight"]) if s["id"] != last_id]
    return random.choice(pool)


def main(n_posts=2):
    key = api_key()
    cfg = json.load(open(os.path.join(ENGINE, "scenes.json")))
    captions = core.load_captions()
    state = core.load_state()
    products = [p for p in core.fetch_products() if p.get("images")]
    chosen = core.pick_products(products, state, n_posts)
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


def make_ai_set(key=None, ffmpeg=None):
    """Génère 1 scène complète (3 vues contrôlées) → 1 carrousel. (Reels : uniquement depuis de vraies vidéos, dossier rushes/)"""
    from PIL import Image
    key = key or api_key()
    ffmpeg = ffmpeg or os.environ.get("FFMPEG", "ffmpeg")
    cfg = json.load(open(os.path.join(ENGINE, "scenes.json")))
    captions = core.load_captions()
    state = core.load_state()
    products = [p for p in core.fetch_products() if p.get("images")]
    p = core.pick_products(products, state, 1)[0]
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

BUST_PROMPT = """Professional e-commerce product photograph. The EXACT dress from the reference image — same color, same fabric, same neckline, same straps, same construction, same sheen, every detail from the reference only — displayed on a cream linen tailor's dress form (a sewing mannequin bust, NO person, no head, no limbs).

Setting: a real working studio — warm sand seamless paper backdrop with a soft wrinkle, the dress form standing on a worn wooden floor, soft window light from the left casting honest shadows. The dress shows natural fabric behaviour: gentle creases from handling, the hem falling naturally. Real-world subtlety: the backdrop slightly uneven in tone, one faint tape mark on the floor.

Quiet luxury product photography, crisp and sharp, natural true-to-life muted colors, vertical 4:5 composition, the entire garment visible with generous margin. No text, no logos, no person."""

FLATLAY_PROMPT = """Top-down flat-lay editorial photograph. The EXACT dress from the reference image — same color, same fabric, same neckline, same construction, every detail from the reference only — laid carefully on a warm sand linen sheet, artfully but naturally arranged with soft real fabric folds and creases, one strap casually off-line as if just placed there. A simple wooden hanger rests beside it.

Soft daylight from one side casting honest shadows in the fabric folds. The linen underneath shows natural wrinkles. Quiet luxury flat-lay, crisp and sharp, natural muted colors, vertical 4:5 composition. No text, no logos, no person, no other products."""


def make_no_face(kind, product, captions, state, key):
    """kind: 'bust' ou 'flatlay' — formats produit sans humain."""
    from PIL import Image as _I
    name = core.first_name(product["title"])
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    d = os.path.join(PENDING, f"{stamp}_ai-{kind}_{product['handle']}")
    os.makedirs(d, exist_ok=True)
    ref = os.path.join(d, "reference.jpg")
    core.fetch_image(product["images"][0]["src"], 1200).save(ref, quality=92)
    prompt = BUST_PROMPT if kind == "bust" else FLATLAY_PROMPT
    kept, verdicts = None, []
    for attempt in (1, 2, 3):
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
        return None
    os.remove(ref)
    cap = core.pick_caption(captions, "studio", state, name)
    core.write_meta(d, "studio", cap,
                    f"The {name} dress, {('displayed on a dress form' if kind=='bust' else 'flat-lay editorial')}.",
                    random.choice(captions["hashtags"]))
    print(f"✅ {kind} {name}")
    return d
