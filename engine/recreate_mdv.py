#!/usr/bin/env python3
"""
Recréation des références DA (dossier mdv-refs) avec les robes Shadow Velora.
Chaque brief = décor + lumière + styling + pose + cadrage copiés d'une vraie
image de campagne validée par Laurie. Usage : python3 engine/recreate_mdv.py [n1 n2 ...]
"""
import os
import sys

ENGINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE)
import generate as core
import generate_ai as gai

BRIEFS = [
    {
        "num": 1,
        "handle": "mira-sculpted-mini-muse-dress",
        "concept": "street-luxury architecture classique",
        "scene": ("outside a grand classical European building facade in warm cream stone, with marble statues "
                  "standing in carved niches behind a tall wrought-iron railing with spear-shaped finials, old "
                  "stone paving underfoot. Soft overcast daylight, muted and even. She wears slim rectangular "
                  "black sunglasses, a sleek low bun with a few loose strands, small dark leather handbag held "
                  "low in one hand, heeled mule sandals"),
        "pose": ("standing in full profile, face turned away from the camera toward the building so only the "
                 "line of her cheek shows, weight on one straight leg, calm and unhurried, as if caught "
                 "mid-thought while sightseeing"),
        "framing": "Full-length vertical composition from a slight distance, the entire dress and shoes visible, the architecture filling the background.",
    },
    {
        "num": 2,
        "handle": "alia-draped-strapless-maxi",
        "concept": "euro-summer monument de pierre",
        "scene": ("in front of a weathered carved stone monument with ornate baroque reliefs, aged grey stone "
                  "with moss in the crevices, an old cobblestone ground of small rounded pebbles. Soft diffused "
                  "afternoon light. She wears narrow black cat-eye sunglasses, classic red lipstick, chunky gold "
                  "earrings and a gold bracelet, hair pulled back in a tight low twist, a structured mini black "
                  "handbag with a gold clasp in one hand"),
        "pose": ("standing straight facing the camera but with her chin dipped down, eyes hidden behind the "
                 "sunglasses looking toward the ground beside her, one arm relaxed along her body, poised and "
                 "self-contained"),
        "framing": "Full-length vertical composition, the hem of the gown pooling slightly on the cobblestones, generous stone texture visible around her.",
    },
    {
        "num": 3,
        "handle": "amara-liquid-satin-gown",
        "concept": "studio éditorial fond taupe",
        "scene": ("a professional studio with a warm taupe-beige seamless paper backdrop, softly graded light "
                  "falling from the upper left, gentle shadow gradient on the backdrop behind her. She wears "
                  "bold sculptural gold flower earrings and short dark hair styled in glossy vintage waves"),
        "pose": ("three-quarter stance, shoulders angled, chin slightly lifted, gazing past the camera with a "
                 "composed editorial expression, one arm relaxed, the other hand lightly touching her hip"),
        "framing": "Three-quarter crop from mid-thigh up, editorial fashion-campaign composition with space above her head.",
    },
    {
        "num": 4,
        "handle": "bianca-draped-lace-corset-gown",
        "concept": "sculptural dos studio blanc",
        "scene": ("a minimal studio with an off-white seamless backdrop, soft near-shadowless daylight-balanced "
                  "light wrapping around her. She wears a single chunky gold dome ring, very short cropped dark "
                  "hair, no other accessories, skin and fabric as the only textures in the frame"),
        "pose": ("standing with her back to the camera, the back of the dress as the hero of the image, "
                 "her head turned gently to one side in a soft profile, one hand resting on her hip with "
                 "fingers spread, elegant and sculptural"),
        "framing": "Composition from head to mid-thigh, her back centered in the frame — sculptural editorial detail shot.",
    },
    {
        "num": 5,
        "handle": "selena-the-parisian-flow-dress",
        "concept": "escalier de musée lifestyle",
        "scene": ("inside a grand old museum staircase in pale carved stone, sweeping balustrade with turned "
                  "stone balusters, high arched ceilings and a dim painted mural far above, warm ambient "
                  "indoor light with soft shadows. She wears small black kitten heels and a cream hair claw "
                  "holding a loose low chignon"),
        "pose": ("photographed from behind and below as she climbs the stone steps, one hand trailing on the "
                 "marble handrail, mid-step, completely unaware of the camera, candid documentary feeling"),
        "framing": "Full-length vertical composition from a low angle at the foot of the stairs, the staircase architecture dominating the frame around her.",
    },
]


def main(nums):
    key = gai.api_key()
    captions = core.load_captions()
    state = core.load_state()
    products = {p["handle"]: p for p in core.fetch_products() if p.get("images")}
    todo = [b for b in BRIEFS if not nums or b["num"] in nums]
    for b in todo:
        prod = products.get(b["handle"])
        if not prod:
            print(f"⚠️ robe introuvable : {b['handle']}")
            continue
        print(f"— brief {b['num']} : {b['concept']} ({b['handle']})")
        gai.make_model_post(prod, captions, state, key,
                            scene_text=b["scene"], concept=b["concept"],
                            pose_text=b["pose"], framing_text=b["framing"])
        core.save_state(state)


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]])
