"""Capture actual Textual onboarding cells; no model requests or user config writes."""
import asyncio
import json
import os
import tempfile
from pathlib import Path

from nailong.core.bootstrap import BootstrapStore
from textual.widgets import Input
from ui.setup import SetupApp


async def main():
    # Preview the colored UI independently of the runner's NO_COLOR preference.
    os.environ.pop('NO_COLOR', None)
    screens, geometry = [], []
    output = Path('docs/previews')
    output.mkdir(parents=True, exist_ok=True)
    for size in ((100, 32), (80, 24), (213, 56)):
        with tempfile.TemporaryDirectory() as tmp:
            app = SetupApp(store=BootstrapStore(Path(tmp)/'user'),
                           project_root=Path('/Users/aycxd/Desktop/LLM_project'))
            async with app.run_test(size=size) as pilot:
                for step in range(4):
                    if step == 1:
                        await pilot.press('enter')
                    if step == 2:
                        app.query_one('#setup-key', Input).value = 'preview-fixture-key'
                        await pilot.click('#setup-next')
                    if step == 3:
                        await pilot.press('enter')
                    await pilot.pause()
                    update = app.screen._compositor.render_full_update(simplify=True)
                    lines = []
                    for line in update.strips:
                        segments = []
                        for strip in line:
                            for segment in strip:
                                if segment.control:
                                    continue
                                style = segment.style
                                def color(value, default):
                                    if value is None:
                                        return default
                                    return '#%02x%02x%02x' % tuple(value.get_truecolor())
                                segments.append({'text': segment.text,
                                    'fg': color(style.color if style else None, '#d5dbea'),
                                    'bg': color(style.bgcolor if style else None, '#1d2233'),
                                    'bold': bool(style and style.bold)})
                        lines.append(segments)
                    screens.append({'size': size, 'step': step, 'lines': lines})
                    footer = app.query_one('#setup-keys').region
                    geometry.append({'size': size, 'step': step,
                                     'footer_inside_view': footer.bottom <= size[1],
                                     'left_alignment': app.query_one('#welcome-title').region.x,
                                     'saved_early': app.store.path.exists()})
                    app.save_screenshot(filename=f'ignovate-onboarding-{size[0]}x{size[1]}-{step}.svg', path=output)
    assert all(row['footer_inside_view'] and not row['saved_early'] for row in geometry)
    assert not any('preview-fixture-key' in json.dumps(row['lines']) for row in screens)
    Path('/tmp/ignovate-onboarding-cells.json').write_text(json.dumps(screens, ensure_ascii=False))
    Path('docs/reviews/2026-10-07-onboarding-layout.json').write_text(json.dumps(geometry, indent=2))
    print({'screens': len(screens), 'layout_checks': 'passed', 'secret_visible': False})


if __name__ == '__main__':
    asyncio.run(main())
