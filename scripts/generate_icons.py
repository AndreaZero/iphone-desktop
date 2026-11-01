"""Render the app's existing mint phone mark for Windows and browser icons."""
from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1] / "iphone_desktop" / "web"
scale = 3
icon = Image.new("RGBA", (512 * scale, 512 * scale))
draw = ImageDraw.Draw(icon)


def box(bounds, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(tuple(int(v * scale) for v in bounds), radius * scale, fill, outline, width * scale)


mint = "#96ebc9"
box((8, 8, 504, 504), 112, "#101c21", "#304b46", 5)
box((159, 78, 353, 434), 42, mint)
box((173, 92, 339, 420), 29, "#192c30")
box((222, 100, 290, 114), 7, mint)
box((225, 396, 287, 402), 3, mint)
# A small pair of screen lines keeps the silhouette legible at 16 px.
box((205, 211, 307, 223), 6, "#96ebc9")
box((205, 244, 283, 256), 6, "#527e72")
box((205, 277, 296, 289), 6, "#527e72")
icon = icon.resize((512, 512), Image.Resampling.LANCZOS)
icon.save(root / "app-icon.png")
icon.save(root / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
(root / "app-icon.svg").write_text('''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect x="8" y="8" width="496" height="496" rx="112" fill="#101c21" stroke="#304b46" stroke-width="5"/><rect x="159" y="78" width="194" height="356" rx="42" fill="#96ebc9"/><rect x="173" y="92" width="166" height="328" rx="29" fill="#192c30"/><g fill="#96ebc9"><rect x="222" y="100" width="68" height="14" rx="7"/><rect x="225" y="396" width="62" height="6" rx="3"/><rect x="205" y="211" width="102" height="12" rx="6"/></g><g fill="#527e72"><rect x="205" y="244" width="78" height="12" rx="6"/><rect x="205" y="277" width="91" height="12" rx="6"/></g></svg>''', encoding="utf-8")
print("Created app.ico, app-icon.png and app-icon.svg")
