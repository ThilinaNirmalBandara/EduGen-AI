"""Optional local Remotion adapter. The normal Manim CLI never imports Node."""
import json
import os
import shutil
import tempfile
from pathlib import Path
from src.storyboard.validation import validate_storyboard, fit_text,header_box
from .renderer import _run_streaming
from .storyboard_renderer import mux_shared_audio

EXPERIMENT = Path(__file__).resolve().parents[2]/'experiments'/'remotion'


def check_remotion():
    if not shutil.which('node'):
        raise RuntimeError('Experimental Remotion needs Node.js 18+; the default Manim renderer is unaffected.')
    if not (EXPERIMENT/'node_modules'/'@remotion'/'renderer').exists():
        raise RuntimeError(f'Remotion dependencies missing. Run: cd {EXPERIMENT} && npm ci --no-audit --no-fund')
    browser = os.environ.get('EDUGEN_CHROME') or shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    if not browser or not Path(browser).is_file():
        raise RuntimeError('Remotion needs a local Chrome/Chromium executable. Install it or set EDUGEN_CHROME; no browser is automatically downloaded.')
    return browser


def render_data(board):
    layouts = {}
    for scene in board.scenes:
        labels = [(o.id,o.text,o.width,o.height,o.font_size) for o in scene.objects if o.kind=='label']
        labels += [(c.id,c.text,.91,.14,30) for c in scene.narration]
        heading=header_box(board.title,heading=True)
        badge=header_box(scene.title)
        labels += [('heading',board.title,*heading[2:]),('badge',scene.title,*badge[2:])]
        layouts[scene.id] = {}
        for key,text,w,h,size in labels:
            value,size = fit_text(text,w,h,font_size=size)
            layouts[scene.id][key] = {'text':value,'font_size':size}
            if key in {'heading','badge'}:
                geometry=heading if key=='heading' else badge
                layouts[scene.id][key]['geometry']=dict(zip(('x','y','w','h'),geometry[:4]))
    return {'board':board.model_dump(mode='json'),'textLayouts':layouts}


class FrameReporter:
    def __init__(self,reporter,board):
        self.reporter,self.board,self.last = reporter,board,0
    def verbose_line(self,line):
        if self.reporter: self.reporter.verbose_line(line)
        try: data=json.loads(line)
        except (ValueError,TypeError): return
        frames=data.get('rendered_frames')
        if frames is None or not self.reporter: return
        time=frames/self.board.fps
        elapsed=0
        for i,scene in enumerate(self.board.scenes,1):
            if time >= elapsed and self.last < i:
                if self.last:
                    self.reporter.scene_completed(self.last,len(self.board.scenes),self.board.scenes[self.last-1].title)
                self.last=i
                self.reporter.scene_started(i,len(self.board.scenes),scene.title)
            if elapsed <= time <= elapsed+scene.duration and hasattr(self.reporter,'scene_frame_progress'):
                self.reporter.scene_frame_progress(i,max(0,frames-elapsed*self.board.fps),scene.duration*self.board.fps)
            elapsed+=scene.duration


def render_storyboard_remotion(board,output,*,narration=None,reporter=None,preview_only=False,preview_dir=None):
    board=validate_storyboard(board)
    if any(scene.native_scene is not None for scene in board.scenes):
        raise ValueError('The experimental Remotion renderer cannot typeset native mathematics. Use the primary --renderer manim.')
    browser=check_remotion()
    output=Path(output).resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    previews=Path(preview_dir).resolve() if preview_dir else output.parent/(output.stem+'_previews')
    with tempfile.TemporaryDirectory(prefix='edugen_remotion_') as directory:
        directory=Path(directory)
        inputs=directory/'inputs.json'
        inputs.write_text(json.dumps(render_data(board)))
        silent=directory/'silent.mp4'
        if reporter and not preview_only: reporter.rendering_started([{'title':s.title} for s in board.scenes])
        _run_streaming(['node',str(EXPERIMENT/'render.mjs'),str(inputs),str(silent),browser,str(previews),'1' if preview_only else '0'],
                       reporter=FrameReporter(reporter,board))
        if preview_only:
            if reporter: reporter.planner_stage(f'Remotion preview frames saved: {previews}')
            return previews
        if narration: mux_shared_audio(silent,narration,output,board.duration,reporter=reporter)
        else: shutil.copyfile(silent,output)
        if reporter:
            reporter.scene_completed(len(board.scenes),len(board.scenes),board.scenes[-1].title)
            reporter.rendering_finished(output)
    return output
