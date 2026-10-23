"""Two-stage, data-only lesson planning with bounded repair and quality checks."""

from __future__ import annotations

import json
import hashlib
import math
from copy import deepcopy
from pathlib import Path
from typing import Any

from .llm import OllamaClient, RecoverableOllamaError
from .quality import LessonQualityError, validate_lesson_quality
from .schemas import (LessonOutline, LessonPlan, NarrationRevision, lesson_generation_schema,
                      narration_word_pattern, english_generation_schema)
from .validator import LessonValidationError, validate_outline, validate_plan
from .content import validate_content, validate_revision_coverage
from .video.mathematics import gradient_updates, number


OUTLINE_INSTRUCTIONS = """Create a structured educational outline for the user's request.
Return only JSON matching the outline schema. Choose a coherent sequence of 3-12
sections with learning goals, essential concepts, and a visual type for each section.
Normally cover introduction, core concepts, a step-by-step explanation, an example,
and a summary. For mathematical topics, explicitly plan equation, graph, and worked
example sections when useful. Do not write narration yet; Stage B will add narration_text.
Do not include Python,
code, markdown, sources, or fields outside the schema."""

LANGUAGE_INSTRUCTIONS = """Use English consistently in titles, labels, steps, narration,
and concepts. Do not mix Chinese or other non-English prose into English lessons.
Greek symbols in mathematics are allowed. Complete both sides of every equation.
For generated JSON use English words and ASCII punctuation. In equation fields,
write Greek symbols and subscripts as LaTeX commands, not Unicode characters.
Use ordinary English words when speaking mathematics in narration_text.
Keep the instructional sequence chronological and finish with a concise summary.
Use distinct visuals for concepts, tangent/gradient, learning rates, derivation,
numerical updates and convergence. Do not repeat the same graph for unrelated ideas.
Never remove a section or final summary to meet the duration target."""

EXPANSION_INSTRUCTIONS = """Expand the supplied outline into a complete educational lesson plan.
Return only JSON matching the lesson schema. Preserve the outline's meaning and order.
Every scene must contain:
- a concise display title/content;
- learning_objective;
- narration_text with a clear, detailed spoken explanation;
- visual_type;
- safe data-only animation_params (never Python or code);
- important_concepts;
- duration in seconds, which is the estimated scene duration.

Omit visual_timeline: measured narration cues and times are added locally after TTS.
For gradient descent examples use f(x)=x^2, x_0=3, and learning rate 0.2.
Numerical demonstrations are computed locally; do not invent iteration results.
Use short English process labels, such as "Initialize the parameter", "Calculate the
gradient", "Apply the update", and "Check the new loss". Put equations in equation
fields and detailed arithmetic in worked examples and narration, not process labels.

Use explanatory visuals rather than a sequence of bullet slides. Use process scenes
for causal steps, equation scenes for formulas, graph scenes for function behavior,
and worked_example scenes for numerical substitutions and derivations. For gradient
descent, include a quadratic loss graph with a moving point, the update equation, and
a worked numerical example. You may split outline sections into multiple scenes. Use
at least ceil(target seconds / 45) scenes; a 3-4 minute lesson normally needs 6-9
scenes. Keep display text readable and concise while putting the full explanation in
narration_text. Contract limits include: titles 1-90 characters, subtitles 0-160,
bullets 1-150 each, process steps 1-45 each, worked-example steps 1-80 each,
equations 1-180, comparison fields 1-140 (or 1-100 for left/right), narration_text
at most 4000 characters, 3-45 seconds per scene, and at most 20 scenes. Do not
invent filler just to reach the target.
Aim for 130-150 spoken words per minute and enough narration for the requested target.
Do not include Python, arbitrary code, markdown, sources, or fields outside the schema."""

