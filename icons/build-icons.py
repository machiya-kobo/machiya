#!/usr/bin/env python3
"""Build the Machiya house icon in a colour palette: the three SVG forms and the PNG set. machiya-avatar.svg is the full-bleed square for avatars (GitHub rounds the corners itself).

    python3 icons/build-icons.py                       # the default palette (rooms) into icons/ and icons/png/
    python3 icons/build-icons.py --palette sunset      # another palette
    python3 icons/build-icons.py --palette sakura --out /tmp/icons --no-png

The drawing never changes, only the colours (the PALETTES below, Tokyo Night family). Forms: machiya.svg (rounded
square), machiya-maskable.svg (the house inside the central 80% of a full-bleed square), machiya-small.svg (favicon
sizes: lattice dropped, parts bold). PNGs are rendered with Inkscape: 16/32/48 from the small form, the rest from the
full one, plus the maskable sizes and favicon.ico (ImageMagick). The shipped alternatives are in icons/alt/.
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# role -> colour. noren: one colour or three (left to right). small_*: the favicon form's own greys.
PALETTES = {
    "blue":    dict(bg="#1a1b26", eave="#565f89", floor="#292e42", upbars="#414868", lowbars="#7aa2f7", door="#1a1b26",
                    noren="#7aa2f7", base="#565f89", small_eave="#8791c2", small_floor="#414868", small_low="#7aa2f7"),
    "rooms":   dict(bg="#1a1b26", eave="#7aa2f7", floor="#292e42", upbars="#8f9bdc", lowbars="#bb9af7", door="#1a1b26",
                    noren=("#ff9e64", "#9ece6a", "#f56cc7"), base="#7aa2f7", small_eave="#7aa2f7", small_floor="#414868",
                    small_low="#bb9af7"),
    "sunset":  dict(bg="#1d1a2e", eave="#ff9e64", floor="#2b2440", upbars="#e0af68", lowbars="#e0af68", door="#1d1a2e",
                    noren="#f7768e", base="#ff9e64", small_eave="#ff9e64", small_floor="#3b3258", small_low="#e0af68"),
    "indigo":  dict(bg="#16213e", eave="#7a88b8", floor="#1f2d57", upbars="#9aa5ce", lowbars="#c0caf5", door="#16213e",
                    noren="#e0505f", base="#7a88b8", small_eave="#9aa5ce", small_floor="#2c3f78", small_low="#c0caf5"),
    "garden":  dict(bg="#102a2b", eave="#2ac3a2", floor="#17403f", upbars="#73daca", lowbars="#9ece6a", door="#102a2b",
                    noren="#9ece6a", base="#2ac3a2", small_eave="#2ac3a2", small_floor="#1f5654", small_low="#9ece6a"),
    "sakura":  dict(bg="#241c3a", eave="#f7768e", floor="#322650", upbars="#bb9af7", lowbars="#bb9af7", door="#241c3a",
                    noren="#ff9ec7", base="#f7768e", small_eave="#f7768e", small_floor="#463670", small_low="#bb9af7"),
}


def read(name):
    with open(os.path.join(HERE, "alt", "_templates", name), encoding="utf-8") as f:
        return f.read()


def noren(p, n):
    cols = p["noren"] if isinstance(p["noren"], tuple) else (p["noren"],) * 3
    return cols[n]


def full(p, maskable):
    t = read("maskable.svg" if maskable else "machiya.svg")
    t = t.replace('<rect width="512" height="512" rx="112" fill="#1a1b26"/>', '<rect width="512" height="512" rx="112" fill="%s"/>' % p["bg"])
    t = t.replace('<rect width="512" height="512" fill="#1a1b26"/>', '<rect width="512" height="512" fill="%s"/>' % p["bg"])
    t = re.sub(r'(<path d="M(?:92 140|60 228)[^"]*" fill=")#565f89', lambda m: m.group(1) + p["eave"], t)
    t = re.sub(r'(<rect x="(?:116" y="164|104" y="256)"[^>]*fill=")#292e42', lambda m: m.group(1) + p["floor"], t)
    t = t.replace('<g fill="#414868">', '<g fill="%s">' % p["upbars"])
    t = t.replace('<g fill="#7aa2f7" opacity=".85">', '<g fill="%s" opacity=".85">' % p["lowbars"])
    t = t.replace('<rect x="252" y="256" width="146" height="160" fill="#1a1b26"/>', '<rect x="252" y="256" width="146" height="160" fill="%s"/>' % p["door"])
    t = re.sub(r'<g fill="#7aa2f7">(<rect x="256".*?)</g>', lambda m: "<g>%s</g>" % re.sub(
        r'<rect ', lambda r, it=iter(range(3)): '<rect fill="%s" ' % noren(p, next(it)), m.group(1)), t)
    t = t.replace('<rect x="84" y="416" width="344" height="18" rx="9" fill="#565f89"/>', '<rect x="84" y="416" width="344" height="18" rx="9" fill="%s"/>' % p["base"])
    t = re.sub(r"<!--.*?-->", lambda m: m.group(0), t)
    return t


def small(p):
    t = read("small.svg")
    t = t.replace('fill="#1a1b26"/>', 'fill="%s"/>' % p["bg"], 1)
    t = t.replace('fill="#8791c2"', 'fill="%s"' % p["small_eave"])
    t = t.replace('fill="#414868"', 'fill="%s"' % p["small_floor"])
    t = t.replace('fill="#7aa2f7" opacity=".6"', 'fill="%s" opacity=".6"' % p["small_low"])
    it = iter(range(3))
    t = re.sub(r'<rect (x="(?:240|308|376)"[^>]*)fill="#7aa2f7"', lambda m: '<rect %sfill="%s"' % (m.group(1), noren(p, next(it))), t)
    return t


def render(svg, png, size):
    subprocess.run(["inkscape", "-w", str(size), "-h", str(size), svg, "-o", png], check=True, capture_output=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--palette", default="rooms", choices=sorted(PALETTES))
    ap.add_argument("--out", default=HERE)
    ap.add_argument("--no-png", action="store_true")
    a = ap.parse_args()
    p = PALETTES[a.palette]
    os.makedirs(a.out, exist_ok=True)
    mask = full(p, True)
    avatar = mask.replace("translate(51.2 51.2) scale(0.8)", "translate(-51.2 -88.4) scale(1.2)")   # full bleed, the house enlarged and centred (about 92% of the width)
    forms = {"machiya.svg": full(p, False), "machiya-maskable.svg": mask, "machiya-small.svg": small(p), "machiya-avatar.svg": avatar}
    for name, text in forms.items():
        with open(os.path.join(a.out, name), "w", encoding="utf-8") as f:
            f.write(text)
    if a.no_png:
        return 0
    png = os.path.join(a.out, "png")
    os.makedirs(png, exist_ok=True)
    for s in (16, 32, 48):
        render(os.path.join(a.out, "machiya-small.svg"), os.path.join(png, "machiya-%d.png" % s), s)
    for s in (64, 96, 128, 180, 192, 256, 512):
        render(os.path.join(a.out, "machiya.svg"), os.path.join(png, "machiya-%d.png" % s), s)
    for s in (180, 192, 512):
        render(os.path.join(a.out, "machiya-maskable.svg"), os.path.join(png, "machiya-maskable-%d.png" % s), s)
    render(os.path.join(a.out, "machiya-avatar.svg"), os.path.join(png, "machiya-avatar-512.png"), 512)
    subprocess.run(["convert", os.path.join(png, "machiya-16.png"), os.path.join(png, "machiya-32.png"), os.path.join(png, "machiya-48.png"),
                    os.path.join(png, "favicon.ico")], check=True)
    print("built the %s palette into %s" % (a.palette, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
