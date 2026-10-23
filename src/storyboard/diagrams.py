"""Explanatory scene graphs, without renderer imports or executable lesson data.

Polyline points are absolute normalized canvas coordinates; their bounding box
is recorded on the object as well. Header/caption centers are reserved at .065
and .90. Labels never shrink below 28px or silently lose words. Cue windows are
half open: [visible_from, visible_until).
"""
from __future__ import annotations

import math
from numbers import Real

from .models import Motion, Point, StoryboardScene, Transition, VisualObject
from .selection import Selection, _has, _mapping, _normal, _strings, _unique
from .validation import StoryboardQualityError, fit_text,header_box

HEADER_Y = .065
CAPTION_Y = .90
MAX_OBJECTS = 120
MAX_CURVE_POINTS = 256


class _Canvas:
    def __init__(self, theme):
        self.theme = theme
        self.objects = []
        self.motions = []
        self.cache = {}

    def begin(self, cue, index):
        self.cue, self.index = cue, index

    def add(self, name, kind, *, shared=True, **data):
        start = data.pop('visible_from', self.cue.start)
        stop = data.pop('visible_until', self.cue.start + self.cue.duration)
        payload = {'kind': kind, 'color': self.theme.accent, **data}
        # Reuse adjacent identical static diagrams. Dynamic highlights and
        # packets remain cue-namespaced, avoiding unbounded repeated topology.
        key = repr(payload)
        previous = self.cache.get(key) if shared else None
        if previous is not None and abs(previous.visible_until - start) < 1e-8:
            previous.visible_until = stop
            previous.concepts = _unique([*previous.concepts, *self.cue.concepts])
            return previous
        obj = VisualObject(id=f'c{self.index}-{name}', concepts=list(self.cue.concepts),
                           visible_from=start, visible_until=stop, **payload)
        if (obj.x - obj.width/2 < .015 - 1e-9 or obj.x + obj.width/2 > .985 + 1e-9
                or obj.y - obj.height/2 < .015 - 1e-9 or obj.y + obj.height/2 > .985 + 1e-9):
            raise StoryboardQualityError(f'{obj.id}: diagram cannot fit inside safe frame bounds.')
        self.objects.append(obj)
        if len(self.objects) > MAX_OBJECTS:
            raise StoryboardQualityError('Diagram exceeds 120 objects. Split the scene; no content was truncated.')
        if shared:
            self.cache[key] = obj
        return obj

    def text(self, name, text, x, y, width, height, size=30, **options):
        wrapped, size = fit_text(text, width, height, font_size=size)
        return self.add(name, 'label', role='label', text=wrapped, x=x, y=y,
                        width=width, height=height, font_size=size,
                        color=self.theme.foreground, z=30, **options)

    def panel(self, name, x, y, width, height):
        return self.add(name, 'rectangle', x=x, y=y, width=width, height=height,
                        color=self.theme.panel, z=3)

    def line(self, name, points, *, arrow=False, shared=True, **data):
        points = list(points)
        if arrow:
            a, b = points[-2:]
            dx, dy = (b[0]-a[0])*1280, (b[1]-a[1])*720
            length = math.hypot(dx, dy)
            if not length:
                raise StoryboardQualityError('A directed relationship needs distinct endpoints.')
            ux, uy = dx/length, dy/length
            points += [(b[0] + (-10*ux + 5*uy)/1280, b[1] + (-10*uy - 5*ux)/720),
                       b, (b[0] + (-10*ux - 5*uy)/1280, b[1] + (-10*uy + 5*ux)/720)]
        if not 2 <= len(points) <= MAX_CURVE_POINTS:
            raise StoryboardQualityError('Diagram polylines require 2–256 points; split the curve instead of truncating.')
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return self.add(name, 'polyline', x=(min(xs)+max(xs))/2, y=(min(ys)+max(ys))/2,
                        width=max(.002, max(xs)-min(xs)), height=max(.002, max(ys)-min(ys)),
                        points=[Point(x=x, y=y) for x, y in points], z=8,
                        shared=shared, **data)

    def emphasize(self, name, points):
        obj = self.line(name, points, shared=False)
        self.motions.append(Motion(id=f'c{self.index}-{name}-reveal', object_id=obj.id,
            action='opacity', start=self.cue.start, duration=self.cue.duration,
            from_value=.2, to_value=1, narration_ids=[self.cue.id], concepts=list(self.cue.concepts)))

    def move(self, name, points, *, start=None, duration=None, width=.025, height=.035):
        start = self.cue.start if start is None else start
        duration = self.cue.duration if duration is None else duration
        points = list(points)
        obj = self.add(name, 'rectangle', shared=False, x=points[0][0], y=points[0][1],
                       width=width, height=height, z=20, visible_from=start,
                       visible_until=start+duration)
        # Use straight segments: three path points would be interpreted as a
        # Bezier by the shared motion engine, cutting across the depicted nodes.
        for i, (a, b) in enumerate(zip(points, points[1:])):
            self.motions.append(Motion(id=f'c{self.index}-{name}-move-{i}', object_id=obj.id,
                action='move', start=start+duration*i/(len(points)-1),
                duration=duration/(len(points)-1), easing='linear',
                path=[Point(x=a[0], y=a[1]), Point(x=b[0], y=b[1])],
                narration_ids=[self.cue.id], concepts=list(self.cue.concepts)))