REVISION_INSTRUCTIONS = """Revise a previously validated lesson plan to satisfy the measured
duration constraint. Return the complete lesson plan JSON and preserve the original
meaning, logical order, mathematical explanations, worked examples, and visual
instructions wherever possible.

Display text is not narration: process.steps, titles, labels, bullets, and other
on-screen strings must be concise, readable, and 1-45 characters where the schema
requires it. Specifically keep titles <=90, subtitles <=160, bullets <=150,
worked-example steps <=80, equations <=180, comparison left/right <=100 and
relationship <=140, narration_text <=4000, scene duration 3-45 seconds, and no more
than 20 scenes. Put detailed explanations, derivations, and examples in narration_text,
which may be much longer. Never put a paragraph into process.steps, never silently
truncate text, and never remove an essential idea just to satisfy a length limit.
Only change content needed for duration or validation. Preserve animation_params and
use different visual parameters for split or revised segments when appropriate.
Return only complete JSON matching the lesson schema; do not return Python, code, or
markdown."""

MAX_ATTEMPTS = 3

OUTLINE_INSTRUCTIONS += '\n' + LANGUAGE_INSTRUCTIONS
EXPANSION_INSTRUCTIONS += '\n' + LANGUAGE_INSTRUCTIONS
REVISION_INSTRUCTIONS += '\n' + LANGUAGE_INSTRUCTIONS


class LessonPlanGenerationError(LessonValidationError):
    """Raised when outline expansion cannot produce a valid quality plan."""


