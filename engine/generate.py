#!/usr/bin/env python3
"""
SV Social Engine — Générateur hebdomadaire.
Fabrique un lot de contenus Instagram à partir des VRAIES photos produits
de la boutique (CDN Shopify public). Aucune IA générative : zéro risque
de fausse robe ou de visage inventé.

Sorties dans queue/pending/ (à valider dans le Cockpit) :
  YYYY-MM-DD_HHMM_<type>_<produit>/
      media.jpg | media.mp4 | slide-1.jpg..slide-N.jpg
      meta.json   (caption, alt, hashtags, type)
"""
import json
import os
import io
import re
import random
import subprocess
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE = os.path.join(ROOT, "queue", "pending")
ENGINE = os.path.dirname(os.path.abspath(__file__))

STORE = "https://shadowvelora.com"
ESPRESSO = (75, 62, 60)
CREAM = (255, 249, 232)
GOLD = (168, 151, 127)
OFFWHITE = (250, 249, 247)

FONT_DIR = os.path.join(ENGINE, "fonts")


def font(name, size):
    """Cormorant si dispo (fonts/ du repo), sinon Georgia (macOS), sinon défaut."""
    candidates = [
        os.path.join(FONT_DIR, name),
        f"/System/Library/Fonts/Supplemental/{name}",
    ]
    for c in candidates:
        if os.path.exists(c):
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


F_ITAL = lambda s: font("CormorantGaramond-Italic.ttf", s) if os.path.exists(
    os.path.join(FONT_DIR, "CormorantGaramond-Italic.ttf")) else font("Georgia Italic.ttf", s)
F_REG = lambda s: font("CormorantGaramond-Regular.ttf", s) if os.path.exists(
    os.path.join(FONT_DIR, "CormorantGaramond-Regular.ttf")) else font("Georgia.ttf", s)


def curl(url):
    """Shopify bloque urllib (fingerprint TLS) ; curl passe partout."""
    r = subprocess.run(["curl", "-sL", "--max-time", "60", "-A", "Mozilla/5.0", url],
                       capture_output=True, check=True)
    return r.stdout


def fetch_products():
    return json.loads(curl(f"{STORE}/products.json?limit=50"))["products"]


def first_name(title):
    t = title.replace("—", "-").replace("–", "-")
    return t.split("-")[0].strip().title()


def fetch_image(src, width=1400):
    src = re.sub(r"(\.\w+)(\?|$)", rf"_{width}x\1\2", src, count=1)
    return Image.open(io.BytesIO(curl(src))).convert("RGB")


def cover(img, tw, th, anchor_top=True):
    r = max(tw / img.width, th / img.height)
    img = img.resize((int(img.width * r) + 1, int(img.height * r) + 1), Image.LANCZOS)
    x = (img.width - tw) // 2
    y = 0 if anchor_top else (img.height - th) // 2
    return img.crop((x, y, x + tw, y + th))


def load_captions():
    with open(os.path.join(ENGINE, "captions.json")) as f:
        return json.load(f)


