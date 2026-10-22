"""Local OpenAI-compatible adapter for the grounded Flash-Brain voice layer."""
from __future__ import annotations

import logging

from jarvis.brain.ack_brain.config import LocalOpenAIAckProviderConfig
from jarvis.core import config as cfg
from jarvis.plugins.brain.local_openai import normalize_server_root

log = logging.getLogger(__name__)


class LocalOpenAIFlashAck:
    """Make a bounded, one-sentence call to the configured local brain server."""

    def __init__(self, config: LocalOpenAIAckProviderConfig) -> None:
        self._config = config

    async def run(
        self,
        utterance: str,
        language: str,
        *,
        persona_prompt: str,
    ) -> str | None:
        try:
            local_cfg = cfg.load_config().brain.providers.get("local-openai")
            endpoint = (self._config.endpoint or "").strip()
            if not endpoint:
                provider_endpoint = cfg.resolve_provider_endpoint(
                    "local-openai", vendor_default_base_url=None
                )
                endpoint = provider_endpoint.base_url or ""
            root = normalize_server_root(endpoint) if endpoint else ""
            model = (self._config.model or "").strip() or str(
                getattr(local_cfg, "model", "") or ""
            ).strip()
            if not root or not model:
                log.info("Local OpenAI Flash-Brain ack skipped: model or endpoint is unset")
                return None

            provider_endpoint = cfg.resolve_provider_endpoint(
                "local-openai", vendor_default_base_url=None
            )
            credential = getattr(provider_endpoint, "credential", None) or cfg.get_secret(
                "local_openai_api_key", "LOCAL_OPENAI_API_KEY"
            )
            headers = {"Authorization": f"Bearer {credential}"} if credential else {}

            import httpx

            timeout = httpx.Timeout(connect=0.8, read=1.2, write=0.8, pool=0.8)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{root}/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": persona_prompt},
                            {"role": "user", "content": utterance},
                        ],
                        "temperature": self._config.temperature,
                        "max_tokens": self._config.max_output_tokens,
                        "stream": False,
                    },
                )
                response.raise_for_status()
                choices = response.json().get("choices") or []
                text = str((choices[0].get("message") or {}).get("content") or "").strip()
                return text or None
        except Exception as exc:  # noqa: BLE001 — voice acknowledgements fail silent
            log.warning("Local OpenAI Flash-Brain ack failed: %s", exc)
            return None
