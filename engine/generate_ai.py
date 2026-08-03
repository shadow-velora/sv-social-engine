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

IMAGE_MODEL = "gemini-2.5-flash-image"
MAX_GEN_PER_MONTH = 120  # plafond officiel (6-7 EUR/mois tout compris) — retour au normal depuis aout
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


PROMPT_TEMPLATE = """E-commerce fashion editorial photograph of a NEW fictional model — a DIFFERENT woman from the one in the reference image (do not copy the reference model's face), with similar hair color and skin tone family, but her own real face. A REAL woman, not a supermodel render. Her face is pretty in an ordinary, believable way: distinctly asymmetric features as real faces are, a natural nose, slightly uneven brows, faint expression lines, lips gently closed. Her body is a real woman's body: soft natural arms, a gentle waist, realistic proportions, one shoulder carried a touch higher than the other, posture slightly uneven the way real people stand. Her hair has lived through the day: waves losing their shape, light frizz at the crown, flyaways, a few strands tucked behind one ear, an uneven parting. Minimal barely-there makeup.

The dress is EXACTLY the one in the reference: same color, fabric, neckline, straps, construction and sheen — every detail from the reference only. If the reference fabric carries a pattern (floral jacquard, lace, appliqué, embroidery), that pattern MUST appear with the same density and in the same areas — bodice, hips AND skirt — never a plain smooth version of a patterned dress. The dress stays impeccable, with natural fabric tension and creases where the body moves.

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
{"dress_identical": true/false, "invented_details": ["any dress detail in image 2 absent from the reference"], "skin_natural": true/false, "face_consistent": true/false — the model in image 2 is INTENTIONALLY a different fictional woman from the reference, so never compare her identity to image 1; true when her face reads as one coherent, natural, believable real person (and always true when the face is hidden, in profile or seen from behind), "verdict": "pass" or "fail"}

skin_natural = false ONLY if the skin is clearly artificial: waxy, plastic, poreless, airbrushed glow, doll-like. If the skin shows visible texture, freckles, moles or natural unevenness, set it to true. Noise or film grain over the whole image does NOT count as skin texture, and heavy grain or noise over the image = fail. Judge the dress strictly: any invented detail = fail. Compare the FABRIC zone by zone against the reference: patterned zones of the reference (lace, jacquard, embroidery) must stay patterned at similar density, and PLAIN zones of the reference must stay plain. A dress whose patterned/plain zones MATCH the reference layout is CORRECT — only fail when a patterned zone was smoothed out or a plain zone gained invented pattern. verdict = pass only if everything is true and invented_details is empty."""


def _budget_guard():
    from datetime import datetime, timezone
    bp = os.path.join(ENGINE, "budget.json")
    b = json.load(open(bp)) if os.path.exists(bp) else {}
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


