"""Small Ollama client wrapper; no model output is ever executed."""

from __future__ import annotations

from typing import Any
import inspect


class OllamaError(RuntimeError):
    """A user-facing error communicating with the local Ollama service."""


class RecoverableOllamaError(OllamaError):
    """An aborted inference, retried within the planner's existing attempt budget."""

    def __init__(self, message: str, *, partial_response: str | None = None) -> None:
        super().__init__(message)
        self.partial_response = partial_response


class OllamaClient:
    def __init__(self, model: str = "qwen3:4b", host: str | None = None) -> None:
        self.model = model
        self.host = host
        self.is_qwen3 = model.lower().split('/')[-1].startswith('qwen3')
        self.options = {"num_ctx": 16384, "num_predict": 8192, "temperature": 0}
        if self.is_qwen3:
            # Qwen3's non-thinking recommendations explicitly avoid greedy decoding.
            self.options.update(temperature=0.7, top_p=0.8, top_k=20)

    def generate_json(self, prompt: str, schema: dict[str, Any]) -> Any:
        try:
            import ollama
        except ImportError as exc:
            raise OllamaError("The Ollama Python package is not installed.") from exc

        try:
            client = ollama.Client(host=self.host) if self.host else ollama
            arguments = dict(model=self.model, messages=[{"role": "user", "content": prompt}],
                             format=schema, options=dict(self.options))
            if self.is_qwen3:
                parameters = inspect.signature(client.chat).parameters
                if 'think' in parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
                    arguments['think'] = False
                else:
                    # Compatibility with older ollama-python clients in the supported range.
                    arguments['messages'][0]['content'] += '\n/no_think'
            response = client.chat(**arguments)
            message = response.get("message", {}) if isinstance(response, dict) else response.message
            content = message.get("content", "") if isinstance(message, dict) else message.content
            reason = response.get('done_reason') if isinstance(response, dict) else getattr(response, 'done_reason', None)
            if reason == 'length':
                raise RecoverableOllamaError(
                    f'Ollama reached the {self.options["num_predict"]}-token output limit before completing JSON. '
                    'Return a concise complete plan without repetitive metadata.', partial_response=content,
                )
            if not content:
                raise RecoverableOllamaError("Ollama returned an empty response. Return complete lesson JSON directly.")
            return content
        except OllamaError:
            raise
        except Exception as exc:
            from httpx import TimeoutException
            status = getattr(exc, 'status_code', None)
            if 'token repeat limit reached' in str(exc).lower() or (isinstance(status, int) and status >= 500) or isinstance(exc, (TimeoutError, TimeoutException)):
                raise RecoverableOllamaError(
                    f'Ollama aborted generation ({self.model}): {exc}. '
                    'Generate a fresh complete JSON response; avoid repeating tokens or fields.'
                ) from exc
            raise OllamaError(
                f"Could not generate a lesson with Ollama ({self.model}): {exc}"
            ) from exc

