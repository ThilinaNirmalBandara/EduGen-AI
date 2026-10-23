"""Existing CLI defaults and new opt-in dispatch do not call real services."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import main


class StoryboardCLITests(unittest.TestCase):
    def run_cli(self,extra):
        with tempfile.TemporaryDirectory() as directory, patch('sys.argv',['main.py','--lesson','examples/sample_lesson.json','--no-voice',*extra]), patch.object(main,'check_startup'), patch.object(main,'configure_logging',return_value=(__import__('logging').getLogger('cli-test'),Path(directory)/'run.log')), patch.object(main,'ExecutionReporter'):
            return main.main()

    def test_default_path_is_enhanced_manim_and_never_requires_node(self):
        with patch('src.video.illustrated_pipeline.render_illustrated_lesson') as render,patch.object(main,'render_lesson') as legacy,patch('src.video.remotion_renderer.check_remotion',side_effect=AssertionError('Not needed')):
            self.assertEqual(self.run_cli([]),0)
            render.assert_called_once()
            self.assertEqual(render.call_args.kwargs['renderer'],'manim')
            legacy.assert_not_called()

    def test_legacy_renderer_remains_explicitly_available(self):
        with patch.object(main,'render_lesson') as legacy,patch('src.video.illustrated_pipeline.render_illustrated_lesson') as enhanced:
            self.assertEqual(self.run_cli(['--renderer','legacy']),0)
            legacy.assert_called_once()
            enhanced.assert_not_called()

    def test_illustrated_path_is_explicit_and_separate(self):
        with patch.object(main,'render_lesson') as legacy,patch('src.video.illustrated_pipeline.render_illustrated_lesson') as illustrated:
            self.assertEqual(self.run_cli(['--renderer','illustrated','--theme','ocean']),0)
            legacy.assert_not_called()
            self.assertEqual(illustrated.call_args.kwargs['theme'],'ocean')

    def test_missing_remotion_dependency_fails_before_planning_or_rendering(self):
        with patch('src.video.remotion_renderer.check_remotion',side_effect=RuntimeError('Run npm ci')),patch.object(main,'render_lesson') as render:
            self.assertEqual(self.run_cli(['--renderer','remotion']),1)
            render.assert_not_called()

    def test_prompt_uses_primary_renderer_after_duration_validation(self):
        import json
        from src.video.timeline import prepare_visual_lesson,narration_units
        from src.tts import AudioSegment,NarrationTiming
        lesson=prepare_visual_lesson(json.loads(Path('tests/fixtures/multisubject/networking.json').read_text()))
        audio=[]
        for scene in lesson['scenes']:
            pairs=[(i,text) for i,cue in enumerate(scene['visual_timeline']) for text in narration_units(cue['narration_text'])]
            length=12/len(pairs)
            audio.append(AudioSegment(Path('unused.wav'),12,tuple(NarrationTiming(text,i*length,length,cue) for i,(cue,text) in enumerate(pairs))))
        with tempfile.TemporaryDirectory() as directory,patch('sys.argv',['main.py','--prompt','Explain TCP packet routing','--duration','0.8']),patch.object(main,'check_startup'),patch.object(main,'configure_logging',return_value=(__import__('logging').getLogger('cli-test'),Path(directory)/'log')),patch.object(main,'ExecutionReporter'),patch.object(main,'LessonPlanner') as planner,patch.object(main,'KokoroTTS') as tts,patch('src.video.illustrated_pipeline.render_illustrated_lesson') as render:
            planner.return_value.plan.return_value=lesson
            tts.return_value.synthesize_plan.return_value=audio
            self.assertEqual(main.main(),0)
            planner.return_value.revise_for_duration.assert_not_called()
            self.assertEqual(render.call_args.kwargs['renderer'],'manim')
            self.assertEqual(render.call_args.kwargs['prompt'],'Explain TCP packet routing')
            self.assertEqual(len(render.call_args.kwargs['audio_segments']),4)
