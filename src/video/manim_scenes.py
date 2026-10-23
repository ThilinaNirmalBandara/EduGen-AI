"""Fixed, audited Manim templates driven by measured narration events."""
from __future__ import annotations

import json
import os
import re
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from manim import (
    Axes, BLUE_C, DOWN, Dot, FadeIn, LEFT, Line, MathTex, RED_C,
    Scene, SurroundingRectangle, Text, VGroup, WHITE, YELLOW_C,
    UpdateFromAlphaFunc, config, linear,
)
from src.video.layout import LayoutBox, MIN_FONT_SIZE, VisualQualityError, validate_layout, validate_contrast
from src.video.mathematics import gradient_updates, mathtex, number, polynomial_function, sanitize_math, quadratic_loss_history
from src.video.timeline import estimated_timeline, validate_timeline


def fitted_text(value, *, font_size=32, max_width=11.5, max_height=2.5):
    """Reflow rather than shrinking below a readable font size; preserve all words."""
    for size in range(font_size, MIN_FONT_SIZE - 1, -2):
        width = max(10, int(max_width * 5.1 * 32 / size))
        for columns in (width, int(width * 0.85), int(width * 0.7)):
            content = '\n'.join(textwrap.wrap(value, width=columns, break_long_words=False, break_on_hyphens=False))
            label = Text(content or ' ', font_size=size, color=WHITE)
            if label.width <= max_width and label.height <= max_height:
                return label
    raise VisualQualityError(f'Text cannot fit at {MIN_FONT_SIZE}pt: {value!r}. Use shorter display labels or separate scenes.')


def fitted_math(value, *, max_width=11.5, font_size=42):
    result = mathtex(value, font_size=font_size)
    if result.width > max_width:
        scale = max_width / result.width
        if font_size * scale < MIN_FONT_SIZE:
            raise VisualQualityError(f'Equation is too wide at readable font sizes: {value!r}. Split it into calculation steps.')
        result.scale(scale)
    return result


def process_label(value, *, font_size=36, max_height=1.2):
    """Legacy process labels may contain math; never show raw TeX using Text."""
    if '=' not in value and not re.search(r'\\[A-Za-z]+|[_^]', value):
        return fitted_text(value, font_size=font_size, max_width=10.5, max_height=max_height)
    prefix, separator, expression = value.partition(':')
    result = fitted_math(expression if separator else value, font_size=font_size, max_width=10.5)
    if separator:
        result = VGroup(fitted_text(prefix, font_size=28, max_height=0.5), result).arrange(DOWN, buff=0.1)
    if result.height > max_height:
        raise VisualQualityError('Process equation is too tall at readable size; move the calculation into an equation scene.')
    return result


