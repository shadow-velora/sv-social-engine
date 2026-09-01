#!/usr/bin/env python3
"""
KIT STORY DU DIMANCHE — la machine prépare, Laurie poste.
Choisit un post publié pas encore passé en story, le recadre en 1080×1920,
et rédige les consignes en anglais (texte à écrire + sondage à créer).
Le kit apparaît dans l'onglet 📖 STORY du Cockpit.
"""
import base64
import json
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "engine")
sys.path.insert(0, ENGINE)
import cerveau
PUBLISHED = os.path.join(ROOT, "queue", "published")
KITS = os.path.join(ROOT, "queue", "stories")
STATE = os.path.join(ENGINE, "story-state.json")


def sh(*cmd):
    subprocess.run(cmd, cwd=ROOT, check=False, capture_output=True)


def consignes(media_path, alt, key):
    """Demande au stratège IA le texte + le sondage de la story (en anglais)."""
    img = base64.b64encode(open(media_path, "rb").read()).decode()
    prompt = (cerveau.contexte(role="cm", pour_texte=True) + "\n\n"
              "You are the social media manager of Shadow Velora, writing this week's Instagram STORY "
              "SEQUENCE. Context of the image: " + (alt or "brand visual") + ". "
              "A story sequence is three frames that build: (1) a tight detail that creates curiosity, "
              "(2) the full look revealed, (3) the same look with a link sticker to shop. "
              "Text on a story is 3 to 6 words maximum — a hook, never a product description. "
              "Frame 3 must give a short call to action for the link sticker. "
              'Answer ONLY in JSON: {"frame1_text": "...", "frame2_text": "...", '
              '"frame3_text": "...", "link_label": "short CTA for the link sticker, max 4 words", '
              '"conseil": "one short sentence in French: where to place the texts and the link sticker"}')
    body = json.dumps({"contents": [{"parts": [
        {"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": img}}]}]}).encode()
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key=" + key,
        data=body, headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=90).read())
    txt = r["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(txt[txt.find("{"):txt.rfind("}") + 1])


def main():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        envp = os.path.join(ROOT, ".env")
        if os.path.exists(envp):
            for line in open(envp):
                if line.startswith("GEMINI_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("GEMINI_API_KEY manquante")

    os.makedirs(KITS, exist_ok=True)
    state = json.load(open(STATE)) if os.path.exists(STATE) else {"faites": []}
    biblio = os.path.join(ROOT, "queue", "bibliotheque")
    # une story "look" = une ROBE portée : jamais les découpes de réels (sacs, détails, boîtes)
    inedites = sorted((f for f in os.listdir(biblio)
                       if f.lower().endswith((".jpg", ".png"))
                       and "reel" not in f.lower() and "detail" not in f.lower()
                       and "boite" not in f.lower() and "bag" not in f.lower()), reverse=True) if os.path.isdir(biblio) else []
    inedites = [f for f in inedites if f not in state["faites"]]
    posts = sorted((d for d in os.listdir(PUBLISHED)
                    if os.path.isdir(os.path.join(PUBLISHED, d))
                    and os.path.exists(os.path.join(PUBLISHED, d, "media.jpg"))), reverse=True)
    # 1) une image inédite de la bibliothèque (déjà payée, jamais vue) 2) sinon un post publié
    cible, src_path = None, None
    if inedites:
        cible = inedites[0]
        src_path = os.path.join(biblio, cible)
    else:
        cible = next((p for p in posts if p not in state["faites"]), None)
        if not cible and posts:
            state["faites"] = []
            cible = posts[0]
        if cible:
            src_path = os.path.join(PUBLISHED, cible, "media.jpg")
    if not cible:
        print("aucun post publié — pas de kit story")
        sys.exit(0)

    from PIL import Image
    img = Image.open(src_path).convert("RGB")
    tw, th = 1080, 1920
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    d = os.path.join(KITS, f"{stamp}_story")
    os.makedirs(d, exist_ok=True)

    def cadre(im, zoom=1.0, focus_y=0.33):
        """Recadre en 9:16. zoom>1 = plan resserré (le détail)."""
        e = max(tw / im.width, th / im.height) * zoom
        r = im.resize((max(1, round(im.width * e)), max(1, round(im.height * e))), Image.LANCZOS)
        x = (r.width - tw) // 2
        y = max(0, min(r.height - th, int(r.height * focus_y - th / 2)))
        return r.crop((x, y, x + tw, y + th))

    cadre(img, zoom=2.1, focus_y=0.42).save(os.path.join(d, "frame-1.jpg"), quality=93)  # détail
    plein = cadre(img, zoom=1.0, focus_y=0.33)
    plein.save(os.path.join(d, "frame-2.jpg"), quality=93)                               # plan complet
    plein.save(os.path.join(d, "frame-3.jpg"), quality=93)                               # + lien

    alt = ""
    mp = os.path.join(PUBLISHED, cible, "meta.json")
    if os.path.exists(mp):
        alt = json.load(open(mp)).get("alt", "")
    elif src_path and "bibliotheque" in src_path:
        alt = "unpublished Shadow Velora image, never seen by the audience"
    try:
        c = consignes(os.path.join(d, "frame-2.jpg"), alt, key)
    except Exception as e:
        c = {"frame1_text": "Look closer.", "frame2_text": "The one for tonight.",
             "frame3_text": "Yours this week.", "link_label": "Shop the dress",
             "conseil": f"(consignes IA indisponibles : {e})"}
    json.dump({"type": "story", "source": cible, "frames": 3, **c},
              open(os.path.join(d, "kit.json"), "w"), indent=2, ensure_ascii=False)

    state["faites"].append(cible)
    json.dump(state, open(STATE, "w"), indent=2)
    sh("git", "config", "user.name", "sv-engine")
    sh("git", "config", "user.email", "engine@shadowvelora.com")
    sh("git", "add", "queue/stories", "engine/story-state.json")
    sh("git", "commit", "-m", f"kit story {stamp}")
    sh("git", "push")
    print("kit story prêt :", d)


if __name__ == "__main__":
    main()
