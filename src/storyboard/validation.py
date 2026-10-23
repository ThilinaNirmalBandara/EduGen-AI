"""Pre-render quality findings; never delete educational content automatically."""
import re
import math
from difflib import SequenceMatcher
from PIL import ImageFont
from src.video.layout import validate_contrast
from .models import Storyboard
from .motion import object_state


class StoryboardQualityError(ValueError):
    pass


def rgb(color):
    return tuple(int(color[i:i+2], 16)/255 for i in (1, 3, 5))


def fit_text(text, width, height, *, font_size=32, canvas_width=1280, canvas_height=720):
    """Local font metrics, whole words, no ellipses or arbitrary text truncation."""
    for size in range(font_size, 27, -1):
        font = ImageFont.truetype('DejaVuSans.ttf', size)
        lines, current = [], ''
        for word in text.split():
            candidate = f'{current} {word}'.strip()
            if font.getlength(candidate) > width*canvas_width-20 and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current: lines.append(current)
        if lines and max(font.getlength(line) for line in lines) <= width*canvas_width-16 and len(lines)*size*1.22 <= height*canvas_height-8:
            return '\n'.join(lines), size
    raise StoryboardQualityError(f'Text cannot fit at a readable 28px minimum: {text!r}. Split the label or change the layout.')


def header_box(text,*,heading=False):
    """Use a larger multi-line header region before rejecting legacy long titles."""
    normal=(.30,.065,.55,.085,40) if heading else (.80,.065,.32,.085,32)
    expanded=(.29,.095,.55,.16,36) if heading else (.78,.095,.40,.16,30)
    for box in (normal,expanded):
        try:
            fit_text(text,box[2],box[3],font_size=box[4])
            return box
        except StoryboardQualityError:
            if box is expanded: raise


