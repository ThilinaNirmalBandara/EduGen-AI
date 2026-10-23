"""Compile lesson meaning and measured audio to audited visual instructions."""
from copy import deepcopy
from .models import Storyboard, StoryboardScene, NarrationCue
from .themes import load_theme
from .water_cycle import PHASES, water_scene, water_overview
from .validation import validate_storyboard
from src.video.timeline import bind_audio_timeline, validate_timeline
from src.video.sequence import identify_lesson, validate_sequence
from src.video.contract import validate_lesson


def _cues(scene,audio,concepts):
    if not audio.timings:
        raise ValueError('Phrase timings are required for storyboard scheduling. Regenerate narration with Kokoro.')
    if ' '.join(t.text for t in audio.timings).split()!=scene.get('narration_text','').split():
        raise ValueError('Storyboard narration differs from the validated lesson. No spoken content will be dropped or invented.')
    events=scene.get('visual_timeline') or []
    measured=scene.get('animation_params',{}).get('timing_source')=='measured'
    cues=[]
    for i,timing in enumerate(audio.timings):
        # Bound events are one per phrase; timing.cue_index still names the
        # original unsplit source. Never use it to re-index bound/split events.
        source_index=i if measured else timing.cue_index
        source=events[source_index] if source_index<len(events) else {}
        cues.append(NarrationCue(id=f'{scene["scene_id"]}-cue-{i}',text=timing.text,start=timing.start,
            duration=timing.duration,concepts=list(concepts),source_scene_id=scene['scene_id'],
            source_event_id=source.get('source_event_id') or source.get('event_id') or f'{scene["parent_scene_id"]}:cue:{timing.cue_index}',
            step_index=source.get('parameters',{}).get('step_index')))
    if abs(cues[-1].start+cues[-1].duration-audio.duration)>.025:
        raise ValueError('Storyboard cues do not cover the complete measured audio.')
    return cues


def _native(scene,cues,audio,selection):
    native=deepcopy(scene)
    native['duration']=max(3,min(45,audio.duration+.35))
    if native.get('animation_params',{}).get('timing_source')!='measured':
        native=bind_audio_timeline(native,audio)
    validate_timeline(native)
    return StoryboardScene(id=scene['scene_id'],title=scene['title'],
        objective=scene.get('learning_objective') or scene['title'],duration=audio.duration,
        concepts=selection.concepts,layout='math',background='solid',narration=cues,
        native_scene=native,template='native_manim',selection_reason=selection.reason)


def compile_storyboard(lesson,audio_segments,*,theme=None,evidence=None,prompt=''):
    """No second LLM call: choose visuals from each validated educational concept."""
    from .selection import select_visual
    from .diagrams import diagram_scene
    validate_lesson(lesson)
    lesson=identify_lesson(lesson)
    validate_sequence(lesson)
    if len(lesson['scenes'])!=len(audio_segments):
        raise ValueError('Storyboard needs one narration segment per lesson scene.')
    theme=theme or load_theme()
    context=' '.join([prompt,*[s.get('learning_objective','')+' '+s.get('narration_text','') for s in lesson['scenes']]])
    compiled=[]
    for index,(scene,audio) in enumerate(zip(lesson['scenes'],audio_segments)):
        selection=select_visual(scene,context=context)
        cues=_cues(scene,audio,selection.concepts)
        for cue in cues: cue.evidence=(evidence or {}).get(scene['scene_id'],[])
        kind=selection.template
        if kind in {'native_manim','native_math','math'}:
            board_scene=_native(scene,cues,audio,selection)
        elif kind.startswith('water_') and kind.removeprefix('water_') in PHASES:
            board_scene=water_scene(scene['scene_id'],kind.removeprefix('water_'),cues,theme,scene.get('learning_objective',''))
        elif kind in {'water_overview','water_summary','water_process'}:
            if kind=='water_process':
                aliases={'evaporation':('evapor','vapour rises','vapor rises'),
                         'condensation':('condens',),'precipitation':('precipit','rain falls'),
                         'collection':('collect','runoff','groundwater')}
                phase_order=[next((phase for phase,names in aliases.items() if any(name in step.lower() for name in names)),None)
                             for step in scene.get('steps',[])]
                for cue in cues:
                    if cue.step_index is not None and cue.step_index<len(phase_order) and phase_order[cue.step_index]:
                        cue.concepts=[phase_order[cue.step_index]]
            board_scene=water_overview(scene['scene_id'],cues,theme,summary=scene['type']=='summary',
                objective=scene.get('learning_objective',''))
        else:
            board_scene=diagram_scene(scene,cues,theme,selection,index=index)
        board_scene.id=scene['scene_id']
        board_scene.source_scene_id=scene['scene_id']
        board_scene.template=('manim_'+(scene.get('visual_template') or scene['type'])) if board_scene.native_scene is not None else kind
        board_scene.selection_reason=selection.reason
        board_scene.selection_assets=selection.assets
        introduction=scene['type']=='title' or (index==0 and scene.get('learning_objective','').lower().startswith(('introduce','overview')))
        board_scene.purpose=('summarize' if scene['type']=='summary' else 'introduce' if introduction
                             else 'compare' if scene['type']=='comparison' else 'explain')
        if scene['type']=='summary':
            board_scene.repetition_justification='Concluding recap that connects previously explained concepts.'
        if scene['type']=='worked_example' and scene.get('animation_params',{}).get('demo')=='quadratic_descent':
            board_scene.repetition_justification='Reuse the deterministic numerical givens from the graph, now expanding them into arithmetic substitutions rather than replaying the graph.'
        if compiled and board_scene.layout==compiled[-1].layout and not board_scene.layout_justification:
            if board_scene.template!=compiled[-1].template:
                board_scene.layout_justification='Related concepts use the same coordinate frame with different explanatory actions.'
            elif compiled[-1].purpose=='introduce' and board_scene.purpose=='explain' and [
                (o.kind,o.width,o.height) for o in board_scene.objects]!=[
                (o.kind,o.width,o.height) for o in compiled[-1].objects]:
                board_scene.layout_justification='Expand the introductory idea into a structurally different map of the supplied explanatory relationships.'
            elif scene.get('parent_scene_id')==lesson['scenes'][index-1].get('parent_scene_id'):
                board_scene.layout_justification='Measured narration split: continue the same instructional sequence and source events.'
        compiled.append(board_scene)
    required=list(dict.fromkeys(c for s in compiled for c in s.concepts))
    if any(word in (context+' '+lesson['title']).lower() for word in ('water cycle','water-cycle','hydrological cycle')):
        required=list(dict.fromkeys([*required,*PHASES]))
    board=Storyboard(title=lesson['title'],topic='multisubject',theme=theme,
        required_concepts=required,scenes=compiled,provenance={'timing_source':'measured Kokoro phrases','compiler':'concept-aware-v2'})
    return validate_storyboard(board)


def flow_scene(scene,cues,theme,index):
    """Compatibility wrapper for callers constructing connected process boards."""
    from .selection import select_visual
    from .diagrams import diagram_scene
    selection=select_visual(scene)
    if not scene.get('steps') and not scene.get('bullets') and not scene.get('important_concepts'):
        raise ValueError('Provide related concepts rather than decorative motion.')
    return diagram_scene(scene,cues,theme,selection,index=index)
