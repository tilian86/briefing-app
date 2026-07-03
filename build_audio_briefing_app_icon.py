#!/usr/bin/env python3
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


SIZE = 1024
ASSET_DIR = Path("/Users/florian/Library/Application Support/Projects/Briefing-App/assets")
OUTPUT_PATH = ASSET_DIR / "briefing_app_icon_macos.png"


def _vertical_gradient(size: int, top_rgb: tuple[int, int, int], bottom_rgb: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGBA", (size, size))
    px = image.load()
    for y in range(size):
        t = y / max(size - 1, 1)
        rgb = tuple(int(top_rgb[i] * (1 - t) + bottom_rgb[i] * t) for i in range(3))
        for x in range(size):
            px[x, y] = (*rgb, 255)
    return image


def build_icon() -> Path:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)

    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    card_size = 816
    card_x = (SIZE - card_size) // 2
    card_y = (SIZE - card_size) // 2
    card_box = (card_x, card_y, card_x + card_size, card_y + card_size)
    radius = 198

    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (card_x + 12, card_y + 28, card_x + card_size + 12, card_y + card_size + 28),
        radius=radius,
        fill=(3, 7, 18, 120),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(32))
    canvas.alpha_composite(shadow)

    bg = _vertical_gradient(card_size, (11, 18, 32), (22, 63, 201))
    mask = Image.new("L", (card_size, card_size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, card_size, card_size), radius=radius, fill=255)
    bg.putalpha(mask)
    canvas.alpha_composite(bg, dest=(card_x, card_y))

    shine = Image.new("RGBA", (card_size, card_size), (0, 0, 0, 0))
    shine_draw = ImageDraw.Draw(shine)
    shine_draw.rounded_rectangle(
        (44, 36, card_size - 44, card_size // 2),
        radius=radius - 40,
        fill=(255, 255, 255, 24),
    )
    shine = shine.filter(ImageFilter.GaussianBlur(26))
    canvas.alpha_composite(shine, dest=(card_x, card_y))

    draw = ImageDraw.Draw(canvas)

    doc_x = card_x + 184
    doc_y = card_y + 146
    doc_w = 352
    doc_h = 486
    doc_r = 62

    draw.rounded_rectangle(
        (doc_x, doc_y, doc_x + doc_w, doc_y + doc_h),
        radius=doc_r,
        fill=(248, 250, 252, 248),
    )

    draw.rounded_rectangle(
        (doc_x + 42, doc_y + 48, doc_x + 262, doc_y + 118),
        radius=24,
        fill=(208, 218, 232, 255),
    )

    line_color = (133, 150, 176, 255)
    line_specs = [
        (doc_x + 42, doc_y + 166, doc_x + 276, doc_y + 194),
        (doc_x + 42, doc_y + 218, doc_x + 302, doc_y + 246),
        (doc_x + 42, doc_y + 270, doc_x + 248, doc_y + 298),
        (doc_x + 42, doc_y + 342, doc_x + 302, doc_y + 370),
        (doc_x + 42, doc_y + 394, doc_x + 256, doc_y + 422),
    ]
    for spec in line_specs:
        draw.rounded_rectangle(spec, radius=14, fill=line_color)

    accent_cx = card_x + 622
    accent_cy = card_y + 474
    accent_r = 98
    draw.ellipse(
        (accent_cx - accent_r, accent_cy - accent_r, accent_cx + accent_r, accent_cy + accent_r),
        fill=(31, 214, 102, 255),
    )

    play = [
        (accent_cx - 18, accent_cy - 34),
        (accent_cx + 42, accent_cy),
        (accent_cx - 18, accent_cy + 34),
    ]
    draw.polygon(play, fill=(244, 250, 247, 255))

    wave_color_1 = (166, 227, 196, 255)
    wave_color_2 = (214, 255, 229, 255)
    draw.arc(
        (accent_cx + 38, accent_cy - 52, accent_cx + 126, accent_cy + 52),
        start=-44,
        end=44,
        fill=wave_color_1,
        width=14,
    )
    draw.arc(
        (accent_cx + 16, accent_cy - 80, accent_cx + 156, accent_cy + 80),
        start=-44,
        end=44,
        fill=wave_color_2,
        width=14,
    )

    canvas.save(OUTPUT_PATH)
    return OUTPUT_PATH


if __name__ == "__main__":
    path = build_icon()
    print(path)
