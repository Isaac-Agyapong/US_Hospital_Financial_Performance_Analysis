"""Draw the page artwork for the Power BI report, one PNG per page, from dashboard/assets/layout.json
(written by 06_build_powerbi_project.py so the artwork always lines up with the visuals).

Each image holds what Power BI cannot draw well: the dark sidebar with the logo and title lettering,
rounded tiles with soft shadows and a lit top edge, KPI icons in tinted circles and KPI labels, and a faint
glow and dot grid in the background. The visuals sit on top with transparent backgrounds.
Rendered at 2x the 1280 x 720 page for sharp text on high-DPI screens.
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "dashboard" / "assets"
S = 2
W, H = 1280 * S, 720 * S
FONTS = Path("C:/Windows/Fonts")


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), int(size * S))


def hex_rgb(h, a=255):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


def base(colors):
    """Dark gradient, a soft mint glow in the top right, a faint dot grid over the content area."""
    top, bottom = np.array(hex_rgb(colors["bg_top"])[:3], float), np.array(hex_rgb(colors["bg_bottom"])[:3], float)
    t = np.linspace(0, 1, H)[:, None, None]
    img = (top * (1 - t) + bottom * t).repeat(W, axis=1)
    yy, xx = np.mgrid[0:H, 0:W]
    glow = np.exp(-(((xx - W * 0.92) / (W * 0.30)) ** 2 + ((yy - H * 0.05) / (H * 0.45)) ** 2))
    img += glow[..., None] * np.array(hex_rgb(colors["glow"])[:3]) * 0.10
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).convert("RGBA")
    dots = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(dots)
    for y in range(20 * S, H, 22 * S):
        for x in range(240 * S, W, 22 * S):
            d.ellipse([x, y, x + S, y + S], fill=(255, 255, 255, 10))
    return Image.alpha_composite(im, dots)


def shadowed_tile(im, x, y, w, h, colors, radius=14):
    box = [x * S, y * S, (x + w) * S, (y + h) * S]
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([box[0], box[1] + 5 * S, box[2], box[3] + 5 * S], radius * S, fill=(0, 0, 0, 120))
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(9 * S)))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(box, radius * S, fill=hex_rgb(colors["tile"]), outline=hex_rgb(colors["tile_border"]), width=S)
    # lit top edge
    d.line([box[0] + radius * S, box[1] + S, box[2] - radius * S, box[1] + S], fill=(255, 255, 255, 26), width=S)
    im.alpha_composite(layer)


def icon(im, x, y, glyph, accent, size=34):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([x * S, y * S, (x + size) * S, (y + size) * S], fill=hex_rgb(accent, 46))
    f = font("seguisym.ttf" if not glyph.isascii() else "seguisb.ttf", size * 0.5)
    d.text(((x + size / 2) * S, (y + size / 2) * S), glyph, font=f, fill=hex_rgb(accent), anchor="mm")
    im.alpha_composite(layer)


def logo(im, x, y, colors, size=46):
    """Rounded square with a mint-to-sky gradient, a white medical cross and a rising line."""
    n = size * S
    g = np.linspace(0, 1, n)
    a, b = np.array(hex_rgb(colors["mint"])[:3], float), np.array(hex_rgb(colors["sky"])[:3], float)
    grad = (a * (1 - (g[:, None] + g[None, :]) / 2)[..., None] + b * ((g[:, None] + g[None, :]) / 2)[..., None])
    sq = Image.fromarray(grad.astype(np.uint8)).convert("RGBA")
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, n - 1, n - 1], 12 * S, fill=255)
    sq.putalpha(mask)
    d = ImageDraw.Draw(sq)
    c, arm, thick = n / 2, n * 0.26, n * 0.11
    d.rounded_rectangle([c - thick, c - arm, c + thick, c + arm], 3 * S, fill=(255, 255, 255, 235))
    d.rounded_rectangle([c - arm, c - thick, c + arm, c + thick], 3 * S, fill=(255, 255, 255, 235))
    pts = [(n * 0.14, n * 0.80), (n * 0.38, n * 0.62), (n * 0.56, n * 0.70), (n * 0.86, n * 0.34)]
    d.line(pts, fill=hex_rgb(colors["ink_dark"]), width=int(3.2 * S), joint="curve")
    im.alpha_composite(sq, (x * S, y * S))


def sidebar(im, page, colors):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rectangle([0, 0, 224 * S, H], fill=hex_rgb(colors["sidebar"]))
    d.line([224 * S, 0, 224 * S, H], fill=hex_rgb(colors["tile_border"]), width=S)
    im.alpha_composite(layer)
    logo(im, 18, 20, colors)
    d = ImageDraw.Draw(im)
    d.text((74 * S, 20 * S), "US Hospital", font=font("seguisb.ttf", 16.5), fill=hex_rgb(colors["text"]))
    d.text((74 * S, 41 * S), "Financial Performance", font=font("seguisb.ttf", 12.5), fill=hex_rgb(colors["mint"]))
    d.text((18 * S, 80 * S), "4,300 HOSPITALS  ·  2011-2023", font=font("segoeui.ttf", 8), fill=hex_rgb(colors["muted"]))
    d.line([18 * S, 100 * S, 206 * S, 100 * S], fill=hex_rgb(colors["tile_border"]), width=S)
    for text, y in page["sidebar_labels"]:
        d.text((20 * S, y * S), text, font=font("seguisb.ttf", 8), fill=hex_rgb(colors["muted"]))


def render(name, page, colors):
    im = base(colors)
    sidebar(im, page, colors)
    for t in page["tiles"]:
        shadowed_tile(im, t["x"], t["y"], t["w"], t["h"], colors)
        if t.get("icon"):
            icon(im, t["x"] + 14, t["y"] + 14, t["icon"], t["accent"])
            ImageDraw.Draw(im).text(((t["x"] + 58) * S, (t["y"] + 31) * S), t["label"], font=font("seguisb.ttf", 10),
                                    fill=hex_rgb(colors["muted"]), anchor="lm")
    out = ASSETS / f"bg_{name}.png"
    im.convert("RGB").save(out, optimize=True)
    return out


def main():
    layout = json.loads((ASSETS / "layout.json").read_text(encoding="utf-8"))
    for name, page in layout["pages"].items():
        render(name, page, layout["colors"])
    print(f"wrote {len(layout['pages'])} page backgrounds to {ASSETS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
