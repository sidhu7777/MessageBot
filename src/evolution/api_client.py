from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import request as urlrequest


@dataclass(frozen=True)
class EvolutionApiSettings:
    base_url: str
    api_key: str
    send_text_path_template: str


class EvolutionApiClient:
    def __init__(self, settings: EvolutionApiSettings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self._settings.base_url and self._settings.api_key)

    def send_text(self, *, instance_name: str, remote_jid: str, text: str) -> None:
        if not self.enabled:
            raise RuntimeError("Evolution API client is not configured.")
        instance = str(instance_name or "").strip()
        if not instance:
            raise RuntimeError("Evolution instance name is required.")
        number = str(remote_jid or "").split("@", 1)[0].strip()
        if not number:
            raise RuntimeError("Evolution remote JID is required.")
        payload = json.dumps({"number": number, "text": str(text or "")}).encode("utf-8")
        path = self._settings.send_text_path_template.format(instance=instance)
        url = f"{self._settings.base_url.rstrip('/')}/{path.lstrip('/')}"
        req = urlrequest.Request(
            url=url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "apikey": self._settings.api_key,
            },
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=20):
            return None


__all__ = ["EvolutionApiClient", "EvolutionApiSettings"]