def load_state():
    p = os.path.join(ENGINE, "state.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"used_captions": [], "used_products": []}


def save_state(state):
    with open(os.path.join(ENGINE, "state.json"), "w") as f:
        json.dump(state, f, indent=2)


def pick_caption(captions, kind, state, product_name=""):
    pool = [c for c in captions[kind] if c not in state["used_captions"]]
    if not pool:  # réservoir épuisé pour ce type → on recycle
        pool = captions[kind]
        state["used_captions"] = [c for c in state["used_captions"] if c not in captions[kind]]
    c = random.choice(pool)
    state["used_captions"].append(c)
    return c.replace("{name}", product_name)


def out_dir(kind, slug):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    d = os.path.join(QUEUE, f"{stamp}_{kind}_{slug}")
    os.makedirs(d, exist_ok=True)
    return d


def write_meta(d, kind, caption, alt, tags):
    with open(os.path.join(d, "meta.json"), "w") as f:
        json.dump({"type": kind, "caption": f"{caption}\n\n{tags}", "alt": alt}, f,
                  indent=2, ensure_ascii=False)


# ---------- FORMATS ----------

def make_studio(product, captions, state):
    """Post photo 4:5 — photo produit recadrée."""
    name = first_name(product["title"])
    img = cover(fetch_image(product["images"][0]["src"]), 1080, 1350)
    d = out_dir("studio", product["handle"])
    img.save(os.path.join(d, "media.jpg"), quality=92)
    cap = pick_caption(captions, "studio", state, name)
    write_meta(d, "studio", cap,
               f"{name} dress on model, studio photograph.",
               random.choice(captions["hashtags"]))
    return d


def make_carousel(product, captions, state, n=3):
    """Carrousel 4:5 — n angles/coloris de la même robe."""
    name = first_name(product["title"])
    d = out_dir("carousel", product["handle"])
    for i, im in enumerate(product["images"][:n]):
        cover(fetch_image(im["src"]), 1080, 1350).save(
            os.path.join(d, f"slide-{i + 1}.jpg"), quality=92)
    cap = pick_caption(captions, "carousel", state, name)
    write_meta(d, "carousel", cap,
               f"Carousel of the {name} dress, multiple views.",
               random.choice(captions["hashtags"]))
    return d


def make_band(captions, state):
    """Bande éditoriale espresso 4:5."""
    W, H = 1080, 1350
    quote = pick_caption(captions, "bands", state)
    im = Image.new("RGB", (W, H), ESPRESSO)
    dr = ImageDraw.Draw(im)
    dr.rectangle([W // 2 - 40, 330, W // 2 + 40, 332], fill=GOLD)
    f = F_ITAL(62)
    # découpage naïf en lignes ~22 caractères
    words, lines, cur = quote.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > 22:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    y = H // 2 - len(lines) * 44
    for ln in lines:
        wpx = dr.textbbox((0, 0), ln, font=f)[2]
        dr.text(((W - wpx) // 2, y), ln, font=f, fill=CREAM)
        y += 88
    sig = "SHADOW VELORA"
    fs = F_REG(28)
    wpx = dr.textbbox((0, 0), sig, font=fs)[2]
    dr.text(((W - wpx) // 2, y + 55), sig, font=fs, fill=GOLD)
    d = out_dir("band", "editorial")
    im.save(os.path.join(d, "media.jpg"), quality=95)
    write_meta(d, "band", "~",
               f"Editorial quote on espresso background: {quote}",
               random.choice(captions["hashtags"]))
    return d


def make_reel(product, captions, state, ffmpeg="ffmpeg"):
    """Reel Ken Burns 1080x1920 : zoom lent sur 2 photos de la robe, fondu."""
    name = first_name(product["title"])
    d = out_dir("reel", product["handle"])
    srcs = product["images"][:2] if len(product["images"]) >= 2 else product["images"][:1]
    frames = []
    for i, im in enumerate(srcs):
        p = os.path.join(d, f"src-{i}.jpg")
        cover(fetch_image(im["src"], 1600), 1080, 1920).save(p, quality=95)
        frames.append(p)
    seg = 4  # secondes par image
    fps = 30
    filters, inputs = [], []
    for i, p in enumerate(frames):
        inputs += ["-i", p]
        # image unique → zoompan fabrique seg*fps frames (zoom très lent 1.0 → 1.08)
        filters.append(
            f"[{i}:v]scale=2160:3840,zoompan=z='1+0.08*on/{seg * fps}':"
            f"x='iw/2-(iw/zoom/2)':y='ih*0.10-(ih*0.10)*on/{seg * fps}':"
            f"d={seg * fps}:s=1080x1920:fps={fps},settb=AVTB[v{i}]")
    if len(frames) == 2:
        chain = f"{filters[0]};{filters[1]};[v0][v1]xfade=transition=fade:duration=0.8:offset={seg - 0.8},format=yuv420p[v]"
    else:
        chain = f"{filters[0]};[v0]format=yuv420p[v]"
    cmd = [ffmpeg, "-y", *inputs, "-filter_complex", chain, "-map", "[v]",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-movflags", "+faststart",
           os.path.join(d, "media.mp4")]
    subprocess.run(cmd, check=True, capture_output=True)
    for p in frames:
        os.remove(p)
    cap = pick_caption(captions, "reel", state, name)
    write_meta(d, "reel", cap,
               f"Slow cinematic pan over the {name} dress.",
               random.choice(captions["hashtags"]))
    return d


def _blacklist():
    bp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blacklist.json")
    if os.path.exists(bp):
        return set(json.load(open(bp)).get("exclues_generation", []))
    return set()


def pick_products(products, state, n):
    products = [p for p in products if p.get("handle") not in _blacklist()]
    """Tourne sur le catalogue sans répéter avant d'avoir tout couvert."""
    eligible = [p for p in products if p.get("images")]
    fresh = [p for p in eligible if p["handle"] not in state["used_products"]]
    if len(fresh) < n:
        state["used_products"] = []
        fresh = eligible
    random.shuffle(fresh)
    chosen = fresh[:n]
    state["used_products"] += [p["handle"] for p in chosen]
    return chosen


def main():
    ffmpeg = os.environ.get("FFMPEG", "ffmpeg")
    captions = load_captions()
    state = load_state()
    products = fetch_products()

    # Lot hebdo : 2 studio + 1 carrousel + 1 bande + 2 reels = 6 contenus
    chosen = pick_products(products, state, 5)
    made = []
    made.append(make_studio(chosen[0], captions, state))
    made.append(make_studio(chosen[1], captions, state))
    multi = next((p for p in chosen[2:] if len(p["images"]) >= 3), chosen[2])
    made.append(make_carousel(multi, captions, state))
    made.append(make_band(captions, state))
    # Reels retirés du lot auto (verdict Laurie 26/07 : diaporama = pas luxe discret).
    # Les reels se font uniquement depuis de vraies vidéos (dossier rushes/).
    save_state(state)
    print(f"{len(made)} contenus générés :")
    for m in made:
        print(" -", os.path.basename(m))


if __name__ == "__main__":
    main()
