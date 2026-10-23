# Optional Remotion experiment

This is not the default EduGen renderer. Install pinned local dependencies with
`npm ci --no-audit --no-fund`; run pure state tests with `npm test`.
An existing Chrome/Chromium executable is required (`EDUGEN_CHROME` can select it).
No browser download, model change, paid API, cloud renderer or new speech synthesis
is involved in the cache-only comparison.

From the repository root:

```bash
python -m src.visual_benchmark --renderers baseline,illustrated,remotion
```

The Python adapter validates the storyboard and precomputes typography before
passing JSON props to the fixed React composition. SVG assets are served from the
same repository `assets/` folder as Manim. Python/JS motion-state parity is tested.
Previews are generated for each narration cue; video progress uses actual rendered
frame counts. Both video adapters use the same FFmpeg audio muxing code.

Extending the storyboard does not mean allowing generated JSX. Only reviewed,
checked-in components render data. Keep mathematical scenes on the default Manim
renderer until a separately tested math bridge exists.

Licensing is conditional. Local evaluation is among the free-use categories in
the [official license FAQ](https://www.remotion.dev/docs/license/faq); check the
organization/commercial terms before shipping a larger automated service.
