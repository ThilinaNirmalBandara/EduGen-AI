"""Render each fixed scene via Manim CLI, then concatenate with FFmpeg."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .contract import validate_lesson
from .mathematics import check_latex_dependencies, sanitize_math
from .timeline import bind_audio_timeline, estimated_timeline, validate_timeline
from .sequence import identify_lesson, validate_sequence
from src.content import validate_content

SCENE_TEMPLATE = Path(__file__).with_name("manim_scenes.py").resolve()


def render_lesson(lesson: dict, output_path: Path, quality: str = "l",
                  reporter: Any | None = None, audio_segments: list[Any] | None = None,
                  *, preview_only: bool = False, preview_dir: Path | None = None) -> Path:
    validate_lesson(lesson)
    validate_content(lesson)
    lesson = identify_lesson(lesson)
    if quality not in {"l", "m", "h"}:
        raise ValueError("Quality must be l, m, or h")
    if audio_segments is not None and len(audio_segments) != len(lesson["scenes"]):
        raise ValueError("There must be one narration audio segment per scene.")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg is not installed or is not on PATH")

    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview_dir = (Path(preview_dir) if preview_dir else output_path.parent / (output_path.stem + "_previews")).resolve()
    preview_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for index, scene in enumerate(lesson["scenes"]):
        render_scene = dict(scene)
        if (scene['type'] == 'graph' and scene.get('graph_type', 'gradient_descent') == 'gradient_descent'
                and scene.get('equation', 'x^2').split('=')[-1].replace(' ', '').replace('²', '^2') not in {'x^2', 'x^{2}'}):
            raise ValueError('The gradient-descent template models f(x)=x^2. Use graph_type=parabola for another polynomial; a different equation will not be silently replaced.')
        if audio_segments is not None:
            audio = audio_segments[index]
            if audio.duration > 44.5:
                raise ValueError(f"Scene {index+1} narration exceeds 44.5s; split it before rendering.")
            render_scene["duration"] = max(3, audio.duration + 0.35)
            if getattr(audio, "timings", ()) and render_scene.get("animation_params", {}).get("timing_source") != "measured":
                render_scene = bind_audio_timeline(render_scene, audio)
        elif render_scene.get("animation_params", {}).get("timing_source") != "measured":
            render_scene["visual_timeline"] = estimated_timeline(render_scene, cover_silent=True)
        if not render_scene.get("visual_timeline"):
            render_scene["visual_timeline"] = estimated_timeline(render_scene, cover_silent=True)
        validate_timeline(render_scene)
        if scene["type"] in {"equation", "worked_example", "graph"}:
            check_latex_dependencies()
            sanitize_math(scene.get("equation", "f(x)=x^2"))
        staged.append(render_scene)
    lesson = dict(lesson, scenes=staged)
    validate_lesson(lesson)
    validate_sequence(lesson)
    # Preserve the exact timed plan used for this render separately from LLM caches.
    output_path.with_suffix(".lesson.json").write_text(json.dumps(lesson, ensure_ascii=False, indent=2))
    with tempfile.TemporaryDirectory(prefix="edugen_") as tmp:
        working = Path(tmp)
        videos: list[Path] = []
        final_segments: list[Path] = []
        if reporter:
            reporter.planner_stage("Visual preflight: equations, layouts, timeline coverage, and preview frames")
        jobs = []
        # Exercise every event and compile every MathTex expression before rendering
        # any movie. -s skips movie frames but still constructs/audits all states.
        for index, scene in enumerate(staged, start=1):
            number = f"{index:03d}"
            scene_json = working / f"scene_{number}.json"
            scene_json.write_text(json.dumps(scene, ensure_ascii=False), encoding="utf-8")
            media_dir = working / f"media_{number}"
            env = os.environ.copy()
            env["EDUGEN_SCENE_FILE"] = str(scene_json)
            command = [sys.executable, "-m", "manim", f"-q{quality}", "--frame_rate", "30",
                       "--disable_caching", "--media_dir", str(media_dir),
                       "-o", f"scene_{number}", str(SCENE_TEMPLATE), "TemplateScene"]
            scene_previews = preview_dir / f"scene_{number}"
            scene_previews.mkdir(parents=True, exist_ok=True)
            preview_env = dict(env, EDUGEN_PREVIEW_DIR=str(scene_previews),
                               EDUGEN_LAYOUT_REPORT=str(scene_previews / "layout.json"))
            status = reporter.console.status(f"Checking visual layout and equations: scene {index}/{len(staged)}") if reporter else nullcontext()
            with status:
                _run_streaming(command + ["-s"], env=preview_env, reporter=reporter)
            jobs.append((scene_json, media_dir, env, command))
        if preview_only:
            if reporter:
                reporter.console.print(f"Preview frames and layout reports: {preview_dir}")
            return preview_dir
        if reporter:
            reporter.rendering_started(lesson["scenes"])
        for index, scene in enumerate(lesson["scenes"], start=1):
            number = f"{index:03d}"
            scene_json, media_dir, env, command = jobs[index-1]
            title = scene.get("title", f"Scene {index}")
            if reporter:
                reporter.scene_started(index, len(lesson["scenes"]), title)
            else:
                print(f"[EduGen] Rendering scene {index}/{len(lesson['scenes'])}...")
            _run_streaming(command, env=env, reporter=reporter)
            results = sorted(media_dir.rglob(f"scene_{number}.mp4"))
            if len(results) != 1:
                raise RuntimeError(f"Expected 1 rendered MP4 for scene {number}; got {len(results)}")
            videos.append(results[0])
            if audio_segments is not None:
                muxed = working / f"scene_{number}_audio.mp4"
                _run_streaming(
                    ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                     "-i", str(results[0]), "-i", str(audio_segments[index - 1].path),
                     "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
                     "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(muxed)],
                    reporter=reporter,
                )
                final_segments.append(muxed)
            if reporter:
                reporter.scene_completed(index, len(lesson["scenes"]), title)

        concat_file = working / "concat.txt"
        # All files live in the generated temp directory. Escape apostrophes for concat demuxer.
        concat_inputs = final_segments or videos
        concat_file.write_text(
            "".join("file '" + str(video).replace("'", "'\\''") + "'\n" for video in concat_inputs),
            encoding="utf-8",
        )
        concat_command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
             "-safe", "0", "-i", str(concat_file), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if audio_segments is None:
            concat_command.append("-an")
        else:
            concat_command.extend(["-c:a", "aac"])
        concat_command.extend(["-movflags", "+faststart", str(output_path)])
        _run_streaming(
            concat_command,
            reporter=reporter,
        )
    if reporter:
        reporter.rendering_finished(output_path)
    return output_path


def _run_streaming(command: list[str], *, env: dict[str, str] | None = None,
                   reporter: Any | None = None) -> None:
    """Run a renderer command while preserving output and Ctrl+C cancellation."""
    process = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    from collections import deque
    tail = deque(maxlen=30)
    try:
        assert process.stdout is not None
        for line in process.stdout:
            tail.append(line.rstrip())
            if reporter:
                reporter.verbose_line(line.rstrip())
            else:
                print(line, end="")
        return_code = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()
    if return_code:
        errors = [line for line in tail if any(name in line for name in ("MathRenderingError", "VisualQualityError", "ValueError", "Error:"))]
        detail = "\n".join(errors[-3:] or list(tail)[-5:])
        raise RuntimeError(f"{Path(command[0]).name} failed (exit {return_code}): {detail}. See the execution log for full output.")
