"""Draws the flame app icon (transparent background, flame only) into assets/. Run: python make_icons.py (needs Pillow)."""
import os
from PIL import Image, ImageDraw
import flame

HERE = os.path.dirname(os.path.abspath(__file__))


def render(size, ss=4):
    big = size * ss
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for segs, col in ((flame.OUTER, (217, 160, 43, 255)), (flame.INNER, (255, 211, 107, 255))):
        d.polygon(flame.outline(segs, big * 0.14, big * 0.04, big * 0.72, big * 0.92, 24), fill=col)
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    out = os.path.join(HERE, "assets")
    os.makedirs(out, exist_ok=True)
    big = render(256)
    big.save(os.path.join(out, "cindrix.png"))
    big.save(os.path.join(out, "cindrix.ico"), sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("wrote assets/cindrix.png and assets/cindrix.ico")
