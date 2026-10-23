"""Local, fixed asset catalog. Model data never supplies filesystem/HTTP paths."""
from pathlib import Path
import xml.etree.ElementTree as ET

ASSET_ROOT = Path(__file__).resolve().parents[2]/'assets'/'svg'
CATALOG = {name: f'{name}.svg' for name in ('sun', 'cloud', 'lake', 'rain', 'river', 'mountain')}


def asset_path(asset_id: str) -> Path:
    if asset_id not in CATALOG:
        raise ValueError(f'Unknown asset {asset_id!r}; choose a registered local asset ID.')
    path = ASSET_ROOT/CATALOG[asset_id]
    if not path.is_file():
        raise ValueError(f'Missing illustration asset: {path}')
    return path


def validate_assets() -> None:
    allowed = {'svg', 'g', 'path', 'circle', 'ellipse', 'rect', 'line', 'polyline', 'polygon'}
    for asset in CATALOG:
        path = asset_path(asset)
        raw = path.read_text()
        if '<!DOCTYPE' in raw.upper() or '<!ENTITY' in raw.upper():
            raise ValueError('SVG entities are not allowed.')
        root = ET.fromstring(raw)
        if 'viewBox' not in root.attrib:
            raise ValueError('SVG assets need a viewBox.')
        for element in root.iter():
            if element.tag.split('}')[-1] not in allowed:
                raise ValueError(f'Unsafe SVG element in {path.name}')
            for key, value in element.attrib.items():
                if key.lower().startswith('on') or 'href' in key or 'url(' in value.lower():
                    raise ValueError(f'External/active SVG content in {path.name}')
