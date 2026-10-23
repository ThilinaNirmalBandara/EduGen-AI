"""Stable identities and lossless chronology checks; never sort instructional content."""
from copy import deepcopy
import hashlib


def identify_lesson(lesson: dict) -> dict:
    result = deepcopy(lesson)
    for index, scene in enumerate(result['scenes']):
        digest = hashlib.sha256(f"{index}:{scene['type']}:{scene['title']}".encode()).hexdigest()[:8]
        scene.setdefault('scene_id', '')
        scene['scene_id'] = scene['scene_id'] or f's{index+1:03d}-{digest}'
        scene['parent_scene_id'] = scene.get('parent_scene_id') or scene['scene_id']
        identify_events(scene)
    return result


def identify_events(scene: dict) -> None:
    parent = scene.get('parent_scene_id') or scene.get('scene_id', 'scene')
    cues = scene.get('visual_timeline', [])
    for index, cue in enumerate(cues):
        cue['event_id'] = cue.get('event_id') or f'{parent}.e{index:03d}'
        cue['source_event_id'] = cue.get('source_event_id') or cue['event_id']
        # Estimated schedules are re-timed after TTS, not sorted by guessed meaning.
        if not cue.get('event_order'):
            cue['event_order'] = index
        cue.setdefault('parameters', {}).setdefault('visual_index', index)
        cue['parameters'].setdefault('visual_count', len(cues))
        if scene['type'] == 'process':
            cue.setdefault('parameters', {}).setdefault('step_index',
                min(len(scene['steps'])-1, index * len(scene['steps']) // max(1, len(cues))))


def validate_sequence(lesson: dict, original: dict | None = None) -> None:
    ids, closed, last_parent = set(), set(), None
    last_order, last_step = -1, -1
    parents = []
    process_coverage = {}
    for scene in lesson['scenes']:
        identity = scene.get('scene_id')
        if not identity or identity in ids:
            raise ValueError('Scene IDs must be present and unique.')
        ids.add(identity)
        parent = scene.get('parent_scene_id') or identity
        if parent != last_parent:
            if parent in closed:
                raise ValueError('Split scenes are not in their original chronological order.')
            if last_parent is not None:
                closed.add(last_parent)
            parents.append(parent)
            last_order, last_step, last_parent = -1, -1, parent
        for cue in scene.get('visual_timeline', []):
            event_id = cue.get('event_id')
            if not event_id or event_id in ids:
                raise ValueError('Visual event IDs must be present and unique.')
            ids.add(event_id)
            order = cue.get('event_order', 0)
            if order < last_order:
                raise ValueError('Visual events are not in chronological order.')
            last_order = order
            if scene['type'] == 'process':
                step = cue.get('parameters', {}).get('step_index')
                if not isinstance(step, int) or not last_step <= step < len(scene['steps']):
                    raise ValueError('Process steps must remain in their original instructional order.')
                last_step = step
                if scene.get('animation_params', {}).get('require_all_steps'):
                    record = process_coverage.setdefault(parent, (set(), len(scene['steps'])))
                    record[0].add(step)
    for parent, (steps, count) in process_coverage.items():
        if steps != set(range(count)):
            raise ValueError(f'Process {parent} is missing visual coverage of an essential step; expand or re-segment the narration.')
    if original is not None:
        source_parents = [s.get('parent_scene_id') or s['scene_id'] for s in original['scenes']]
        if parents != source_parents:
            raise ValueError('Lesson coverage changed: an essential scene was dropped or reordered.')
        for source in original['scenes']:
            parent = source.get('parent_scene_id') or source['scene_id']
            text = ' '.join(s.get('narration_text', '') for s in lesson['scenes']
                            if (s.get('parent_scene_id') or s['scene_id']) == parent)
            if text.split() != source.get('narration_text', '').split():
                raise ValueError(f'Narration was lost or reordered while splitting {parent}.')
