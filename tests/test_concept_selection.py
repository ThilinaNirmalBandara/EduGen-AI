"""Local, deterministic selector and diagram-contract regression tests."""
import copy
import json
from pathlib import Path
import unittest

from src.storyboard.assets import CATALOG
from src.storyboard.diagrams import diagram_scene, _signal_samples
from src.storyboard.models import Evidence, NarrationCue, Storyboard, StoryboardScene
from src.storyboard.motion import object_state
from src.storyboard.selection import Selection, select_visual
from src.storyboard.themes import load_theme
from src.storyboard.validation import StoryboardQualityError, validate_storyboard


class ConceptSelectionTests(unittest.TestCase):
    def test_selection_lists_are_independent(self):
        one, two = Selection('process', 'ordered stages'), Selection('concept_map', 'related ideas')
        one.assets.append('sun')
        one.concepts.append('sunlight')
        self.assertEqual(two.assets, [])
        self.assertEqual(two.concepts, [])

    def test_neutral_and_misleading_titles_do_not_choose_the_subject(self):
        scene = {'type': 'process', 'learning_objective': 'Trace TCP/IP packets through routers.',
                 'important_concepts': ['IP packets', 'routers']}
        expected = select_visual(scene)
        for title in ('A closer look', 'Water cycle', 'Gradient descent', 'Cloud computing'):
            with self.subTest(title=title):
                self.assertEqual(select_visual(dict(scene, title=title)), expected)
        self.assertEqual(expected.template, 'network_routing')

    def test_each_content_source_can_supply_subject_evidence(self):
        for key, value in (
            ('learning_objective', 'Trace TCP/IP packets through routers.'),
            ('important_concepts', ['IP packets', 'routers']),
            ('narration_text', 'IP packets travel through routers.'),
            ('display', {'labels': ['IP packets', 'routers']}),
            ('display_fields', {'caption': 'Routers forward IP packets.'}),
        ):
            with self.subTest(field=key):
                self.assertEqual(select_visual({'title': 'A closer look', key: value}).template, 'network_routing')

    def test_explicit_math_uses_native_compilation(self):
        for scene in (
            {'type': 'equation'}, {'type': 'graph'}, {'type': 'worked_example'},
            {'equation': 'x = 2'}, {'latex': r'\frac{1}{2}'},
            {'display': r'\alpha + \beta'}, {'steps': ['Update x = 3']},
            {'display_text': '$x^2$'},
        ):
            with self.subTest(scene=scene):
                self.assertEqual(select_visual(scene).template, 'native_math')

    def test_gradient_templates_need_context_and_summary_stays_a_map(self):
        for concept in ('learning_rates', 'convergence', 'tangent'):
            with self.subTest(concept=concept):
                scene = {'important_concepts': [concept], 'visual_template': concept}
                self.assertNotEqual(select_visual(scene).template, 'native_math')
                self.assertEqual(select_visual(scene, context='gradient descent').template, 'native_math')
        summary = {'type': 'summary', 'important_concepts': ['gradient', 'learning rate', 'convergence'],
                   'bullets': ['Check loss and repeat']}
        self.assertEqual(select_visual(summary).template, 'concept_map')
        self.assertEqual(select_visual(dict(summary, equation='x = 2')).template, 'native_math')

    def test_context_cannot_supply_all_local_subject_evidence(self):
        scene = {'type': 'process', 'steps': ['Prepare the input', 'Inspect the result']}
        self.assertEqual(select_visual(scene, 'water cycle, TCP/IP, gradient descent').template, 'process')

    def test_local_water_evidence_and_phase_variety(self):
        for phase in ('evaporation', 'condensation', 'precipitation', 'collection'):
            with self.subTest(phase=phase):
                self.assertEqual(select_visual({'important_concepts': ['water', phase]}).template, 'water_'+phase)
        self.assertEqual(select_visual({'important_concepts': ['collection', 'sorting']}).template, 'concept_map')
        carbon = {'learning_objective': 'Explain CO2 condensation.', 'important_concepts': ['carbon dioxide', 'condensation']}
        self.assertFalse(select_visual(carbon, 'The water cycle').template.startswith('water_'))
        life = {'learning_objective': 'Explain the butterfly life cycle.', 'steps': ['Egg', 'Larva', 'Pupa', 'Adult']}
        self.assertEqual(select_visual(life, 'Water cycle').template, 'natural_cycle')

    def test_network_and_signal_comparisons_keep_their_domain_visual(self):
        network = {'type': 'comparison', 'left': 'IP packets toward receiver',
                   'right': 'TCP ACK toward sender', 'important_concepts': ['routers', 'TCP acknowledgments']}
        signal = {'type': 'comparison', 'left': 'Input waveform', 'right': 'Output waveform',
                  'learning_objective': 'Compare low-pass signal filtering.'}
        self.assertEqual(select_visual(network).template, 'network_comparison')
        self.assertEqual(select_visual(signal).template, 'signal_comparison')
        self.assertEqual(select_visual({'important_concepts': ['TCP', 'acknowledgment']}).template, 'network_ack')
        self.assertEqual(select_visual({'important_concepts': ['TCP', 'sequence numbers', 'retransmission']}).template,
                         'network_reliability')
        self.assertEqual(select_visual({'learning_objective': 'Explain a neural network hidden layer.'}).template,
                         'neural_network')
        self.assertEqual(select_visual({'important_concepts': ['signal', 'cutoff choice']}).template, 'signal_response')
        self.assertEqual(select_visual({'important_concepts': ['signal', 'delay']}).template, 'signal_delay')
        self.assertEqual(select_visual({'important_concepts': ['signal waveform', 'amplitude']}).template, 'signal_waveform')

    def test_catalog_matching_is_semantic_and_fixed(self):
        computing = select_visual({'learning_objective': 'Cloud computing stores data and transfers energy.'})
        self.assertEqual(computing.assets, [])
        self.assertEqual(select_visual({'important_concepts': ['generic energy']}).assets, [])
        natural = select_visual({'important_concepts': ['sunlight', 'lake', 'plant growth']})
        self.assertEqual(set(natural.assets), {'sun', 'lake'})
        self.assertLessEqual(set(natural.assets), set(CATALOG))
        weather = select_visual({'narration_text': 'Clouds contain water droplets in the sky.'})
        self.assertIn('cloud', weather.assets)
        self.assertEqual(select_visual({'asset': '../../secret', 'title': 'sun'}).assets, [])

    def test_multisubject_fixture_routing(self):
        root = Path(__file__).parent/'fixtures'/'multisubject'
        expected = {
            'water': ['water_overview', 'water_process', 'water_states', 'water_summary'],
            'math': ['native_math', 'native_math', 'native_math', 'concept_map'],
            'networking': ['network_routing', 'network_comparison', 'network_reliability', 'concept_map'],
            'signals': ['signal_lowpass', 'signal_comparison', 'signal_response', 'concept_map'],
        }
        if not root.exists():
            self.skipTest('Main-agent integration fixtures are not installed.')
        for subject, templates in expected.items():
            with self.subTest(subject=subject):
                lesson = json.loads((root/f'{subject}.json').read_text())
                self.assertEqual([select_visual(scene).template for scene in lesson['scenes']], templates)


