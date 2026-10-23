"""Data-only storyboard, quality, cache-only comparison and adapter regressions."""
import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
import soundfile
from pydantic import ValidationError
from src.storyboard.assets import asset_path, validate_assets
from src.storyboard.models import Storyboard, NarrationCue, Point, VisualObject
from src.storyboard.motion import object_state, path_point, scene_at
from src.storyboard.validation import validate_storyboard, quality_issues, fit_text
from src.storyboard.water_cycle import water_scene, PHASES
from src.storyboard.themes import load_theme
from src.storyboard.compiler import compile_storyboard
from src.storyboard.cached_preview import build_cached_preview
from src.tts import AudioSegment, NarrationTiming
from src.video.remotion_renderer import render_data
from src.video.storyboard_renderer import SceneReporter,mux_shared_audio
from src.video.illustrated_pipeline import join_narration


def fixed_board(seconds=12):
    scenes=[]
    for phase in PHASES:
        cue=NarrationCue(id=phase+'-cue',text=f'This demonstrates {phase} in the water cycle.',
            start=0,duration=seconds,concepts=[phase])
        scenes.append(water_scene(phase,phase,[cue],load_theme()))
    return Storyboard(title='The water cycle',topic='water_cycle',required_concepts=list(PHASES),scenes=scenes)


