"""A separate cache of validated visual plans, independent of render resolution."""
import hashlib
import json
from pathlib import Path
from .compiler import compile_storyboard
from .models import Storyboard
from .validation import validate_storyboard
from .assets import CATALOG,asset_path


def implementation_digest():
    digest=hashlib.sha256()
    for file in sorted(Path(__file__).parent.glob('*.py')):
        digest.update(file.name.encode()); digest.update(file.read_bytes())
    for asset in sorted(CATALOG): digest.update(asset_path(asset).read_bytes())
    return digest.hexdigest()


def cached_storyboard(lesson,segments,theme,*,cache_dir=Path('outputs/cache/storyboards'),force=False,prompt='',reporter=None):
    inputs={'version':2,'compiler':implementation_digest(),'lesson':lesson,'theme':theme.model_dump(),
        'prompt':prompt,'timings':[{'duration':a.duration,'phrases':[
            {'text':t.text,'start':t.start,'duration':t.duration,'cue_index':t.cue_index} for t in a.timings]} for a in segments]}
    key=hashlib.sha256(json.dumps(inputs,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:32]
    path=Path(cache_dir)/(key+'.json')
    if path.exists() and not force:
        try:
            board=validate_storyboard(Storyboard.model_validate_json(path.read_text()))
            if reporter: reporter.planner_stage('Reused cached validated storyboard (no Ollama or TTS call)')
            return board
        except (ValueError,OSError):
            if reporter: reporter.planner_stage('Cached storyboard needs revalidation; preserving it until compilation succeeds')
    board=compile_storyboard(lesson,segments,theme=theme,prompt=prompt)
    path.parent.mkdir(parents=True,exist_ok=True)
    candidate=path.with_suffix('.pending')
    candidate.write_text(board.model_dump_json(indent=2))
    candidate.replace(path)
    if reporter: reporter.planner_stage(f'Validated concept-based storyboard: {len(board.scenes)} scenes')
    return board