class TemplateScene(Scene):
    def construct(self):
        path = os.environ.get('EDUGEN_SCENE_FILE')
        if not path:
            raise RuntimeError('EDUGEN_SCENE_FILE must point to validated scene JSON')
        self.data = json.loads(Path(path).read_text(encoding='utf-8'))
        config.tex_dir = str(Path(os.environ.get('EDUGEN_TEX_DIR', 'outputs/cache/tex')).resolve())
        self.events = self.data.get('visual_timeline') or estimated_timeline(self.data, cover_silent=True)
        self.data['visual_timeline'] = self.events
        validate_timeline(self.data)
        self.heading = fitted_text(self.data['title'], font_size=38, max_height=1.0).move_to([0, 3.15, 0])
        self.add(self.heading)
        self.regions = {'heading': self.heading}
        self.report = []
        kind = self.data['type']
        template = self.data.get('visual_template', '')
        if template == 'learning_rates':
            self.render_loss_history(comparison=True)
        elif template == 'convergence':
            self.render_loss_history()
        elif template == 'tangent':
            self.render_tangent()
        elif template == 'summary':
            self.render_summary()
        elif template == 'concept' and kind in {'title', 'bullets'}:
            self.render_concept()
        elif template == 'numerical' and self.data.get('animation_params', {}).get('demo') == 'quadratic_descent':
            self.render_numerical()
        elif kind == 'graph' and self.data.get('graph_type') == 'parabola' and self.data.get('animation_params', {}).get('demo') != 'quadratic_descent':
            self.render_function()
        elif kind == 'graph' or self.data.get('animation_params', {}).get('demo') == 'quadratic_descent' or self.data.get('animation_params', {}).get('illustration') == 'quadratic_descent':
            self.render_descent(worked=kind == 'worked_example')
        elif kind in {'equation', 'worked_example'}:
            self.render_equation(worked=kind == 'worked_example')
        elif kind == 'process':
            self.render_process()
        elif kind == 'comparison':
            self.render_comparison()
        else:
            self.render_cards()
        remaining = float(self.data['duration']) - float(self.time)
        if remaining > 0:
            self.wait(remaining)
        report_path = os.environ.get('EDUGEN_LAYOUT_REPORT')
        if report_path:
            Path(report_path).write_text(json.dumps(self.report, indent=2))

    def audit(self, index):
        boxes, labels, seen = [], [], set()
        for name, obj in self.regions.items():
            boxes.append(LayoutBox(name, float(obj.get_left()[0]), float(obj.get_right()[0]),
                                   float(obj.get_bottom()[1]), float(obj.get_top()[1])))
            for child in obj.get_family():
                if isinstance(child, (Text, MathTex)) and id(child) not in seen:
                    seen.add(id(child))
                    labels.append(LayoutBox(name + ' label', float(child.get_left()[0]), float(child.get_right()[0]),
                                            float(child.get_bottom()[1]), float(child.get_top()[1]), float(child.font_size)))
                    # MathTex can contain a transparent grouping object; inspect
                    # the visible glyph paths rather than its container opacity.
                    for glyph in child.family_members_with_points():
                        try:
                            validate_contrast(glyph.get_color().to_rgb(), config.background_color.to_rgb(),
                                              opacity=float(glyph.get_fill_opacity()))
                        except VisualQualityError as exc:
                            raise VisualQualityError(f'{name}: {type(child).__name__} text contrast failed '
                                f'(color={glyph.get_color()}, opacity={glyph.get_fill_opacity()}): {exc}') from exc
        validate_layout(boxes, width=config.frame_width, height=config.frame_height)
        validate_layout(labels, width=config.frame_width, height=config.frame_height)
        self.report.append({'event': index, 'event_id': self.events[index].get('event_id'),
                            'scene_id': self.data.get('scene_id'), 'template': self.data.get('visual_template'),
                            'step_index': self.events[index].get('parameters', {}).get('step_index'),
                            'time': float(self.time), 'regions': [box.__dict__ for box in boxes],
                            'labels': [box.__dict__ for box in labels]})
        preview = os.environ.get('EDUGEN_PREVIEW_DIR')
        if preview:
            from PIL import Image
            root = Path(preview)
            root.mkdir(parents=True, exist_ok=True)
            self.renderer.update_frame(self, ignore_skipping=True)
            Image.fromarray(self.renderer.get_frame()).save(root / f'event_{index + 1:03d}.png')

    def event(self, index, animations=()):
        cue = self.events[index]
        start, length = float(cue['start_time']), float(cue['animation_duration'])
        if start > float(self.time) + 0.001:
            self.wait(start - float(self.time))
        if animations:
            self.play(*animations, run_time=max(1/30, length * 0.65), rate_func=linear)
        self.audit(index)
        remaining = start + length - float(self.time)
        if remaining > 0.001:
            self.wait(remaining)

    def swap(self, old, new):
        if old is not None:
            self.remove(old)
        return FadeIn(new)

    def caption(self, cue, *, y=-2.7, width=11.5):
        return fitted_text(cue.get('narration_text') or self.data['title'], font_size=30,
                           max_width=width, max_height=1.4).move_to([0, y, 0])

    def render_cards(self):
        old = None
        bullets = self.data.get('bullets', [])
        for index, cue in enumerate(self.events):
            label = bullets[min(index * len(bullets) // len(self.events), len(bullets)-1)] if bullets else self.data.get('subtitle', '')
            concept = fitted_text(label or self.data['title'], font_size=36, max_height=1.6).move_to([0, 1.2, 0])
            frame = SurroundingRectangle(concept, buff=0.35, color=BLUE_C)
            caption = self.caption(cue, y=-0.9)
            count = fitted_text(f'{index+1} / {len(self.events)}', font_size=28, max_width=3).move_to([0, -3.15, 0])
            card = VGroup(frame, concept, caption, count)
            self.regions['content'] = card
            self.event(index, [self.swap(old, card)])
            old = card

    def render_concept(self):
        """An active concept and its causal relationship, not another loss curve."""
        concepts = self.data.get('important_concepts') or self.data.get('bullets') or [self.data.get('subtitle') or self.data['title']]
        old = None
        for index, cue in enumerate(self.events):
            params = cue.get('parameters', {})
            active = min(len(concepts)-1, int(params.get('visual_index', index))*len(concepts)//max(1, int(params.get('visual_count', len(self.events)))))
            label = fitted_text(concepts[active], font_size=36, max_width=10.5, max_height=1.5).move_to([0, 1.1, 0])
            frame = SurroundingRectangle(label, color=YELLOW_C, buff=0.3)
            connector = Line([0, 0.1, 0], [0, -0.6, 0], color=BLUE_C)
            panel = VGroup(label, frame, connector, self.caption(cue, y=-1.7))
            self.regions['concept'] = panel
            self.event(index, [self.swap(old, panel)])
            old = panel

    def render_summary(self):
        """Accumulate the takeaways; the ending retains the complete conclusion."""
        bullets = self.data.get('bullets') or self.data.get('important_concepts') or [self.data['title']]
        old = None
        for index, cue in enumerate(self.events):
            params = cue.get('parameters', {})
            position = int(params.get('visual_index', index))
            total = max(1, int(params.get('visual_count', len(self.events))))
            active = min(len(bullets)-1, position*len(bullets)//total)
            if position >= total-1:
                active = len(bullets)-1
            rows = VGroup(*(fitted_text(f'{i+1}. {text}', font_size=30, max_width=11,
                                          max_height=0.85) for i, text in enumerate(bullets[:active+1])))
            rows.arrange(DOWN, aligned_edge=LEFT, buff=0.25).move_to([0, 0.8, 0])
            focus = SurroundingRectangle(rows[-1], color=YELLOW_C, buff=0.12)
            panel = VGroup(rows, focus, self.caption(cue, y=-2.8))
            self.regions['summary'] = panel
            self.event(index, [self.swap(old, panel)])
            old = panel

    def render_numerical(self):
        params = self.data.get('animation_params', {})
        states = gradient_updates(float(params.get('initial_x', 3)), float(params.get('learning_rate', 0.2)),
                                  int(params.get('iterations', 2)))
        old = None
        for index, cue in enumerate(self.events):
            state = states[min(int(cue.get('parameters', {}).get('iteration', 0)), len(states)-1)]
            phase = cue.get('action', 'reveal')
            formulas = {
                'gradient': [f"f'(x)=2x", f"f'({number(state.x)})=2\\cdot {number(state.x)}={number(state.gradient)}"],
                'update': [f"x_{{{state.iteration+1}}}=x_{{{state.iteration}}}-\\alpha f'(x_{{{state.iteration}}})",
                           f"={number(state.x)}-{number(state.learning_rate)}\\cdot {number(state.gradient)}={number(state.next_x)}"],
                'loss': [f"f(x_{{{state.iteration+1}}})=({number(state.next_x)})^2={number(state.next_loss)}",
                         f"{number(state.next_loss)}<{number(state.loss)}"],
            }.get(phase, [f'f(x)=x^2,\\quad\\alpha={number(state.learning_rate)}', f'x_{{{state.iteration}}}={number(state.x)}'])
            rows = VGroup(*(fitted_math(formula, font_size=42, max_width=11) for formula in formulas)).arrange(DOWN, buff=0.5).move_to([0, 0.65, 0])
            highlight = SurroundingRectangle(rows[-1], color=YELLOW_C, buff=0.2)
            iteration = fitted_text(f'Iteration {state.iteration+1}: {phase}', font_size=28, max_height=0.6).move_to([0, 2.25, 0])
            panel = VGroup(rows, highlight, iteration, self.caption(cue, y=-2.5))
            self.regions['numerical'] = panel
            self.event(index, [self.swap(old, panel)])
            old = panel

    def render_tangent(self):
        """Show local rise/run and the negative-gradient direction explicitly."""
        params = self.data.get('animation_params', {})
        states = gradient_updates(float(params.get('initial_x', 3)), float(params.get('learning_rate', 0.2)), int(params.get('iterations', 2)))
        axes = Axes(x_range=[-3.3, 3.3, 1], y_range=[0, 11, 2], x_length=6, y_length=3.8,
                    axis_config={'include_tip': False}).move_to([-3, 0.4, 0])
        curve = axes.plot(lambda x: x*x, x_range=[-3.1, 3.1], color=BLUE_C)
        graph = VGroup(axes, curve)
        self.add(graph)
        self.regions['graph'] = graph
        first_state = states[min(int(self.events[0].get('parameters', {}).get('iteration', 0)), len(states)-1)]
        point = Dot(axes.c2p(first_state.x, first_state.loss), color=RED_C)
        self.add(point)
        old = None
        for index, cue in enumerate(self.events):
            state = states[min(int(cue.get('parameters', {}).get('iteration', 0)), len(states)-1)]
            x = state.next_x if cue.get('action') in {'update', 'loss', 'highlight'} else state.x
            slope, loss = 2*x, x*x
            dx = 0.25
            tangent = Line(axes.c2p(x-dx, loss-slope*dx), axes.c2p(x+dx, loss+slope*dx), color=YELLOW_C)
            run = Line(axes.c2p(x-dx, loss-slope*dx), axes.c2p(x+dx, loss-slope*dx), color=WHITE)
            rise = Line(axes.c2p(x+dx, loss-slope*dx), axes.c2p(x+dx, loss+slope*dx), color=WHITE)
            formulas = VGroup(*(fitted_math(text, font_size=34, max_width=5.2) for text in (
                f'x={number(x)}', f"f'(x)=2x={number(slope)}", f'-\\nabla f(x)={number(-slope)}'))).arrange(DOWN, buff=0.4).move_to([3.65, 0.5, 0])
            caption = self.caption(cue, y=-2.9)
            panel = VGroup(tangent, run, rise, formulas, caption)
            self.regions['slope'], self.regions['caption'] = formulas, caption
            start = float(axes.p2c(point.get_center())[0])
            movement = UpdateFromAlphaFunc(point, lambda obj, alpha, a=start, b=x:
                obj.move_to(axes.c2p(a+(b-a)*alpha, (a+(b-a)*alpha)**2)))
            self.event(index, [self.swap(old, panel), movement])
            old = panel

    def render_loss_history(self, comparison=False):
        """Plot computed loss histories, distinct from the parameter-space curve."""
        rates = [0.05, 0.2, 1.05] if comparison else [0.2]
        colors = [BLUE_C, YELLOW_C, RED_C] if comparison else [YELLOW_C]
        histories = [quadratic_loss_history(rate) for rate in rates]
        maximum = max(max(values) for values in histories)*1.1
        axes = Axes(x_range=[0, 6.5, 1], y_range=[0, maximum, maximum/4], x_length=10, y_length=3,
                    axis_config={'include_tip': False}).move_to([0, 0.45, 0])
        axis_label = fitted_text('Quadratic loss versus update number', font_size=28, max_height=0.5).move_to([0, 2.3, 0])
        graph = VGroup(axes, axis_label)
        for value in (0, 2, 4, 6):
            tick = fitted_math(str(value), font_size=28).move_to(axes.c2p(value, 0)+[0, -0.3, 0])
            graph.add(tick)
        for value in (0, round(maximum/2), round(maximum)):
            tick = fitted_math(str(value), font_size=28).move_to(axes.c2p(0, value)+[-0.55, 0, 0])
            graph.add(tick)
        self.add(graph)
        self.regions['history'] = graph
        old = None
        for index, cue in enumerate(self.events):
            params = cue.get('parameters', {})
            progress = (int(params.get('visual_index', index))+1)/max(1, int(params.get('visual_count', len(self.events))))
            upto = max(1, min(6, round(6*progress)))
            lines, labels = VGroup(), VGroup()
            for rate, color, values in zip(rates, colors, histories):
                for i in range(upto):
                    lines.add(Line(axes.c2p(i, values[i]), axes.c2p(i+1, values[i+1]), color=color))
                lines.add(Dot(axes.c2p(upto, values[upto]), color=color))
                label = VGroup(fitted_math(f'\\alpha={number(rate)}', font_size=30, max_width=3),
                               fitted_math(f'L={number(values[upto])}', font_size=28, max_width=3.4)).arrange(DOWN, buff=0.08)
                label.set_color(color)
                labels.add(label)
            labels.arrange(LEFT, buff=1.2).move_to([0, -2.05, 0])
            caption = self.caption(cue, y=-3.1)
            panel = VGroup(lines, labels, caption)
            self.regions['legend'], self.regions['caption'] = labels, caption
            self.event(index, [self.swap(old, panel)])
            old = panel

    def render_process(self):
        steps, old = self.data['steps'], None
        for index, cue in enumerate(self.events):
            active = int(cue.get('parameters', {}).get('step_index',
                         min(len(steps)-1, index * len(steps) // len(self.events))))
            if not 0 <= active < len(steps):
                raise VisualQualityError('Invalid process step reference. Rebuild the visual timeline before rendering.')
            label = process_label(steps[active]).move_to([0, 0.7, 0])
            box = SurroundingRectangle(label, buff=0.35, color=YELLOW_C)
            position = fitted_text(f'Step {active+1} of {len(steps)}', font_size=28, max_height=0.7).move_to([0, 2.1, 0])
            items = [position, box, label, self.caption(cue, y=-1.5)]
            if active:
                previous = steps[active-1]
                if re.search(r'[=^_]|\\[A-Za-z]', previous):
                    items.append(process_label(previous, font_size=28, max_height=0.8).move_to([0, -3.1, 0]))
                else:
                    items.append(fitted_text('Previous: ' + previous, font_size=28,
                                             max_width=11, max_height=0.8).move_to([0, -3.1, 0]))
            panel = VGroup(*items)
            self.regions['process'] = panel
            self.event(index, [self.swap(old, panel)])
            old = panel

    def render_equation(self, worked=False):
        equation = sanitize_math(self.data['equation'])
        base = fitted_math(equation).move_to([0, 1.6, 0])
        self.add(base)
        self.regions['equation'] = base
        components = [part.strip() for part in equation.split('=') if part.strip()]
        old = None
        for index, cue in enumerate(self.events):
            part = cue.get('parameters', {}).get('equation') or components[min(index * len(components) // len(self.events), len(components)-1)]
            if not cue.get('parameters', {}).get('equation'):
                if cue.get('action') == 'gradient' and r'\nabla' in equation:
                    part = r'\nabla L(\theta_t)'
                elif 'learning rate' in cue.get('narration_text', '').lower() and r'\alpha' in equation:
                    part = r'\alpha'
            focus = fitted_math(part, font_size=40, max_width=10.5).move_to([0, -0.1, 0])
            highlight = SurroundingRectangle(focus, color=YELLOW_C, buff=0.2)
            panel = VGroup(focus, highlight, self.caption(cue, y=-1.65 if worked else -2.3))
            if worked:
                step = self.data['steps'][min(index * len(self.data['steps']) // len(self.events), len(self.data['steps'])-1)]
                if re.search(r'[=^_]|\\[A-Za-z]', step):
                    prefix, separator, expression = step.partition(':')
                    calculation = fitted_math(expression if separator else step, font_size=30, max_width=10.5)
                    if separator:
                        calculation = VGroup(fitted_text(prefix, font_size=28, max_height=0.5), calculation).arrange(DOWN, buff=0.12)
                    calculation.move_to([0, -3.05, 0])
                else:
                    calculation = fitted_text(step, font_size=28, max_width=11, max_height=0.8).move_to([0, -3.05, 0])
                panel.add(calculation)
            self.regions['explanation'] = panel
            self.event(index, [self.swap(old, panel)])
            old = panel

    def render_comparison(self):
        old = None
        for index, cue in enumerate(self.events):
            left = fitted_text(self.data['left'], font_size=32, max_height=1.0).move_to([0, 1.65, 0])
            right = fitted_text(self.data['right'], font_size=32, max_height=1.0).move_to([0, 0.1, 0])
            relation = fitted_text(self.data['relationship'], font_size=28, max_height=0.9).move_to([0, -1.3, 0])
            caption = self.caption(cue, y=-2.85)
            frame = SurroundingRectangle(left if index % 2 == 0 else right, color=YELLOW_C, buff=0.2)
            panel = VGroup(left, right, relation, caption, frame)
            self.regions['comparison'] = panel
            self.event(index, [self.swap(old, panel)])
            old = panel

    def render_descent(self, worked=False):
        params = self.data.get('animation_params', {})
        states = gradient_updates(float(params.get('initial_x', 3)), float(params.get('learning_rate', 0.2)),
                                  iterations=max(1, min(12, int(params.get('iterations', 4)))))
        axes = Axes(x_range=[-3.3, 3.3, 1], y_range=[0, 10, 2], x_length=6.4, y_length=3.5,
                    axis_config={'include_tip': False}).move_to([-2.95, 0.45, 0])
        curve = axes.plot(lambda x: x*x, x_range=[-3.1, 3.1], color=BLUE_C)
        label = fitted_math('f(x)=x^2', max_width=5, font_size=34).move_to([-3, 2.35, 0])
        x_label = fitted_math('x', font_size=28).move_to([0.55, -1.45, 0])
        graph = VGroup(axes, curve, label, x_label)
        self.add(graph)
        self.regions['graph'] = graph
        first = min(int(self.events[0].get('parameters', {}).get('iteration', 0)), len(states)-1)
        point = Dot(axes.c2p(states[first].x, states[first].loss), color=RED_C)
        self.add(point)
        old, tangent = None, None
        for index, cue in enumerate(self.events):
            iteration = int(cue.get('parameters', {}).get('iteration', min(index // 3, len(states)-1)))
            state = states[max(0, min(iteration, len(states)-1))]
            phase = cue.get('action', 'reveal')
            if params.get('illustration') == 'quadratic_descent':
                phase = ('gradient', 'update', 'loss')[index % 3]
            x = state.next_x if phase in {'loss', 'highlight'} else state.x
            current_index = state.iteration + (1 if phase in {'loss', 'highlight'} else 0)
            formulas = [f'x_{{{current_index}}}={number(x)}', f"f'(x)=2x={number(2*x)}",
                        f'\\alpha={number(state.learning_rate)}', f'f(x)={number(x*x)}']
            if phase == 'update':
                formulas = [f"x_{{{state.iteration+1}}}=x_{{{state.iteration}}}-\\alpha f'(x)",
                            f'={number(state.x)}-{number(state.learning_rate)}\\cdot {number(state.gradient)}',
                            f'={number(state.next_x)}', f'f(x_{{{state.iteration+1}}})={number(state.next_loss)}']
            if worked and phase != 'update':
                formulas[0] = f'\\text{{Iteration }}{state.iteration+1}: x={number(x)}'
            rows = VGroup(*(fitted_math(value, font_size=32, max_width=5.3) for value in formulas)).arrange(DOWN, aligned_edge=LEFT, buff=0.35)
            rows.move_to([3.7, 0.55, 0])
            focus = SurroundingRectangle(rows[1 if phase == 'gradient' else 2 if phase == 'update' else 3], color=YELLOW_C, buff=0.14)
            panel = VGroup(rows, focus)
            caption = self.caption(cue, y=-2.8)
            combined = VGroup(panel, caption)
            self.regions['calculations'], self.regions['caption'] = panel, caption
            if tangent is not None:
                self.remove(tangent)
            lo, hi = max(-3.25, state.x-0.35), min(3.25, state.x+0.35)
            tangent = Line(axes.c2p(lo, state.loss+state.gradient*(lo-state.x)),
                           axes.c2p(hi, state.loss+state.gradient*(hi-state.x)), color=YELLOW_C)
            if phase == 'gradient':
                self.add(tangent)
            target = state.next_x if phase in {'update', 'loss', 'highlight'} else state.x
            start = float(axes.p2c(point.get_center())[0])
            movement = UpdateFromAlphaFunc(point, lambda obj, alpha, a=start, b=target:
                obj.move_to(axes.c2p(a+(b-a)*alpha, (a+(b-a)*alpha)**2)))
            self.event(index, [self.swap(old, combined), movement])
            old = combined

    def render_function(self):
        expression = self.data.get('equation', 'f(x)=x^2')
        function = polynomial_function(expression)
        values = [function(-3+index*0.1) for index in range(61)]
        bottom, top = min(0, min(values)), max(1, max(values))
        span = max(1, top-bottom)
        axes = Axes(x_range=[-3.3, 3.3, 1], y_range=[bottom-span*0.1, top+span*0.1, span/5],
                    x_length=10.5, y_length=3.6, axis_config={'include_tip': False}).move_to([0, 0.4, 0])
        curve = axes.plot(function, x_range=[-3, 3], color=BLUE_C)
        formula = fitted_math(expression, font_size=34).move_to([0, 2.35, 0])
        graph = VGroup(axes, curve, formula)
        self.add(graph)
        self.regions['function'] = graph
        point = Dot(axes.c2p(-3, function(-3)), color=RED_C)
        self.add(point)
        old = None
        for index, cue in enumerate(self.events):
            x = -3+6*(index+1)/len(self.events)
            caption = self.caption(cue, y=-2.9)
            self.regions['caption'] = caption
            start = float(axes.p2c(point.get_center())[0])
            movement = UpdateFromAlphaFunc(point, lambda obj, alpha, a=start, b=x:
                obj.move_to(axes.c2p(a+(b-a)*alpha, function(a+(b-a)*alpha))))
            self.event(index, [self.swap(old, caption), movement])
            old = caption
