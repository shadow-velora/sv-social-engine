#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_ai as g
import generate as core

key = g.api_key()
captions = core.load_captions()
state = core.load_state()
products = [p for p in core.fetch_products() if p.get("images")]
chosen = core.pick_products(products, state, 2)
g.main(1)
g.make_no_face("bust", chosen[0], captions, state, key)
core.save_state(state)
