"""Primary Manim dispatcher: illustrated storyboards plus audited native math."""
import html
import json
import shutil
import tempfile
from pathlib import Path
from src.storyboard.models import Storyboard
from src.storyboard.validation import validate_storyboard
from .renderer import render_lesson, _run_streaming
from .storyboard_renderer import render_storyboard_manim, mux_shared_audio, probe_video


class StageReporter:
    """Forward diagnostics; aggregate scene progress belongs to the outer job."""
    def __init__(self,reporter,total): self.reporter,self.total,self.offset,self.active=reporter,total,0,False
    @property
    def console(self): return self.reporter.console
    def verbose_line(self,line): self.reporter.verbose_line(line)
    def planner_stage(self,stage): self.reporter.planner_stage(stage)
    def rendering_started(self,*args): pass
    def rendering_finished(self,*args): pass
    def scene_started(self,index,total,title):
        if self.active: self.reporter.scene_started(self.offset+index,self.total,title)
    def scene_completed(self,index,total,title):
        if self.active: self.reporter.scene_completed(self.offset+index,self.total,title)
    def scene_frame_progress(self,index,completed,total):
        if self.active and hasattr(self.reporter,'scene_frame_progress'):
            self.reporter.scene_frame_progress(self.offset+index,completed,total)


def _groups(board):
    """Only consecutive illustrated scenes batch; never regroup by visual type."""
    groups=[]
    for index,scene in enumerate(board.scenes):
        native=scene.native_scene is not None
        if groups and not native and not groups[-1]['native']:
            groups[-1]['scenes'].append(scene)
        else: groups.append({'native':native,'scenes':[scene],'index':index})
    return groups


def _subboard(board,scenes):
    data=board.model_dump()
    data['scenes']=[s.model_dump() for s in scenes]
    data['required_concepts']=list(dict.fromkeys(c for s in scenes for c in s.concepts))
    return Storyboard.model_validate(data)


