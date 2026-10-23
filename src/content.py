"""English-language and bounded mathematical checks shared by planning and rendering."""
import re
import unicodedata
from copy import deepcopy

from .video.mathematics import MathRenderingError, validate_calculation, numerical_equalities, validate_math_in_text


def content_issues(plan: dict) -> list[str]:
    issues = []
    def walk(value, path):
        if isinstance(value, str):
            unexpected = sorted({char for char in value if char.isalpha()
                and not any(script in unicodedata.name(char, '') for script in ('LATIN', 'GREEK'))})
            if unexpected:
                issues.append(f'{path}: unexpected non-English characters {"".join(unexpected)!r}; rewrite the text in English, preserving its meaning')
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, f'{path}.{key}')
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f'{path}.{index}')
    walk(plan, 'lesson')
    for index, scene in enumerate(plan.get('scenes', [])):
        expressions = [('equation', scene['equation'])] if scene.get('equation') else []
        # A label such as "Start at an initial point (e.g., x₀ = 3)" is
        # English prose containing math, not a single equation with RHS "3)".
        for step_index, step in enumerate(scene.get('steps', [])):
            if '=' in step or '\\' in step:
                try:
                    validate_math_in_text(step)
                except MathRenderingError as exc:
                    issues.append(f'scenes.{index}.steps.{step_index}: {exc}')
        if scene['type'] == 'process' and 'gradient descent' in plan.get('title', '').lower():
            for step_index, step in enumerate(scene.get('steps', [])):
                if '=' in step and re.search(r'-\s*(?:\\?alpha|\\?eta|α|η|learning[_ -]?rate)\s*$', step):
                    issues.append(f'scenes.{index}.steps.{step_index}: incomplete gradient update; the learning rate needs a gradient factor. Use a short English label and put the complete update in an equation scene and narration_text.')
        expressions += [(f'visual_timeline.{i}.equation', event['parameters']['equation'])
                        for i, event in enumerate(scene.get('visual_timeline', []))
                        if event.get('parameters', {}).get('equation')]
        # Spoken arithmetic is checked when written with explicit numerals/equality.
        # Free-form verbal/symbolic truth is not inferred from plausibility.
        for expression in numerical_equalities(scene.get('narration_text', '')):
            expressions.append(('narration_text', expression))
        for field, expression in expressions:
            try:
                validate_calculation(expression)
            except MathRenderingError as exc:
                issues.append(f'scenes.{index}.{field}: {exc}')
        quadratic = scene.get('equation', '').split('=')[-1].replace(' ', '').replace('²', '^2') in {'x^2', 'x^{2}'}
        if quadratic:
            text = ' '.join([scene.get('narration_text', ''), *scene.get('steps', [])])
            for match in re.finditer(r"f(?P<prime>'|\\prime)?\(\s*(?P<x>-?\d+(?:\.\d+)?)\s*\)\s*=\s*(?P<value>-?\d+(?:\.\d+)?)", text):
                x, value = float(match['x']), float(match['value'])
                expected = 2*x if match['prime'] else x*x
                if abs(value-expected) > 0.00011:
                    issues.append(f'scenes.{index}: incorrect quadratic calculation {match.group()!r}; expected {expected:g}. Correct the explanation rather than hiding it with another visual.')
    return issues


def validate_content(plan: dict) -> None:
    issues = content_issues(plan)
    if issues:
        raise ValueError('Educational content validation failed: ' + '; '.join(issues))


def validate_revision_coverage(original: dict, revised: dict) -> None:
    """Duration revisions may adjust pacing, not silently delete instructional scenes."""
    before, after = original['scenes'], revised['scenes']
    if len(before) != len(after) or [s['type'] for s in before] != [s['type'] for s in after]:
        raise ValueError('Duration revision removed or reordered instructional scene types; preserve every scene, including the final summary, and condense narration instead.')
    for source, target in zip(before, after):
        if source['title'] != target['title']:
            raise ValueError('Duration revision changed the instructional sequence. Preserve each scene title and order; change narration and pacing instead.')
        if source.get('animation_params', {}).get('demo') == 'quadratic_descent':
            if source.get('narration_text', '').split() != target.get('narration_text', '').split() or source.get('equation') != target.get('equation'):
                raise ValueError('Preserve the computed quadratic demonstration narration and equation exactly; adjust pacing in other explanations instead of inventing numerical results.')
        if source.get('scene_id') and target.get('scene_id') not in ('', None, source['scene_id']):
            raise ValueError('Duration revision reordered stable scene IDs.')
        for field in ('scene_id', 'parent_scene_id', 'visual_template'):
            if source.get(field):
                target[field] = source[field]
        # Runtime timing/demo metadata is not requested from the LLM. Preserve it
        # while allowing fresh narration to be re-segmented and measured locally.
        if source.get('animation_params', {}).get('prepared_visuals'):
            target['animation_params'] = deepcopy(source['animation_params'])
    if before[-1]['type'] == 'summary' and after[-1]['type'] != 'summary':
        raise ValueError('Duration revision must preserve the final summary.')
