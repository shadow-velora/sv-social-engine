#!/usr/bin/env python3
"""
Générateur de réels slideshow (0 € — assemblage local ffmpeg).
Style validé sur les refs de Laurie : photos qui défilent en cuts rapides,
une petite phrase élégante constante, format 1080×1920.

Usage :
  python3 engine/reel_slideshow.py sortie.mp4 "phrase élégante." img1.jpg img2.jpg ...
"""
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = "/System/Library/Fonts/Supplemental/Didot.ttc"
DUREE_PLAN = 0.45     # cuts TRÈS rapides (règle Laurie : trop vite pour tout voir → on rerergarde → l'algo pousse)
TW, TH = 1080, 1920


def cadre(img_path, phrase):
    """Photo recadrée 9:16 + phrase discrète en Didot."""
    img = Image.open(img_path).convert("RGB")
    scale = max(TW / img.width, TH / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    x, y = (img.width - TW) // 2, (img.height - TH) // 3
    img = img.crop((x, y, x + TW, y + TH))
    if phrase:
        dr = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype(FONT, 42)
        except OSError:
            font = ImageFont.load_default()
        bb = dr.textbbox((0, 0), phrase, font=font)
        px = (TW - (bb[2] - bb[0])) // 2
        py = int(TH * 0.16)
        dr.text((px + 2, py + 2), phrase, font=font, fill=(0, 0, 0, 90))
        dr.text((px, py), phrase, font=font, fill=(255, 249, 232))
    return img


def build(out_path, phrase, images, ffmpeg=None):
    ffmpeg = ffmpeg or os.environ.get("FFMPEG") or os.path.join(ROOT, "bin", "ffmpeg")
    if not os.path.exists(ffmpeg):
        ffmpeg = "ffmpeg"
    with tempfile.TemporaryDirectory() as tmp:
        for i, im in enumerate(images):
            cadre(im, phrase).save(os.path.join(tmp, f"f{i:03d}.jpg"), quality=93)
        subprocess.run([ffmpeg, "-y", "-framerate", str(1 / DUREE_PLAN),
                        "-i", os.path.join(tmp, "f%03d.jpg"),
                        "-vf", "fps=30,format=yuv420p", "-c:v", "libx264",
                        "-preset", "medium", "-crf", "20", out_path],
                       check=True, capture_output=True)
    return out_path


if __name__ == "__main__":
    out, phrase, imgs = sys.argv[1], sys.argv[2], sys.argv[3:]
    print(build(out, phrase, imgs))
