"""Rasterize captured terminal cells with macOS fonts, avoiding SVG CJK spacing issues."""
import json
import unicodedata
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Draw box glyphs on cell boundaries so the preview joins rails as a terminal does.
_BOX_MASKS = {'║':5,'═':10,'╔':6,'╗':12,'╚':3,'╝':9,'╠':7,'╣':13,'╦':14,'╩':11,'╬':15}

def draw_box(draw, bounds, char, color):
    left, top, right, bottom = bounds
    cx, cy = (left+right)//2, (top+bottom)//2
    gap = 2
    mask = _BOX_MASKS[char]
    if mask & 1 and mask & 4:
        for dx in (-gap,gap):
            draw.line((cx+dx,top,cx+dx,bottom),fill=color,width=1)
        for bit, edge, dx in ((2,right,gap),(8,left,-gap)):
            if mask & bit:
                for dy in (-gap,gap):
                    draw.line((cx+dx,cy+dy,edge,cy+dy),fill=color,width=1)
    elif mask & 2 and mask & 8:
        for dy in (-gap,gap):
            draw.line((left,cy+dy,right,cy+dy),fill=color,width=1)
        for bit, edge, dy in ((1,top,-gap),(4,bottom,gap)):
            if mask & bit:
                for dx in (-gap,gap):
                    draw.line((cx+dx,cy+dy,cx+dx,edge),fill=color,width=1)
    else:
        sx, sy = (1 if mask & 2 else -1), (1 if mask & 4 else -1)
        edge_x, edge_y = (right if sx==1 else left), (bottom if sy==1 else top)
        for sign in (-1,1):
            x, y = cx+sign*sx*gap, cy+sign*sy*gap
            draw.line((x,y,edge_x,y),fill=color,width=1)
            draw.line((x,y,x,edge_y),fill=color,width=1)

font = ImageFont.truetype('/System/Library/Fonts/Menlo.ttc', 23, index=0)
bold = ImageFont.truetype('/System/Library/Fonts/Menlo.ttc', 23, index=1)
cjk = ImageFont.truetype('/System/Library/Fonts/STHeiti Medium.ttc', 23)
for screen in json.loads(Path('/tmp/ignovate-onboarding-cells.json').read_text()):
    cols, rows = screen['size']
    cell_width, cell_height = 15, 29
    canvas = Image.new('RGB', (cols*cell_width, rows*cell_height), '#1d2233')
    draw = ImageDraw.Draw(canvas)
    for y, line in enumerate(screen['lines']):
        x = 0
        for segment in line:
            for char in segment['text']:
                width = (0 if unicodedata.combining(char) else
                         2 if unicodedata.east_asian_width(char) in 'WF' else 1)
                if not width:
                    continue
                left, top = x*cell_width, y*cell_height
                box = (left, top, left+cell_width*width-1, top+cell_height-1)
                draw.rectangle(box, fill=segment['bg'])
                if char == '█':
                    draw.rectangle(box, fill=segment['fg'])
                elif char in _BOX_MASKS:
                    draw_box(draw,box,char,segment['fg'])
                elif char != ' ':
                    face = cjk if width == 2 else bold if segment['bold'] else font
                    draw.text((left+1, top+3), char, font=face, fill=segment['fg'])
                x += width
    canvas.save(f"docs/previews/ignovate-onboarding-{cols}x{rows}-{screen['step']}.png")
    if screen['size'] == [100,32] and screen['step'] == 0:
        canvas.crop((0,0,76*cell_width,9*cell_height)).save('docs/previews/ignovate-wordmark.png')
print('12 cell-layout previews rendered')
