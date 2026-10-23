"""Deterministic, frame-addressable scene state shared with the JS adapter."""
import math
from .models import Motion, Storyboard, StoryboardScene, VisualObject


def path_point(points, progress):
    """Bezier for 2-4 points, ordered polyline for longer reusable paths."""
    if len(points) <= 4:
        values = [(p.x, p.y) for p in points]
        while len(values) > 1:
            values = [(a[0]+(b[0]-a[0])*progress, a[1]+(b[1]-a[1])*progress)
                      for a, b in zip(values, values[1:])]
        return values[0]
    index = min(len(points)-2, int(progress*(len(points)-1)))
    ratio = progress*(len(points)-1)-index
    a, b = points[index:index+2]
    return a.x+(b.x-a.x)*ratio, a.y+(b.y-a.y)*ratio


def object_state(obj: VisualObject, motions: list[Motion], time: float) -> dict:
    state = {'x': obj.x, 'y': obj.y, 'scale': 1.0, 'opacity': obj.opacity, 'rotate': 0.0}
    for motion in motions:
        if motion.object_id != obj.id or time < motion.start:
            continue
        progress = (time-motion.start)/motion.duration
        if motion.loop:
            progress %= 1
        else:
            progress = min(1, progress)
        if motion.easing == 'smooth':
            progress = progress*progress*(3-2*progress)
        if motion.action == 'move':
            state['x'], state['y'] = path_point(motion.path, progress)
        else:
            state[motion.action] = motion.from_value+(motion.to_value-motion.from_value)*progress
    if time<obj.visible_from or (obj.visible_until is not None and time>=obj.visible_until):
        state['opacity']=0.0
    return state


def scene_at(board: Storyboard, time: float):
    start = 0.0
    for index, scene in enumerate(board.scenes):
        if time < start+scene.duration or index == len(board.scenes)-1:
            return index, scene, max(0.0, min(scene.duration, time-start))
        start += scene.duration


def active_narration(scene: StoryboardScene, time: float):
    return next((c for c in scene.narration if c.start <= time < c.start+c.duration), scene.narration[-1])