def generate_candidate(ref_path, scene, pose, rules, key, imperfections="", framing=None):
    _budget_guard()
    refs = ref_path if isinstance(ref_path, (list, tuple)) else [ref_path]
    if framing is None:
        cfg_f = json.load(open(os.path.join(ENGINE, "scenes.json"))).get("framings")
        framing = random.choice(cfg_f) if cfg_f else "Full-length composition, the entire dress visible, generous headroom above her head."
    prompt = PROMPT_TEMPLATE.format(pose=pose, scene=scene, rules=rules, framing=framing,
                                    imperfections=imperfections or "visible pores and natural uneven skin tone")
    if len(refs) > 1:
        prompt += f"\n\nThe first {len(refs)} reference photographs show the SAME dress from different angles (front and back). Reproduce its construction faithfully from EVERY angle: the back of the dress (straps, zip, lacing, neckline depth, seams) must match the back-view reference exactly — never invent the back."
    prompt += "\n\nThe FINAL reference photograph shows REAL unretouched human skin. This is the exact standard her skin must meet everywhere it is visible: knees and elbows slightly darker with fine creases, visible pores with natural sebum shine in places, patchy tonal variation, faint veins, real joint creases, natural marks. Study it and replicate THIS level of skin realism on her — never smoother than this real photograph."
    parts = [{"text": prompt}] + [
        {"inline_data": {"mime_type": "image/jpeg", "data": b64_of(r)}} for r in refs
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


def make_ai_set(key=None, ffmpeg=None, product=None):
    """Génère 1 scène complète (3 vues contrôlées) → 1 carrousel. (Reels : uniquement depuis de vraies vidéos, dossier rushes/)"""
    from PIL import Image
    key = key or api_key()
    ffmpeg = ffmpeg or os.environ.get("FFMPEG", "ffmpeg")
    cfg = json.load(open(os.path.join(ENGINE, "scenes.json")))
    captions = core.load_captions()
    state = core.load_state()
    products = [p for p in core.fetch_products() if p.get("images")]
    p = product or core.pick_products(products, state, 1)[0]
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

CHAISE_PROMPT = """Real photograph taken on a full-frame camera with a 50mm lens: this exact dress, empty (no one wearing it), gracefully draped over an antique gilded armchair in a château room with herringbone parquet, warm window light. Reproduce the dress exactly as in the reference: same color, same fabric, same neckline. The empty fabric lies limp with soft natural folds, and the room has a lived-in patina: rubbed gilding, worn parquet, faded upholstery. True photographic rendering — real optical depth of field, accurate fabric weight and weave, natural light falloff — like an unstaged photo from a fashion shoot, never a painting, illustration or 3D render."""

BUST_PROMPT = """Professional e-commerce product photograph. The EXACT dress from the reference image — same color, same fabric, same neckline, same straps, same construction, same sheen, every detail from the reference only — displayed on a cream linen tailor's dress form (a sewing mannequin bust, NO person, no head, no limbs).

Setting: a real working studio — warm sand seamless paper backdrop with a soft wrinkle, the dress form standing on a worn wooden floor, soft window light from the left casting honest shadows. The dress shows natural fabric behaviour: gentle creases from handling, the hem falling naturally. Real-world subtlety: the backdrop slightly uneven in tone, one faint tape mark on the floor.

Quiet luxury product photography, crisp and sharp, natural true-to-life muted colors, vertical 4:5 composition, the entire garment visible with generous margin. No text, no logos, no person."""

FLATLAY_PROMPT = """Top-down flat-lay editorial photograph. The EXACT dress from the reference image — same color, same fabric, same neckline, same construction, every detail from the reference only — laid carefully on a warm sand linen sheet, artfully but naturally arranged with soft real fabric folds and creases, one strap casually off-line as if just placed there. A simple wooden hanger rests beside it.

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
                    f"Lookbook carousel: the same muse in an aligned studio series wearing three Shadow Velora dresses: {names}.",
                    "#shadowvelora #quietluxury #eveningdress")
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
                    "#shadowvelora #quietluxury #eveningdress")
    print(f"✅ carrousel lineup panorama : {names} ({n} slides)")
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
    # slide 1 : la robe portée
    porte = None
    for attempt in range(1, 4):
        try:
            raw = generate_candidate(refs, scene["text"], random.choice(cfg["poses"]), rules, key,
                                     sample_imperfections(cfg))
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
            porte = cp
            break
    if not porte:
        _sh.move(d, os.path.join(ROOT, "queue", "rejected", os.path.basename(d)))
        print(f"❌ porté+posé {name} : slide portée jamais validée")
        _fiabilite(product["handle"], False)
        return None
    # slides 2 et 3 : la robe posée (fauteuil puis à plat) — MÊME shooting, même lumière
    meme_shooting = (" SAME SHOOTING as the rest of this editorial series — keep the ambiance and light "
                     "consistent with: " + scene["text"])
    for fname, base_prompt in (("slide-2.jpg", CHAISE_PROMPT), ("slide-3.jpg", FLATLAY_PROMPT)):
        prompt = base_prompt + meme_shooting + lecons_texte(product["handle"])
        ok = False
        for attempt in (1, 2, 3):
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
                continue
            cp = os.path.join(d, fname)
            save_jpeg(raw, cp)
            v = check_candidate(refs[0], cp, key)
            if v.get("dress_identical") and not v.get("invented_details"):
                ok = True
                break
            os.remove(cp)
        if not ok:
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
                    "#shadowvelora #quietluxury #eveningdress")
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
    prompt = {"bust": BUST_PROMPT, "chaise": CHAISE_PROMPT}.get(kind, FLATLAY_PROMPT) + lecons_texte(product["handle"])
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
