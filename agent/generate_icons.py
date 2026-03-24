"""
Generates Fetchr extension icons (16, 48, 128 px) as PNG files.
Uses only stdlib + built-in drawing — no Pillow required.
Run once: python generate_icons.py
"""

import struct
import zlib
import os

# Output directory (extension/icons relative to this script's parent)
ICONS_DIR = os.path.join(os.path.dirname(__file__), "..", "extension", "icons")
os.makedirs(ICONS_DIR, exist_ok=True)


def create_png(width: int, height: int, pixels: list[tuple[int,int,int,int]]) -> bytes:
    """Minimal PNG encoder — RGBA, no compression trickery."""
    def chunk(name: bytes, data: bytes) -> bytes:
        c = name + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    header = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # RGB
    ihdr = chunk(b"IHDR", ihdr_data)

    # Build raw image data (RGB, no alpha for simplicity)
    raw = b""
    for y in range(height):
        raw += b"\x00"  # filter byte = None
        for x in range(width):
            r, g, b, _ = pixels[y * width + x]
            raw += bytes([r, g, b])

    # Re-encode as RGBA properly
    raw2 = b""
    for y in range(height):
        raw2 += b"\x00"
        for x in range(width):
            r, g, b, a = pixels[y * width + x]
            raw2 += bytes([r, g, b, a])

    ihdr_data2 = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # RGBA
    ihdr2 = chunk(b"IHDR", ihdr_data2)
    idat  = chunk(b"IDAT", zlib.compress(raw2, 9))
    iend  = chunk(b"IEND", b"")
    return header + ihdr2 + idat + iend


def draw_icon(size: int) -> list[tuple[int,int,int,int]]:
    """Draw a simple download-arrow icon at the given size."""
    pixels = [(0, 0, 0, 0)] * (size * size)
    cx, cy = size // 2, size // 2
    r_outer = int(size * 0.46)
    r_inner = int(size * 0.34)

    # Colors
    bg     = (79, 110, 247, 255)   # accent blue
    arrow  = (255, 255, 255, 255)  # white arrow
    transp = (0, 0, 0, 0)

    def dist(x, y, ox, oy):
        return ((x - ox) ** 2 + (y - oy) ** 2) ** 0.5

    for py in range(size):
        for px in range(size):
            d = dist(px, py, cx, cy)
            idx = py * size + px

            # Circular background
            if d <= r_outer:
                pixels[idx] = bg
            else:
                pixels[idx] = transp

    # Draw arrow (downward) — stem + arrowhead
    stem_w  = max(2, size // 10)
    stem_h  = size // 3
    arr_w   = max(4, size // 4)
    arr_h   = max(3, size // 6)
    bar_h   = max(1, size // 12)
    bar_w   = size // 3

    stem_x  = cx - stem_w // 2
    stem_y  = cy - size // 5

    # Vertical stem
    for py in range(stem_y, stem_y + stem_h):
        for px in range(stem_x, stem_x + stem_w):
            if 0 <= px < size and 0 <= py < size:
                if dist(px, py, cx, cy) <= r_outer:
                    pixels[py * size + px] = arrow

    # Arrowhead (triangle pointing down)
    tip_y  = stem_y + stem_h + arr_h
    tip_x  = cx
    left_x = cx - arr_w // 2
    right_x = cx + arr_w // 2
    for py in range(stem_y + stem_h, tip_y + 1):
        frac = (py - (stem_y + stem_h)) / max(arr_h, 1)
        row_left  = int(tip_x - (1 - frac) * arr_w / 2)
        row_right = int(tip_x + (1 - frac) * arr_w / 2)
        for px in range(row_left, row_right + 1):
            if 0 <= px < size and 0 <= py < size:
                if dist(px, py, cx, cy) <= r_outer:
                    pixels[py * size + px] = arrow

    # Horizontal bar at bottom
    bar_y = tip_y + max(1, size // 10)
    bar_x = cx - bar_w // 2
    for py in range(bar_y, bar_y + bar_h + 1):
        for px in range(bar_x, bar_x + bar_w):
            if 0 <= px < size and 0 <= py < size:
                if dist(px, py, cx, cy) <= r_outer:
                    pixels[py * size + px] = arrow

    return pixels


for sz in [16, 48, 128]:
    px  = draw_icon(sz)
    png = create_png(sz, sz, px)
    path = os.path.join(ICONS_DIR, f"icon{sz}.png")
    with open(path, "wb") as f:
        f.write(png)
    print(f"✅  Written {path} ({sz}×{sz})")

print("\nAll icons generated.")