def quality_issues(board: Storyboard) -> list[str]:
    issues, covered, previous = [], set(), None
    for background in (board.theme.background,board.theme.sky_top):
        try: validate_contrast(rgb(board.theme.foreground),rgb(background))
        except ValueError as exc: issues.append(f'Theme heading contrast: {exc}')
    try: header_box(board.title,heading=True)
    except ValueError as exc: issues.append(f'Storyboard heading: {exc}')
    sentences = []
    scene_roles={s.id:s.purpose for s in board.scenes}
    for scene in board.scenes:
        try: header_box(scene.title)
        except ValueError as exc: issues.append(f'{scene.id} heading: {exc}')
        if previous and previous.layout == scene.layout and not scene.layout_justification.strip():
            issues.append(f'{scene.id}: consecutive identical layouts need a continuity justification or a different layout')
        text = ' '.join(c.text for c in scene.narration)
        for sentence in re.split(r'(?<=[.!?])\s+', text):
            normalized = ' '.join(re.findall(r'\w+', sentence.lower()))
            if len(normalized.split()) >= 8:
                duplicated=[(source,old) for source,old in sentences if SequenceMatcher(None,old,normalized).ratio()>=.9]
                # A short recalled definition followed by new detail is a valid
                # introduction-to-explanation transition, not a duplicate scene.
                intro_reference=bool(duplicated) and all(scene_roles.get(source)=='introduce' and source!=scene.id
                    for source,_ in duplicated) and len(normalized.split())<.35*len(text.split())
                if not scene.repetition_justification and duplicated and not intro_reference:
                    issues.append(f'{scene.id}: repeated educational explanation; revise or justify the recap, do not silently remove it')
                sentences.append((scene.id, normalized))
        if scene.native_scene is not None:
            from src.video.timeline import validate_timeline
            from src.content import validate_content
            native=scene.native_scene.model_dump()
            try:
                validate_content({'title':board.title,'scenes':[native]})
                validate_timeline(native)
                if native['narration_text'].split()!=text.split():
                    raise ValueError('Native mathematical narration differs from storyboard cues.')
                if abs(scene.duration-max(c.start+c.duration for c in scene.narration))>.04:
                    raise ValueError('Native scene does not preserve complete measured narration.')
            except ValueError as exc: issues.append(f'{scene.id}: {exc}')
            covered.update(scene.concepts)
            previous=scene
            continue
        objects = {o.id: o for o in scene.objects}
        meaningful = [m for m in scene.motions if objects[m.object_id].role == 'illustration'
                      and (m.action == 'move' and len({(p.x, p.y) for p in m.path}) > 1
                           or m.action != 'move' and abs(m.to_value-m.from_value) > 0.03)]
        for cue in scene.narration:
            relevant = [m for m in meaningful if cue.id in m.narration_ids and set(cue.concepts)&set(m.concepts)
                        and set(m.concepts)&set(objects[m.object_id].concepts)]
            intervals = sorted((max(cue.start,m.start,objects[m.object_id].visible_from),
                                min(cue.start+cue.duration,scene.duration if m.loop else m.start+m.duration,
                                    objects[m.object_id].visible_until or scene.duration)) for m in relevant)
            intervals = [(start,stop) for start,stop in intervals if stop>start]
            end = cue.start
            for start, stop in intervals:
                if stop <= start: continue
                if start-end > 8:
                    issues.append(f'{cue.id}: excessively static interval without relevant motion')
                end = max(end, stop)
            if not intervals or cue.start+cue.duration-end > 8:
                issues.append(f'{cue.id}: narration lacks relevant visual demonstration')
            else:
                covered.update(set(cue.concepts)&set(scene.concepts)&{concept for m in relevant for concept in m.concepts})
        times = {0, scene.duration}
        for motion in scene.motions:
            times.update(motion.start+motion.duration*i/16 for i in range(17))
        for obj in scene.objects:
            times.add(min(scene.duration,obj.visible_from+.001))
            if obj.visible_until is not None: times.add(max(0,obj.visible_until-.001))
        labels = []
        for obj in scene.objects:
            if obj.kind == 'label':
                try:
                    fit_text(obj.text, obj.width, obj.height, font_size=obj.font_size,
                             canvas_width=1280, canvas_height=720)
                    validate_contrast(rgb(obj.color), rgb(board.theme.panel), opacity=obj.opacity)
                except ValueError as exc:
                    issues.append(f'{obj.id}: {exc}')
                labels.append(obj)
            for time in times:
                state = object_state(obj, scene.motions, min(time, scene.duration))
                if obj.kind=='label' and state['opacity']>0:
                    if obj.font_size*state['scale'] < 28-.001:
                        issues.append(f'{obj.id}: animated text falls below the readable 28px design minimum')
                    try: validate_contrast(rgb(obj.color),rgb(board.theme.panel),opacity=state['opacity'])
                    except ValueError as exc: issues.append(f'{obj.id}: {exc}')
                angle = state['rotate']
                half_w = (abs(math.cos(angle))*obj.width + abs(math.sin(angle))*obj.height*9/16)*state['scale']/2
                half_h = (abs(math.cos(angle))*obj.height + abs(math.sin(angle))*obj.width*16/9)*state['scale']/2
                if state['x']-half_w < 0.015 or state['x']+half_w > 0.985 or state['y']-half_h < 0.015 or state['y']+half_h > 0.985:
                    issues.append(f'{scene.id}/{obj.id}: object or movement exceeds safe frame boundaries')
                    break
            if obj.kind=='polyline' and any(not .015<=p.x<=.985 or not .015<=p.y<=.985 for p in obj.points):
                issues.append(f'{scene.id}/{obj.id}: curve extends outside safe frame boundaries')
        # Illustrations deliberately layer (clouds/rain/mountains). Text labels may not.
        for i, a in enumerate(labels):
            for b in labels[i+1:]:
                if min(a.visible_until or scene.duration,b.visible_until or scene.duration)<=max(a.visible_from,b.visible_from):
                    continue
                if abs(a.x-b.x) < (a.width+b.width)/2-0.003 and abs(a.y-b.y) < (a.height+b.height)/2-0.003:
                    issues.append(f'{scene.id}: overlapping readable labels {a.id} and {b.id}')
        for cue in scene.narration:
            try: fit_text(cue.text, 0.91, 0.14, font_size=30, canvas_width=1280, canvas_height=720)
            except ValueError as exc: issues.append(f'{cue.id}: {exc}')
        previous = scene
    for missing in sorted(set(board.required_concepts)-covered):
        issues.append(f'Missing essential concept: {missing}')
    return list(dict.fromkeys(issues))


def validate_storyboard(board: Storyboard | dict) -> Storyboard:
    # Revalidate even an instance: compilers and callers can mutate Pydantic
    # objects after construction. A typed object is not a validation bypass.
    board = Storyboard.model_validate(board.model_dump() if isinstance(board,Storyboard) else board)
    from .assets import validate_assets
    validate_assets()
    issues = quality_issues(board)
    if issues:
        raise StoryboardQualityError('Storyboard quality validation failed:\n- ' + '\n- '.join(issues))
    return board