def _items(scene, selection):
    for key in ('steps', 'bullets', 'important_concepts', 'labels', 'display_text', 'display'):
        values = list(_strings(scene.get(key)))
        if values:
            return values
    return list(selection.concepts)


def _active(cue, items, index, count, mapped_index=None):
    step = cue.step_index if cue.step_index is not None else mapped_index
    if step is not None:
        if isinstance(step, bool) or not isinstance(step, int) or not 0 <= step < len(items):
            raise StoryboardQualityError(f'{cue.id}: step_index {step!r} has no displayed step.')
        return step
    # Phrase wording must never reorder authored steps.
    return min(len(items)-1, index*len(items)//count)


def _process(canvas, scene, selection, cues, *, cycle=False):
    items = _items(scene, selection)
    if len(items) < 2:
        return _concept_map(canvas, scene, selection, cues)
    if len(items) > 6:
        raise StoryboardQualityError('A readable process supports at most six stages. Split the scene; all stages must remain.')
    height = .105 if len(items) <= 4 else .075
    positions = [.25+i*.49/(len(items)-1) for i in range(len(items))]
    event_steps = {event.get('event_id'): event.get('parameters', {}).get('step_index')
                   for event in scene.get('visual_timeline', [])}
    active_steps = [_active(c, items, n, len(cues), event_steps.get(c.source_event_id))
                    for n, c in enumerate(cues)]
    mapped = any(c.step_index is not None or event_steps.get(c.source_event_id) is not None for c in cues)
    for n, cue in enumerate(cues):
        canvas.begin(cue, n)
        active = active_steps[n]
        for i, (text, y) in enumerate(zip(items, positions)):
            # Authored step indices determine the first reveal. Subsequent
            # cues can revisit a step without rewriting their source indices.
            if not cycle and n < len(cues)-1 and mapped and i > max(active_steps[:n+1]):
                continue
            canvas.panel(f'stage-{i}', .55, y, .75, height+.012)
            canvas.text(f'stage-label-{i}', text, .55, y, .73, height)
            if i and (cycle or n == len(cues)-1 or i <= max(active_steps[:n+1]) or not mapped):
                canvas.line(f'stage-link-{i}', [(.55, positions[i-1]+height/2+.008),
                                               (.55, y-height/2-.008)], arrow=True)
        y = positions[active]
        canvas.emphasize('current-stage', [(.162, y-height/2), (.162, y+height/2)])
        if cycle:
            canvas.line('cycle-return', [(.17, positions[-1]), (.09, positions[-1]),
                                        (.09, positions[0]), (.17, positions[0])], arrow=True)
            canvas.text('cycle-repeat', 'Repeat', .11, .16, .19, .065, 28)
    return 'cycle' if cycle else 'vertical_flow'


def _concept_map(canvas, scene, selection, cues):
    items = _items(scene, selection)
    if len(items) <= 1:
        authored = any(scene.get(key) for key in ('learning_objective', 'steps', 'bullets', 'important_concepts', 'labels', 'display_text', 'display'))
        idea = items[0] if items and authored else scene.get('subtitle') or scene.get('title') or 'Key idea'
        for n, cue in enumerate(cues):
            canvas.begin(cue, n)
            for i, asset in enumerate(selection.assets):
                canvas.add(f'concept-asset-{i}', 'asset', asset=asset,
                           x=.07 if i == 0 else .93, y=.405, width=.075, height=.065, z=10)
            canvas.panel('idea-panel', .5, .27, .87, .18)
            canvas.text('idea', idea, .5, .27, .84, .165)
            canvas.panel('explanation-panel', .5, .61, .87, .30)
            canvas.text('explanation', cue.text, .5, .61, .83, .28, 28)
            canvas.text('relation', 'explained by', .73, .405, .30, .065, 28)
            canvas.emphasize('explanation-link', [(.5, .365), (.5, .445)])
        return 'concept_map'
    if len(items) > 6:
        raise StoryboardQualityError('A concept map supports at most six readable concepts. Split the scene without dropping concepts.')
    # A structured map gives the supplied ideas a common explanatory root;
    # it asserts no invented causal order between otherwise unrelated ideas.
    root = 'Review' if scene.get('type') in {'summary', 'conclusion'} else 'Related ideas'
    height = .115 if len(items) <= 4 else .075
    positions = [.24+i*.50/(len(items)-1) for i in range(len(items))]
    for n, cue in enumerate(cues):
        canvas.begin(cue, n)
        for i, asset in enumerate(selection.assets):
            canvas.add(f'concept-asset-{i}', 'asset', asset=asset,
                       x=.14+i*.17, y=.27, width=.12, height=.12, z=10)
        canvas.panel('root-panel', .225, .49, .32, .16)
        canvas.text('root', root, .225, .49, .30, .14)
        for i, (text, y) in enumerate(zip(items, positions)):
            canvas.panel(f'concept-panel-{i}', .72, y, .47, height+.01)
            canvas.text(f'concept-{i}', text, .72, y, .445, height, 28)
            canvas.line(f'relation-{i}', [(.39, .49), (.47, y)])
        active = _active(cue, items, n, len(cues))
        canvas.emphasize('current-relation', [(.39, .49), (.47, positions[active])])
    return 'concept_map'


def _comparison(canvas, scene, selection, cues):
    items = _items(scene, selection)
    left, right = scene.get('left'), scene.get('right')
    if not (left and right):
        if len(items) != 2:
            # Keep all ideas in the fallback instead of inventing alternatives
            # or silently selecting just the first two.
            return _concept_map(canvas, scene, selection, cues)
        left, right = items
    relation = scene.get('relationship') or 'Compare the stated concepts'
    for n, cue in enumerate(cues):
        canvas.begin(cue, n)
        for name, text, x in (('left', left, .265), ('right', right, .735)):
            canvas.panel(name+'-panel', x, .40, .43, .28)
            canvas.text(name, text, x, .40, .40, .25, 28)
        canvas.text('relationship', relation, .5, .69, .86, .15, 28)
        canvas.line('compare-link', [(.265, .55), (.265, .59), (.735, .59), (.735, .55)])
        canvas.emphasize('compare-relation', [(.5, .59), (.5, .615)])
    return 'comparison'


def _network(canvas, scene, selection, cues):
    for n, cue in enumerate(cues):
        canvas.begin(cue, n)
        local = _normal(cue.text)
        retry = _has(local, r'retransmi(?:t|ts|ssion)|timeout|lost|missing|retry|retries')
        ack = _has(local, r'ack|acknowledg(?:e|ement|ment|ments|ements)|receipt|confirms?')
        routing = _has(local, r'routers?|rout(?:e|es|ing)|forward(?:ing|s)?')
        if routing and _has(local, r'do not acknowledge|does not acknowledge') and not _has(local, r'receiver|receiving endpoint'):
            ack = False
        reliability = selection.template == 'network_reliability'
        comparison = selection.template == 'network_comparison'
        if retry or ack or comparison or not routing and (reliability or selection.template == 'network_ack'):
            for name, x in (('Sender', .16), ('Receiver', .84)):
                canvas.panel(name.lower(), x, .50, .19, .40)
                canvas.text(name.lower()+'-label', name, x, .50, .18, .075, 28)
            canvas.text('data-label', 'Data packet', .5, .27, .40, .07)
            canvas.line('data-route', [(.28, .37), (.72, .37)], arrow=True)
            if retry:
                canvas.text('retry-label', 'Missing progress → retransmit', .5, .75, .64, .07, 28)
                canvas.line('retry-route', [(.28, .60), (.72, .60)], arrow=True)
                canvas.move('lost-packet', [(.28, .37), (.49, .37)], duration=cue.duration*.35)
                canvas.move('retransmitted-packet', [(.28, .60), (.72, .60)],
                            start=cue.start+cue.duration*.35, duration=cue.duration*.65)
            elif ack or comparison:
                canvas.text('ack-label', 'ACK confirms receipt', .5, .75, .55, .07)
                canvas.line('ack-route', [(.72, .65), (.28, .65)], arrow=True)
                half = cue.duration/2
                canvas.move('data-packet', [(.28, .37), (.72, .37)], duration=half)
                canvas.move('ack-packet', [(.72, .65), (.28, .65)], start=cue.start+half, duration=half)
            else:
                canvas.text('delivery-label', 'Data travels to the receiver', .5, .75, .66, .07, 28)
                canvas.move('data-packet', [(.28, .37), (.72, .37)])
            if reliability and _has(local, r'sequence|order|bytes?|missing'):
                canvas.text('sequence-note', 'Illustrative byte ranges', .5, .18, .75, .065, 28)
                # Start/size are local schematic values, explicitly labelled.
                # The missing middle range appears after its retransmission.
                for slot, x in enumerate((.34, .50, .66)):
                    canvas.text(f'byte-range-{slot}', f'{100*slot}–{100*(slot+1)-1}', x, .49, .135, .075, 28,
                                shared=False, visible_from=cue.start+cue.duration*(.65 if retry and slot == 1 else .10+slot*.15))
        else:
            for name, x in (('Sender', .16), ('Router', .50), ('Receiver', .84)):
                canvas.panel(name.lower(), x, .5, .18, .13)
                canvas.text(name.lower()+'-label', name, x, .34, .21, .075, 28)
            canvas.line('route-in', [(.26, .5), (.40, .5)], arrow=True)
            canvas.line('route-out', [(.60, .5), (.74, .5)], arrow=True)
            canvas.text('packet-label', 'Packet follows a route', .5, .66, .65, .075)
            canvas.text('topology-note', 'Illustrative topology', .5, .76, .65, .065, 28)
            canvas.move('packet', [(.16, .5), (.5, .5), (.84, .5)])
    return 'comparison' if selection.template == 'network_comparison' else 'network'


def _water_states(canvas, scene, selection, cues):
    left = scene.get('left') or 'Water vapor: gas'
    right = scene.get('right') or 'Water droplets: liquid'
    for n, cue in enumerate(cues):
        canvas.begin(cue, n)
        canvas.text('vapor-label', left, .265, .25, .43, .14, 28)
        canvas.text('droplets-label', right, .735, .25, .43, .14, 28)
        canvas.panel('vapor-region', .265, .515, .41, .32)
        canvas.panel('liquid-region', .735, .515, .41, .32)
        for i, (dx, dy) in enumerate(((-.12, -.07), (.04, -.09), (.12, .02), (-.10, .07), (.03, .08), (-.02, 0))):
            canvas.add(f'gas-particle-{i}', 'circle', x=.265+dx, y=.515+dy,
                       width=.011, height=.020, z=12)
            canvas.add(f'liquid-particle-{i}', 'circle', x=.735+dx*.28, y=.515+dy*.28,
                       width=.018, height=.032, z=12)
        canvas.text('state-note', 'Schematic particles; water vapor itself is invisible', .5, .77, .91, .065, 28)
        # A cue highlights the described physical state, not a decorative orbit.
        gas = _has(_normal(cue.text), r'vapou?r|gas|evaporat(?:ion|e|es)')
        x = .265 if gas else .735
        canvas.emphasize('state-highlight', [(x-.19, .36), (x+.19, .36)])
    return 'comparison'


def _neural(canvas, scene, selection, cues):
    layers = [(.20, [.36, .51, .66]), (.50, [.43, .59]), (.80, [.51])]
    for n, cue in enumerate(cues):
        canvas.begin(cue, n)
        for layer, (x, ys) in enumerate(layers):
            canvas.text(f'layer-label-{layer}', ('Input', 'Hidden layer', 'Output')[layer], x, .23, .28, .075, 28)
            for i, y in enumerate(ys):
                canvas.add(f'neuron-{layer}-{i}', 'circle', x=x, y=y, width=.045, height=.08,
                           color=canvas.theme.panel, z=12)
            if layer:
                px, pys = layers[layer-1]
                for i, py in enumerate(pys):
                    for j, y in enumerate(ys):
                        canvas.line(f'weight-{layer}-{i}-{j}', [(px+.025, py), (x-.025, y)], arrow=True)
        canvas.text('connectivity-note', 'Illustrative connectivity; signal propagates', .5, .76, .86, .065, 28)
        canvas.move('activation', [(.20, .51), (.50, .43), (.80, .51)], width=.017, height=.025)
    return 'horizontal_flow'


def _number(params, key, default, *, minimum=0, positive=False):
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise StoryboardQualityError(f'{key} must be a finite numeric value; expressions are not executed.')
    if value < minimum or positive and value <= 0:
        raise StoryboardQualityError(f'{key} is outside its supported numeric range.')
    return float(value)


def _signal_samples(scene, lowpass):
    """Sample an explicitly illustrative sine mixture and a one-pole low-pass.

    Supported animation_params: sample_count (2..256), sample_rate (Hz),
    frequency (Hz), amplitude, high_frequency (Hz), high_amplitude, and
    cutoff_frequency (Hz). No formula strings are interpreted.
    """
    params = scene.get('animation_params') or {}
    count = _number(params, 'sample_count', 192, minimum=2)
    if not count.is_integer() or count > MAX_CURVE_POINTS:
        raise StoryboardQualityError('sample_count must be an integer from 2 to 256; curves are never truncated.')
    count = int(count)
    rate = _number(params, 'sample_rate', 192, positive=True)
    frequency = _number(params, 'frequency', 2, positive=True)
    amplitude = _number(params, 'amplitude', 1, positive=True)
    high_frequency = _number(params, 'high_frequency', 16, positive=True) if lowpass else 0
    high_amplitude = _number(params, 'high_amplitude', .35, minimum=0) if lowpass else 0
    if max(frequency, high_frequency) >= rate/2:
        raise StoryboardQualityError('Signal frequencies must be below half the sample rate.')
    samples = [amplitude*math.sin(2*math.pi*(frequency/rate)*i)
               + high_amplitude*math.sin(2*math.pi*(high_frequency/rate)*i) for i in range(count)]
    output = samples
    required = {'frequency', 'amplitude', 'sample_rate'}
    if lowpass:
        cutoff = _number(params, 'cutoff_frequency', 4, positive=True)
        if cutoff >= rate/2:
            raise StoryboardQualityError('cutoff_frequency must be below half the sample rate.')
        alpha = 1-math.exp(-2*math.pi*(cutoff/rate))
        output, value = [], samples[0]
        for sample in samples:
            value += alpha*(sample-value)
            output.append(value)
        required |= {'cutoff_frequency', 'high_frequency', 'high_amplitude'}
    scale = amplitude+high_amplitude
    if not all(math.isfinite(value) for value in [scale, *samples, *output]):
        raise StoryboardQualityError('Signal parameters exceed finite numeric precision.')
    return samples, output, scale, not required <= params.keys()


def _signal(canvas, scene, selection, cues):
    lowpass = selection.template in {'signal_lowpass', 'signal_comparison'}
    samples, output, scale, illustrative = _signal_samples(scene, lowpass)
    for n, cue in enumerate(cues):
        canvas.begin(cue, n)
        canvas.text('signal-note', 'Illustrative signal; unspecified parameters' if illustrative else 'Sampled signal with supplied parameters',
                    .5, .20, .89, .075, 28)
        charts = [('Input', samples, .38), ('Output', output, .66)] if selection.template == 'signal_comparison' else (
            [('Input', samples, .35), ('Output', output, .65)] if lowpass else [('Signal', samples, .49)])
        for label, values, center in charts:
            amplitude = .07 if lowpass else .17
            canvas.text(label.lower()+'-label', label, .095, center, .15, .11, 28)
            canvas.line(label.lower()+'-axis', [(.21, center+amplitude+.015), (.92, center+amplitude+.015)], arrow=True)
            canvas.line(label.lower()+'-curve', [(.22+i*.68/(len(values)-1), center-amplitude*value/scale)
                                                for i, value in enumerate(values)])
            canvas.move(label.lower()+'-time-cursor', [(.22, center), (.90, center)],
                        width=.003, height=2*amplitude+.015)
        if selection.template == 'signal_comparison' and scene.get('left') and scene.get('right'):
            canvas.text('source-input', scene['left'], .58, .28, .66, .065, 28)
            canvas.text('source-output', scene['right'], .58, .55, .66, .065, 28)
        elif lowpass:
            canvas.text('filter-label', 'Low-pass: attenuate fast variations', .5, .49, .86, .065, 28)
        canvas.text('time-label', 'Time →', .77, .79, .30, .065, 28)
    return 'signal'


def _delay_cue(canvas, scene):
    samples, _, scale, illustrative = _signal_samples(scene, False)
    params = scene.get('animation_params') or {}
    rate = _number(params, 'sample_rate', 192, positive=True)
    frequency = _number(params, 'frequency', 2, positive=True)
    delay = _number(params, 'delay_seconds', .10)
    if delay*frequency > 1e8:
        raise StoryboardQualityError('Delay exceeds the supported illustrative time range.')
    values = [math.sin(2*math.pi*((frequency/rate)*i-delay*frequency)) for i in range(len(samples))]
    canvas.text('delay-note', 'Illustrative time shift' if illustrative or 'delay_seconds' not in params else 'Time shift with supplied parameters',
                .5, .20, .89, .075, 28)
    canvas.text('input-legend', 'Input', .32, .29, .30, .065, 28)
    canvas.text('delayed-legend', 'Delayed output', .70, .29, .40, .065, 28)
    canvas.line('input-wave', [(.22+i*.68/(len(samples)-1), .49-.13*value/scale) for i, value in enumerate(samples)])
    canvas.line('delayed-wave', [(.22+i*.68/(len(values)-1), .49-.13*value) for i, value in enumerate(values)], color=canvas.theme.foreground)
    canvas.line('delay-time-axis', [(.21, .68), (.92, .68)], arrow=True)
    canvas.move('delay-time-cursor', [(.22, .49), (.90, .49)], width=.003, height=.29)
    canvas.text('delay-caption', 'Output features occur later in time', .5, .77, .89, .065, 28)


def _response(canvas, scene, selection, cues):
    for n, cue in enumerate(cues):
        canvas.begin(cue, n)
        if selection.template == 'signal_delay' or (_has(_normal(cue.text), r'delay|phase shift')
                                                  and not _has(_normal(cue.text), r'cutoff|cut off')):
            _delay_cue(canvas, scene)
            continue
        canvas.text('response-note', 'Illustrative first-order frequency responses', .5, .18, .89, .075, 28)
        canvas.text('low-cutoff', 'Lower cutoff', .36, .26, .33, .065, 28)
        canvas.text('high-cutoff', 'Higher cutoff', .72, .26, .33, .065, 28)
        canvas.text('gain-label', 'Gain', .105, .48, .16, .11, 28)
        canvas.line('frequency-axis', [(.21, .69), (.92, .69)], arrow=True)
        canvas.line('gain-axis', [(.22, .69), (.22, .31)])
        for name, cutoff, color in (('lower-response', 1, canvas.theme.accent), ('higher-response', 2, canvas.theme.foreground)):
            canvas.line(name, [(.22+i*.68/191, .66-.34/math.sqrt(1+(4*i/191/cutoff)**2)) for i in range(192)], color=color)
        canvas.move('frequency-cursor', [(.22, .49), (.90, .49)], width=.003, height=.35)
        canvas.text('frequency-label', 'Frequency relative to lower cutoff', .56, .75, .75, .065, 28)
    return 'signal'


_TEMPLATES = {
    'process': _process,
    'concept_map': _concept_map,
    'comparison': _comparison,
    'network_routing': _network,
    'network_ack': _network,
    'network_comparison': _network,
    'network_reliability': _network,
    'neural_network': _neural,
    'signal_waveform': _signal,
    'signal_lowpass': _signal,
    'signal_comparison': _signal,
    'signal_response': _response,
    'signal_delay': _response,
    'water_states': _water_states,
}


def diagram_scene(scene, cues, theme, selection: Selection, index=0):
    """Build a validated data scene while preserving every supplied cue verbatim.

    Native mathematics and water selections must be routed by the integrating
    compiler. Generic templates do not impersonate these domain compilers.
    Concept maps place relevant selected catalog assets in reserved icon space;
    primitives explain relationships when no semantically supported SVG exists.
    """
    scene, cues = _mapping(scene), list(cues)
    if not cues:
        raise StoryboardQualityError('Diagram scenes require all measured narration cues.')
    if selection.template == 'natural_cycle':
        build = lambda canvas, source, selected, narration: _process(canvas, source, selected, narration, cycle=True)
    else:
        build = _TEMPLATES.get(selection.template)
    if build is None:
        raise StoryboardQualityError(f'{selection.template!r} must be routed to its dedicated compiler.')
    # Reserve the renderer-owned fixed header and caption, validating their real
    # font metrics now instead of reducing fonts or dropping narration later.
    title = scene.get('title') or 'Concepts'
    header_box(title)
    for cue in cues:
        fit_text(cue.text, .91, .14, font_size=30)
    canvas = _Canvas(theme)
    layout = build(canvas, scene, selection, cues)
    if len(canvas.motions) > 240:
        raise StoryboardQualityError('Diagram exceeds 240 motions. Split the scene without dropping cues.')
    # All essential cue concepts remain on the scene. Selection concepts add
    # semantic provenance, while motion references cover the original cue IDs.
    concepts = _unique([*selection.concepts, *(concept for cue in cues for concept in cue.concepts)])
    kind = scene.get('type')
    purpose = ('introduce' if kind in {'title', 'intro', 'introduction'} else
               'summarize' if kind in {'summary', 'conclusion'} else
               'compare' if selection.template == 'comparison' else 'explain')
    return StoryboardScene(id=scene.get('scene_id') or f'scene-{index+1}', title=title,
        objective=scene.get('learning_objective') or 'Explain the stated concepts and their relationships.',
        duration=cues[-1].start+cues[-1].duration, concepts=concepts, layout=layout,
        background='solid', narration=cues, objects=canvas.objects, motions=canvas.motions,
        template=selection.template, selection_reason=selection.reason,
        selection_assets=list(selection.assets), purpose=purpose,
        source_scene_id=scene.get('scene_id', ''),
        transition=Transition(kind='cut', duration=0) if cues[-1].start+cues[-1].duration < .35 else Transition(),
        repetition_justification='Concluding recap of the supplied concepts.' if purpose == 'summarize' else '')