class LessonPlanner:
    def __init__(self, client: OllamaClient | None = None, reporter: Any | None = None,
                 cache_dir: Path | None = None, generation_settings: dict | None = None) -> None:
        self.client = client or OllamaClient()
        self.reporter = reporter
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.generation_settings = dict(generation_settings or {})
        if "speed" in self.generation_settings:
            self.generation_settings["speed"] = float(self.generation_settings["speed"])

    def plan(self, educational_prompt: str, duration_minutes: float = 3.5,
             *, force: bool = False) -> dict:
        if not isinstance(educational_prompt, str) or not educational_prompt.strip():
            raise ValueError("Educational prompt must be a non-empty string.")
        if not 0.5 <= duration_minutes <= 30:
            raise ValueError("Target duration must be between 0.5 and 30 minutes.")
        model = getattr(self.client, "model", "qwen3:4b")
        cache_key = self._cache_key(educational_prompt, duration_minutes, model)
        self._legacy_response: Any = None
        if self.reporter:
            self.reporter.ollama_started(model)
        cached = None if force else self._load_cache("plan", cache_key)
        cached_failure = ''
        if cached is not None:
            try:
                plan = validate_plan(cached).model_dump(mode="json", exclude_defaults=True)
                validate_lesson_quality(plan, duration_minutes, prompt=educational_prompt)
            except (LessonValidationError, ValueError) as exc:
                cached_failure = str(exc)
                if self.reporter:
                    self.reporter.planner_stage('Cached lesson needs content repair; preserving the original until validation succeeds')
            else:
                if self.reporter:
                    if hasattr(self.reporter, "lesson_cache_hit"):
                        self.reporter.lesson_cache_hit("validated lesson plan")
                    self.reporter.ollama_finished(model)
                    self.reporter.lesson_validated(len(plan["scenes"]), 0)
                return plan

        outline = self._generate_outline(educational_prompt, duration_minutes, cache_key, force=force)
        if outline is None:
            # Compatibility bridge for older integrations returning a complete plan.
            plan = self._legacy_plan(educational_prompt, duration_minutes, model, self._legacy_response)
            self._save_cache("plan", cache_key, plan)
            return plan

        if self.reporter and hasattr(self.reporter, "planner_stage"):
            self.reporter.planner_stage("Stage B: expanding outline into narration and visuals")
        scene_types = self._outline_scene_types(outline, educational_prompt, duration_minutes)
        expansion_schema = lesson_generation_schema(scene_types, duration_minutes * 60)
        outline_json = json.dumps(outline.model_dump(mode="json"), ensure_ascii=False)
        request = f"""{EXPANSION_INSTRUCTIONS}

Target video duration: approximately {duration_minutes:g} minutes.
Target narration length: approximately {duration_minutes * 130:.0f}-{duration_minutes * 150:.0f} words.
{self._narration_budget(duration_minutes, len(scene_types))}
{self._numerical_context(educational_prompt)}
The output schema fixes scene types in this order: {', '.join(scene_types)}.
Follow the outline's concepts in order and use the extra scenes for relevant
derivations, numerical examples, or visual explanations. Spread narration across
these {len(scene_types)} scenes (about {math.ceil(duration_minutes * 140 / len(scene_types))}
spoken words each). The duration field estimates this spoken explanation, not just
the time needed to read the display labels.

Outline from Stage A:
{outline_json}

Original user request:
{educational_prompt.strip()}"""
        previous: Any = cached if cached_failure else None
        failure = cached_failure
        narration_base = None
        checkpoint = None if force else self._load_cache("expansion_draft", cache_key)
        if isinstance(checkpoint, dict) and checkpoint.get("version") == 1:
            previous = checkpoint.get("previous")
            failure = checkpoint.get("failure", "")
            narration_base = checkpoint.get("narration_base")
            if narration_base is not None:
                # A base saved by older validation rules may now have invalid
                # display text, arithmetic, or coverage. A narration-only schema
                # cannot fix those fields. Revalidate before freezing anything.
                candidate = deepcopy(narration_base)
                try:
                    candidate = self._apply_narration_revision(candidate, self._parse_json(previous))
                except (LessonValidationError, ValueError, TypeError):
                    pass
                try:
                    candidate = validate_plan(candidate).model_dump(mode='json', exclude_defaults=True)
                    candidate = self._validate_generated_quality(candidate, duration_minutes, educational_prompt)
                except (LessonValidationError, LessonQualityError, ValueError) as exc:
                    if isinstance(exc, LessonQualityError) and exc.narration_only:
                        narration_base, failure = candidate, str(exc)
                    else:
                        previous, failure, narration_base = candidate, str(exc), None
                        if self.reporter:
                            self.reporter.planner_stage('Resuming complete-plan repair: cached narration-only base no longer passes content validation')
                else:
                    self._save_cache('plan', cache_key, candidate)
                    if self.reporter:
                        self.reporter.lesson_cache_hit('validated expansion draft')
                        self.reporter.ollama_finished(model)
                        self.reporter.lesson_validated(len(candidate['scenes']), 0)
                    return candidate
            if self.reporter:
                self.reporter.lesson_cache_hit("expansion draft (resuming validation repair)")
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.reporter:
                self.reporter.ollama_attempt(attempt, MAX_ATTEMPTS)
            prompt = request if previous is None and not failure else self._repair_prompt(
                previous, failure, attempt, duration_minutes, stage="expansion", context=request
            )
            schema = expansion_schema
            if narration_base is not None:
                prompt += self._narration_repair_instructions(narration_base, duration_minutes)
                schema = NarrationRevision.model_json_schema()
                scene_count = len(narration_base["scenes"])
                schema["properties"]["scenes"].update(minItems=scene_count, maxItems=scene_count)
                schema["$defs"]["SceneNarration"]["properties"]["scene_index"]["maximum"] = scene_count
                minimum_words = max(math.ceil(120 / scene_count), math.floor(duration_minutes * 130 / scene_count))
                maximum_words = max(minimum_words, math.ceil(duration_minutes * 150 / scene_count))
                schema['$defs']['SceneNarration']['properties']['narration_text']['pattern'] = narration_word_pattern(minimum_words, maximum_words)
                prompt += f'\nWrite {minimum_words}-{maximum_words} space-separated spoken words per scene. Explain concrete reasons or calculations rather than repeating earlier narration. Use spoken words for mathematics, with no raw LaTeX or embedded quotation marks.\n'
            try:
                raw = self.client.generate_json(prompt, schema)
                previous = raw
                parsed = self._parse_json(raw)
                if narration_base is not None:
                    parsed = self._apply_narration_revision(narration_base, parsed)
                plan = validate_plan(parsed).model_dump(mode="json", exclude_defaults=True)
                try:
                    plan = self._validate_generated_quality(plan, duration_minutes, educational_prompt)
                except LessonQualityError as exc:
                    if exc.narration_only:
                        narration_base = plan
                    else:
                        # Include the full merged draft, not just a narration
                        # patch, so the next attempt can repair every bad field.
                        previous, narration_base = plan, None
                    raise
                if self.reporter:
                    self.reporter.ollama_finished(model)
                    self.reporter.lesson_validated(len(plan["scenes"]), attempt - 1)
                self._save_cache("plan", cache_key, plan)
                return plan
            except RecoverableOllamaError as exc:
                failure = str(exc)
                if exc.partial_response is not None:
                    previous = exc.partial_response
                self._save_cache("expansion_draft", cache_key, {
                    "version": 1, "previous": previous, "failure": failure,
                    "narration_base": narration_base,
                })
                self._generation_retry(attempt, failure)
            except (LessonValidationError, LessonQualityError, ValueError) as exc:
                failure = str(exc)
                self._save_cache("expansion_draft", cache_key, {
                    "version": 1, "previous": previous, "failure": failure,
                    "narration_base": narration_base,
                })
                if self.reporter:
                    self.reporter.validation_retry(attempt, failure)

        raise LessonPlanGenerationError(
            f"Could not produce a valid educational lesson after {MAX_ATTEMPTS} expansion attempts. "
            f"Remaining failures: {failure}. "
            + (f"Outline and latest draft saved in {self.cache_dir}; rerun the same command to "
               "resume repair, or use --force to start again. " if self.cache_dir else "")
            + "Rendering was not started; duration and content requirements remain enforced."
        )

    def _generate_outline(self, educational_prompt: str, duration_minutes: float,
                          cache_key: str, *, force: bool = False) -> LessonOutline | None:
        cached = None if force else self._load_cache("outline", cache_key)
        if cached is not None:
            if self.reporter and hasattr(self.reporter, "lesson_cache_hit"):
                self.reporter.lesson_cache_hit("lesson outline")
            outline = validate_outline(cached)
            validate_content(outline.model_dump(mode='json'))
            return outline
        if self.reporter and hasattr(self.reporter, "planner_stage"):
            self.reporter.planner_stage("Stage A: generating structured outline")
        request = f"""{OUTLINE_INSTRUCTIONS}

Target duration: approximately {duration_minutes:g} minutes. Plan enough meaningful
sections to support that duration at 130-150 spoken words per minute without filler.

User request:
{educational_prompt.strip()}"""
        schema = english_generation_schema(LessonOutline.model_json_schema())
        previous: Any = None
        failure = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.reporter:
                self.reporter.ollama_attempt(attempt, MAX_ATTEMPTS)
            prompt = request if attempt == 1 else self._repair_prompt(
                previous, failure, attempt, duration_minutes, stage="outline", context=request
            )
            try:
                raw = self.client.generate_json(prompt, schema)
                previous = raw
                parsed = self._parse_json(raw)
                # Preserve the existing direct LessonPlan client contract.
                if attempt == 1 and isinstance(parsed, dict) and "scenes" in parsed:
                    self._legacy_response = raw
                    return None
                outline = validate_outline(parsed)
                validate_content(outline.model_dump(mode='json'))
                self._save_cache("outline", cache_key, outline.model_dump(mode="json"))
                return outline
            except RecoverableOllamaError as exc:
                failure = str(exc)
                if exc.partial_response is not None:
                    previous = exc.partial_response
                self._generation_retry(attempt, failure)
            except (LessonValidationError, ValueError) as exc:
                failure = str(exc)
                if self.reporter:
                    self.reporter.validation_retry(attempt, failure)
        raise LessonPlanGenerationError(
            f"Could not produce a valid lesson outline after {MAX_ATTEMPTS} attempts. "
            f"Remaining failures: {failure}"
        )

    def revise_for_duration(self, plan: dict, educational_prompt: str,
                            duration_minutes: float, duration_error: str,
                            *, force: bool = False) -> dict:
        """Revise with bounded validation/repair, without mutating the last valid plan."""
        model = getattr(self.client, "model", "qwen3:4b")
        original = validate_plan(plan).model_dump(mode="json", exclude_defaults=True)
        revision_key = self._revision_cache_key(original, educational_prompt, duration_minutes, duration_error, model)
        if not force:
            cached = self._load_cache("revised_plan", revision_key)
            if cached is not None:
                revised = validate_plan(cached).model_dump(mode="json", exclude_defaults=True)
                revised = self._validate_generated_quality(revised, duration_minutes, educational_prompt)
                validate_revision_coverage(original, revised)
                return revised
        request = f"""{REVISION_INSTRUCTIONS}

Target duration: approximately {duration_minutes:g} minutes.
Measured narration failure: {duration_error}
{self._narration_budget(duration_minutes, len(original['scenes']))}
{self._numerical_context(educational_prompt)}
Original educational request: {educational_prompt}
Preserve scene titles and their order. Preserve narration and equations in scenes
marked animation_params.demo=quadratic_descent exactly: their calculations and cues
are computed locally. Adjust the depth and pacing of other scenes instead.
Current validated lesson JSON:
{json.dumps(original, ensure_ascii=False)}"""
        if self.reporter and hasattr(self.reporter, "planner_stage"):
            self.reporter.planner_stage("Revising lesson for measured narration duration")
        previous: Any = None
        failure = ""
        revision_schema = lesson_generation_schema([s['type'] for s in original['scenes']], duration_minutes * 60)
        for item, scene in zip(revision_schema['properties']['scenes']['prefixItems'], original['scenes']):
            if scene.get('animation_params', {}).get('demo') == 'quadratic_descent':
                item['properties']['narration_text']['const'] = scene['narration_text']
                item['properties']['narration_text'].pop('pattern', None)
                item['properties']['equation']['const'] = scene['equation']
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.reporter:
                self.reporter.ollama_attempt(attempt, MAX_ATTEMPTS)
            prompt = request if attempt == 1 else self._repair_prompt(
                previous, failure, attempt, duration_minutes, stage="duration_revision", context=request
            )
            try:
                raw = self.client.generate_json(prompt, revision_schema)
                previous = raw
                revised = validate_plan(self._parse_json(raw)).model_dump(
                    mode="json", exclude_defaults=True
                )
                revised = self._validate_generated_quality(revised, duration_minutes, educational_prompt)
                validate_revision_coverage(original, revised)
                self._save_cache("revised_plan", revision_key, revised)
                return revised
            except RecoverableOllamaError as exc:
                failure = str(exc)
                if exc.partial_response is not None:
                    previous = exc.partial_response
                self._generation_retry(attempt, failure)
            except (LessonValidationError, LessonQualityError, ValueError) as exc:
                failure = str(exc)
                if self.reporter:
                    self.reporter.validation_retry(attempt, failure)
        raise LessonPlanGenerationError(
            f"Duration revision failed after {MAX_ATTEMPTS} attempts; the original valid "
            f"lesson was preserved. Remaining validation failures: {failure}"
        )

    def _legacy_plan(self, educational_prompt: str, duration_minutes: float,
                     model: str, initial: Any = None) -> dict:
        request = f"{EXPANSION_INSTRUCTIONS}\n\nTarget duration: {duration_minutes:g} minutes.\nUser request:\n{educational_prompt.strip()}"
        schema = LessonPlan.model_json_schema()
        previous: Any = None
        failure = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.reporter:
                self.reporter.ollama_attempt(attempt, MAX_ATTEMPTS)
            try:
                if attempt == 1 and initial is not None:
                    raw = initial
                else:
                    raw = self.client.generate_json(request if attempt == 1 else self._repair_prompt(
                        previous, failure, attempt, duration_minutes, stage="expansion", context=request
                    ), schema)
                previous = raw
                plan = validate_plan(self._parse_json(raw)).model_dump(mode="json", exclude_defaults=True)
                validate_content(plan)
                if self.reporter:
                    self.reporter.ollama_finished(model)
                    self.reporter.lesson_validated(len(plan["scenes"]), attempt - 1)
                return plan
            except RecoverableOllamaError as exc:
                failure = str(exc)
                if exc.partial_response is not None:
                    previous = exc.partial_response
                self._generation_retry(attempt, failure)
            except (LessonValidationError, ValueError) as exc:
                failure = str(exc)
                if self.reporter:
                    self.reporter.validation_retry(attempt, failure)
        raise LessonPlanGenerationError(
            f"Could not produce a valid lesson after {MAX_ATTEMPTS} attempts. "
            f"Remaining validation failures: {failure}"
        )

    def _generation_retry(self, attempt: int, failure: str) -> None:
        if self.reporter:
            if hasattr(self.reporter, 'generation_retry'):
                self.reporter.generation_retry(attempt, MAX_ATTEMPTS, failure)
            else:
                self.reporter.validation_retry(attempt, failure)

    def _validate_generated_quality(self, plan: dict, duration: float, prompt: str) -> dict:
        try:
            validate_lesson_quality(plan, duration, prompt=prompt, require_summary=True)
        except LessonQualityError as exc:
            if not exc.issues or not all(issue.startswith('estimated visual time') for issue in exc.issues):
                raise
            # Estimated seconds are derived metadata, not educational content. A
            # valid narration should not be rewritten just to correct LLM arithmetic.
            estimated = deepcopy(plan)
            for scene in estimated['scenes']:
                seconds = len(scene.get('narration_text', '').split()) * 60 / 140
                scene['duration'] = max(3.0, min(45.0, seconds))
            validate_lesson_quality(estimated, duration, prompt=prompt, require_summary=True)
            if self.reporter and hasattr(self.reporter, 'planner_stage'):
                self.reporter.planner_stage('Estimated scene timing from narration; actual timing will be measured by Kokoro')
            return estimated
        return plan

    @staticmethod
    def _parse_json(raw: Any) -> Any:
        if not isinstance(raw, str):
            return raw
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Ollama returned invalid JSON: {exc.msg}") from exc

    @staticmethod
    def _repair_prompt(previous: Any, failure: str, attempt: int,
                       duration_minutes: float, *, stage: str, context: str = "") -> str:
        try:
            previous_json = previous if isinstance(previous, str) else json.dumps(previous, ensure_ascii=False)
        except (TypeError, ValueError):
            previous_json = repr(previous)
        if stage == "outline":
            instructions, schema_name = OUTLINE_INSTRUCTIONS, "outline"
        elif stage == "duration_revision":
            instructions, schema_name = REVISION_INSTRUCTIONS, "complete lesson plan"
        else:
            instructions, schema_name = EXPANSION_INSTRUCTIONS, "complete lesson plan"
        return f"""{instructions}

This is repair attempt {attempt} of {MAX_ATTEMPTS} for the {stage} stage. Correct only
the invalid content or structure while preserving the original educational meaning.
For expansion or duration revision, add relevant derivations, examples, and visual
explanations if the quality error says the content is too shallow; never add unsupported
filler. Keep display text short and put detailed explanations in narration_text.
Preserve an approximately {duration_minutes:g}-minute lesson.
{LessonPlanner._narration_budget(duration_minutes) if not context else ''}
Return only the complete corrected {schema_name} JSON.

Original stage instructions, user request, and outline (preserve their context):
{context}

Validation or quality errors:
{failure}

Previous generated JSON:
{previous_json}"""

    @staticmethod
    def _outline_scene_types(outline: LessonOutline, prompt: str,
                             duration_minutes: float) -> list[str]:
        types = ["bullets" if section.visual_type == "concepts" else section.visual_type
                 for section in outline.sections]
        if any(word in outline.sections[-1].title.lower() for word in ('summary', 'conclusion', 'recap')):
            types.pop()  # Reserve that section for the concluding summary below.
        mathematical = any(term in prompt.lower() for term in (
            "gradient", "derivative", "equation", "calculus", "math", "function", "loss",
        ))
        if mathematical:
            for required in ("equation", "graph", "worked_example"):
                if required not in types:
                    types.append(required)
        # Leave headroom for audio timing: no planned scene should need >40s.
        minimum = max(3, math.ceil(duration_minutes * 60 / 40))
        explanatory = [kind for kind in types if kind not in {"title", "bullets", "summary"}]
        if not explanatory:
            explanatory = ["process", "comparison"]
        while len(types)+1 < minimum or sum(kind not in {"title", "bullets", "summary"}
                                          for kind in types) < max(2, math.ceil(len(types) * 0.4)):
            types.append(explanatory[len(types) % len(explanatory)])
        types.append('summary')
        if len(types) > 20:
            raise LessonPlanGenerationError(
                "The requested duration needs more than 20 scenes at the current scene limit. "
                "Choose a shorter duration or split the topic into multiple lessons."
            )
        return types

    @staticmethod
    def _narration_budget(duration_minutes: float, scene_count: int | None = None) -> str:
        minimum = max(120, math.ceil(duration_minutes * 130 * 0.85))
        words = max(minimum, math.ceil(duration_minutes * 140))
        scenes = scene_count or min(20, max(3, math.ceil(duration_minutes * 60 / 32)))
        return (
            f"Narration budget: aim for {words} spoken words across about {scenes} scenes "
            f"(about {math.ceil(words / scenes)} words per scene). The complete narration "
            f"must have at least {minimum} words. Count narration_text only, not display text. "
            "Explain why each step works, define symbols, and walk through the arithmetic "
            "in relevant examples. Keep introductions and summaries brief and avoid repetition. "
            "Use actual scene types graph, equation, and worked_example for the corresponding "
            "visuals, rather than merely setting visual_type on a bullet scene."
        )

    @staticmethod
    def _numerical_context(prompt: str) -> str:
        if 'gradient descent' not in prompt.lower():
            return ''
        lines = ['Authoritative computed reference for f(x)=x^2, derivative 2x, learning rate 0.2:']
        for state in gradient_updates(iterations=3):
            lines.append(f'Iteration {state.iteration+1}: x={number(state.x)}, gradient={number(state.gradient)}, '
                         f'new x={number(state.x)} - 0.2 * {number(state.gradient)} = {number(state.next_x)}, '
                         f'new loss={number(state.next_loss)}.')
        return '\n'.join(lines) + '\nUse these computed values consistently. Never invent different iteration results.'

    @staticmethod
    def _narration_repair_instructions(plan: dict, duration_minutes: float) -> str:
        count = len(plan["scenes"])
        per_scene = math.ceil(max(120, duration_minutes * 140) / count)
        return f"""

NARRATION-ONLY REPAIR: the visual plan has already passed validation. The output
schema for this call is NarrationRevision, overriding the full-plan response format
above. Return only {{"scenes": [{{"scene_index": 1, "narration_text": "..."}}, ...]}}.
Return all {count} scenes, indexed 1 through {count}, exactly once each. Aim for about
{per_scene} spoken words per scene, with more detail in derivations and examples.
Expand meaningfully with reasoning, symbol definitions, numerical substitutions,
and interpretation of the result; do not repeat introductions or pad with filler.
All display steps, equations, animation parameters, and other fields will be preserved.
Current complete lesson (use this to align each explanation with its visual):
{json.dumps(plan, ensure_ascii=False)}"""

    @staticmethod
    def _apply_narration_revision(plan: dict, data: Any) -> dict:
        revision = NarrationRevision.model_validate(data)
        indices = [scene.scene_index for scene in revision.scenes]
        if sorted(indices) != list(range(1, len(plan["scenes"]) + 1)):
            raise LessonValidationError("Narration repair must include every scene index exactly once.")
        result = deepcopy(plan)
        for scene in revision.scenes:
            result["scenes"][scene.scene_index - 1]["narration_text"] = scene.narration_text
        return result

    def _cache_key(self, prompt: str, duration_minutes: float, model: str) -> str:
        return hashlib.sha256(json.dumps({
            "cache_version": 3,
            "schema_version": 3,
            "prompt": prompt.strip(), "duration": float(duration_minutes), "model": model,
            "generation_settings": self.generation_settings,
        }, sort_keys=True).encode()).hexdigest()[:24]

    def _revision_cache_key(self, plan: dict, prompt: str, duration: float,
                            duration_error: str, model: str) -> str:
        payload = {
            "cache_version": 3, "schema_version": 3, "plan": plan,
            "prompt": prompt.strip(), "duration": float(duration),
            "duration_error": duration_error, "model": model,
            "generation_settings": self.generation_settings,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]

    def _load_cache(self, kind: str, key: str) -> Any | None:
        if not self.cache_dir:
            return None
        path = self.cache_dir / f"{kind}_{key}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        except (OSError, json.JSONDecodeError):
            return None

    def _save_cache(self, kind: str, key: str, value: Any) -> None:
        if not self.cache_dir:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{kind}_{key}.json"
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
