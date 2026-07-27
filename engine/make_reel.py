#!/usr/bin/env python3
"""
KIT RÉEL HEBDO — génère 6-8 images NEUVES (jamais postées), les monte en slideshow
ultra-rapide (0,25 s/plan, multi-crops), SANS musique : Laurie télécharge la vidéo
et la poste elle-même dans l'appli avec un son tendance Instagram.
Le kit (vidéo + légende + hashtags) apparaît dans l'onglet 📖 À POSTER du Cockpit.
"""
import glob
import json
import os
import random
import sys
from datetime import datetime, timezone

ENGINE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)
import generate as core
import generate_ai as gai
import reel_slideshow

PHRASES = [
    "for the moments that matter.",
    "quietly unforgettable.",
    "designed in London.",
    "the art of the evening.",
    "some dresses speak softly.",
]


def main():
    key = gai.api_key()
    state = core.load_state()
    cfg = json.load(open(os.path.join(ENGINE, "scenes.json")))
    products = [p for p in core.fetch_products() if p.get("images")]
    duo = core.pick_products(products, state, 2)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    d = os.path.join(ROOT, "queue", "stories", f"{stamp}_reel_{'-'.join(core.first_name(p['title']).lower() for p in duo)}")
    os.makedirs(d, exist_ok=True)
    framings = [
        "Full-length composition, the entire dress visible, generous headroom.",
        "Composition from behind, the back of the dress as the hero, her head turned in soft profile.",
        "Three-quarter crop from mid-thigh up, editorial campaign composition.",
        "Full-length from a slight distance, the setting visible around her.",
    ]
    keepers = []
    for p in duo:
        ref = os.path.join(d, f"ref-{p['handle']}.jpg")
        core.fetch_image(p["images"][0]["src"], 1200).save(ref, quality=92)
        for fr in framings:
            scene = gai.pick_scene(cfg["scenes"], state.get("last_scene"))
            state["last_scene"] = scene["id"]
            pose = random.choice(cfg["poses"])
            try:
                raw = gai.generate_candidate(ref, scene["text"], pose, cfg["rules"], key,
                                             gai.sample_imperfections(cfg), framing=fr)
            except RuntimeError:
                continue
            cp = os.path.join(d, f"img-{len(keepers)+1:02d}.jpg")
            gai.save_jpeg(raw, cp)
            try:
                gai.save_jpeg(gai.texture_pass(cp, key), cp)
            except RuntimeError:
                pass
            v = gai.check_candidate(ref, cp, key)
            if v.get("verdict") == "pass":
                gai.clean_noise(cp)
                keepers.append(cp)
            else:
                os.remove(cp)
        os.remove(ref)
    if len(keepers) < 5:
        import shutil
        shutil.move(d, os.path.join(ROOT, "queue", "rejected", os.path.basename(d)))
        print(f"❌ réel : seulement {len(keepers)} images validées — abandonné")
        return
    phrase = random.choice(PHRASES)
    reel_slideshow.build(os.path.join(d, "media.mp4"), phrase, keepers)
    names = " & ".join(core.first_name(p["title"]) for p in duo)
    json.dump({
        "type": "reel",
        "legende": f"Blink and you miss it. {names}, designed in London. ~",
        "hashtags": "#shadowvelora #quietluxury #eveningdress #fashionreels #dressinspo #oldmoneystyle",
        "consigne_musique": "Poste avec un son TENDANCE Instagram (onglet sons > tendances, style mode/élégant, rythme rapide).",
        "images": len(keepers),
    }, open(os.path.join(d, "kit.json"), "w"), indent=2, ensure_ascii=False)
    state["reel_count"] = state.get("reel_count", 0) + 1
    core.save_state(state)
    print(f"✅ kit réel prêt : {names} — {len(keepers)} images neuves")


if __name__ == "__main__":
    main()
