"""
Generate Hebrew text bitmaps for SSD1306 OLED display (128x64).
Output: MONO_VLSB format raw binary files (1024 bytes each).
Run on PC, then upload .bin files to ESP32.
"""
from PIL import Image, ImageDraw, ImageFont
import os

DISPLAY_W = 128
DISPLAY_H = 64
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

# Hebrew text -> filename mappings
BITMAPS = [
    ("התרעה",  "alert_warn.bin",    48),
    ("למקלט!", "alert_shelter.bin",  44),
    ("לצאת",   "alert_clear.bin",   48),
]

def find_hebrew_font():
    """Find a Hebrew-capable font on Windows."""
    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",  # bold
        "C:/Windows/Fonts/david.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

def render_to_mono_vlsb(text, font_path, font_size):
    """Render text to 128x64 MONO_VLSB bytearray."""
    img = Image.new('1', (DISPLAY_W, DISPLAY_H), 0)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, font_size)
    
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (DISPLAY_W - tw) // 2 - bbox[0]
    y = (DISPLAY_H - th) // 2 - bbox[1]
    draw.text((x, y), text, fill=1, font=font)
    
    # Convert to MONO_VLSB: each byte = 8 vertical pixels, bit0 = top
    buf = bytearray(DISPLAY_W * DISPLAY_H // 8)
    pixels = img.load()
    for page in range(DISPLAY_H // 8):
        for col in range(DISPLAY_W):
            byte = 0
            for bit in range(8):
                row = page * 8 + bit
                if pixels[col, row]:
                    byte |= (1 << bit)
            buf[page * DISPLAY_W + col] = byte
    return buf

def main():
    font_path = find_hebrew_font()
    if not font_path:
        print("ERROR: No Hebrew font found!")
        return
    print(f"Using font: {font_path}")
    
    # Prefer bold for better visibility on small OLED
    bold_path = font_path.replace('.ttf', 'bd.ttf')
    if os.path.exists(bold_path):
        font_path = bold_path
        print(f"Using bold: {font_path}")
    
    for text, filename, size in BITMAPS:
        out_path = os.path.join(OUTPUT_DIR, filename)
        buf = render_to_mono_vlsb(text, font_path, size)
        with open(out_path, 'wb') as f:
            f.write(buf)
        print(f"  {filename}: '{text}' at size {size} -> {len(buf)} bytes")
    
    print("Done!")

if __name__ == '__main__':
    main()
