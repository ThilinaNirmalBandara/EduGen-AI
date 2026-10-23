"""Adapters keep validation, rendering and common audio muxing separate."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from src.storyboard.models import Storyboard
from src.storyboard.validation import validate_storyboard
from .renderer import _run_streaming


class SceneReporter:
    def __init__(self, reporter, board):
        self.reporter, self.board, self.current = reporter, board, 0
    def verbose_line(self,line):
        if self.reporter: self.reporter.verbose_line(line)
        # Manim's tqdm output can precede the structured event on the same line.
        try: data = json.loads(line[line.index('{'):])
        except (ValueError,TypeError): return
        index = data.get('storyboard_scene')
        if index and index != self.current and self.reporter:
            if self.current:
                old = self.board.scenes[self.current-1]
                self.reporter.scene_completed(self.current,len(self.board.scenes),old.title)
            self.current = index
            self.reporter.scene_started(index,len(self.board.scenes),self.board.scenes[index-1].title)
        if 'storyboard_frame' in data and self.reporter and hasattr(self.reporter,'scene_frame_progress'):
            self.reporter.scene_frame_progress(data['scene_index'],data['scene_frame'],data['scene_frames'])


def probe_video(path: Path):
    return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]))


def mux_shared_audio(video, narration, output, duration, *, reporter=None):
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    audio_probe=probe_video(narration)
    video_probe=probe_video(video)
    if abs(float(audio_probe['format']['duration'])-duration)>.04:
        raise RuntimeError('Narration waveform duration disagrees with the storyboard; no content will be cut or padded.')
    if abs(float(video_probe['format']['duration'])-duration)>.1:
        raise RuntimeError('Visual frames do not cover the complete narration; refusing to freeze or truncate the lesson.')
    # Preserve an existing destination until both streams and duration validate.
    with tempfile.TemporaryDirectory(prefix='.edugen_mux_',dir=output.parent) as directory:
        candidate=Path(directory)/'complete.mp4'
        _run_streaming(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(video),'-i',str(narration),
            '-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',
            '-c:a','aac','-b:a','128k','-ar','24000','-t',str(duration),'-movflags','+faststart',str(candidate)],reporter=reporter)
        probe = probe_video(candidate)
        if {s['codec_type'] for s in probe['streams']} != {'audio','video'}:
            raise RuntimeError('Rendered MP4 must contain both video and narration audio.')
        if any(abs(float(s.get('duration',0))-duration)>.2 for s in probe['streams']):
            raise RuntimeError('Final MP4 stream duration differs from measured narration; refusing to report success.')
        candidate.replace(output)
    return Path(output)


def render_storyboard_manim(board: Storyboard, output: Path, *, narration: Path | None = None,
                            reporter=None, preview_only=False, preview_dir=None, _preflight_done=False):
    board = validate_storyboard(board)
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise RuntimeError('Illustrated rendering needs FFmpeg and FFprobe on PATH.')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    previews = Path(preview_dir).resolve() if preview_dir else output.parent/(output.stem+'_previews')
    previews.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='edugen_storyboard_') as directory:
        root = Path(directory)
        path = root/'storyboard.json'
        path.write_text(board.model_dump_json(indent=2))
        env = dict(os.environ,EDUGEN_STORYBOARD=str(path))
        command = [sys.executable,'-m','manim','-qm','--resolution',f'{board.width},{board.height}',
            '--frame_rate','30','--disable_caching','--media_dir',str(root/'media'),'-o','illustrated',
            str(Path(__file__).with_name('storyboard_manim.py')),'StoryboardManim']
        if not _preflight_done:
            if reporter: reporter.planner_stage('Illustrated preflight: SVG assets, readable labels, motion and narration coverage')
            _run_streaming(command+['-s'],env=dict(env,EDUGEN_STORYBOARD_PREFLIGHT='1',EDUGEN_STORYBOARD_PREVIEWS=str(previews)),reporter=reporter)
        if preview_only:
            if reporter: reporter.planner_stage(f'Illustrated preview frames saved: {previews}')
            return previews
        if reporter: reporter.rendering_started([{'title':s.title} for s in board.scenes])
        proxy = SceneReporter(reporter,board)
        _run_streaming(command,env=env,reporter=proxy)
        results = list((root/'media').rglob('illustrated.mp4'))
        if len(results) != 1: raise RuntimeError('Manim did not produce exactly one storyboard MP4.')
        if narration:
            mux_shared_audio(results[0],narration,output,board.duration,reporter=reporter)
        else:
            shutil.copyfile(results[0],output)
        if reporter:
            last = board.scenes[-1]
            reporter.scene_completed(len(board.scenes),len(board.scenes),last.title)
            reporter.rendering_finished(output)
    return output
