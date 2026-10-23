"""Narration cues, measured visual schedules, and deterministic demo narration."""

from __future__ import annotations

import math
import re
from copy import deepcopy

from src.schemas import VisualEvent
from .mathematics import gradient_updates, number
from .sequence import identify_lesson, identify_events
from src.content import validate_content

MAX_STATIC_SECONDS = 8.0


def narration_units(text: str, max_words: int = 16) -> list[str]:
    """Preserve every word, preferring sentence, clause, and then phrase boundaries."""
    units = []
    for sentence in re.split(r"(?<=[.!?;])\s+", text.strip()):
        words = sentence.split()
        while len(words) > max_words:
            boundary = max_words
            for index in range(max_words, max_words // 2, -1):
                if words[index - 1].endswith((",", ":", ";")):
                    boundary = index
                    break
            units.append(" ".join(words[:boundary]))
            words = words[boundary:]
        if words:
            units.append(" ".join(words))
    return units


def estimated_timeline(scene: dict, *, cover_silent: bool = False) -> list[dict]:
    text = scene.get("narration_text", "")
    sources = scene.get("visual_timeline") or [{"narration_text": text or scene["title"]}]
    pairs = [(source, unit) for source in sources for unit in narration_units(source.get("narration_text", ""))]
    if not pairs:
        pairs = [({}, scene["title"])]
    duration = float(scene["duration"])
    # Silent/legacy scenes still need visual coverage throughout their duration.
    count = max(len(pairs), math.ceil(duration / MAX_STATIC_SECONDS)) if cover_silent else len(pairs)
    events = []
    for index in range(count):
        source, unit = pairs[min(len(pairs)-1, index * len(pairs) // count)]
        source_id = source.get('source_event_id') or source.get('event_id') or f"{scene.get('scene_id', 'scene')}.e{index:03d}"
        cue = dict(source, narration_text=unit, narration_segment=index,
                   start_time=index * duration / count, animation_duration=duration / count,
                   source_event_id=source_id, event_id=f'{source_id}.v{index:03d}', event_order=index)
        events.append(VisualEvent.model_validate(cue).model_dump())
    if scene['type'] == 'process':
        for index, event in enumerate(events):
            event['parameters'].setdefault('step_index', min(len(scene['steps'])-1,
                index * len(scene['steps']) // len(events)))
    temporary = dict(scene, visual_timeline=events)
    identify_events(temporary)
    return events


def bind_audio_timeline(scene: dict, audio) -> dict:
    updated = deepcopy(scene)
    timings = getattr(audio, "timings", ())
    if not timings:
        # Third-party/legacy audio with no measured segment boundaries is labelled
        # estimated; no word-level alignment is claimed for it.
        updated["visual_timeline"] = estimated_timeline(dict(scene, duration=audio.duration), cover_silent=True)
        offset = float(scene.get('animation_params', {}).get('timeline_offset', 0))
        for event in updated['visual_timeline']:
            order = round((offset + event['start_time']) * 1000000)
            source_id = event['source_event_id']
            event.update(event_id=f'{source_id}.t{order}', event_order=order)
        updated.setdefault("animation_params", {})["timing_source"] = "estimated"
        _cover_short_scene(updated, audio.duration)
        return updated
    cues = scene.get("visual_timeline", [])
    events = []
    previous_cue = -1
    offset = float(scene.get('animation_params', {}).get('timeline_offset', 0))
    for index, timing in enumerate(timings):
        if timing.cue_index < previous_cue or (cues and timing.cue_index >= len(cues)):
            raise ValueError('Measured narration references out-of-order or missing visual cues. Regenerate its timing cache.')
        previous_cue = timing.cue_index
        cue = deepcopy(cues[timing.cue_index]) if cues and timing.cue_index < len(cues) else {}
        source_id = cue.get('source_event_id') or cue.get('event_id') or f"{scene.get('parent_scene_id', 'scene')}.e{timing.cue_index:03d}"
        order = round((timing.start + offset) * 1000000)
        cue.update(narration_text=timing.text, narration_segment=index, start_time=timing.start,
                   animation_duration=timing.duration, source_event_id=source_id,
                   event_id=f'{source_id}.t{order}', event_order=order)
        events.append(VisualEvent.model_validate(cue).model_dump())
    updated["visual_timeline"] = events
    updated.setdefault("animation_params", {})["timing_source"] = "measured"
    _cover_short_scene(updated, audio.duration)
    return updated


def _cover_short_scene(scene: dict, audio_duration: float) -> None:
    # Legacy scenes have a three-second minimum. A short utterance can end before
    # that; explicitly hold/highlight its final visual rather than leaving a gap.
    padding = float(scene["duration"]) - audio_duration
    if padding > 0.6:
        previous = scene["visual_timeline"][-1]
        cue = dict(previous, narration_text="", action="highlight", start_time=audio_duration,
                   animation_duration=padding, event_id=previous.get('event_id', 'event')+'.hold',
                   event_order=previous.get('event_order', 0)+1)
        scene["visual_timeline"].append(VisualEvent.model_validate(cue).model_dump())


def validate_timeline(scene: dict, duration: float | None = None) -> None:
    duration = float(scene["duration"]) if duration is None else duration
    events = scene.get("visual_timeline", [])
    if not events:
        raise ValueError(f"Scene {scene['title']!r} is missing visual coverage. Generate a visual timeline first.")
    end = 0.0
    for raw in events:
        event = VisualEvent.model_validate(raw)
        if event.start_time < end - 0.05:
            raise ValueError("Visual timeline events overlap or are out of order.")
        if event.start_time - end > 0.5 or event.animation_duration > MAX_STATIC_SECONDS + 0.1:
            raise ValueError("Visual timeline has an excessively static or uncovered narration interval.")
        end = event.start_time + event.animation_duration
        if end > duration + 0.1:
            raise ValueError("Visual event extends beyond the scene/audio duration.")
    if duration - end > 0.6:
        raise ValueError("Visual timeline does not cover the end of narration.")


def prepare_visual_lesson(lesson: dict, *, prompt: str = "") -> dict:
    """Upgrade a copy; cached lesson content remains available unchanged.

    Numerical demo narration is generated from the same computed states as the
    visuals. This replaces inconsistent LLM arithmetic only in explicit quadratic
    demonstrations and keeps the rest of the educational lesson intact.
    """
    validate_content(lesson)  # Reject incorrect/foreign text BEFORE any authored template is selected.
    result = identify_lesson(lesson)
    is_descent = "gradient descent" in (prompt + " " + lesson.get("title", "")).lower()
    needs_conclusion = bool(prompt) or is_descent
    if needs_conclusion and not any(s['type'] == 'summary' for s in result['scenes']):
        if len(result['scenes']) >= 20:
            raise ValueError('Lesson has no final summary and no scene capacity. Revise the plan; essential scenes will not be cut.')
        summary = {
            'type': 'summary', 'title': 'Summary: gradient descent', 'duration': 18,
            'learning_objective': 'Connect the gradient, learning rate, update and convergence.',
            'bullets': ['The gradient measures local slope', 'Move against the gradient',
                        'The learning rate controls step size', 'Monitor the loss and convergence'],
            'narration_text': 'The gradient measures local slope. Gradient descent moves against that slope to reduce a smooth loss with a suitable learning rate. In our quadratic example, x moves from three to one point eight and the loss falls from nine to three point two four. Repeat the update, monitor the loss, and stop when further improvement is small.'}
        if not is_descent:
            concepts = list(dict.fromkeys(concept for s in result['scenes']
                for concept in (s.get('important_concepts') or [s.get('learning_objective') or s['title']]) if concept))[-4:]
            summary.update(title='Summary', learning_objective='Connect the lesson objectives.',
                bullets=[c if len(c) <= 150 else f'Review learning objective {i+1}' for i, c in enumerate(concepts)],
                narration_text='To summarize, connect these key ideas: ' + '. '.join(concepts) + '.')
        result['scenes'].append(summary)
    for scene in result["scenes"]:
        if not scene.get("narration_text", "").strip():
            from src.tts import narration_for_scene
            scene["narration_text"] = narration_for_scene(scene)
        scene['visual_template'] = select_template(scene, is_descent=is_descent)
        prepared = scene.get('animation_params', {}).get('prepared_visuals', False)
        quadratic = scene.get('equation', 'x^2').split('=')[-1].replace(' ', '').replace('²', '^2') in {'x^2', 'x^{2}'}
        if is_descent and quadratic and scene['visual_template'] not in {'learning_rates', 'convergence'} and scene["type"] in {"graph", "worked_example"} and not prepared:
            _prepare_demo(scene)
        elif is_descent and scene["type"] == "equation" and 'nabla' in scene['equation'] and not prepared:
            _prepare_derivation(scene)
        scene.setdefault('animation_params', {})['prepared_visuals'] = True
        if scene['type'] == 'process':
            scene['animation_params']['require_all_steps'] = True
        # Old cached plans used a graph even for introductions and summaries.
        scene['animation_params'].pop('illustration', None)
        cues_text = " ".join(event.get("narration_text", "") for event in scene.get("visual_timeline", []))
        if not scene.get("visual_timeline") or cues_text.split() != scene["narration_text"].split():
            scene.pop("visual_timeline", None)
            scene["visual_timeline"] = process_timeline(scene) if scene['type'] == 'process' else estimated_timeline(scene)
        identify_events(scene)
    # A summary in the middle does not satisfy complete concluding coverage.
    if needs_conclusion and result['scenes'][-1]['type'] != 'summary':
        raise ValueError('The final scene must be a concise summary. Revise the outline without reordering essential content.')
    result = identify_lesson(result)
    validate_content(result)
    return result


def select_template(scene: dict, *, is_descent: bool = False) -> str:
    """Use the learning objective, not a blanket graph for the whole topic."""
    if scene.get('visual_template'):
        return scene['visual_template']
    kind = scene['type']
    if kind == 'graph' and scene.get('graph_type') == 'parabola':
        return ''  # Preserve the supplied polynomial rather than substituting x².
    objective = (scene.get('learning_objective', '') + ' ' + scene['title']).lower()
    if kind == 'summary': return 'summary'
    if kind == 'process': return 'process'
    if kind == 'equation': return 'derivation'
    if kind == 'worked_example': return 'numerical'
    if is_descent and kind in {'graph', 'comparison', 'bullets'}:
        if 'learning rate' in objective or 'step size' in objective: return 'learning_rates'
        if 'converg' in objective or 'loss reduction' in objective: return 'convergence'
        if 'tangent' in objective or 'slope' in objective or 'gradient direction' in objective: return 'tangent'
    return 'descent' if kind == 'graph' else 'concept'


def process_timeline(scene: dict) -> list[dict]:
    """Assign ordered display steps ONCE, before TTS and automatic splitting.

    Never infer order from words such as 'check' in a later narration phrase.
    Partition short prose more finely if necessary, preserving every spoken word.
    """
    units = narration_units(scene['narration_text'])
    count = len(scene['steps'])
    if len(units) < count and len(scene['narration_text'].split()) >= count:
        words = scene['narration_text'].split()
        units = [' '.join(words[i*len(words)//count:(i+1)*len(words)//count]) for i in range(count)]
    cues = [VisualEvent(narration_text=text, parameters={'step_index':
        min(count-1, index*count//len(units))}).model_dump() for index, text in enumerate(units)]
    return estimated_timeline(dict(scene, visual_timeline=cues))


def _prepare_derivation(scene: dict) -> None:
    # Keep the validated source equation visible; never guess a replacement for
    # malformed model output. The local derivation uses L as a defined loss.
    pairs = [
        ("For a small parameter change, approximate the new loss using the current loss plus the gradient dotted with that change.", r"L(\theta+\Delta\theta)\approx L(\theta)+\nabla L(\theta)^T\Delta\theta"),
        ("Choose the change opposite to the gradient. The positive learning rate alpha controls how far we move.", r"\Delta\theta=-\alpha\nabla L(\theta),\quad\alpha>0"),
        ("Substituting gives a nonpositive first order change. For a smooth loss and sufficiently small learning rate, this is a descent direction.", r"\Delta L\approx-\alpha\Vert\nabla L(\theta)\Vert^2\leq0"),
        ("Add this change to the old parameters to obtain the gradient descent update. Recompute the gradient at every iteration.", scene["equation"]),
    ]
    scene["visual_timeline"] = [VisualEvent(narration_text=text, action="equation", start_time=index*4,
                                           animation_duration=4, parameters={"equation": equation}).model_dump()
                                for index, (text, equation) in enumerate(pairs)]
    scene["narration_text"] = " ".join(text for text, _ in pairs)


def _prepare_demo(scene: dict) -> None:
    params = scene.setdefault("animation_params", {})
    iterations = int(params.get('iterations', 1 if "concept" in scene["title"].lower() else 2))
    initial_x, rate = float(params.get('initial_x', 3)), float(params.get('learning_rate', 0.2))
    states = gradient_updates(initial_x, rate, iterations=iterations)
    params.update(demo="quadratic_descent", initial_x=initial_x, learning_rate=rate, iterations=iterations)
    # The caller checks that this is actually a quadratic before selecting this demo.
    scene.setdefault('equation', 'f(x)=x^2')
    # Each phase is short enough to receive its own measured audio boundary.
    cues = [(f"Minimize x squared: initial x {number(initial_x)}, learning rate {number(rate)}.", "reveal", 0)]
    for state in states:
        details = [
            (f"At x {number(state.x)}, loss is {number(state.loss)}; gradient two x equals {number(state.gradient)}.", "gradient", state.iteration),
            (f"Subtract {number(rate)} times {number(state.gradient)} from {number(state.x)}, giving {number(state.next_x)}.", "update", state.iteration),
            (f"Squaring {number(state.next_x)} gives new loss {number(state.next_loss)}, reduced from {number(state.loss)}.", "loss", state.iteration),
        ]
        if scene["type"] == "graph":
            details = [
                (f"Notice the tangent: the gradient is {number(state.gradient)}.", "gradient", state.iteration),
                (f"With learning rate {number(rate)}, move to {number(state.next_x)}.", "update", state.iteration),
                (f"The loss decreases to {number(state.next_loss)}.", "loss", state.iteration),
            ]
        cues.extend(details)
    cues.append(("Continue until the slope is nearly zero.", "highlight", iterations - 1))
    events = []
    for index, (text, action, iteration) in enumerate(cues):
        events.append(VisualEvent(narration_text=text, narration_segment=index, action=action,
                                  highlight_target=action, parameters={"iteration": iteration},
                                  start_time=index * 3, animation_duration=3).model_dump())
    scene["visual_timeline"] = events
    scene["narration_text"] = " ".join(text for text, _, _ in cues)
