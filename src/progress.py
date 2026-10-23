"""Rich-based terminal presentation and execution logging."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)


class ExecutionReporter:
    """Render pipeline events without owning any generation or rendering logic."""

    def __init__(self, *, console: Console | None = None, verbose: bool = False,
                 logger: logging.Logger | None = None) -> None:
        self.console = console or Console()
        self.verbose = verbose
        self.logger = logger or logging.getLogger("edugen")
        self._ollama_started_at: float | None = None
        self._render_started_at: float | None = None
        self._total_started_at = time.perf_counter()
        self._status = None
        self._scene_progress: Progress | None = None
        self._scene_tasks: list[int] = []
        self._tts_progress: Progress | None = None

    def header(self, prompt: str | None) -> None:
        self.console.print(Panel.fit("[bold cyan]EDUGEN AI GENERATOR[/bold cyan]"))
        if prompt:
            self.console.print("\n[bold]Prompt:[/bold]")
            self.console.print(prompt)

    def ollama_started(self, model: str) -> None:
        self._ollama_started_at = time.perf_counter()
        self._status = self.console.status(
            f"[cyan]Waiting for Ollama ({model})...[/cyan]", spinner="dots"
        )
        self._status.start()
        self.logger.info("Ollama generation started; model=%s", model)

    def ollama_attempt(self, attempt: int, max_attempts: int) -> None:
        if self._status:
            self._status.update(
                f"[cyan]Generating lesson with Ollama (attempt {attempt}/{max_attempts})...[/cyan]"
            )
        self.logger.info("Ollama attempt %d/%d", attempt, max_attempts)

    def planner_stage(self, stage: str) -> None:
        if self._status:
            self._status.update(f"[cyan]{stage}...[/cyan]")
        self.console.print(f"[cyan]•[/cyan] {stage}")
        self.logger.info("Planner stage: %s", stage)

    def lesson_cache_hit(self, kind: str) -> None:
        self.console.print(f"[green]✓[/green] Reused cached {kind}")
        self.logger.info("Cache hit: %s", kind)

    def ollama_finished(self, model: str) -> None:
        self._stop_status()
        elapsed = self._elapsed(self._ollama_started_at)
        self.console.print(f"[green]✓[/green] Ollama connection established")
        self.console.print(f"[green]✓[/green] Model loaded: {model} [dim]({elapsed:.1f}s)[/dim]")
        self.logger.info("Ollama generation completed in %.2fs", elapsed)

    def validation_retry(self, attempt: int, error: str) -> None:
        message = (f"Validation failed; preparing attempt {attempt + 1}/3" if attempt < 3
                   else "Validation failed; all 3 attempts exhausted")
        self.console.print(f"[yellow]↻[/yellow] {message}")
        self.logger.warning("Validation failed on attempt %d: %s", attempt, error)

    def generation_retry(self, attempt: int, maximum: int, error: str) -> None:
        suffix = f'preparing attempt {attempt+1}/{maximum}' if attempt < maximum else f'all {maximum} attempts exhausted'
        self.console.print(f'[yellow]↻[/yellow] Ollama generation aborted; {suffix}')
        self.logger.warning('Ollama generation failure on attempt %d/%d: %s', attempt, maximum, error)

    def lesson_validated(self, scene_count: int, retries: int) -> None:
        elapsed = self._elapsed(self._ollama_started_at)
        self.console.print(
            f"[green]✓[/green] Lesson plan generated ({scene_count} scenes) "
            f"[dim]({elapsed:.1f}s, {retries} retries)[/dim]"
        )
        self.console.print("[green]✓[/green] JSON schema validation passed")
        self.logger.info("Lesson validated: scenes=%d retries=%d", scene_count, retries)

    def rendering_started(self, scenes: list[dict[str, Any]]) -> None:
        self._render_started_at = time.perf_counter()
        self.console.print("\n[cyan][●][/cyan] Generating educational animations...")
        self._scene_progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=self.console,
            transient=True,
        )
        self._scene_progress.start()
        self._scene_tasks = [
            self._scene_progress.add_task(
                f"Scene {index}/{len(scenes)}: {scene.get('title', 'Untitled')}",
                total=1,
            )
            for index, scene in enumerate(scenes, start=1)
        ]
        self.logger.info("Rendering started: scenes=%d", len(scenes))

    def tts_started(self, scene_count: int, voice: str, speed: float) -> None:
        self.console.print(
            f"\n[cyan][●][/cyan] Generating narration with Kokoro "
            f"(voice={voice}, speed={speed:g})..."
        )
        self._tts_progress = Progress(
            SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
            TaskProgressColumn(), TimeElapsedColumn(), console=self.console,
            transient=True,
        )
        self._tts_progress.start()
        self._tts_progress.add_task("Narration scenes", total=scene_count)
        self.logger.info("TTS started: voice=%s speed=%s scenes=%d", voice, speed, scene_count)

    def tts_scene_started(self, index: int, total: int, title: str) -> None:
        self.logger.info("Generating narration %d/%d: %s", index, total, title)

    def tts_scene_completed(self, index: int, total: int, duration: float) -> None:
        if self._tts_progress and self._tts_progress.tasks:
            self._tts_progress.advance(self._tts_progress.tasks[0].id)
        self.console.print(f"[dim]Narration scene {index}/{total}: {duration:.1f}s[/dim]")
        self.logger.info("Narration completed %d/%d: %.2fs", index, total, duration)

    def tts_finished(self) -> None:
        if self._tts_progress:
            self._tts_progress.stop()
            self._tts_progress = None
        self.console.print("[green]✓[/green] Narration audio generated and cached")

    def narration_split(self, index: int, title: str, parts: int, duration: float) -> None:
        self.console.print(
            f"[yellow]↳[/yellow] Split scene {index} ({title}) into {parts} parts "
            f"because narration measured {duration:.1f}s"
        )
        self.logger.info("Split scene %d into %d parts; original audio=%.2fs", index, parts, duration)

    def duration_report(self, estimated: float, measured: float, target: float,
                        tolerance: float) -> None:
        self.console.print(
            f"[green]✓[/green] Duration: estimated {estimated:.1f}s, measured {measured:.1f}s, "
            f"target {target:.1f}s (±{tolerance:.0%})"
        )
        self.logger.info(
            "Duration report: estimated=%.2fs measured=%.2fs target=%.2fs tolerance=%.2f",
            estimated, measured, target, tolerance,
        )

    def scene_started(self, index: int, total: int, title: str) -> None:
        self.logger.info("Rendering scene %d/%d: %s", index, total, title)

    def scene_completed(self, index: int, total: int, title: str) -> None:
        if self._scene_progress and 1 <= index <= len(self._scene_tasks):
            self._scene_progress.update(self._scene_tasks[index - 1], completed=1)
        self.logger.info("Completed scene %d/%d: %s", index, total, title)

    def scene_frame_progress(self,index: int,completed: float,total: float) -> None:
        """Optional adapters report actual rendered frames, never guessed time."""
        if self._scene_progress and 1 <= index <= len(self._scene_tasks) and total>0:
            self._scene_progress.update(self._scene_tasks[index-1],completed=min(1,max(0,completed/total)))

    def rendering_finished(self, output_path: Path) -> None:
        if self._scene_progress:
            self._scene_progress.stop()
            self._scene_progress = None
        if self._tts_progress:
            self._tts_progress.stop()
            self._tts_progress = None
        elapsed = self._elapsed(self._render_started_at)
        total = time.perf_counter() - self._total_started_at
        self.console.print(f"[green]✓[/green] Rendering complete [dim]({elapsed:.1f}s)[/dim]")
        self.console.print(f"[bold green]Created:[/bold green] {output_path}")
        self.console.print(f"[dim]Total execution time: {total:.1f}s[/dim]")
        self.logger.info("Rendering completed: output=%s elapsed=%.2fs total=%.2fs", output_path, elapsed, total)

    def verbose_line(self, line: str) -> None:
        self.logger.info("Manim: %s", line)
        if self.verbose:
            self.console.print(f"[dim]{line}[/dim]", markup=False)

    def failed(self, error: BaseException) -> None:
        self._stop_status()
        if self._scene_progress:
            self._scene_progress.stop()
            self._scene_progress = None
        if self._tts_progress:
            self._tts_progress.stop()
            self._tts_progress = None
        self.logger.error(
            "Generation failed: %s", error,
            exc_info=(type(error), error, error.__traceback__),
        )
        self.console.print(f"[bold red]✗ Generation failed:[/bold red] {error}")
        if self.verbose and error.__traceback__ is not None:
            from rich.traceback import Traceback
            self.console.print(Traceback.from_exception(type(error), error, error.__traceback__))

    def cancelled(self) -> None:
        self._stop_status()
        if self._scene_progress:
            self._scene_progress.stop()
            self._scene_progress = None
        if self._tts_progress:
            self._tts_progress.stop()
            self._tts_progress = None
        self.logger.warning("Generation cancelled by user")
        self.console.print("[yellow]Generation cancelled.[/yellow]")

    def _stop_status(self) -> None:
        if self._status:
            self._status.stop()
            self._status = None

    @staticmethod
    def _elapsed(started: float | None) -> float:
        return 0.0 if started is None else time.perf_counter() - started


def configure_logging(log_dir: Path = Path("logs"), verbose: bool = False) -> tuple[logging.Logger, Path]:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / time.strftime("edugen_%Y%m%d_%H%M%S.log")
    logger = logging.getLogger("edugen")
    logger.setLevel(logging.DEBUG)
    # Manim configures the root logger with Rich handlers at import time. Keep file
    # diagnostics from being echoed through those handlers over the live spinner.
    logger.propagate = False
    for old_handler in logger.handlers:
        old_handler.close()
    logger.handlers.clear()
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.DEBUG if verbose else logging.WARNING)
    return logger, path
