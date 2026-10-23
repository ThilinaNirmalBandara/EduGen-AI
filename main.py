"""EduGen-AI command-line entry point for JSON and natural-language lessons."""

import argparse
import json
import subprocess
from pathlib import Path

from src.video.contract import validate_lesson
from src.video.renderer import render_lesson
from src.llm import OllamaClient
from src.planner import LessonPlanner
from src.progress import ExecutionReporter, configure_logging
from src.dependencies import check_startup
from src.tts import KokoroTTS
from src.duration import DurationConstraintError, prepare_audio_lesson, validate_measured_duration
from src.video.timeline import prepare_visual_lesson
from src.video.mathematics import check_latex_dependencies


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a JSON or prompted lesson plan to MP4")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--lesson", type=Path, help="Path to lesson JSON")
    source.add_argument("--prompt", help="Natural-language educational prompt")
    parser.add_argument("--output", type=Path, default=Path("outputs/educational_video.mp4"))
    parser.add_argument("--quality", choices=("l", "m", "h"), default="m",
                        help="Render quality: m is the default 720p preview; l is low quality")
    parser.add_argument('--renderer',choices=('manim','illustrated','legacy','remotion'),default='manim',
                        help='Enhanced Manim with automatic illustrations and native math (default); legacy templates or experimental Remotion are optional')
    parser.add_argument('--theme',default='daylight',choices=('daylight','ocean','chalk'))
    parser.add_argument('--theme-file',type=Path,help='Custom illustrated-renderer theme JSON')
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--preview-only", action="store_true", help="Generate and validate scene preview frames without rendering the final MP4")
    parser.add_argument("--model", default="qwen3:4b", help="Ollama model (default: qwen3:4b)")
    parser.add_argument("--ollama-host", help="Optional Ollama server URL")
    parser.add_argument("--verbose", action="store_true", help="Show detailed renderer output")
    parser.add_argument("--duration", type=float, default=3.5, help="Target video duration in minutes (default: 3.5)")
    parser.add_argument("--voice", default="af_heart", help="Kokoro voice (default: af_heart)")
    parser.add_argument("--speed", type=float, default=1.0, help="Narration speech speed")
    parser.add_argument("--no-voice", action="store_true", help="Disable narration audio")
    parser.add_argument("--force", action="store_true", help="Ignore cached plans and narration audio")
    parser.add_argument("--duration-tolerance", type=float, default=0.15,
                        help="Allowed measured-duration deviation as a fraction (default: 0.15)")
    args = parser.parse_args()
    logger, log_path = configure_logging(verbose=args.verbose)
    reporter = ExecutionReporter(verbose=args.verbose, logger=logger)
    reporter.header(args.prompt)
    logger.info("EduGen started; log=%s", log_path)
    try:
        if not args.validate_only and args.renderer!='legacy':
            from src.storyboard.assets import validate_assets
            from src.storyboard.themes import load_theme
            validate_assets()
            load_theme(args.theme,args.theme_file)
            if args.renderer=='remotion':
                from src.video.remotion_renderer import check_remotion
                check_remotion()
        check_startup(
            require_ollama=args.prompt is not None,
            require_renderer=not args.validate_only,
            require_tts=not args.no_voice and not args.validate_only,
            model=args.model,
            ollama_host=args.ollama_host,
            require_math=not args.validate_only and args.prompt is not None and any(
                word in args.prompt.lower() for word in ("gradient", "equation", "derivative", "calculus", "mathematical")
            ),
        )
        logger.info("Startup dependency check passed")
        if args.prompt is not None:
            planner = LessonPlanner(
                OllamaClient(model=args.model, host=args.ollama_host), reporter=reporter,
                cache_dir=Path("outputs/cache"),
                generation_settings={
                    "voice": None if args.no_voice else args.voice,
                    "speed": args.speed,
                },
            )
            lesson = planner.plan(args.prompt, duration_minutes=args.duration, force=args.force)
        else:
            planner = None
            lesson = validate_lesson(json.loads(args.lesson.read_text(encoding="utf-8")))
            reporter.lesson_validated(len(lesson["scenes"]), 0)
        if args.validate_only:
            return 0
        lesson = prepare_visual_lesson(lesson, prompt=args.prompt or "")
        if any(scene["type"] in {"graph", "equation", "worked_example"} for scene in lesson["scenes"]):
            check_latex_dependencies()
        audio_segments = None
        if not args.no_voice:
            tts = KokoroTTS(
                voice=args.voice, speed=args.speed, reporter=reporter, force=args.force
            )
            audio_segments = tts.synthesize_plan(lesson)
            if args.prompt is not None:
                prepared = prepare_audio_lesson(lesson, audio_segments, tts, reporter=reporter)
                estimated = sum(float(scene["duration"]) for scene in lesson["scenes"])
                if hasattr(reporter, "duration_report"):
                    reporter.duration_report(
                        estimated, prepared.total_audio_duration, args.duration * 60,
                        args.duration_tolerance,
                    )
                try:
                    validate_measured_duration(
                        prepared.total_audio_duration, args.duration, args.duration_tolerance
                    )
                except DurationConstraintError as duration_error:
                    if planner is None:
                        raise
                    lesson = planner.revise_for_duration(
                        lesson, args.prompt, args.duration, str(duration_error), force=args.force
                    )
                    lesson = prepare_visual_lesson(lesson, prompt=args.prompt or "")
                    audio_segments = tts.synthesize_plan(lesson)
                    prepared = prepare_audio_lesson(lesson, audio_segments, tts, reporter=reporter)
                    validate_measured_duration(
                        prepared.total_audio_duration, args.duration, args.duration_tolerance
                    )
                    if hasattr(reporter, "duration_report"):
                        reporter.duration_report(
                            sum(float(scene["duration"]) for scene in lesson["scenes"]),
                            prepared.total_audio_duration, args.duration * 60,
                            args.duration_tolerance,
                        )
                lesson = prepared.lesson
                audio_segments = prepared.audio_segments
            else:
                prepared = prepare_audio_lesson(lesson, audio_segments, tts, reporter=reporter)
                lesson, audio_segments = prepared.lesson, prepared.audio_segments
        if args.renderer=='legacy':
            result = render_lesson(
                lesson, args.output, args.quality, reporter=reporter,
                audio_segments=audio_segments,
                preview_only=args.preview_only,
            )
        else:
            from src.video.illustrated_pipeline import render_illustrated_lesson
            result=render_illustrated_lesson(lesson,args.output,args.quality,renderer=args.renderer,
                audio_segments=audio_segments,theme=args.theme,theme_file=args.theme_file,
                reporter=reporter,preview_only=args.preview_only,force=args.force,prompt=args.prompt or '')
        return 0
    except KeyboardInterrupt:
        reporter.cancelled()
        return 130
    except (OSError, json.JSONDecodeError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        reporter.failed(exc)
        reporter.console.print(f"Detailed log: {log_path}", markup=False)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
