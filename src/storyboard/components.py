"""Reusable graphical components compile to data, not renderer-specific code."""
from .models import VisualObject, Motion, Point


def label(identity, text, x, y, width, height, concept, theme, font_size=28):
    return VisualObject(id=identity, kind='label', role='label', x=x, y=y, width=width, height=height,
                        color=theme.foreground, text=text, font_size=font_size, z=30, concepts=[concept])


def particle_stream(prefix, concept, cue_ids, start, end, duration, *, count=8,
                    color='#329CC8', asset='', curve=None, period=3.5):
    objects, motions = [], []
    for i in range(count):
        spread = (i-(count-1)/2)*0.012
        a, b = (start[0]+spread, start[1]), (end[0]+spread, end[1])
        path = [a, b] if curve is None else [a, *curve, b]
        identity = f'{prefix}-{i}'
        objects.append(VisualObject(id=identity, kind='asset' if asset else 'circle', asset=asset,
            x=a[0], y=a[1], width=0.013 if asset else 0.009, height=0.032 if asset else 0.016,
            color=color, opacity=0.7, z=12, concepts=[concept]))
        delay = min(i*0.10, duration*0.4*i/max(1,count-1))
        cycle_duration = min(period, duration-delay)
        motions.append(Motion(id=identity+'-move', object_id=identity, action='move', start=delay,
            duration=cycle_duration, path=[Point(x=x, y=y) for x, y in path], loop=True,
            narration_ids=cue_ids, concepts=[concept], easing='linear'))
        motions.append(Motion(id=identity+'-fade', object_id=identity, action='opacity', start=delay,
            duration=cycle_duration, from_value=0.85, to_value=0.15, loop=True,
            narration_ids=cue_ids, concepts=[concept], easing='linear'))
    return objects, motions
