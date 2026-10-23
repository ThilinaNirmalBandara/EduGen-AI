# Water-cycle visual rendering evaluation

## Recommendation

Use enhanced Manim as the next production direction. Keep Remotion as an optional
experiment rather than replacing Manim or introducing automatic mixed-renderer
lessons now. The shared storyboard already provides a clean seam for a future
hybrid, but this experiment does not implement per-scene hybrid composition.

In this example, illustrations, spatial continuity and causal object motion—not
a renderer change—provide the important visual improvement. Both illustrated
backends produce a similar clear landscape from the same data and SVGs. Manim
also retains the existing mathematical rendering and is faster in this local run.
Remotion becomes more compelling if browser previews, interactive editing or
CSS-rich presentation layouts become a concrete product requirement.

## Artifacts

- [Original-template Manim baseline](../outputs/visual_benchmark/baseline.mp4)
- [Illustrated SVG Manim](../outputs/visual_benchmark/illustrated.mp4)
- [Experimental Remotion](../outputs/visual_benchmark/remotion.mp4)
- [Machine-readable measurements](../outputs/visual_benchmark/metrics.json)
- [Shared storyboard](../outputs/visual_benchmark/inputs/storyboard.json)
- [Narration sample provenance](../outputs/visual_benchmark/inputs/provenance.json)

All three are 1280×720, 30 FPS, with a 57.467-second MP4 container duration.
Their shared narration waveform is 57.450 seconds; the small difference is the
normal final video-frame boundary. Per-cue PNGs are retained in each renderer's
`*_previews` directory. Original full-length user videos were not overwritten.

## Fair-comparison method

The source is the existing `outputs/water_cycle.lesson.json` and its matching
Kokoro timing/WAV cache. The comparison selects complete introductory sentences
defining evaporation, condensation, precipitation and collection, in that order.
It preserves exact PCM sample prefixes at measured sentence boundaries. It does
not speed up speech, pad narration, cut a sentence, call Ollama, or invoke TTS.
This is an explicitly labelled short comparison excerpt, not a replacement for
the original four-minute lesson.

The baseline uses existing active-process templates with concise causal labels.
The illustrated versions use the same scene/narration order, but show water
leaving the lake, vapour gathering into a growing cloud, rain falling, and runoff
and groundwater returning water to storage. Shared rail labels make the four
phases easy to follow. Crossfades maintain the landscape between related scenes.

All backends render silent video and then use the same FFmpeg H.264/AAC muxing
stage with the same WAV. Every output has both video and audio streams, matching
durations, and can be decoded completely with FFmpeg without errors. Decoding
each AAC stream to PCM gives the identical SHA-256:

```text
f6b2d2e148ae9127eb4ca27a32ba79a7b443f6b2e8f6e9e87ab3981ace32a426
```

Thus narration content, timing, voice and speed are controlled across the three
outputs. Synchronization is measured phrase-level scheduling, not phoneme/word
forced alignment. A narration caption follows the exact cue boundaries.

## Performance measurements

Final backends ran sequentially on an Intel Core i7-13650HX Linux workstation,
using the installed Python/Manim environment and Node 20.20.2. Remotion packages
are pinned to 4.0.537 and use the existing system Chrome, with two rendering
workers. No browser, LLM or TTS model was downloaded for the comparison. Optional
Node packages were installed once before measuring.

| Renderer | Prepare + render | Including common audio mux | MP4 size |
| --- | ---: | ---: | ---: |
| Existing Manim | 47.5 s | 50.5 s | 2.56 MB |
| Illustrated Manim | 61.1 s | 64.2 s | 1.81 MB |
| Remotion | 182.6 s | 186.5 s | 1.97 MB |

Timings include per-cue preview generation and backend startup; the final column
includes the common video/audio encoding stage. Remotion also bundles its local
React composition and starts a browser. These are single workstation measurements,
not isolated-host or statistically repeated performance results. Some lightweight
tests ran during the comparison. Concurrency/GPU tuning or persistent browser
bundles could change Remotion's numbers; no general speed guarantee is implied.

## Qualitative comparison

The following is an engineering assessment from inspected preview frames and
rendered-motion checks, not a learner study or an automatic measure of teaching
effectiveness. The audio was checked for stream integrity and identity, not given
a new human narration-quality score.

| Criterion | Existing Manim | Illustrated Manim | Remotion |
| --- | --- | --- | --- |
| Visual explanation | Readable active-step text, little spatial explanation | Sun/lake/cloud/mountain landscape; visible causal water movement | Similar illustrated explanation from the same storyboard |
| Animation flexibility | Existing fixed educational templates | SVGs, primitives, Bezier paths, fades, scaling and audited math available separately | Frame-based React/CSS/SVG composition; browser-layout flexibility |
| Readability | Existing template layout preflight | Minimum 28 design-pixel text, measured wrapping, contrast and bounds checks | Same typography layout data and preflight, local browser text rendering |
| Narration synchronization | Original measured event schedule | Actual Kokoro phrase starts drive scene state and captions | Same phrase times and deterministic path state |
| Performance here | Fastest | Moderate extra cost for meaningful illustrations | Slowest with the tested two-worker local setup |
| Code complexity | Existing code, unchanged | Adds a Python scene-graph interpreter and adapter; reuses the current runtime | Adds Python adapter, React composition, mirrored JS motion, npm build and browser lifecycle |
| Additional topics | Extend existing Manim templates | Add reviewed topic compiler/components/assets; renderer stays generic | Same storyboard topic compiler, plus browser-specific capabilities if needed |
| Mathematics | Existing audited MathTex | Use the default math renderer; no new math bridge | Explicitly unsupported in the experimental adapter; use Manim |

The illustrated backends deliberately reuse a landscape across all four phases.
That repeated layout has an explicit pedagogical justification: the learner follows
the same water through the cycle. It is not an unexplained sequence of identical
slides. Particle loops depict continuous physical movement; they are not a claim
to simulate atmospheric physics.

## What is implemented and what remains limited

Implemented: themes, six original SVG assets, reusable primitives/particle paths,
water-cycle phase/overview/summary templates, generic connected-process layouts,
strict storyboard validation, repetition and motion-coverage checks, cue previews,
optional CLI selection, cache-only benchmark, exact-audio comparison, real rendering
tests and Python/JS motion parity tests. No generation/model change was needed.

The architecture is renderer-independent, but the reviewed illustrative topic
library is still small. New subjects need meaningful visual templates; a moving
dot does not make an arbitrary explanation educational. Geometry is sampled rather
than formally collision-checked at every instant. Relevance depends on declared
concept references plus reviewed templates; it is not semantic source grounding.
Source-evidence fields are supported but empty when no source exists. Existing
cached content can be rejected by new repetition checks instead of being silently
rewritten or deleted.

Before adopting Remotion commercially, check its conditional licensing. Its
[official FAQ](https://www.remotion.dev/docs/license/faq) lists local evaluation
and certain individual/small-team uses among free-use categories; it is not an
unconditionally free license for every organization. This experiment used no
paid API, cloud service, subscription, or model replacement.

## Reproduce

```bash
python -m src.visual_benchmark --prepare-only
python -m src.visual_benchmark --renderers baseline,illustrated,remotion --overwrite
python -m unittest discover -s tests -v
cd experiments/remotion
npm test
```

Use `--overwrite` only when intentionally refreshing existing comparison outputs.
Optional Node installation and the storyboard contract are documented in
[the extension guide](storyboards.md). Keep `--renderer manim` as the default
while expanding reviewed illustration templates and evaluating learner feedback.