class StoryboardTests(unittest.TestCase):
    def test_fixed_water_storyboard_assets_and_all_concepts_validate(self):
        board=validate_storyboard(fixed_board())
        validate_assets()
        self.assertEqual(board.duration,48)
        self.assertEqual([s.id for s in board.scenes],list(PHASES))
        self.assertEqual(quality_issues(board),[])
        self.assertEqual(Storyboard.model_validate_json(board.model_dump_json()),board)

    def test_missing_concepts_and_layout_repetition_are_actionable(self):
        board=fixed_board()
        board.scenes.pop()
        board.scenes[1].layout_justification=''
        issues=' '.join(quality_issues(board))
        self.assertIn('Missing essential concept: collection',issues)
        self.assertIn('continuity justification',issues)

    def test_repeated_explanations_are_flagged_not_deleted(self):
        board=fixed_board()
        original='Solar energy lets liquid water molecules escape into the air as invisible vapour.'
        board.scenes[0].narration[0].text=original
        board.scenes[1].narration[0].text=original
        self.assertIn('repeated educational explanation',' '.join(quality_issues(board)))
        self.assertEqual(board.scenes[1].narration[0].text,original)

    def test_static_or_irrelevant_motion_does_not_cover_narration(self):
        board=fixed_board()
        for m in board.scenes[0].motions:
            m.loop=False
            m.duration=.1
        self.assertIn('lacks relevant visual demonstration',' '.join(quality_issues(board)))

    def test_motion_outside_a_short_cue_cannot_claim_visual_coverage(self):
        board=fixed_board(6)
        scene=board.scenes[0]
        first=scene.narration[0]
        first.duration=3
        scene.narration.append(first.model_copy(update={'id':'second-cue','start':3}))
        for motion in scene.motions:
            motion.start=3; motion.duration=1; motion.loop=False
        self.assertIn(f'{first.id}: narration lacks relevant visual demonstration',' '.join(quality_issues(board)))
        board=fixed_board()
        for m in board.scenes[0].motions: m.concepts=['unrelated']
        self.assertIn('lacks relevant visual demonstration',' '.join(quality_issues(board)))

    def test_text_bounds_overlap_contrast_and_readable_minimum(self):
        board=fixed_board()
        label=next(o for o in board.scenes[0].objects if o.kind=='label')
        label.x=0
        label.color='#FFFFFF'
        text=' '.join(quality_issues(board))
        self.assertIn('safe frame boundaries',text)
        self.assertIn('contrast',text.lower())
        with self.assertRaises(ValidationError): label.model_validate({**label.model_dump(),'font_size':12})
        label.x=.20
        other=label.model_copy(update={'id':'overlapping-label'})
        board.scenes[0].objects.append(other)
        self.assertIn('overlapping readable labels',' '.join(quality_issues(board)))
        with self.assertRaises(ValueError): fit_text('A long explanation '*30,.1,.05)

    def test_unknown_assets_nonfinite_values_and_executable_fields_rejected(self):
        for asset in ('../../private','https://example.com/cloud.svg'):
            with self.assertRaises(ValueError): asset_path(asset)
        obj=fixed_board().scenes[0].objects[0].model_dump()
        with self.assertRaises(ValidationError): VisualObject.model_validate({**obj,'x':float('nan')})
        with self.assertRaises(ValidationError): VisualObject.model_validate({**obj,'python':'print(1)'})

    def test_active_svg_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for content in ('<script>alert(1)</script>', '<path onclick="alert(1)"/>', '<image href="https://example.com/secret"/>'):
                (root/'bad.svg').write_text(f'<svg viewBox="0 0 100 100">{content}</svg>')
                with patch('src.storyboard.assets.ASSET_ROOT',root),patch('src.storyboard.assets.CATALOG',{'bad':'bad.svg'}):
                    with self.assertRaises(ValueError): validate_assets()

    def test_bad_references_out_of_order_cues_and_motion_overflow_rejected(self):
        data=fixed_board().model_dump()
        for mutate in (
            lambda s:s['motions'][0].update(object_id='missing'),
            lambda s:s['motions'][0].update(duration=100),
            lambda s:s['narration'][0].update(start=1),
        ):
            broken=copy.deepcopy(data)
            mutate(broken['scenes'][0])
            with self.assertRaises(ValidationError): Storyboard.model_validate(broken)

    def test_scene_order_and_state_are_frame_addressable(self):
        board=fixed_board()
        self.assertEqual([scene_at(board,t)[1].id for t in (0,12,24,36)],list(PHASES))
        self.assertEqual(scene_at(board,1)[1].id,'evaporation')
        self.assertEqual(path_point([Point(x=0,y=0),Point(x=.5,y=1),Point(x=1,y=0)],.5),(.5,.5))
        scene=board.scenes[0]
        particle=next(o for o in scene.objects if o.id=='vapor-0')
        self.assertLess(object_state(particle,scene.motions,2)['y'],object_state(particle,scene.motions,0)['y'])

    def test_themes_and_remotion_receive_same_text_without_truncation(self):
        for theme in ('daylight','ocean','chalk'):
            board=fixed_board()
            board.theme=load_theme(theme)
            # Recompile template colors using the requested theme.
            board.scenes=[water_scene(s.id,s.id,s.narration,board.theme) for s in board.scenes]
            validate_storyboard(board)
            props=render_data(board)
            for scene in board.scenes:
                for cue in scene.narration:
                    self.assertEqual(props['textLayouts'][scene.id][cue.id]['text'].split(),cue.text.split())

    def test_joined_audio_cache_reuse_preserves_order_and_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            segments=[]
            for i in range(2):
                path=root/f'{i}.wav'
                soundfile.write(path,np.full(24000,100*(i+1),dtype=np.int16),24000,subtype='PCM_16')
                segments.append(AudioSegment(path,1))
            result=join_narration(segments,root/'cache')
            mtime=result.stat().st_mtime_ns
            self.assertEqual(join_narration(segments,root/'cache'),result)
            self.assertEqual(mtime,result.stat().st_mtime_ns)
            self.assertNotEqual(join_narration(list(reversed(segments)),root/'cache'),result)
            samples,_=soundfile.read(result,dtype='int16')
            self.assertEqual((samples[0],samples[-1]),(100,200))

    def test_overview_preserves_complete_narration_and_summary(self):
        from src.storyboard.water_cycle import water_overview
        cues=[NarrationCue(id=f'cue-{i}',text=text,start=i*3,duration=3,concepts=['overview']) for i,text in enumerate((
            'Solar heat begins evaporation from lakes.', 'Condensation gathers tiny droplets inside clouds.',
            'Rain falls as precipitation over land.', 'Collection returns the water to lakes and oceans.'))]
        scene=water_overview('summary',cues,load_theme(),summary=True)
        board=Storyboard(title='The water cycle',topic='water_cycle',required_concepts=list(PHASES),scenes=[scene])
        validate_storyboard(board)
        self.assertEqual(scene.title,'Cycle summary')
        self.assertEqual([c.text for c in scene.narration],[c.text for c in cues])
        self.assertEqual(scene.duration,12)

    def test_generic_process_is_not_water_specific_and_refuses_decorative_motion(self):
        from src.storyboard.compiler import flow_scene
        cue=NarrationCue(id='cue',text='Electric current flows around the circuit.',start=0,duration=6,concepts=['circuit'])
        scene=flow_scene({'title':'A circuit','type':'process','steps':['Battery supplies energy','Current flows in a loop','Lamp transfers energy']},[cue],load_theme(),0)
        validate_storyboard(Storyboard(title='Electric circuits',topic='circuits',required_concepts=['circuit'],scenes=[scene]))
        with self.assertRaisesRegex(ValueError,'decorative motion'):
            flow_scene({'title':'One idea','type':'title'},[cue],load_theme(),0)

    def test_math_stays_with_existing_manim_renderer(self):
        scene={'title':'An equation','type':'equation','narration_text':'An equation.','duration':3,'equation':'f(x)=x^2'}
        audio=AudioSegment(Path('unused.wav'),3,(NarrationTiming('An equation.',0,3),))
        board=compile_storyboard({'title':'Math','scenes':[scene]},[audio])
        self.assertEqual(board.scenes[0].native_scene.equation,'f(x)=x^2')
        scene.update(type='process',steps=['Compute g=2x','Update the parameter'])
        scene.pop('equation')
        board=compile_storyboard({'title':'Math','scenes':[scene]},[audio])
        self.assertIsNotNone(board.scenes[0].native_scene)

    def test_compiler_does_not_weaken_required_concepts_for_incomplete_lesson(self):
        scene={'title':'Evaporation','type':'process','steps':['Heat water','Vapour rises'],'duration':12,'narration_text':'Warm water evaporates.'}
        audio=AudioSegment(Path('unused.wav'),12,(NarrationTiming('Warm water evaporates.',0,12),))
        with self.assertRaisesRegex(ValueError,'Missing essential concept: collection'):
            compile_storyboard({'title':'The hydrological cycle','scenes':[scene]},[audio])

    def test_embedded_manim_events_are_reported_from_actual_output(self):
        reporter=Mock()
        proxy=SceneReporter(reporter,fixed_board())
        proxy.verbose_line('Animation 0: 25% {"storyboard_scene":2}')
        reporter.scene_started.assert_called_once_with(2,4,'Condensation')

    def test_mux_failure_never_overwrites_previous_video(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'keep.mp4'; output.write_bytes(b'original validated video')
            probe={'format':{'duration':'12'}}
            with patch('src.video.storyboard_renderer.probe_video',return_value=probe),patch('src.video.storyboard_renderer._run_streaming',side_effect=RuntimeError('encoder failed')):
                with self.assertRaisesRegex(RuntimeError,'encoder failed'):
                    mux_shared_audio(Path('silent.mp4'),Path('audio.wav'),output,12)
            self.assertEqual(output.read_bytes(),b'original validated video')

    def test_mux_refuses_incomplete_audio_before_encoding(self):
        with tempfile.TemporaryDirectory() as directory,patch('src.video.storyboard_renderer.probe_video',return_value={'format':{'duration':'1'}}),patch('src.video.storyboard_renderer._run_streaming') as encode:
            with self.assertRaisesRegex(RuntimeError,'no content will be cut'):
                mux_shared_audio(Path('silent.mp4'),Path('audio.wav'),Path(directory)/'out.mp4',12)
            encode.assert_not_called()

    def test_cache_only_benchmark_reuses_exact_wave_samples_without_llm_or_tts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            cache=root/'cache'; cache.mkdir()
            scenes=[]
            sources=[]
            for i,phase in enumerate(PHASES):
                text={
                    'evaporation':'Solar energy drives evaporation as liquid water escapes into the air.',
                    'condensation':'Higher in the atmosphere cooling causes condensation and forms cloud droplets.',
                    'precipitation':'Once clouds become heavy precipitation returns water to the ground as rain.',
                    'collection':'Rivers transport runoff downhill and collection stores water in lakes and oceans.',
                }[phase]
                samples=np.full((24000*12,1),100*(i+1),dtype=np.int16)
                path=cache/f'timed_{phase}.wav'
                soundfile.write(path,samples,24000,subtype='PCM_16')
                path.with_suffix('.json').write_text(json.dumps([asdict(NarrationTiming(text,0,12))]))
                sources.append(samples)
                scenes.append({'title':phase.title(),'narration_text':text})
            lesson=root/'lesson.json'; lesson.write_text(json.dumps({'title':'Water cycle','scenes':scenes}))
            before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in cache.iterdir()}
            with patch('src.tts.KokoroTTS.synthesize',side_effect=AssertionError('No new TTS')), patch('src.planner.LessonPlanner.plan',side_effect=AssertionError('No LLM')):
                board,audio,_=build_cached_preview(lesson,cache,root/'out')
            actual,rate=soundfile.read(audio,dtype='int16',always_2d=True)
            np.testing.assert_array_equal(actual,np.concatenate(sources))
            self.assertEqual(rate,24000)
            self.assertEqual(board.duration,48)
            self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in cache.iterdir()})

    @unittest.skipUnless(shutil.which('node'),'Node is optional')
    def test_python_js_motion_parity(self):
        scene=fixed_board().scenes[2]
        obj=next(o for o in scene.objects if o.id=='rain-3')
        inputs={'object':obj.model_dump(),'motions':[m.model_dump() for m in scene.motions]}
        module=(Path(__file__).resolve().parents[1]/'experiments/remotion/src/state.mjs').as_uri()
        script=f"import {{objectState}} from {json.dumps(module)}; const d={json.dumps(inputs)}; console.log(JSON.stringify([0,.5,2,11].map(t=>objectState(d.object,d.motions,t))));"
        actual=json.loads(subprocess.check_output(['node','--input-type=module','-e',script]))
        for time,state in zip((0,.5,2,11),actual):
            expected=object_state(obj,scene.motions,time)
            for key in expected: self.assertAlmostEqual(state[key],expected[key],places=10)
