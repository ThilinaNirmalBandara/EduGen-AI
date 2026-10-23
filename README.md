# EduGen-AI — Educational Video Generator

EduGen-AI turns either a structured lesson JSON file or a natural-language educational prompt into structured, narrated Manim scenes and joins them into an MP4 with FFmpeg. Ollama first creates a lesson outline, then expands it into validated narration and visual instructions; Kokoro generates cached narration audio; fixed Manim templates render the visuals. The model emits data only—it never emits or executes Python.

## Primary enhanced Manim renderer

Enhanced Manim is now the default for existing prompt and JSON commands. Each
scene's learning objective, narration and display content select a reviewed visual
template; changing a lesson title does not change its subject. One validated
storyboard can contain SVG illustrations, connected diagrams and native MathTex,
graph or numerical scenes in their original order.

```bash
# Compare three 45–60-second previews using only an existing water-cycle cache.
# Does not contact Ollama or synthesize speech. Default: 720p, 30 FPS.
python -m src.visual_benchmark --renderers baseline,illustrated

# Existing commands automatically use enhanced Manim (no new flag required).
python main.py --prompt "Explain the water cycle for a 12-year-old, with a final summary" \
  --duration 4 --theme daylight

python main.py --prompt "Explain gradient descent with a loss curve and numerical example" --duration 4
python main.py --prompt "Explain IP packet routing and TCP acknowledgments" --duration 4
python main.py --prompt "Explain low-pass filtering of a sensor signal" --duration 4
```

The SVG library is used only when its imagery matches the concept. Otherwise,
reviewed templates draw packet routes, ACKs and retransmission; neural connections;
sampled waveforms and low-pass response curves; natural cycles; concept maps and
ordered processes. Unspecified numerical demonstrations are labelled illustrative.
No arbitrary generated Python or JavaScript is executed.

Mathematical scenes automatically use the existing audited Manim templates.
`--renderer illustrated` remains an alias for enhanced Manim. Use
`--renderer legacy` for the original templates or `--renderer remotion` for the
optional experiment. Native mathematics is not supported by Remotion.

Available illustrated themes: `daylight`, `ocean`, `chalk`; `--theme-file` accepts
custom theme JSON. `--quality l` is a scaled draft; default `m` is 720p. Use
`--preview-only` to inspect cue frames before producing an MP4. Every default run
also writes `<output_stem>_previews/index.html` before movie rendering starts.
Unreadable layouts, unjustified repetition and missing coverage are rejected;
narration is never deleted to hide a quality failure. Validated storyboards have
their own cache under `outputs/cache/storyboards/`. Changing quality or theme
does not regenerate the LLM lesson or unchanged speech.

Remotion is optional and requires a separate local Node installation and an existing
Chrome/Chromium browser (no automatic browser download):

```bash
cd experiments/remotion
npm ci --no-audit --no-fund
cd ../..
python -m src.visual_benchmark --renderers baseline,illustrated,remotion
```

The benchmark reads `outputs/water_cycle.lesson.json` and exact matching Kokoro
phrase audio in `outputs/narration/`. Override these with `--lesson` and
`--audio-cache`. It writes only its comparison folder and refuses to overwrite
existing comparison videos unless `--overwrite` is explicit. Run backends
sequentially, not in concurrent invocations into the same output directory.

