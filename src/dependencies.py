"""Startup checks for external commands, Python packages, and Ollama."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class DependencyCheckError(RuntimeError):
    message: str

    def __str__(self) -> str:
        return self.message


def check_startup(*, require_ollama: bool, require_renderer: bool = True,
                  require_tts: bool = False, model: str = "qwen3:4b",
                  ollama_host: str | None = None, require_math: bool = False) -> None:
    """Fail fast with actionable instructions before expensive work starts."""
    missing_packages: list[str] = []
    optional_packages = (("ollama",) if require_ollama else ()) + (("kokoro", "soundfile") if require_tts else ())
    for package in ("pydantic", "rich") + optional_packages:
        try:
            importlib.import_module(package)
        except ImportError:
            missing_packages.append(package)
    if missing_packages:
        raise DependencyCheckError(
            "Missing Python package(s): " + ", ".join(missing_packages) +
            ". Install them with: python -m pip install -r requirements.txt"
        )

    if require_renderer and shutil.which("ffmpeg") is None:
        raise DependencyCheckError(
            "FFmpeg is not installed or is not on PATH. Install it with: "
            "sudo apt update && sudo apt install -y ffmpeg"
        )

    if require_renderer and shutil.which('ffprobe') is None:
        raise DependencyCheckError(
            'FFprobe is required to verify narration/video timing. Install it with: '
            'sudo apt update && sudo apt install -y ffmpeg'
        )

    if require_renderer:
        try:
            importlib.import_module("manim")
        except ImportError as exc:
            raise DependencyCheckError(
                "Manim is not installed in the active Python environment. Install it with: "
                "python -m pip install -r requirements.txt"
            ) from exc
        manim_check = subprocess.run(
            [sys.executable, "-m", "manim", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if manim_check.returncode != 0:
            raise DependencyCheckError(
                "Manim cannot be run by the active Python environment. Install it with: "
                "python -m pip install -r requirements.txt"
            )

    if require_math:
        from .video.mathematics import check_latex_dependencies
        check_latex_dependencies()

    if not require_ollama:
        return

    try:
        import ollama
        client = ollama.Client(host=ollama_host) if ollama_host else ollama
        response = client.list()
    except Exception as exc:
        host = ollama_host or "http://localhost:11434"
        raise DependencyCheckError(
            f"Ollama service is unavailable at {host}. Start it with: ollama serve"
        ) from exc

    # Ollama's Python client has returned both dictionaries and typed responses
    # across releases. If model names are available, provide an early pull hint.
    models = response.get("models", []) if isinstance(response, dict) else getattr(response, "models", [])
    names = {
        item.get("name") or item.get("model") if isinstance(item, dict)
        else getattr(item, "name", None) or getattr(item, "model", None)
        for item in models
    }
    if names and not any(name == model or str(name).split(":")[0] == model.split(":")[0] for name in names):
        raise DependencyCheckError(
            f"Ollama is running, but model '{model}' is not available. Pull it with: "
            f"ollama pull {model}"
        )
