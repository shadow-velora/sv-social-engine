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
    prompt = (cerveau.contexte(role="smm", pour_texte=True) + "\n\n"
              "You are writing an Instagram STORY for Shadow Velora. The founder posts it herself, "
              "by hand, with a poll sticker. Context of the image: " + (alt or "brand visual") + ". "
              "A story must EARN its place: it is not a caption, it is a reason to tap. "
              "Give her: a SHORT text overlay (max 7 words, English, an angle or a hook — never a "
              "product description), and ONE poll whose answer teaches the brand something useful "
              "(what to restock, which colour to launch next, which occasion the audience dresses "
              "for). Question max 6 words, exactly 2 short options. "
              'Answer ONLY in JSON: {"text_overlay": "...", "poll_question": "...", '
              '"poll_options": ["...", "..."], "placement_tip": "one short sentence in French '
              'telling her where to place text and poll on the image"}')
    body = json.dumps({"contents": [{"parts": [
        {"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": img}}]}]}).encode()
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key=" + key,
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
    inedites = sorted((f for f in os.listdir(biblio)
                       if f.lower().endswith((".jpg", ".png"))), reverse=True) if os.path.isdir(biblio) else []
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
    scale = max(tw / img.width, th / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    x, y = (img.width - tw) // 2, (img.height - th) // 3
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    d = os.path.join(KITS, f"{stamp}_story")
    os.makedirs(d, exist_ok=True)
    img.crop((x, y, x + tw, y + th)).save(os.path.join(d, "media.jpg"), quality=93)

    alt = ""
    mp = os.path.join(PUBLISHED, cible, "meta.json")
    if os.path.exists(mp):
        alt = json.load(open(mp)).get("alt", "")
    elif src_path and "bibliotheque" in src_path:
        alt = "unpublished Shadow Velora image, never seen by the audience"
    try:
        c = consignes(os.path.join(d, "media.jpg"), alt, key)
    except Exception as e:
        c = {"text_overlay": "New in — which one is you?",
             "poll_question": "Your pick?", "poll_options": ["This one", "Need it all"],
             "placement_tip": f"(consignes IA indisponibles : {e})"}
    json.dump({"source": cible, **c}, open(os.path.join(d, "kit.json"), "w"), indent=2, ensure_ascii=False)

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