See [storyboard architecture and extension guide](docs/storyboards.md) and
[measured visual-rendering evaluation](docs/visual-rendering-evaluation.md).
The [multi-subject integration evaluation](docs/primary-manim-integration.md)
documents deterministic planner replay, actual Kokoro narration, native math and
real rendering across four subjects.
Remotion has conditional licensing; this local evaluation uses no paid APIs. See
the [official eligibility rules](https://www.remotion.dev/docs/license/faq) before
adopting it for a larger commercial organization.

## Why begin here?

Make the renderer dependable before adding Ollama, document parsing, local TTS, or agents. In later stages, the LLM should generate a validated JSON plan like `examples/sample_lesson.json` rather than writing and executing unrestricted Python.

## Setup (Ubuntu / WSL2 recommended)

Requires Python 3.11, Manim Community, and FFmpeg. Manim has some native dependencies; consult https://docs.manim.community/ if installation is unsuccessful.

```bash
sudo apt update
sudo apt install -y ffmpeg pkg-config libcairo2-dev libpango1.0-dev
sudo apt install -y texlive-latex-base texlive-latex-recommended dvisvgm
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
```

The CLI uses Rich for live terminal status and progress reporting. Normal runs show
stage summaries and scene completion; add `--verbose` to include streamed Manim
output. Detailed execution logs are written to `logs/`.

Each generation performs a startup preflight before contacting Ollama. It checks the
active Python environment, Manim, FFmpeg, Ollama connectivity, and the requested
model, then exits with an installation or startup command if a dependency is missing.

Install and start Ollama separately, then pull the default model:

```bash
ollama pull qwen3:4b
ollama serve
```

Some distributions additionally require build tools and system libraries for Manim. On Windows, prefer WSL2 or follow Manim's official Windows setup.

Test that the dependencies can run:

```bash
manim --version
ffmpeg -version
```

## Run a sample video

```bash
python main.py --lesson examples/sample_lesson.json --quality l
```

Output: `outputs/educational_video.mp4`. Medium quality (`-qm`) is the default 1280×720, 30 FPS render; use `--quality l` for a low-quality preview or `--no-voice` for the legacy silent output. Manim documents medium quality as 1280×720 at 30 FPS. [Manim quality configuration](https://docs.manim.community/en/stable/guides/configuration.html)

You can also validate lesson JSON without rendering:

```bash
python main.py --lesson examples/sample_lesson.json --validate-only
```

Generate a lesson from a prompt and render it:

```bash
python main.py --prompt "Explain photosynthesis to a beginner" --duration 4
```

`--duration` is specified in minutes and defaults to approximately 3.5 minutes.
Prompt-generated lessons include concise display text plus longer `narration_text`,
with equation, graph, and worked-example templates available for mathematical topics.
Before rendering, EduGen checks narration depth, scene count, visual variety, required
mathematical visuals, and measured audio duration against the requested target.

Voice options:

```bash
python main.py --prompt "Explain gradient descent" --duration 4 \
  --voice af_heart --speed 1.0
python main.py --prompt "Explain gradient descent" --duration 4 --no-voice
```

Narration WAV files are cached under `outputs/narration/` using the narration text,
voice, and speed as the cache key. Scene video duration is extended as needed to
cover the measured audio duration, and FFmpeg muxes the audio into the final MP4.
If a measured narration segment exceeds the 45-second scene limit, EduGen splits it
at sentence or word boundaries, reuses the relevant visual plan, and remeasures each
new audio segment before rendering. Validated outlines and completed plans are cached
under `outputs/cache/`.

Mathematics uses Manim `MathTex` with a fixed LaTeX preamble. Equations are
normalized (including Greek symbols and Unicode powers) and checked against an
allowlist of mathematical commands. File access, document commands, and custom
LaTeX definitions are rejected. Missing LaTeX tools or invalid expressions produce
an installation/syntax error; equation strings are never shown as raw LaTeX.

Narration is synthesized as short sentence/clause segments. Each segment's actual
sample duration determines the corresponding visual event's start and duration.
The assembled WAV and its timing manifest are cached separately from lesson plans.
Overlong scenes are cut at these measured boundaries without regenerating audio;
their visual cues and numerical iteration references remain attached to the cuts.
This is phrase-level synchronization, not forced alignment of individual words.

For gradient-descent lessons, the visual preparation stage supplies a consistent
quadratic demonstration: `f(x)=x²`, initial `x=3`, learning rate `0.2`. Gradients,
updates, losses, and the associated numerical narration are calculated locally.
Graphs focus on motion and slopes, while worked examples reveal the arithmetic.
The mathematical derivation uses a first-order Taylor approximation and explains
why a sufficiently small step in the negative-gradient direction reduces loss.
These prepared scenes are saved separately; the cached LLM lesson is preserved.

Process templates show one full-size active step and its predecessor. Text reflows
without shrinking below 28 Manim font units. Before movie rendering, every visual
event is constructed in a preview pass, compiling equations and checking safe frame
boundaries, text sizes, overlapping text, and timeline coverage. An uncovered interval
or event longer than eight seconds fails validation. The checks catch geometry and
coverage problems; they are not a general factual or pedagogical quality guarantee.

Scenes and visual events carry stable IDs. Process steps are assigned ordered
`step_index` references before TTS, not guessed from narration keywords during
rendering. Measured audio splits preserve these references and the original numerical
parameters. A coverage check verifies the original scene order and every spoken word;
it rejects missing steps or reordered events rather than sorting them silently.

English lessons are checked for unexpected non-Latin/non-Greek alphabetic characters
in narration and display text. Incomplete equations, unsafe LaTeX, incorrect constant
arithmetic and explicit incorrect quadratic evaluations are rejected with field-specific
errors. These errors participate in the existing three-attempt planner repair loop.
Older cached plans that fail the new checks reuse their outline and enter repair; a
failed repair does not replace the cached source. This is bounded arithmetic/domain
validation, not a proof checker for arbitrary mathematical or natural-language claims.

Resumed narration-only drafts are revalidated with the current content and coverage
rules before their visual fields are frozen. If an older draft has invalid steps,
arithmetic, language, or a missing conclusion, repair switches back to complete-plan
JSON within the same three-attempt budget. No cache deletion or `--force` is needed
to escape a stale narration-only draft. Unicode numeric subscripts such as `x₀` are
normalized to mathematical LaTeX, while embedded arithmetic is checked as complete
expressions rather than mistaken numeric suffixes. Gradient-descent repair prompts
include a locally computed iteration reference; generated values must still validate.

Full-plan generation and repair now constrain English prose and display labels in
the output schema itself, including their existing length limits. Per-scene spoken
word budgets apply during initial expansion and duration revision, not just in
narration-only repairs. Generated duration fields choose legal second values from
3 through 45 instead of relying on floating-point schema bounds alone. These are
generation-only constraints: legacy Unicode JSON, mathematical LaTeX, measured TTS
timing, and the existing Pydantic scene contract remain supported. Schema-constrained
formatting does not guarantee factual accuracy; all content and duration checks
still run, with at most three attempts and no silent truncation or filler insertion.

Visual templates are selected from scene objectives: concepts, tangent slopes,
learning-rate comparisons, derivations, numerical calculations, convergence/loss
histories, processes and concluding summaries. Comparisons calculate their loss
histories locally, including an explicitly unstable quadratic learning rate. Worked
examples display calculated substitutions instead of another copy of the same graph.
Text contrast and opacity are audited in addition to font size and layout boundaries.

New plans reserve a final summary. Older prompted plans without one gain a concise
summary before narration is measured; essential scenes are never dropped to make room.
Duration revisions preserve scene titles, order, IDs and the conclusion. Locally
computed numerical narration is held fixed during revision; other explanations can
be expanded or condensed, then all replacement audio is remeasured as before.

Preview PNGs and layout reports are saved alongside the output, in
`outputs/educational_video_previews/`. The exact timed scene plan is saved as
`outputs/educational_video.lesson.json`. To inspect these without rendering an MP4:

```bash
python main.py --prompt "Explain gradient descent" --duration 4 --preview-only
```

Run tests (real rendering tests run when LaTeX, Manim, FFmpeg, and dvisvgm are installed):

```bash
python -m unittest discover -s tests -v
```

End-to-end tests cover legacy templates and a fixed four-minute gradient-descent
lesson in `tests/fixtures/gradient_descent_quality.json`. The four-minute test includes
a 60-second process narration that must split into ordered sub-scenes, eight distinct
visual templates, a complete final summary, readable event previews and FFprobe checks
of actual MP4 duration and audio/video streams. Synthetic test audio exercises the
same measured timing contract as Kokoro without downloading models or calling Ollama.
Test videos, previews and LaTeX caches live in temporary directories.

For prompted generation, the Ollama output schema requires `narration_text` in
every scene and includes the display-text length limits (including 45 characters
per process step). Four-minute lessons aim for about 560 spoken words, spread
across the scenes. The minimum-depth and measured-duration checks still apply.
When only narration is missing or too short, repairs expand the spoken explanation
while preserving validated diagrams, equations, display text, and animation settings.
Repairs retain the original request and outline and allow at most three attempts.

Qwen3 structured generation uses non-thinking mode and non-greedy sampling
(temperature 0.7, top-p 0.8, top-k 20), following the
[Qwen3 model guidance](https://huggingface.co/Qwen/Qwen3-4B). Context and output
budgets are explicitly bounded at 16,384 and 8,192 tokens. Runtime-only narration
timelines are excluded from the generation schema, and generated animation options
use known data fields; the complete plan still passes the existing Pydantic checks.

If Ollama aborts with a token-repetition error, a transient server failure, empty
output, or a truncated response, that failure uses the same three-attempt budget as
schema repairs. The outline and last usable draft are retained. Missing-model and
other non-recoverable errors still terminate with a clear message. No model switch,
unlimited retry loop, or bypass of content/duration validation is used.
If only estimated scene seconds are inconsistent, they are recalculated from
narration at 140 words per minute (bounded by the existing scene limits), and the
whole plan is checked again. Narration is preserved. This estimate never replaces
the final measured Kokoro duration and tolerance checks.

If expansion exhausts its attempts, the outline and latest draft are saved separately
from completed plans. Run the same command again to resume repair from that draft.
Changing `--quality` or `--output` reuses the validated plan and narration. To bypass
cached outlines, drafts, plans, and narration explicitly, add `--force`:

```bash
python main.py --prompt "Explain gradient descent" --duration 4 --force
```

Normal errors are displayed once as stage messages; complete tracebacks are retained
in `logs/`. Add `--verbose` to show exception details in the terminal too.

For detailed renderer diagnostics:

```bash
python main.py --prompt "Explain gradient descent" --verbose
```

Use `--model` to select another locally installed Ollama model and `--ollama-host` for a non-default Ollama server.

## Lesson JSON contract

The `scenes` list can contain the following `type` values:

- `title`: `title`, optional `subtitle`.
- `bullets` / `summary`: `title`, `bullets` (1–5 short strings).
- `process`: `title`, `steps` (2–4 short strings).
- `equation`: `title`, `equation`.
- `graph`: `title`, `graph_type` (`gradient_descent` or `parabola`), optional `equation`.

All scenes require `duration` (seconds, minimum 3) and may include `narration_text`.
This is a visual target; measured narration duration is used during rendering.

## Planned phases

1. **This scaffold:** JSON -> rendered video; build more safe, preset Manim scene templates.
2. **Ollama:** prompt -> structured JSON `LessonPlan` using JSON-schema output, validated against the contract. (Implemented.)
3. **Documents:** PDF/PPTX -> page-indexed source records with Docling or PyMuPDF.
4. **Grounding:** every claim -> source page + quote; reject missing evidence; manual review of paraphrases and equations.
5. **Voice:** Kokoro TTS -> WAV per scene -> align scene timings and join with FFmpeg. (Implemented.)
6. **App:** Streamlit upload/prompt/review/download UI.
7. **Optional:** LangGraph orchestration and an MCP interface after the above is robust.

## Important limitations

- Scene titles and bullet contents come from the input JSON and are not fact-checked.
- There is no document-grounding or hallucination guarantee in Phase 1.
- This scaffold only renders predefined types; it does not execute model-generated code.
- Mathematical equations require the system LaTeX packages listed in Setup.
