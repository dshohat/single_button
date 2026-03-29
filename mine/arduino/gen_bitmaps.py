"""Generate Hebrew XBM bitmaps for SSD1306 OLED 128x64 (U8g2 drawXBM format).

Pillow does not apply the Unicode BiDi algorithm, so Hebrew text renders LTR.
We reverse the string to get correct RTL visual display.
"""
from PIL import Image, ImageDraw, ImageFont
import os

W, H = 128, 64
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "hebrew_bitmaps.h")

def render_xbm(text, font_path, font_size):
    img = Image.new('1', (W, H), 0)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, font_size)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (W - tw) // 2 - bbox[0]
    y = (H - th) // 2 - bbox[1]
    draw.text((x, y), text, fill=1, font=font)
    data = []
    pixels = img.load()
    for row in range(H):
        for col_byte in range(W // 8):
            byte = 0
            for bit in range(8):
                col = col_byte * 8 + bit
                if pixels[col, row]:
                    byte |= (1 << bit)
            data.append(byte)
    return data

def to_c_array(name, data):
    lines = [f"static const unsigned char {name}[] PROGMEM = {{"]
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        line = ", ".join(f"0x{b:02x}" for b in chunk)
        lines.append("  " + line + ",")
    lines.append("};")
    return "\n".join(lines)

def main():
    font = "C:/Windows/Fonts/arialbd.ttf"
    if not os.path.exists(font):
        font = "C:/Windows/Fonts/arial.ttf"
    if not os.path.exists(font):
        print("ERROR: No font found!")
        return
    print(f"Using font: {font}")

    # Pillow renders LTR. To get RTL visual order, reverse the string.
    # "!טלקמל" renders visually as "למקלט!"
    # "תאצל"  renders visually as "לצאת"
    # "הערתה" renders visually as "התרעה"
    bitmaps = [
        ("bmp_hatraa",   "\u05d4\u05ea\u05e8\u05e2\u05d4"[::-1],  48),
        ("bmp_lamiklat", "!\u05d8\u05dc\u05e7\u05de\u05dc",        44),
        ("bmp_latzet",   "\u05ea\u05d0\u05e6\u05dc",               48),
    ]

    out = "#pragma once\n// Auto-generated Hebrew XBM bitmaps for OLED 128x64 (RTL-corrected)\n\n"
    for name, text, size in bitmaps:
        data = render_xbm(text, font, size)
        out += to_c_array(name, data) + "\n\n"
        nz = sum(1 for b in data if b != 0)
        print(f"  {name}: visual text from '{text}' -> {nz} non-zero bytes")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Written to {OUT}")

if __name__ == "__main__":
    main()
