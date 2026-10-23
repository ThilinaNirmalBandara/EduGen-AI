# Primary enhanced Manim integration

## Result and normal usage

Enhanced Manim is the default in `main.py`. Existing prompt, JSON, voice, duration,
quality, verbose, force and validation commands remain available. Qwen3:4B and
Kokoro are unchanged; visual planning requires no additional model call.

```bash
python main.py --prompt "Explain gradient descent with a numerical example" --duration 4
python main.py --prompt "Explain IP packet routing and TCP acknowledgments" --duration 4
python main.py --prompt "Explain low-pass filtering of a sensor signal" --duration 4
```

`--renderer manim` selects the new default explicitly. `--renderer illustrated`
remains a compatible alias. `--renderer legacy` selects the old templates;
`--renderer remotion` retains the separate optional experiment. Remotion is not
selected automatically and rejects native math rather than displaying raw LaTeX.

Every default render first produces `<output_stem>_previews/index.html`, with
ordered cue images, narration, timing, template and source-event metadata.
`--preview-only` stops before movie generation. Both `.lesson.json` and
`.storyboard.json` sidecars preserve the structured JSON workflow.

## Architecture

The existing pipeline remains prompt → Ollama outline/lesson → Pydantic/content
validation → Kokoro → measured duration revision/splitting. It then compiles the
prepared plan into a storyboard and dispatches it through Manim.

`selection.py` uses each scene's objective, important concepts, narration and
display fields, not its title. Context may disambiguate evidence but cannot supply
the entire subject. For example, a lesson mentioning the water cycle cannot turn
a butterfly life-cycle scene into evaporation; cloud computing does not select a
weather cloud icon. Asset IDs come from the fixed SVG registry, never supplied
paths or remote URLs.

Reviewed visualization families include:

- Natural processes: water movement, state comparisons, ordered phases and cycles.
- Mathematics/ML: existing MathTex derivations, function/gradient animations,
  calculated numerical examples, and schematic neural connectivity.
- Networking: packet routing, reverse ACKs, retransmission and byte-order examples.
- Engineering/signals: sampled waveforms, input/output filtering comparisons,
  frequency response and delay demonstrations.
- General fallback: relationships between supplied concepts, comparisons and
  progressive processes. No unrelated illustration is invented to fill the frame.

Native math payloads use the existing Pydantic scene union and audited renderer.
Only adjacent illustrated scenes batch; mathematical clips are never sorted into
a separate section. All groups preflight before any movies render. Cumulative
frame budgets prevent rounding drift; a complete narration WAV is muxed once.

Bound phrase events are addressed by their current local index while retaining
original source IDs and process indices. Water-cycle splitting now uses those
indices instead of restarting the phase sequence. An active-phase underline
follows the measured narration.

The independent storyboard cache includes lesson content, prompt, phrase timings,
theme and compiler/asset fingerprints. Resolution changes reuse the same plan;
theme changes do not regenerate the lesson or unchanged speech. Existing planner,
outline and Kokoro caches are preserved. Invalid recompilation does not replace a
previous valid cached storyboard.

## Four-subject end-to-end evaluation

The four fixed lessons have **identical generic lesson and scene titles**. An
injected client replays an outline and structured lesson through the real
two-stage planner; actual local Kokoro, duration checks, scene preparation,
storyboard compilation, Manim, FFmpeg and full video/audio decoding then run.
This makes template routing reproducible. It is **not a live Qwen generation
accuracy evaluation**; the evaluation makes zero LLM calls and disables network
access. The normal CLI still uses the existing Ollama integration.

Each lesson targets one minute with the existing ±15% duration validator
(51–69 seconds). All four passed. Both renderers use the same narration segments
within each subject, without changing speech speed. Videos are 720p, 30 FPS.

| Subject | Measured narration | Enhanced render | Legacy render | Enhanced visual sequence |
| --- | ---: | ---: | ---: | --- |
| Water cycle | 61.075 s | 58.9 s | 48.4 s | Overview → ordered water movement → gas/liquid comparison → summary |
| Gradient descent | 53.000 s | 46.6 s | 36.3 s | MathTex update → tangent/point → numerical substitution → concept summary |
| IP/TCP networking | 63.200 s | 60.5 s | 53.3 s | Routing → forward/reverse paths → recovery/order → summary |
| Low-pass filtering | 60.425 s | 69.7 s | 47.9 s | Input/output curves → comparison → response/delay → summary |

