"""Smooth (anti-aliased) flame pictures drawn with Pillow: Tk's own canvas draws jagged edges, so the banner and the
window icon use these instead. Pillow is bundled in the exe; without it the callers fall back to plain shapes."""
import base64
import io

import flame

BOX = (0.14, 0.04, 0.72, 0.92)     # where the flame sits inside its square, as fractions


def render(w, h, ss=6, box=BOX):
    """RGBA picture of the flame, w x h pixels, transparent background."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bx, by, bw, bh = box
    for segs, col in flame.LAYERS:
        d.polygon(flame.outline(segs, w * ss * bx, h * ss * by, w * ss * bw, h * ss * bh, 40), fill=col)
    return img.resize((w, h), Image.LANCZOS)


def png_bytes(img):
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def photo_data(w, h):
    """Base64 PNG for tk.PhotoImage(data=...), flame filling a w x h box (no padding)."""
    return base64.b64encode(png_bytes(render(w, h, box=(0, 0, 1, 1)))).decode("ascii")