class DiagramContractTests(unittest.TestCase):
    def compile(self, source, texts=('Follow the stated relationship.',), *, steps=None, selection=None):
        selected = selection or select_visual(source)
        cues = [NarrationCue(id=f'phrase-{i}', text=text, start=i*4, duration=4,
                 concepts=selected.concepts or ['idea'], source_scene_id='source-7',
                 source_event_id=f'event-{i}', step_index=None if steps is None else steps[i],
                 evidence=[Evidence(source_id='reference', uri='local:reference', excerpt='Source excerpt')])
                for i, text in enumerate(texts)]
        before = [cue.model_dump() for cue in cues]
        scene = diagram_scene(source, cues, load_theme(), selected, index=3)
        self.assertEqual([cue.model_dump() for cue in cues], before)
        self.assertEqual([cue.model_dump() for cue in scene.narration], before)
        board = Storyboard(title='Relationships', topic='test', theme=load_theme(),
                           required_concepts=list(dict.fromkeys(concept for cue in cues for concept in cue.concepts)), scenes=[scene])
        validate_storyboard(board)
        self.assertEqual(StoryboardScene.model_validate_json(scene.model_dump_json()), scene)
        return scene

    def test_all_generic_templates_pass_real_quality_validation(self):
        sources = [
            {'title': 'Stages', 'type': 'process', 'steps': ['Measure input', 'Transform input', 'Check output']},
            {'title': 'Related ideas', 'type': 'title', 'important_concepts': ['Data', 'Patterns', 'Predictions']},
            {'title': 'One idea', 'type': 'title'},
            {'title': 'Alternatives', 'type': 'comparison', 'left': 'Option A', 'right': 'Option B', 'relationship': 'Different ways to reach the same goal'},
            {'title': 'Life cycle', 'learning_objective': 'Follow the butterfly life cycle.', 'steps': ['Egg', 'Larva', 'Pupa', 'Adult']},
            {'title': 'Layers', 'important_concepts': ['input layer', 'neural network', 'output layer']},
            {'title': 'Routing', 'important_concepts': ['IP packets', 'routers']},
            {'title': 'Receipt', 'important_concepts': ['TCP', 'acknowledgment']},
            {'title': 'Reliability', 'important_concepts': ['TCP', 'sequence numbers', 'retransmission']},
            {'title': 'Waveform', 'important_concepts': ['signal waveform', 'amplitude']},
            {'title': 'Filtering', 'important_concepts': ['signal', 'low-pass filter']},
            {'title': 'Cutoff', 'important_concepts': ['signal', 'cutoff choice']},
            {'title': 'Delay', 'important_concepts': ['signal', 'delay']},
            {'title': 'Water states', 'type': 'comparison', 'left': 'Water vapor: gas', 'right': 'Droplets: liquid',
             'important_concepts': ['water vapor', 'liquid droplets']},
        ]
        for source in sources:
            with self.subTest(template=select_visual(source).template):
                scene = self.compile(source)
                self.assertLessEqual(len(scene.objects), 120)
                self.assertLessEqual(len(scene.motions), 240)
                self.assertTrue(all(obj.font_size >= 28 for obj in scene.objects if obj.kind == 'label'))
                self.assertTrue(all(2 <= len(obj.points) <= 256 for obj in scene.objects if obj.kind == 'polyline'))

    def test_progressive_cues_preserve_evidence_indices_and_visibility(self):
        source = {'scene_id': 'source-7', 'title': 'Stages', 'type': 'process',
                  'steps': ['Read input', 'Transform input', 'Check output']}
        scene = self.compile(source, ('Check output is mentioned early.', 'Read input is mentioned again.', 'Transform input is mentioned last.'), steps=[0, 1, 2])
        highlights = [obj for obj in scene.objects if obj.id.endswith('current-stage')]
        self.assertEqual([round(obj.y, 3) for obj in highlights], [.25, .495, .74])
        late_label = next(obj for obj in scene.objects if obj.id.endswith('stage-label-2'))
        self.assertEqual(late_label.visible_from, 8)
        self.assertEqual(object_state(late_label, scene.motions, 7)['opacity'], 0)
        self.assertGreater(object_state(late_label, scene.motions, 9)['opacity'], 0)
        for cue in scene.narration:
            related = [motion for motion in scene.motions if motion.narration_ids == [cue.id]]
            self.assertTrue(related)
            self.assertTrue(all(cue.start <= motion.start and motion.start+motion.duration <= cue.start+cue.duration for motion in related))

    def test_timeline_step_mapping_beats_word_matches_and_fallback_is_sequential(self):
        source = {'title': 'Stages', 'type': 'process', 'steps': ['First', 'Second', 'Third'],
                  'visual_timeline': [{'event_id': f'event-{i}', 'parameters': {'step_index': i}} for i in range(3)]}
        texts = ('Third is discussed here.', 'First is discussed here.', 'Second is discussed here.')
        for with_mapping in (True, False):
            data = copy.deepcopy(source)
            if not with_mapping:
                data.pop('visual_timeline')
            scene = self.compile(data, texts)
            self.assertEqual([round(obj.y, 3) for obj in scene.objects if obj.id.endswith('current-stage')], [.25, .495, .74])

    def test_ack_retry_and_routing_are_cue_specific(self):
        source = {'title': 'Packet delivery', 'important_concepts': ['TCP', 'sequence numbers', 'retransmission']}
        scene = self.compile(source, ('An ACK confirms receipt.', 'A timeout triggers retransmission.', 'Routers forward packets.'))
        ack = next(obj for obj in scene.objects if obj.id == 'c0-ack-packet')
        retry = next(obj for obj in scene.objects if obj.id == 'c1-retransmitted-packet')
        self.assertEqual((ack.visible_from, ack.visible_until), (2, 4))
        self.assertGreaterEqual(retry.visible_from, 4)
        ack_motion = next(motion for motion in scene.motions if motion.object_id == ack.id)
        retry_motion = next(motion for motion in scene.motions if motion.object_id == retry.id)
        self.assertGreater(ack_motion.path[0].x, ack_motion.path[-1].x)
        self.assertLess(retry_motion.path[0].x, retry_motion.path[-1].x)
        self.assertEqual(object_state(ack, scene.motions, 4)['opacity'], 0)

    def test_neural_topology_is_shared_without_losing_cues(self):
        scene = self.compile({'title': 'Neural network', 'important_concepts': ['neural network', 'input', 'output']},
                             ('Input begins.', 'Weights act.', 'Activations propagate.', 'Output follows.'))
        self.assertLess(len(scene.objects), 60)
        self.assertEqual(len([obj for obj in scene.objects if obj.id.endswith('activation')]), 4)
        self.assertTrue(any('Illustrative' in obj.text for obj in scene.objects))

    def test_sequence_only_cue_does_not_invent_loss_or_hide_routing(self):
        source = {'title': 'Reliability', 'important_concepts': ['TCP', 'sequence numbers', 'retransmission']}
        scene = self.compile(source, ('Sequence numbers identify byte positions.',
                                      'Routers forward packets and do not acknowledge TCP data.'))
        self.assertFalse(any('lost-packet' in obj.id or 'retransmitted-packet' in obj.id for obj in scene.objects))
        self.assertTrue(any('byte-range' in obj.id for obj in scene.objects))
        self.assertTrue(any(obj.id == 'c1-router' for obj in scene.objects))
        self.assertFalse(any('ack-packet' in obj.id for obj in scene.objects))

    def test_relevant_assets_are_actually_drawn_and_computing_uses_primitives(self):
        natural = {'title': 'Plant growth', 'type': 'title', 'important_concepts': ['sunlight', 'lake', 'plant growth']}
        scene = self.compile(natural)
        self.assertEqual({obj.asset for obj in scene.objects if obj.kind == 'asset'}, set(select_visual(natural).assets))
        cloud = self.compile({'title': 'Cloud computing', 'type': 'title', 'important_concepts': ['cloud computing', 'stored data']})
        self.assertFalse(any(obj.kind == 'asset' for obj in cloud.objects))

    def test_single_idea_keeps_complete_narration_and_no_orbiting_dots(self):
        texts = ('A system contains connected parts.', 'Those parts influence its behavior.')
        scene = self.compile({'title': 'A system', 'type': 'title'}, texts)
        explanations = [obj.text.replace('\n', ' ') for obj in scene.objects if obj.id.endswith('explanation')]
        self.assertEqual(explanations, list(texts))
        self.assertFalse(any(obj.kind == 'circle' for obj in scene.objects))

    def test_signal_curves_are_bounded_numerical_and_labelled(self):
        scene = self.compile({'title': 'Filtering', 'important_concepts': ['signal', 'low-pass filter']})
        self.assertTrue(any('Illustrative' in obj.text for obj in scene.objects))
        curves = [obj for obj in scene.objects if obj.id.endswith('-curve')]
        self.assertEqual([len(obj.points) for obj in curves], [192, 192])
        samples, output, _, illustrative = _signal_samples({}, True)
        self.assertTrue(illustrative)
        self.assertLess(sum(abs(b-a) for a, b in zip(output, output[1:])), sum(abs(b-a) for a, b in zip(samples, samples[1:])))
        supplied = {'sample_rate': 192, 'frequency': 2, 'amplitude': 1, 'high_frequency': 16,
                    'high_amplitude': .35, 'cutoff_frequency': 4, 'sample_count': 256}
        data = {'title': 'Filtering', 'important_concepts': ['signal', 'low-pass filter'], 'animation_params': supplied}
        self.assertFalse(_signal_samples(data, True)[-1])
        self.assertTrue(any(len(obj.points) == 256 for obj in self.compile(data).objects))
        for invalid in ({'sample_count': 257}, {'frequency': '__import__("os").system("false")'}, {'sample_rate': float('nan')}, {'cutoff_frequency': 1000}):
            with self.subTest(invalid=invalid), self.assertRaises(StoryboardQualityError):
                _signal_samples({'animation_params': invalid}, True)

    def test_readability_or_capacity_failure_rejects_without_truncation(self):
        for source in (
            {'title': 'Too many stages', 'type': 'process', 'steps': [f'Stage {i}' for i in range(7)]},
            {'title': 'Long label', 'type': 'title', 'important_concepts': ['unreadable '*150]},
        ):
            before = copy.deepcopy(source)
            with self.assertRaises(StoryboardQualityError):
                self.compile(source)
            self.assertEqual(source, before)


if __name__ == '__main__':
    unittest.main()
