#!/usr/bin/env python3
"""Carrousel de secours (règle : 2/semaine garantis) — recette fondatrice « porté + posé »,
la plus fiable ; si elle échoue, tentative « lookbook aligné »."""
import sys, os
ENGINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE)
import generate as core
import generate_ai as gai

key = gai.api_key()
state = core.load_state()
products = [p for p in core.fetch_products() if p.get("images")]
trio = core.pick_products(products, state, 3)
r = gai.make_carousel_lineup(trio, core.load_captions(), state, key)
if not r:
    r = gai.make_carousel_porte_pose(trio[0], core.load_captions(), state, key)
core.save_state(state)
sys.exit(0 if r else 1)