def write_preview_index(board,previews,frames_by_scene=None):
    rows=[]
    cards=[]
    for scene in board.scenes:
        if any(c in scene.id for c in ('/','\\')) or scene.id in {'.','..'}:
            raise ValueError('Scene IDs must not contain filesystem path components.')
        scene_files=frames_by_scene[scene.id] if frames_by_scene is not None else sorted((previews/scene.id).rglob('*.png'))
        if len(scene_files)<len(scene.narration):
            raise RuntimeError(f'Missing pre-render preview coverage for scene {scene.id}; rendering was not started.')
        for i,cue in enumerate(scene.narration):
            file=scene_files[min(i,len(scene_files)-1)] if scene_files else None
            row={'scene_id':scene.id,'cue_id':cue.id,'source_event_id':cue.source_event_id,
                 'step_index':cue.step_index,'template':scene.template,'start':cue.start,'duration':cue.duration,
                 'narration':cue.text,'frame':str(file.relative_to(previews)) if file else None}
            rows.append(row)
            picture=f'<img src="{html.escape(row["frame"],quote=True)}" alt="Scene preview">' if file else ''
            cards.append(f'<article><h2>{html.escape(scene.title)}</h2><p>{html.escape(scene.template)} · {cue.start:.2f}s</p>{picture}<p>{html.escape(cue.text)}</p></article>')
    (previews/'manifest.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    timing='Estimated silent timing' if 'estimated' in board.provenance.get('timing_source','') else 'Measured narration'
    (previews/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>EduGen storyboard previews</title>'
        '<style>body{background:#eaf7ff;color:#16384c;font:18px sans-serif;margin:2rem}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:1rem}article{background:white;padding:1rem;border-radius:12px}img{width:100%}</style>'
        f'<h1>{html.escape(board.title)}</h1><p>{timing}: {board.duration:.2f}s. Ordered visual previews; no content omitted.</p><main>'+''.join(cards)+'</main>')


def render_enhanced_manim(board,output,*,narration=None,reporter=None,preview_only=False):
    board=validate_storyboard(board)
    output=Path(output).resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    previews=output.parent/(output.stem+'_previews')
    previews.mkdir(parents=True,exist_ok=True)
    proxy=StageReporter(reporter,len(board.scenes)) if reporter else None
    quality='l' if board.height==480 else 'h' if board.height==1080 else 'm'
    groups=_groups(board)
    with tempfile.TemporaryDirectory(prefix='edugen_primary_') as directory:
        root=Path(directory)
        frames_by_scene={}
        # ALL equations/layouts/cues preflight before ANY movie generation.
        for group in groups:
            first=group['scenes'][0]
            group['output']=root/f'group-{group["index"]:03d}.mp4'
            if group['native']:
                plan={'title':board.title,'scenes':[first.native_scene.model_dump()]}
                # Global chronology/step completeness was validated before
                # dispatch. A single split fragment cannot cover other fragments.
                plan['scenes'][0].setdefault('animation_params',{})['require_all_steps']=False
                group['plan']=plan
                render_lesson(plan,group['output'],quality,reporter=proxy,preview_only=True,
                              preview_dir=previews/first.id)
                frames_by_scene[first.id]=sorted((previews/first.id/'scene_001').glob('event_*.png'))
            else:
                group['board']=_subboard(board,group['scenes'])
                # One file namespace per original scene, not a renumbered type batch.
                temporary_previews=root/f'previews-{group["index"]}'
                render_storyboard_manim(group['board'],group['output'],reporter=proxy,preview_only=True,
                                        preview_dir=temporary_previews)
                for scene in group['scenes']:
                    destination=previews/scene.id
                    destination.mkdir(exist_ok=True)
                    frames_by_scene[scene.id]=[]
                    for frame in sorted(temporary_previews.glob(f'{scene.id}-*.png')):
                        shutil.copyfile(frame,destination/frame.name)
                        frames_by_scene[scene.id].append(destination/frame.name)
        write_preview_index(board,previews,frames_by_scene)
        if reporter: reporter.planner_stage(f'Storyboard previews ready: {previews/"index.html"}')
        if preview_only: return previews
        if reporter: reporter.rendering_started([{'title':s.title} for s in board.scenes])
        normalized=[]
        elapsed,previous_frames=0.0,0
        for group in groups:
            if proxy: proxy.offset,proxy.active=group['index'],True
            if group['native']:
                render_lesson(group['plan'],group['output'],quality,reporter=proxy,
                              preview_dir=previews/group['scenes'][0].id)
            else:
                render_storyboard_manim(group['board'],group['output'],reporter=proxy,_preflight_done=True,
                                        preview_dir=previews/group['scenes'][0].id)
            elapsed+=sum(s.duration for s in group['scenes'])
            end_frames=round(elapsed*board.fps)
            frames=end_frames-previous_frames
            previous_frames=end_frames
            clip=root/f'clip-{len(normalized):03d}.mp4'
            # Cumulative frame budgets prevent per-scene rounding drift. Only
            # native trailing hold frames are removed; all narration is separate.
            _run_streaming(['ffmpeg','-v','error','-y','-i',str(group['output']),'-an','-vf',
                f'fps={board.fps},scale={board.width}:{board.height},trim=end_frame={frames},setpts=PTS-STARTPTS',
                '-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',str(clip)],reporter=proxy)
            actual=probe_video(clip)
            if abs(float(actual['format']['duration'])-frames/board.fps)>.04:
                raise RuntimeError('Rendered frames do not cover their ordered narration segment.')
            normalized.append(clip)
            for i,scene in enumerate(group['scenes'],group['index']+1):
                if reporter: reporter.scene_completed(i,len(board.scenes),scene.title)
        concat=root/'concat.txt'
        concat.write_text(''.join("file '"+str(p).replace("'","'\\''")+"'\n" for p in normalized))
        silent=root/'complete.mp4'
        _run_streaming(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(concat),'-c','copy',str(silent)],reporter=proxy)
        if narration:
            mux_shared_audio(silent,narration,output,board.duration,reporter=proxy)
        else:
            shutil.copyfile(silent,output)
    if reporter: reporter.rendering_finished(output)
    return output
