# Ignovate desktop app icon

The user selected `ignovate-app-icon-v1.png`, generated with the built-in imagegen tool from the existing blue round, two-eye brand avatar. The original 1254×1254 RGBA PNG and generation prompt are retained unchanged.

- `ignovate.icns`: native macOS app icon, standard 16/32/128/256/512 point representations at 1× and 2× (up to 1024px).
- `ignovate.ico`: Windows icon with 16, 24, 32, 48, 64, 128 and 256px, 32-bit PNG representations.
- Rounded-tile outer transparency is retained. No visual redesign is performed during conversion.

## Reproduction on macOS

Use `sips -z SIZE SIZE ignovate-app-icon-v1.png --out SIZE.png` for each pixel size 16, 24, 32, 48, 64, 128, 256, 512, 1024. Populate `Ignovate.iconset` with `icon_16x16.png`, `icon_16x16@2x.png` and equivalent 32, 128, 256, 512 point names using their corresponding pixel sizes. Run `iconutil -c icns Ignovate.iconset -o ignovate.icns`.

Assemble the Windows container from those resized PNG files with Python's standard library (no further image processing):

```python
from pathlib import Path
import struct
sizes = (16, 24, 32, 48, 64, 128, 256)
offset = 6 + 16 * len(sizes)
entries, images = [], []
for size in sizes:
    image = Path(f'{size}.png').read_bytes()
    entries.append(struct.pack('<BBBBHHII', size % 256, size % 256,
                               0, 0, 1, 32, len(image), offset))
    images.append(image)
    offset += len(image)
Path('ignovate.ico').write_bytes(struct.pack('<HHH', 0, 1, len(sizes))
                                + b''.join(entries) + b''.join(images))
```

The packagers resolve their platform's resource relative to their own module and require it to exist before packaging. The Windows EXE embeds the ICO; Electron's window icon defaults to the executable icon. macOS embeds the ICNS referenced by the bundle's Info.plist. These formats are native packaging resources, so end users need no image-conversion tools.
