import json
from pathlib import Path
from .models import Theme

THEMES = {
    'daylight': Theme(),
    'ocean': Theme(name='ocean', background='#E6F6F4', sky_top='#BFE8EA', foreground='#123B48',
                   accent='#086D7C', ground='#B8D8B4'),
    'chalk': Theme(name='chalk', background='#153247', sky_top='#244B61', foreground='#F3F9FC',
                   accent='#8DD8E8', panel='#23465A', ground='#3D6558'),
}


def load_theme(name: str = 'daylight', path: Path | None = None) -> Theme:
    if path:
        return Theme.model_validate(json.loads(path.read_text()))
    if name not in THEMES:
        raise ValueError(f'Unknown theme {name!r}. Choose {", ".join(THEMES)} or provide --theme-file.')
    return THEMES[name].model_copy(deep=True)
