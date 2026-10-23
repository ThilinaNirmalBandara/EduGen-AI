"""One topic plugin; the scene graph, paths, themes and renderers are generic."""
from .components import label, particle_stream
from .models import StoryboardScene, VisualObject, Motion, Point, Theme

PHASES = ('evaporation', 'condensation', 'precipitation', 'collection')


def water_overview(identity, narration, theme, *, summary=False, objective=''):
    """Recap each phase in the shared landscape, without dropping spoken content."""
    from .models import NarrationCue
    initial = narration[0].model_copy(update={'concepts':['evaporation']})
    board = water_scene(identity,'evaporation',[initial],theme,objective)
    # Keep the reusable landscape and rail; explanation captions are complete
    # narration, so this overview doesn't add a second wall of text.
    board.objects = [o for o in board.objects if o.id in
        {'ground','mountain','river','lake','sun','cloud','lake-label'} or o.id.startswith('phase-')]
    board.motions=[]
    for obj in board.objects:
        if obj.id.startswith('phase-'): obj.color=theme.foreground
    board.narration=narration
    board.duration=sum(c.duration for c in narration)
    board.title='Cycle summary' if summary else 'Water cycle overview'
    board.objective=objective or 'Connect the four phases through a continuous landscape.'
    board.concepts=list(PHASES)
    board.repetition_justification='A concise concluding recap of the four connected phases.' if summary else ''
    for i,cue in enumerate(narration):
        explicit=[p for p in PHASES if p in cue.text.lower()]
        mapped=[c for c in cue.concepts if c in PHASES]
        phase=mapped[0] if len(mapped)==1 else explicit[0] if len(explicit)==1 else PHASES[min(3,i*4//len(narration))]
        cue.concepts=[phase]
        board.objects.append(VisualObject(id=f'cue-{i}-active-phase',kind='rectangle',role='decoration',
            x=.14+PHASES.index(phase)*.24,y=.188,width=.205,height=.006,color=theme.accent,z=35,
            visible_from=cue.start,visible_until=cue.start+cue.duration,concepts=[phase]))
        part=water_scene('cue',phase,[NarrationCue(id=cue.id,text=cue.text,start=0,duration=cue.duration,concepts=[phase])],theme)
        for obj in part.objects:
            if (obj.role=='illustration' and obj.id.startswith(('vapor-','droplets-','rain-','runoff-','groundwater-'))
                    and obj.id.rsplit('-',1)[-1] in {'0','1'}):
                old_id=obj.id
                obj.id=f'cue-{i}-{old_id}'
                obj.visible_from=cue.start
                obj.visible_until=cue.start+cue.duration
                board.objects.append(obj)
                for motion in part.motions:
                    if motion.object_id==old_id:
                        motion.id=f'cue-{i}-{motion.id}'
                        motion.object_id=obj.id
                        motion.start+=cue.start
                        motion.loop=False
                        # Motion spans this cue only, including short clauses.
                        motion.duration=cue.duration-(motion.start-cue.start)
                        board.motions.append(motion)
                        # Hidden outside its cue (including at t=0).
    return board


def water_scene(identity, phase, narration, theme: Theme, objective='') -> StoryboardScene:
    duration = sum(c.duration for c in narration)
    cue_ids = [c.id for c in narration]
    for cue in narration: cue.concepts = [phase]
    objects = [
        VisualObject(id='ground', kind='circle', role='decoration', x=0.5, y=0.72, width=0.94,
                     height=0.17, color=theme.ground),
        VisualObject(id='mountain', kind='asset', asset='mountain', x=0.80, y=0.55, width=0.33,
                     height=0.40, z=1, concepts=[phase]),
        VisualObject(id='river', kind='asset', asset='river', x=0.57, y=0.65, width=0.40,
                     height=0.22, z=2, concepts=[phase]),
        VisualObject(id='lake', kind='asset', asset='lake', x=0.235, y=0.705, width=0.40,
                     height=0.18, z=3, concepts=[phase]),
        VisualObject(id='sun', kind='asset', asset='sun', x=0.14, y=0.32, width=0.13,
                     height=0.23, z=5, concepts=[phase]),
        VisualObject(id='cloud', kind='asset', asset='cloud', x=0.60, y=0.32, width=0.28,
                     height=0.21, z=6, opacity=0.35 if phase == 'evaporation' else 1, concepts=[phase]),
        label('lake-label', 'Lake', 0.20, 0.79, 0.12, 0.065, phase, theme),
    ]
    for i, name in enumerate(PHASES):
        obj = label(f'phase-{name}', f'{i+1} {name.title()}', 0.14+i*0.24, 0.15, 0.225, 0.065,
                    name, theme)
        if name == phase: obj.color = theme.accent
        objects.append(obj)
    motions = []
    if phase == 'evaporation':
        objects += [label('energy-label', 'Solar heat', 0.145, 0.49, 0.18, 0.065, phase, theme),
                    label('vapor-label', 'Vapour: invisible gas', 0.41, 0.49, 0.32, 0.07, phase, theme)]
        parts, paths = particle_stream('vapor', phase, cue_ids, (0.27, 0.65), (0.49, 0.35), duration,
                                      curve=[(0.32, 0.48), (0.45, 0.45)], color='#2698C0')
        motions.append(Motion(id='cloud-forms', object_id='cloud', action='opacity', start=0,
            duration=duration, from_value=0.18, to_value=0.65, narration_ids=cue_ids, concepts=[phase]))
    elif phase == 'condensation':
        objects.append(label('cooling-label', 'Cooling turns vapour into droplets', 0.47, 0.49, 0.49, 0.075, phase, theme))
        parts, paths = particle_stream('droplets', phase, cue_ids, (0.43, 0.55), (0.60, 0.34), duration,
            curve=[(0.47, 0.37), (0.54, 0.29)], color='#429FC2', count=10, period=3.0)
        motions += [Motion(id='cloud-grows', object_id='cloud', action='scale', start=0, duration=duration,
            from_value=0.78, to_value=1.08, narration_ids=cue_ids, concepts=[phase]),
            Motion(id='cloud-thickens', object_id='cloud', action='opacity', start=0, duration=duration,
                   from_value=0.6, to_value=1, narration_ids=cue_ids, concepts=[phase])]
    elif phase == 'precipitation':
        objects.append(label('rain-label', 'Droplets fall as rain, snow or hail', 0.43, 0.49, 0.49, 0.075, phase, theme))
        parts, paths = particle_stream('rain', phase, cue_ids, (0.66, 0.405), (0.72, 0.65), duration,
                                      count=12, asset='rain', period=1.8)
        motions.append(Motion(id='cloud-drifts', object_id='cloud', action='move', start=0,
            duration=min(3, duration), path=[Point(x=0.60, y=0.32), Point(x=0.68, y=0.32)],
            narration_ids=cue_ids, concepts=[phase]))
    else:
        objects.append(label('flow-label', 'Runoff + groundwater return water', 0.47, 0.49, 0.48, 0.075, phase, theme))
        parts, paths = particle_stream('runoff', phase, cue_ids, (0.74, 0.58), (0.38, 0.71), duration,
            curve=[(0.70, 0.70), (0.49, 0.59)], color='#E8FCFF', count=8, period=3.3)
        underground, down = particle_stream('groundwater', phase, cue_ids, (0.56, 0.69), (0.54, 0.78),
            duration, count=4, color='#368FB9', period=2.6)
        parts += underground
        paths += down
        motions.append(Motion(id='lake-collects', object_id='lake', action='scale', start=0, duration=duration,
            from_value=0.96, to_value=1.04, narration_ids=cue_ids, concepts=[phase]))
    objects += parts
    motions += paths
    return StoryboardScene(id=identity, title=phase.title(), objective=objective or f'Visually explain {phase}.',
        duration=duration, concepts=[phase], layout='landscape', narration=narration, objects=objects,
        motions=motions, layout_justification='Keep the same landscape so the learner can follow the same water through successive phases.')
