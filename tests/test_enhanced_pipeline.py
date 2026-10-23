"""Primary renderer integration: lesson meaning, exact ordering and cache reuse."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from src.storyboard.compiler import compile_storyboard
from src.storyboard.cache import cached_storyboard
from src.storyboard.themes import load_theme
from src.storyboard.validation import validate_storyboard,header_box
from src.tts import AudioSegment,NarrationTiming
from src.duration import prepare_audio_lesson
from src.video.timeline import prepare_visual_lesson,narration_units
from src.video.enhanced_renderer import _groups,render_enhanced_manim,write_preview_index

FIXTURES=Path(__file__).parent/'fixtures/multisubject'


def prepared(subject):
    lesson=prepare_visual_lesson(json.loads((FIXTURES/f'{subject}.json').read_text()))
    segments=[]
    for scene in lesson['scenes']:
        pairs=[(i,unit) for i,event in enumerate(scene['visual_timeline']) for unit in narration_units(event['narration_text'])]
        duration=12/len(pairs)
        timings=tuple(NarrationTiming(text,i*duration,duration,source) for i,(source,text) in enumerate(pairs))
        segments.append(AudioSegment(Path('unused.wav'),12,timings))
    result=prepare_audio_lesson(lesson,segments,Mock())
    return result.lesson,result.audio_segments


class EnhancedPipelineTests(unittest.TestCase):
    def test_primary_output_preserves_legacy_json_sidecar(self):
        from src.video.illustrated_pipeline import render_illustrated_lesson
        lesson,audio=prepared('networking')
        board=compile_storyboard(lesson,audio)
        with tempfile.TemporaryDirectory() as directory,patch('src.video.illustrated_pipeline.cached_storyboard',return_value=board),patch('src.video.enhanced_renderer.render_enhanced_manim') as render:
            output=Path(directory)/'lesson.mp4'
            render_illustrated_lesson(lesson,output,audio_segments=audio,preview_only=True)
            self.assertEqual(json.loads(output.with_suffix('.lesson.json').read_text()),lesson)
            self.assertTrue(output.with_suffix('.storyboard.json').exists())
            self.assertTrue(render.call_args.kwargs['preview_only'])

    def test_preview_gallery_ignores_old_frames_after_template_changes(self):
        lesson,audio=prepared('networking')
        board=compile_storyboard(lesson,audio)
        board.provenance['timing_source']='estimated silent preview; no audio'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);frames={}
            for scene in board.scenes:
                folder=root/scene.id;folder.mkdir()
                (folder/'000-stale.png').write_bytes(b'old')
                frames[scene.id]=[]
                for i in range(len(scene.narration)):
                    file=folder/f'current-{i}.png';file.write_bytes(b'current');frames[scene.id].append(file)
            write_preview_index(board,root,frames)
            manifest=json.loads((root/'manifest.json').read_text())
            self.assertTrue(all('current-' in row['frame'] for row in manifest))
            self.assertIn('Estimated silent timing',(root/'index.html').read_text())

    def test_water_split_keeps_original_phase_indices(self):
        import numpy as np
        import soundfile
        texts=['First water warms and starts to rise.','The water rises into the air.',
               'Now the vapor cools higher above.','Small droplets form inside the cloud.',
               'Next the drops fall toward the ground.','More falling drops reach the surface.',
               'Finally water gathers near the river.','The river returns water to the lake.']
        source={'title':'The water cycle','scenes':[{'type':'process','title':'Follow the phases','duration':40,
            'learning_objective':'Follow the water cycle in four ordered phases.',
            'steps':['Evaporation','Condensation','Precipitation','Collection'],
            'narration_text':' '.join(texts),'visual_timeline':[{
                'narration_text':text,'start_time':i*5,'animation_duration':5,
                'parameters':{'step_index':i//2}} for i,text in enumerate(texts)]}]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.wav';soundfile.write(path,np.zeros(8000*64),8000)
            audio=AudioSegment(path,64,tuple(NarrationTiming(text,i*8,8,i) for i,text in enumerate(texts)))
            lesson=prepare_visual_lesson(source)
            split=prepare_audio_lesson(lesson,[audio],Mock())
            board=compile_storyboard(split.lesson,split.audio_segments)
            self.assertEqual(len(board.scenes),2)
            self.assertEqual([c.step_index for s in board.scenes for c in s.narration],[0,0,1,1,2,2,3,3])
            self.assertEqual([c.concepts[0] for s in board.scenes for c in s.narration],
                             ['evaporation']*2+['condensation']*2+['precipitation']*2+['collection']*2)

    def test_long_legacy_titles_reflow_without_shrinking_or_truncation(self):
        box=header_box('Why the received signal changes')
        self.assertGreater(box[3],.085)
        lesson=prepare_visual_lesson(json.loads(Path('examples/sample_lesson.json').read_text()))
        audio=[]
        for scene in lesson['scenes']:
            units=narration_units(scene['narration_text'])
            length=float(scene['duration'])/len(units)
            audio.append(AudioSegment(Path('unused'),float(scene['duration']),tuple(NarrationTiming(text,i*length,length,i) for i,text in enumerate(units))))
        board=compile_storyboard(lesson,audio)
        self.assertEqual(board.scenes[1].title,'Why the received signal changes')
        self.assertFalse(any(s.template=='signal_waveform' for s in board.scenes),'A generic sine must not be substituted for Rayleigh fading.')

    def test_multiple_subjects_with_identical_titles_have_distinct_templates(self):
        selections={}
        for subject in ('water','math','networking','signals'):
            lesson,audio=prepared(subject)
            board=compile_storyboard(lesson,audio)
            validate_storyboard(board)
            selections[subject]=[s.template for s in board.scenes]
            self.assertEqual([s.id for s in board.scenes],[s['scene_id'] for s in lesson['scenes']])
            self.assertEqual(' '.join(c.text for s in board.scenes for c in s.narration).split(),
                ' '.join(s['narration_text'] for s in lesson['scenes']).split())
            self.assertEqual(board.duration,sum(a.duration for a in audio))
            self.assertEqual(board.scenes[-1].purpose,'summarize')
        self.assertNotEqual(selections['water'],selections['networking'])
        self.assertNotEqual(selections['math'],selections['signals'])
        self.assertTrue(any(t.startswith('manim_') for t in selections['math']))
        self.assertTrue(any('network' in t for t in selections['networking']))
        self.assertTrue(any('signal' in t for t in selections['signals']))

    def test_bound_phrase_indices_preserve_process_step_and_source_ids(self):
        lesson,audio=prepared('networking')
        board=compile_storyboard(lesson,audio)
        for source,scene,segment in zip(lesson['scenes'],board.scenes,audio):
            for i,cue in enumerate(scene.narration):
                event=source['visual_timeline'][i]
                self.assertEqual(cue.source_event_id,event['source_event_id'])
                self.assertEqual(cue.step_index,event.get('parameters',{}).get('step_index'))
                self.assertAlmostEqual(cue.start,segment.timings[i].start)

    def test_storyboard_cache_reused_and_not_replaced_by_invalid_recompile(self):
        lesson,audio=prepared('networking')
        theme=load_theme()
        with tempfile.TemporaryDirectory() as directory,patch('src.storyboard.cache.implementation_digest',return_value='compiler-v2'):
            root=Path(directory)
            with patch('src.storyboard.cache.compile_storyboard',wraps=compile_storyboard) as compile:
                first=cached_storyboard(lesson,audio,theme,cache_dir=root)
                second=cached_storyboard(lesson,audio,theme,cache_dir=root)
                self.assertEqual(first,second)
                self.assertEqual(compile.call_count,1)
            path=next(root.glob('*.json'))
            original=path.read_bytes()
            with patch('src.storyboard.cache.compile_storyboard',side_effect=ValueError('invalid revision')):
                with self.assertRaises(ValueError): cached_storyboard(lesson,audio,theme,cache_dir=root,force=True)
            self.assertEqual(path.read_bytes(),original)

    def test_native_and_illustrated_groups_never_sort_or_drop_scenes(self):
        math_lesson,math_audio=prepared('math')
        network,network_audio=prepared('networking')
        scenes=[math_lesson['scenes'][0],network['scenes'][0],math_lesson['scenes'][1]]
        # The fixtures share generic titles and index-derived scene IDs; assign
        # distinct stable parents for this deliberately mixed-source test.
        for i,scene in enumerate(scenes):
            scene['scene_id']=scene['parent_scene_id']=f'mixed-{i}'
        board=compile_storyboard({'title':'Mixed concepts','scenes':scenes},[math_audio[0],network_audio[0],math_audio[1]])
        groups=_groups(board)
        self.assertEqual([g['native'] for g in groups],[True,False,True])
        self.assertEqual([s.id for g in groups for s in g['scenes']],['mixed-0','mixed-1','mixed-2'])

    def test_failed_preflight_blocks_all_movie_rendering(self):
        lesson,audio=prepared('math')
        board=compile_storyboard(lesson,audio)
        with tempfile.TemporaryDirectory() as directory,patch('src.video.enhanced_renderer.render_lesson',side_effect=ValueError('LaTeX failed')) as native,patch('src.video.enhanced_renderer._run_streaming') as encode:
            output=Path(directory)/'keep.mp4';output.write_bytes(b'previous good video')
            with self.assertRaisesRegex(ValueError,'LaTeX failed'): render_enhanced_manim(board,output)
            self.assertTrue(native.call_args.kwargs['preview_only'])
            encode.assert_not_called()
            self.assertEqual(output.read_bytes(),b'previous good video')
