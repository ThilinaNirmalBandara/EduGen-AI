# Illustrated storyboards

## Scope and compatibility

The original planner, lesson JSON contract, retry loop, Kokoro implementation,
duration revision, automatic measured-audio splitting, cache and audited MathTex
templates are preserved. `main.py` still defaults to `--renderer manim`, now the
enhanced storyboard pipeline with automatic SVG/diagram/native-math dispatch.
`--renderer illustrated` is a compatible alias; `--renderer legacy` selects the
original fixed templates. `--renderer remotion` remains experimental.

Both consume `src/storyboard/models.py`, not generated rendering code. Scene
positions are normalized top-left coordinates in a 16:9 frame. Typography is
specified in 1280×720 design pixels, with a minimum of 28; low-quality previews
scale the entire design. Manim converts design pixels to Pango points; Remotion
uses the same whole-word layout derived from local DejaVu Sans font metrics.

## Data flow

1. Existing validated lesson and measured Kokoro phrase timings.
2. Deterministic topic/template compiler creates a Pydantic storyboard.
3. Structural, readability, content-repetition and visual-coverage checks.
4. Every narration cue gets a preview frame using the actual rendering backend.
5. Frame-addressable object movement; FFmpeg muxes the complete narration WAV.

Selection uses learning objectives, important concepts, narration and display
fields—not titles. No extra LLM call is required. A validated-storyboard cache
keys lesson content, phrase timing, theme and compiler/asset versions, independent
of rendering resolution. Existing outline, plan and speech caches are preserved.

Consecutive illustrated scenes may batch, but native mathematics stays in its
original position. All batches preflight before any movie rendering. Cumulative
frame budgets avoid drift when illustrated and mathematical clips are combined.
The full narration WAV is muxed once. The existing target-duration revision and
measured scene-splitting stages still run before storyboard compilation.

`--no-voice` is supported for silent previews: estimated narration timing is
explicitly labelled as estimated, never presented as measured speech alignment.
New output sidecars use `.storyboard.json`; original cached lesson plans are not
rewritten. Audio assembly is content-addressed and reuses the original waveform
samples; changing render settings does not request new speech or a new lesson.

## Storyboard contract

Each scene has stable `id`, `objective`, `concepts`, `layout`, `background`, ordered
`narration`, `objects`, `motions` and a `transition`. Narration cues contain text,
sample-measured `start`/`duration`, source scene/event IDs, and optional `evidence`.
Object kinds are a closed list: registered SVG asset, ellipse, rectangle, label or
arrow or polyline. Visibility windows support progressive diagrams without
overlapping readable text. Motion actions are a closed list: move, scale, rotate or fade.
Each motion names an object, narration IDs and the concepts being demonstrated.
Paths use Bezier curves (2–4 points) or ordered polylines. Timing is deterministic
and frame-addressable, independent of previous frame rendering order.

Evidence records accept `source_id`, `uri`, `excerpt` and `locator`. They are empty
when no source is supplied. Cache file hashes are provenance, not evidence that a
scientific claim has been verified. This work does not implement document uploads,
source retrieval or grounding.

Generate the JSON schema locally if needed:

```python
from src.storyboard.models import Storyboard
schema = Storyboard.model_json_schema()
```

## Quality gates

Before rendering, checks reject unknown references, out-of-order/gapped narration,
non-finite values, missing concepts, unexplained repeated layouts, substantially
repeated sentences, out-of-frame geometry, overlapping text labels, insufficient
text contrast and text that cannot fit without dropping below 28 design pixels.
Narration needs concept-related illustrative movement; uncovered intervals over
eight seconds are rejected. Recaps or continuous landscapes can carry explicit
repetition/layout justifications. Nothing is automatically deleted or truncated.

Animated bounds are sampled, including rotated boxes. This is not a collision
proof for every instant or a general factual/semantic verifier. Illustrations may
intentionally overlap (rain in front of clouds and mountains). The semantic
relevance check uses declared concept references, so a reviewed topic template
and human preview inspection still matter. Repeated water motion is intentional:
water continuously rises, condenses, falls or flows rather than a decorative
animation being used to disguise a static slide.

Native mathematical payloads use the existing discriminated Pydantic scene union,
not generated code. The primary dispatcher automatically invokes audited MathTex,
graph, gradient, numerical and equation templates. Remotion rejects such payloads
rather than displaying raw LaTeX. Without a domain illustration, general scenes
show relationships among supplied concepts or ordered steps, not unrelated images
or decorative orbital dots.
Very long inherited labels may need a reviewed layout or a content revision; the
renderer will not silently crop them. New stricter checks can flag a previously
accepted cached lesson; the source lesson remains untouched.

## Components and extension points

| Module | Responsibility |
| --- | --- |
| `models.py` | Strict Pydantic data contract |
| `assets.py`, `assets/svg/` | Fixed local SVG registry, active/external content rejection |
| `components.py` | Labels and reusable moving particle streams |
| `themes.py` | Validated background, foreground, panel and accent colors |
| `water_cycle.py` | Reviewed topic plugin: landscape, four causal phases, overview/summary |
| `compiler.py` | Lesson/audio → storyboard; generic connected process layouts |
| `selection.py`, `diagrams.py` | Concept-based selection, matching assets and networking/neural/signal/cycle/process/map diagrams |
| `cache.py` | Separately versioned validated storyboard cache |
| `motion.py` | Pure deterministic frame state, mirrored and parity-tested in JS |
| `validation.py` | Layout, coverage and repetition findings |
| `cached_preview.py` | Cache-only complete-sentence comparison excerpts |
| `video/illustrated_pipeline.py` | Primary CLI integration after existing duration checks |
| `video/enhanced_renderer.py` | Ordered native-math/illustrated dispatch, preflight and preview gallery |
| `video/storyboard_manim.py` | Fixed Manim scene-graph interpreter |
| `video/remotion_renderer.py`, `experiments/remotion/` | Optional React adapter and local renderer |

For another topic, add a reviewed compiler/template producing these same objects,
paths and concept references. Register assets by ID rather than accepting arbitrary
file paths or URLs. Use a generic connected-process layout when it genuinely
explains relationships; add a topic template when moving water-like particles
would be inappropriate. The graph and equation renderer should remain Manim.
No LLM-generated Python, JavaScript, SVG scripts, file references or LaTeX command
definitions are evaluated.

## Setup, previews and tests

Python dependencies remain in `requirements.txt` (Pillow is now explicit). Node is
not needed for the default or illustrated Manim paths. Optional Remotion setup:

```bash
cd experiments/remotion
npm ci --no-audit --no-fund
npm test
cd ../..
```

Install an existing Chrome/Chromium browser or set `EDUGEN_CHROME` to its absolute
executable path. The adapter reports missing dependencies before lesson generation;
it does not download a browser. A sandboxed environment must permit localhost
serving and browser subprocesses. `render.mjs` uses local rendering only, no cloud
renderer or paid API. Free-use eligibility is conditional: consult the
[official Remotion license FAQ](https://www.remotion.dev/docs/license/faq).

```bash
python -m unittest discover -s tests -v
python -m src.visual_benchmark --prepare-only
python -m src.visual_benchmark --renderers baseline,illustrated,remotion
```

Benchmark inputs, exact audio origins/sample boundaries, per-cue preview PNGs,
execution logs and FFprobe/audio-hash metrics are retained under
`outputs/visual_benchmark/`. Original four-minute videos and their caches are not
overwritten. This short comparison is deliberately an excerpt, not a shortened
replacement for the requested full lesson.
