"""Cache-only, same-audio comparison. Intentionally separate from normal generation."""
import argparse
import hashlib
import json
import logging
import platform
import subprocess
import tempfile
import time
from pathlib import Path
from rich.console import Console
from src.storyboard.cached_preview import build_cached_preview
from src.storyboard.themes import load_theme
from src.storyboard.validation import quality_issues
from src.video.renderer import render_lesson
from src.video.storyboard_renderer import render_storyboard_manim, mux_shared_audio, probe_video


class BenchmarkReporter:
    def __init__(self, output, verbose=False):
        self.console, self.verbose = Console(), verbose
        self.status=None
        self.logger = logging.getLogger('edugen.visual_benchmark')
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        handler = logging.FileHandler(output/'execution.log')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        self.handler = handler
        self.logger.addHandler(handler)
    def verbose_line(self,line):
        self.logger.info(line)
        if self.verbose: self.console.print(line,markup=False)
    def planner_stage(self,stage): self.console.print(stage,markup=False)
    def rendering_started(self,*args): pass
    def scene_started(self,index,total,title): self.console.print(f'  {index}/{total}: {title}',markup=False)
    def scene_completed(self,*args): pass
    def scene_frame_progress(self,index,completed,total):
        if self.status: self.status.update(f'Scene {index}: {int(completed)}/{int(total)} rendered frames')
    def rendering_finished(self,*args): pass
    def close(self):
        self.logger.removeHandler(self.handler)
        self.handler.close()


def audio_hash(video):
    data = subprocess.check_output(['ffmpeg','-v','error','-i',str(video),'-map','0:a:0','-f','s16le','-acodec','pcm_s16le','-'])
    return hashlib.sha256(data).hexdigest()


def save_metrics(output, report):
    # Separate backend invocations may finish in either order.
    path = output/'metrics.json'
    if path.exists():
        previous = json.loads(path.read_text())
        if all(previous.get(k) == report[k] for k in ('narration_sha256','width','height','fps')):
            report['results'] = {**previous.get('results',{}), **report['results']}
    path.write_text(json.dumps(report,indent=2))


def run_benchmark(args):
    output = args.output_dir.resolve()
    output.mkdir(parents=True,exist_ok=True)
    selected = args.renderers.split(',')
    if not set(selected) <= {'baseline','illustrated','remotion'}:
        raise ValueError('Renderers must be baseline,illustrated,remotion.')
    for name in selected:
        if (output/f'{name}.mp4').exists() and not args.overwrite:
            raise ValueError(f'{output/name}.mp4 already exists. Choose another output directory or explicitly use --overwrite.')
    board, narration, baseline = build_cached_preview(args.lesson,args.audio_cache,output/'inputs',
        theme=load_theme(args.theme,args.theme_file))
    if args.quality == 'l': board.width,board.height = 854,480
    else: board.width,board.height = 1280,720
    # Input/geometry checks use a stable 720p design size; low quality is scaling,
    # not a request to shrink the typography or change the layout.
    (output/'inputs'/'storyboard.json').write_text(board.model_dump_json(indent=2))
    reporter = BenchmarkReporter(output,args.verbose)
    report = {'narration_sha256':hashlib.sha256(narration.read_bytes()).hexdigest(),
        'duration_seconds':board.duration,'fps':30,'width':board.width,'height':board.height,
        'model_calls':0,'tts_calls':0,'source_lesson':str(args.lesson.resolve()),
        'quality_issues':quality_issues(board), 'platform':platform.platform(), 'results':{}}
    previous_report = output/'metrics.json'
    if previous_report.exists():
        previous = json.loads(previous_report.read_text())
        keys = ('narration_sha256','width','height','fps')
        if all(previous.get(key) == report[key] for key in keys):
            report['results'] = previous.get('results',{})
    try:
        if args.prepare_only:
            reporter.console.print(f'Validated storyboard and shared cached narration: {output/"inputs"}')
            return output
        for name in selected:
            reporter.console.print(f'\n{name}: rendering {board.duration:.2f}s at {board.width}x{board.height}, 30 FPS')
            started = time.perf_counter()
            with tempfile.TemporaryDirectory(prefix='edugen_compare_') as directory:
                silent = Path(directory)/'silent.mp4'
                with reporter.console.status(f'{name}: preparing and rendering frames...',spinner='dots') as status:
                    reporter.status=status
                    if name == 'baseline':
                        render_lesson(baseline,silent,quality=args.quality,reporter=reporter,
                                      preview_dir=output/'baseline_previews')
                    elif name == 'illustrated':
                        render_storyboard_manim(board,silent,reporter=reporter,
                                                preview_dir=output/'illustrated_previews')
                    else:
                        from src.video.remotion_renderer import render_storyboard_remotion
                        render_storyboard_remotion(board,silent,reporter=reporter,
                                                   preview_dir=output/'remotion_previews')
                    render_seconds = time.perf_counter()-started
                    destination = output/f'{name}.mp4'
                    mux_shared_audio(silent,narration,destination,board.duration,reporter=reporter)
                reporter.status=None
            probe = probe_video(destination)
            report['results'][name] = {'wall_seconds':round(time.perf_counter()-started,3),
                'render_seconds':round(render_seconds,3),'file_bytes':destination.stat().st_size,
                'duration_seconds':float(probe['format']['duration']), 'decoded_audio_sha256':audio_hash(destination),
                'streams':[s['codec_type'] for s in probe['streams']], 'output':str(destination)}
            save_metrics(output,report)
            reporter.console.print(f'Completed {name}: {report["results"][name]["wall_seconds"]:.1f}s')
        return output
    finally:
        save_metrics(output,report)
        reporter.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lesson',type=Path,default=Path('outputs/water_cycle.lesson.json'))
    parser.add_argument('--audio-cache',type=Path,default=Path('outputs/narration'))
    parser.add_argument('--output-dir',type=Path,default=Path('outputs/visual_benchmark'))
    parser.add_argument('--renderers',default='baseline,illustrated,remotion')
    parser.add_argument('--quality',choices=['l','m'],default='m')
    parser.add_argument('--theme',default='daylight')
    parser.add_argument('--theme-file',type=Path)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--overwrite',action='store_true')
    parser.add_argument('--verbose',action='store_true')
    try: run_benchmark(parser.parse_args())
    except KeyboardInterrupt:
        Console().print('Benchmark cancelled; completed artifacts and logs are preserved.')
        return 130
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        Console().print(f'Benchmark error: {exc}',markup=False)
        return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
