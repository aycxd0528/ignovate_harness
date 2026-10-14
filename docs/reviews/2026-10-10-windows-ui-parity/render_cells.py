"""Render offline compositor cells using local fonts and Pillow.

This export does not edit a user image or alter terminal layout. It substitutes
fonts while honoring each compositor character's measured terminal cell width.
"""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
CELL_WIDTH, CELL_HEIGHT = 14, 28
monospace = ImageFont.truetype('/System/Library/Fonts/Menlo.ttc', 21)
cjk = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Unicode.ttf', 22)

for scene in json.loads((OUT / 'preview-cells.json').read_text()):
    width, height = scene['size']
    canvas = Image.new('RGB', (width * CELL_WIDTH, height * CELL_HEIGHT), scene['background'])
    draw = ImageDraw.Draw(canvas)
    for row, line in enumerate(scene['lines']):
        column = 0
        for cell in line:
            size = cell['width']
            if not size:
                continue
            x, y = column * CELL_WIDTH, row * CELL_HEIGHT
            draw.rectangle((x, y, x + size * CELL_WIDTH - 1, y + CELL_HEIGHT - 1), fill=cell['bg'])
            character = cell['text']
            if character.strip():
                font = cjk if size == 2 else monospace
                advance = draw.textlength(character, font=font)
                position = (x + (size * CELL_WIDTH - advance) / 2, y + 3)
                draw.text(position, character, font=font, fill=cell['fg'],
                          stroke_width=0, anchor='lt')
                if cell['bold']:
                    draw.text((position[0] + .5, position[1]), character, font=font,
                              fill=cell['fg'], anchor='lt')
                if cell['underline']:
                    draw.line((x, y + CELL_HEIGHT - 4, x + size * CELL_WIDTH - 1,
                               y + CELL_HEIGHT - 4), fill=cell['fg'])
            column += size
    canvas.save(OUT / (scene['name'] + '.png'))
print('Rendered native cell previews with fixed cell widths.')