These timings include preflight, movie generation and audio assembly/muxing, but
exclude planner and TTS stages, which are recorded separately in the metrics.
Enhanced rendering costs more than legacy text-oriented templates; its benefit
here is explanatory graphics, not a speed improvement over those templates.
The earlier Remotion comparison remains a separate benchmark. These are single
local runs, not isolated-host measurements or a statistical reliability estimate.

Subsequent focused verification rerendered water with the split-safe active-phase
indicator (64.3 s) and signals with explicitly unfilled function strokes (74.7 s).
Those runs reused all narration audio and passed the same checks. They ran during
other regression work, so their times are not directly comparable to the table.

### Artifacts

These links point to local generated artifacts. Videos, narration caches and
preview frames are intentionally excluded from Git; they remain in the original
workspace and can be recreated using the commands below.

- [Latest water-cycle video](../outputs/enhanced_manim_water_final/water/primary.mp4)
  and [preview gallery](../outputs/enhanced_manim_water_final/water/primary_previews/index.html).
- [Math video](../outputs/enhanced_manim_integration/math/primary.mp4)
  and [preview gallery](../outputs/enhanced_manim_integration/math/primary_previews/index.html).
- [Networking video](../outputs/enhanced_manim_integration/networking/primary.mp4)
  and [preview gallery](../outputs/enhanced_manim_integration/networking/primary_previews/index.html).
- [Latest signal-processing video](../outputs/enhanced_manim_signals_final/signals/primary.mp4)
  and [preview gallery](../outputs/enhanced_manim_signals_final/signals/primary_previews/index.html).
- [Full four-subject comparison metrics](../outputs/enhanced_manim_integration/metrics.json).
- [Updated water verification](../outputs/enhanced_manim_water_final/metrics.json)
  and [updated signal verification](../outputs/enhanced_manim_signals_final/metrics.json).

Legacy videos, source narration manifests and logs are retained beside the
four-subject outputs. No original user video or lesson cache was deleted.

## Quality, reliability and limitations

All four runs preserved narration words, ordered scenes and concluding summaries.
Each had four semantic template IDs, matching expected concept metadata and
visual evidence. These coverage/variety checks are regression proxies, not human
learning scores or proof of semantic correctness. A water overview and summary
deliberately share the same landscape; continuity is justified rather than hidden.

Inspected frames show readable labels, actual formatted mathematics, directional
packet movement and appropriate waveform comparisons. The math fixture calculates
the deterministic update `3 - 0.2 × 6 = 1.8`, with new quadratic loss `3.24`.
Signal parameters not supplied by the lesson are explicitly labelled illustrative;
the curves are not claimed to be measurements or simulations of arbitrary systems.

Validation covers chronology, narration completeness, referenced concepts, motion
coverage, finite values, fonts, sampled bounds, text overlap, contrast and repeated
explanations/layouts. A short definition recalled from an introduction may precede
new detail; summaries and calculated worked-example givens have explicit reasons
for reuse. Content is not silently truncated or deleted.

The reviewed template library is bounded. Unknown topics can use general
relationship/process diagrams, but this is not a universal scientific simulator.
Unsupported equations and layouts still fail actionably. Geometry is sampled,
semantic relevance depends on reviewed templates and declared concepts, and fresh
LLM output remains subject to the existing three-attempt repair limits. Evidence
fields remain source-independent and ready for future grounding; no retrieval or
document upload functionality was added.

The full 162-test suite passed, including real MathTex and rendering tests and the
existing four-minute duration/splitting regression. Additional gallery/sidecar
regressions and the JavaScript state suite also passed. CLI checks covered the new
default, explicit legacy mode, optional Remotion failure, prompt dispatch, and the
existing legacy JSON sample through the enhanced preview-only path.

## Reproduce

```bash
python -m unittest discover -s tests -v
python -m src.integration_evaluation --validate-only
python -m src.integration_evaluation --subjects water math networking signals \
  --quality m --baseline --output-dir outputs/new_integration_run
```

The evaluation requires existing local Kokoro/voice assets and an installed
English language package. Its audio cache is seeded by copying existing WAVs and
manifests into an evaluation-owned directory. It never downloads models or
contacts paid services. Details of the shared representation are in
[the storyboard guide](storyboards.md).
