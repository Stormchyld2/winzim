"""Draws the flame app icon (transparent background, flame only) into assets/. Run: python make_icons.py (needs Pillow)."""
import os
from PIL import Image, ImageDraw
import flameimg

HERE = os.path.dirname(os.path.abspath(__file__))


def render(size):
    return flameimg.render(size, size)


if __name__ == "__main__":
    out = os.path.join(HERE, "assets")
    os.makedirs(out, exist_ok=True)
    big = render(256)
    big.save(os.path.join(out, "cindrix.png"))
    big.save(os.path.join(out, "cindrix.ico"), sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("wrote assets/cindrix.png and assets/cindrix.ico")
