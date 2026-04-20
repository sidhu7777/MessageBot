import json
import urllib.error
import urllib.request


class LLMClient:
    """Ollama chat client used for response polishing and extraction fallback."""

    def __init__(
        self,
        model: str,
        provider: str = "ollama",
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 12.0,
    ) -> None:
        self.model = model
        self.provider = provider.lower()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        if self.provider != "ollama":
            raise RuntimeError(f"Unsupported LLM provider: {self.provider}")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.2},
        }

        request = urllib.request.Request(
            url=f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:
                body = ""
            detail = f"HTTP {exc.code} {exc.reason}"
            if body:
                detail = f"{detail}: {body}"
            raise RuntimeError(f"Ollama chat request failed: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Failed to call Ollama: {exc}") from exc
        except TimeoutError as exc:
            raise RuntimeError(
                f"Ollama chat request timed out after {self.timeout_seconds:.1f}s"
            ) from exc

        data = json.loads(raw)
        content = data.get("message", {}).get("content", "").strip()
        if not content:
            raise RuntimeError("LLM returned empty content")
        return content
