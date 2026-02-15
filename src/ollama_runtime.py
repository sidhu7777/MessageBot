import json
import subprocess
import time
import urllib.error
import urllib.request


class OllamaStartupError(RuntimeError):
    pass


def ensure_ollama_ready(
    base_url: str,
    model: str,
    auto_start: bool = True,
    auto_pull: bool = True,
    timeout_seconds: float = 30.0,
) -> None:
    if _is_ollama_up(base_url):
        _ensure_model(base_url, model, auto_pull=auto_pull)
        return

    if not auto_start:
        raise OllamaStartupError(
            f"Ollama is not reachable at {base_url}. Enable OLLAMA_AUTO_START=true or run 'ollama serve'."
        )

    _start_ollama_process()
    deadline = time.monotonic() + max(timeout_seconds, 1.0)
    while time.monotonic() < deadline:
        if _is_ollama_up(base_url):
            _ensure_model(base_url, model, auto_pull=auto_pull)
            return
        time.sleep(0.5)

    raise OllamaStartupError(
        f"Ollama did not become ready within {timeout_seconds}s at {base_url}."
    )


def _is_ollama_up(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


def _fetch_tags(base_url: str) -> dict:
    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=6) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload)
    except urllib.error.URLError as exc:
        raise OllamaStartupError(f"Failed to query Ollama tags from {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OllamaStartupError("Ollama /api/tags returned invalid JSON.") from exc


def _ensure_model(base_url: str, model: str, auto_pull: bool) -> None:
    if _model_exists(base_url, model):
        return
    if not auto_pull:
        raise OllamaStartupError(
            f"Model '{model}' not found in Ollama. Enable OLLAMA_AUTO_PULL=true or pull it manually."
        )
    _pull_model(model)
    if not _model_exists(base_url, model):
        raise OllamaStartupError(f"Model '{model}' is still missing after pull.")


def _model_exists(base_url: str, model: str) -> bool:
    data = _fetch_tags(base_url)
    models = data.get("models") or []
    names = {item.get("name", "") for item in models if isinstance(item, dict)}
    if model in names:
        return True
    # Ollama may store tags with :latest if no tag was given by caller.
    if ":" not in model and f"{model}:latest" in names:
        return True
    return False


def _pull_model(model: str) -> None:
    try:
        subprocess.run(["ollama", "pull", model], check=True, timeout=1800)
    except FileNotFoundError as exc:
        raise OllamaStartupError(
            "Cannot run 'ollama pull'. Ensure Ollama is installed and available in PATH."
        ) from exc
    except subprocess.SubprocessError as exc:
        raise OllamaStartupError(f"Failed to pull model '{model}': {exc}") from exc


def _start_ollama_process() -> None:
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise OllamaStartupError(
            "Cannot run 'ollama serve'. Ensure Ollama is installed and available in PATH."
        ) from exc

