"""Generate sample emoji stickers for each emotion folder.

Called by setup scripts after pip install. Requires Pillow.
"""

import math
import os
import sys

SIZE = 512
STICKER_DIR = os.path.join("data", "stickers")


def circle(draw, cx, cy, r, fill):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)


def make_base(bg_color):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 20
    circle(draw, SIZE // 2, SIZE // 2, SIZE // 2 - margin, bg_color)
    circle(draw, SIZE // 2, SIZE // 2 + 8, SIZE // 2 - margin, (0, 0, 0, 20))
    circle(draw, SIZE // 2, SIZE // 2, SIZE // 2 - margin, bg_color)
    return img, draw


def make_happy():
    from PIL import Image, ImageDraw

    img, d = make_base((255, 220, 50))
    cx, cy = SIZE // 2, SIZE // 2
    circle(d, cx - 80, cy - 40, 35, (60, 30, 0))
    circle(d, cx + 80, cy - 40, 35, (60, 30, 0))
    circle(d, cx - 70, cy - 55, 12, (255, 255, 255))
    circle(d, cx + 90, cy - 55, 12, (255, 255, 255))
    for a in range(-50, 51):
        x = cx + int(120 * math.cos(math.radians(a)))
        y = cy + 30 + int(60 * math.sin(math.radians(abs(a) * 1.3)))
        circle(d, x, y, 4, (60, 30, 0))
    circle(d, cx - 130, cy + 20, 40, (255, 100, 80, 80))
    circle(d, cx + 130, cy + 20, 40, (255, 100, 80, 80))
    return img


def make_sad():
    from PIL import Image, ImageDraw

    img, d = make_base((120, 180, 255))
    cx, cy = SIZE // 2, SIZE // 2
    circle(d, cx - 75, cy - 50, 35, (20, 40, 100))
    circle(d, cx + 75, cy - 50, 35, (20, 40, 100))
    circle(d, cx - 65, cy - 65, 10, (255, 255, 255))
    circle(d, cx + 85, cy - 65, 10, (255, 255, 255))
    d.polygon([(cx - 75, cy - 10), (cx - 90, cy + 50), (cx - 60, cy + 50)], fill=(100, 180, 255, 180))
    d.polygon([(cx + 75, cy - 10), (cx + 60, cy + 50), (cx + 90, cy + 50)], fill=(100, 180, 255, 180))
    for a in range(-35, 36):
        x = cx + int(80 * math.cos(math.radians(a)))
        y = cy + 70 - int(35 * math.sin(math.radians(abs(a) * 1.5)))
        circle(d, x, y, 3, (20, 40, 100))
    return img


def make_angry():
    from PIL import Image, ImageDraw

    img, d = make_base((240, 60, 40))
    cx, cy = SIZE // 2, SIZE // 2
    for bx, angle in [(-75, 25), (75, -25)]:
        for i in range(20):
            x = cx + bx + int(i * 3 * math.cos(math.radians(angle)))
            y = cy - 65 + int(i * 3 * math.sin(math.radians(angle)))
            circle(d, x, y, 5, (40, 10, 0))
    circle(d, cx - 75, cy - 35, 32, (40, 10, 0))
    circle(d, cx + 75, cy - 35, 32, (40, 10, 0))
    circle(d, cx - 65, cy - 50, 9, (255, 255, 255))
    circle(d, cx + 85, cy - 50, 9, (255, 255, 255))
    mouth_y = cy + 55
    for i in range(-50, 51, 8):
        y_off = -5 if (i // 8) % 2 == 0 else 5
        circle(d, cx + i, mouth_y + y_off, 3, (40, 10, 0))
    for sx, sy in [(cx - 160, cy - 120), (cx + 140, cy - 100), (cx + 160, cy - 130)]:
        for r in range(8, 20, 5):
            circle(d, sx, sy, r, (255, 80, 30, 60))
    return img


def make_love():
    from PIL import Image, ImageDraw

    img, d = make_base((255, 140, 170))
    cx, cy = SIZE // 2, SIZE // 2
    for ex, ey in [(-75, -45), (75, -45)]:
        circle(d, ex - 12, ey - 8, 18, (200, 20, 60))
        circle(d, ex + 12, ey - 8, 18, (200, 20, 60))
        d.polygon([(ex - 28, ey + 3), (ex + 28, ey + 3), (ex, ey + 28)], fill=(200, 20, 60))
    for a in range(-30, 31):
        x = cx + int(80 * math.cos(math.radians(a)))
        y = cy + 40 + int(30 * math.sin(math.radians(abs(a) * 1.5)))
        circle(d, x, y, 3, (180, 20, 50))
    for hx, hy, s in [(cx - 140, cy - 100, 25), (cx + 150, cy - 90, 20), (cx, cy - 140, 18)]:
        circle(d, hx - s // 2, hy - 5, s, (255, 80, 120, 140))
        circle(d, hx + s // 2, hy - 5, s, (255, 80, 120, 140))
        d.polygon([(hx - s, hy + 2), (hx + s, hy + 2), (hx, hy + s + 5)], fill=(255, 80, 120, 140))
    circle(d, cx - 130, cy + 15, 35, (255, 100, 130, 70))
    circle(d, cx + 130, cy + 15, 35, (255, 100, 130, 70))
    return img


def make_surprised():
    from PIL import Image, ImageDraw

    img, d = make_base((255, 180, 50))
    cx, cy = SIZE // 2, SIZE // 2
    circle(d, cx - 75, cy - 45, 45, (255, 255, 255))
    circle(d, cx + 75, cy - 45, 45, (255, 255, 255))
    circle(d, cx - 75, cy - 45, 22, (40, 20, 0))
    circle(d, cx + 75, cy - 45, 22, (40, 20, 0))
    circle(d, cx - 65, cy - 58, 8, (255, 255, 255))
    circle(d, cx + 85, cy - 58, 8, (255, 255, 255))
    circle(d, cx, cy + 50, 38, (60, 20, 0))
    circle(d, cx, cy + 50, 28, (200, 50, 50))
    for bx, y in [(-75, -95), (75, -95)]:
        for i in range(18):
            circle(d, cx + bx - 15 + i * 2, y, 6, (80, 40, 0))
    return img


def make_neutral():
    from PIL import Image, ImageDraw

    img, d = make_base((200, 200, 210))
    cx, cy = SIZE // 2, SIZE // 2
    circle(d, cx - 75, cy - 40, 32, (60, 60, 70))
    circle(d, cx + 75, cy - 40, 32, (60, 60, 70))
    circle(d, cx - 65, cy - 55, 10, (255, 255, 255))
    circle(d, cx + 85, cy - 55, 10, (255, 255, 255))
    d.rounded_rectangle([cx - 50, cy + 45, cx + 50, cy + 55], radius=4, fill=(80, 80, 90))
    return img


GENERATORS = {
    "happy": make_happy,
    "sad": make_sad,
    "angry": make_angry,
    "love": make_love,
    "surprised": make_surprised,
    "neutral": make_neutral,
}


def main():
    for emotion, gen in GENERATORS.items():
        folder = os.path.join(STICKER_DIR, emotion)
        os.makedirs(folder, exist_ok=True)
        filename = "fallback.png" if emotion == "neutral" else f"{emotion}.png"
        path = os.path.join(folder, filename)
        img = gen()
        img.save(path)
        size_kb = os.path.getsize(path) / 1024
        print(f"  {emotion:12s} → {path} ({size_kb:.1f} KB)")
    print("  Done! 6 emotion sticker folders ready.")


if __name__ == "__main__":
    main()
