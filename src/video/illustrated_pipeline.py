"""Opt-in integration after the existing planner, duration checks and audio splits."""
import hashlib
import json
from pathlib import Path
import numpy as np
import soundfile
from src.storyboard.cache import cached_storyboard
from src.storyboard.themes import load_theme
from src.tts import AudioSegment,NarrationTiming,narration_for_scene
from .timeline import narration_units


def join_narration(segments,cache_dir):
    """Content-addressed assembly only: never calls TTS or changes pacing."""
    digest=hashlib.sha256()
    arrays,rate,channels=[],None,None
    for segment in segments:
        digest.update(segment.path.read_bytes())
    cache_dir=Path(cache_dir)
    cache_dir.mkdir(parents=True,exist_ok=True)
    path=cache_dir/f'storyboard_{digest.hexdigest()[:24]}.wav'
    for segment in segments:
        data,current_rate=soundfile.read(segment.path,dtype='int16',always_2d=True)
        if (rate is not None and rate!=current_rate) or (channels is not None and channels!=data.shape[1]):
            raise ValueError('Narration has inconsistent sample rates/channels; regenerate it with the same voice settings.')
        if abs(len(data)/current_rate-segment.duration)>.03:
            raise ValueError('Measured narration duration disagrees with its cached waveform.')
        arrays.append(data)
        rate,channels=current_rate,data.shape[1]
    if not path.exists():
        soundfile.write(path,np.concatenate(arrays),rate,subtype='PCM_16')
    else:
        if abs(soundfile.info(path).duration-sum(s.duration for s in segments))>.03:
            raise ValueError(f'Cached assembled narration is invalid: {path}. Remove this specific cache file and retry.')
    return path


def render_illustrated_lesson(lesson,output,quality='m',*,renderer='illustrated',audio_segments=None,
                              theme='daylight',theme_file=None,reporter=None,preview_only=False,
                              cache_dir=Path('outputs/cache/storyboards'),force=False,prompt=''):
    if renderer not in {'manim','illustrated','remotion'}:
        raise ValueError(f'Unknown enhanced renderer: {renderer}')
    if quality not in {'l','m','h'}:
        raise ValueError('Quality must be l, m, or h')
    measured=audio_segments is not None
    if not measured:
        # Explicit silent previews retain the lesson narration as timed visual
        # cues. These estimates are never labelled as measured audio timings.
        audio_segments=[]
        for scene in lesson['scenes']:
            units=narration_units(narration_for_scene(scene))
            words=sum(len(t.split()) for t in units)
            start,timings=0,[]
            for i,text in enumerate(units):
                duration=float(scene['duration'])*len(text.split())/max(1,words)
                timings.append(NarrationTiming(text,start,duration,i)); start+=duration
            audio_segments.append(AudioSegment(Path('silent-preview'),start,tuple(timings)))
    board=cached_storyboard(lesson,audio_segments,load_theme(theme,theme_file),cache_dir=cache_dir,
                           force=force,prompt=prompt,reporter=reporter)
    board.provenance['timing_source']='measured Kokoro phrases' if measured else 'estimated silent preview; no audio'
    board.width,board.height={'l':(854,480),'m':(1280,720),'h':(1920,1080)}[quality]
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.with_suffix('.lesson.json').write_text(json.dumps(lesson,ensure_ascii=False,indent=2))
    output.with_suffix('.storyboard.json').write_text(board.model_dump_json(indent=2))
    narration=join_narration(audio_segments,output.parent/'narration') if measured and not preview_only else None
    if renderer=='remotion':
        from .remotion_renderer import render_storyboard_remotion as render
    elif renderer in {'illustrated','manim'}:
        from .enhanced_renderer import render_enhanced_manim as render
    else:
        raise ValueError(f'Unknown illustrated renderer: {renderer}')
    return render(board,output,narration=narration,reporter=reporter,preview_only=preview_only)
