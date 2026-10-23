"""Pure, deterministic visual selection from educational content, never titles.

Template IDs are an integration contract: ``native_math`` and ``water_*`` are
delegated to their existing compilers; the other IDs are handled by diagrams.
Context can disambiguate local evidence, but cannot select a topic by itself.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import re

from .assets import CATALOG


@dataclass
class Selection:
    template: str
    reason: str
    assets: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)


def _mapping(scene):
    return scene.model_dump() if hasattr(scene, 'model_dump') else dict(scene)


def _strings(value):
    if isinstance(value, str):
        if value.strip():
            yield value.strip()
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if key not in {'title', 'id', 'scene_id', 'source_id', 'code', 'python'}:
                yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


CONTENT_FIELDS = (
    'learning_objective', 'important_concepts', 'narration_text', 'narration',
    'steps', 'bullets', 'left', 'right', 'relationship', 'equation', 'latex',
    'display', 'display_text', 'display_fields', 'subtitle', 'labels', 'concepts',
)
DISPLAY_FIELDS = (
    'equation', 'latex', 'steps', 'bullets', 'left', 'right', 'relationship',
    'display', 'display_text', 'display_fields', 'labels',
)


def _normal(text):
    return re.sub(r'[_\-–]', ' ', text.casefold())


def _has(text, pattern):
    return re.search(r'(?<!\w)(?:' + pattern + r')(?!\w)', text) is not None


def _unique(values):
    return list(dict.fromkeys(value for value in values if value.strip()))


def _assets(text):
    """Literal natural referents only: computing clouds and energy are not icons."""
    rules = {
        'sun': r'sun|sunlight|solar (?:heat|radiation|energy)',
        'lake': r'lakes?',
        'rain': r'rain|rainfall|raining',
        'river': r'rivers?',
        'mountain': r'mountains?',
    }
    result = [name for name, pattern in rules.items() if name in CATALOG and _has(text, pattern)]
    # A bare "cloud" is ambiguous. Require weather evidence in the same scene.
    if ('cloud' in CATALOG and _has(text, r'clouds?|cloud droplets')
            and _has(text, r'weather|rain|rainfall|water|vapou?r|condensation|droplets|atmosphere|sky')
            and not _has(text, r'cloud computing|cloud storage|cloud service|cloud infrastructure')):
        result.append('cloud')
    # Choose a small optional illustration set; educational concepts and text
    # are never shortened to make room for catalog artwork.
    return result[:2]


def select_visual(scene, context='') -> Selection:
    """Choose an explanatory template without consulting scene/lesson titles.

    ``scene`` may be a lesson dictionary or a Pydantic lesson scene. All returned
    asset IDs belong to the fixed local catalog. No supplied paths, expressions,
    visual-template hints, or code are executed.
    """
    scene = _mapping(scene)
    raw = ' '.join(part for key in CONTENT_FIELDS for part in _strings(scene.get(key)))
    text = _normal(raw)
    surrounding = _normal(context) if isinstance(context, str) else ''
    combined = f'{text} {surrounding}'
    display = ' '.join(part for key in DISPLAY_FIELDS for part in _strings(scene.get(key)))
    concepts = _unique(_strings(scene.get('important_concepts')))
    if not concepts:
        concepts = _unique(part for key in ('steps', 'bullets', 'concepts', 'labels')
                           for part in _strings(scene.get(key)))
    if not concepts:
        concepts = _unique(_strings(scene.get('learning_objective')))
    assets = _assets(text)

    def choose(template, reason, inferred=()):
        return Selection(template, reason, assets.copy(), concepts.copy() or list(inferred))

    kind = scene.get('type', '')
    math_display = re.search(r'\\[A-Za-z]+|\$[^$]+\$|[=≠≤≥∑∫√²³]|\w\s*\^\s*[-\w{]', display)
    if (kind in {'equation', 'graph', 'worked_example', 'math'}
            or scene.get('visual_type') in {'equation', 'graph', 'worked_example'}
            or scene.get('equation') or scene.get('latex') or math_display):
        return choose('native_math', 'Scene type or displayed mathematical notation requires native math.', ('mathematics',))
    # Water requires evidence in this scene. A lesson-level water context must
    # not turn CO2 condensation or a biological life cycle into a water scene.
    water_context = _has(text, r'water|hydrolog(?:ical|ic)|rain|rainfall|runoff|groundwater|lakes?|rivers?|oceans?')
    phases = []
    for phase, pattern in (
        ('evaporation', r'evaporation|evaporat(?:e|es|ing)'),
        ('condensation', r'condensation|condens(?:e|es|ing)'),
        ('precipitation', r'precipitation|rainfall'),
        ('collection', r'collection|runoff|groundwater|water accumulat(?:es|ion)'),
    ):
        if _has(text, pattern) and water_context:
            phases.append(phase)
    comparison = (kind == 'comparison' or scene.get('visual_type') == 'comparison'
                  or bool(scene.get('left') and scene.get('right'))
                  or _has(text, r'compare|comparison|versus|vs\.?|differences? between'))
    if water_context and comparison and _has(text, r'vapou?r|gas') and _has(text, r'droplets?|liquid'):
        return choose('water_states', 'Local evidence contrasts gaseous water vapor with liquid droplets.', ('water vapor', 'liquid droplets'))
    if water_context and (len(phases) > 1 or _has(text, r'water cycle|hydrolog(?:ical|ic) cycle')):
        if kind in {'summary', 'conclusion'}:
            return choose('water_summary', 'A concluding recap connects the locally stated water phases.', phases or ('water cycle',))
        if scene.get('steps') or kind == 'process':
            return choose('water_process', 'Authored ordered steps follow several water-cycle phases.', phases or ('water cycle',))
        return choose('water_overview', 'Local content connects the water cycle or several water phases.', phases or ('water cycle',))
    if phases:
        return choose('water_' + phases[0], f'Local content explains water-cycle {phases[0]}.', phases)

    if kind in {'title', 'intro', 'introduction', 'summary', 'conclusion'}:
        return choose('concept_map', 'Introductory or concluding content connects the supplied concepts.', ('overview',))

    gradient = _has(combined, r'gradient(?: descent)?|derivatives?|optimization|optimisation|loss function')
    hint = _normal(str(scene.get('visual_template', '')))
    if gradient and (_has(text, r'learning rates?|convergence|converges?|tangents?|gradient descent')
                     or hint in {'learning rates', 'convergence', 'tangent', 'descent', 'derivation', 'numerical'}):
        return choose('native_math', 'Gradient context with a tangent, update, or convergence demonstration needs native math.', ('gradient descent',))

    network = _has(combined, r'tcp(?:/ip)?|internet protocol|routers?|network(?:ing)?|packet rout(?:ing|es)|ip packets?')
    packet_evidence = _has(text, r'packets?|tcp(?:/ip)?|routers?|routing|ack|acknowledg(?:e|ement|ment|ments|ements)|retransmi(?:t|ssion)')
    if network and packet_evidence:
        if comparison:
            return choose('network_comparison', 'Compare forward data and reverse endpoint acknowledgment with directed packet paths.', ('data packet', 'acknowledgment'))
        if _has(text, r'retransmi(?:t|ts|ssion)|sequence numbers?|timeout|lost packets?|missing (?:data|bytes)|ordered delivery'):
            # Ordered delivery on its own can be background context for a
            # routing scene; explicit sequence/retry evidence is decisive.
            if _has(text, r'retransmi(?:t|ts|ssion)|sequence numbers?|timeout|lost packets?|missing (?:data|bytes)'):
                return choose('network_reliability', 'Sequence ranges and retransmission explain recovery and ordered delivery.', ('sequence numbers', 'retransmission', 'ordered delivery'))
        if _has(text, r'ack|acknowledg(?:e|ement|ment|ments|ements)|retransmi(?:t|ssion)|reliable delivery|lost packets?'):
            return choose('network_ack', 'Acknowledgments or retransmission explain reliable packet delivery.', ('packet', 'acknowledgment'))
        return choose('network_routing', 'Networking content follows packets between endpoints through routing.', ('packet', 'routing'))

    neural = _has(text, r'neural networks?|neurons?|perceptrons?|hidden layers?|backpropagation')
    if neural or (_has(combined, r'machine learning|deep learning') and _has(text, r'input layers?|output layers?|weights|activations?')):
        return choose('neural_network', 'Neural layers, weights, or activations describe connected computation.', ('input', 'hidden layer', 'output'))

    signal_context = _has(combined, r'signals?|waveforms?|frequenc(?:y|ies)|electrical|voltage|audio|digital signal processing')
    if signal_context and _has(text, r'cutoff|cut off'):
        return choose('signal_response', 'Cutoff choice is explained with frequency attenuation and its tradeoff.', ('frequency', 'attenuation', 'cutoff'))
    if signal_context and _has(text, r'delay|phase shift|latency'):
        return choose('signal_delay', 'A time-shifted signal explains delay relative to the input.', ('input signal', 'delay', 'output signal'))
    if signal_context and comparison:
        return choose('signal_comparison', 'Compare the stated signals using sampled input and output curves.', ('input waveform', 'output waveform'))
    if signal_context and _has(text, r'low ?pass|cutoff|cut off|filter(?:ing|s)?|attenuat(?:ion|e|es)'):
        return choose('signal_lowpass', 'Signal content explains filtering or frequency attenuation.', ('input signal', 'low-pass filter', 'output signal'))
    if signal_context and _has(text, r'waveforms?|sine|sinusoid(?:al)?|oscillat(?:ion|ions|e|es)|frequency|samples?|sampling'):
        return choose('signal_waveform', 'Signal content describes amplitude, frequency, or sampled waveforms.', ('signal', 'time'))

    if comparison:
        return choose('comparison', 'The content explicitly compares alternatives or their relationship.', ('comparison',))
    natural = _has(text, r'biolog(?:y|ical)|ecolog(?:y|ical)|natural|plants?|carbon|nitrogen|rocks?|life|seasons?|organisms?|butterfl(?:y|ies)|frogs?')
    if natural and _has(text, r'cycles?|cyclic|returns? to|repeats?|stages? of life'):
        return choose('natural_cycle', 'Natural stages explicitly repeat or return to their starting state.', ('cycle',))
    if kind == 'process' or scene.get('steps') or _has(text, r'sequence|then|stages?|procedure|process'):
        return choose('process', 'Ordered content explains a process through connected stages.', ('process',))
    return choose('concept_map', 'The supplied concepts need explicit relationships in a concept map.', ('concept',))
