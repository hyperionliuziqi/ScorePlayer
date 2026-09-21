from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1]
out = root / "fixtures" / "warmup_score.png"
w, h = 1400, 420
img = Image.new("RGB", (w, h), "white")
d = ImageDraw.Draw(img)

top = 120
spacing = 28
for i in range(5):
    y = top + i * spacing
    d.line((90, y, 1310, y), fill="black", width=3)

# Filled noteheads + stems, enough to force all inference models to load.
steps = [0, 1, 2, 3, 4, 3, 2, 1, 0]
for i, step in enumerate(steps):
    x = 180 + i * 115
    y = top + 4 * spacing - step * spacing / 2
    d.ellipse((x-13, y-9, x+13, y+9), fill="black")
    d.line((x+12, y, x+12, y-70), fill="black", width=4)

img.save(out)
print(out)
