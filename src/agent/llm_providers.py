"""LLM provider registry and circuit breaker mechanism for multi-provider fallback."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import os
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


@dataclass
class LLMProviderConfig:
    name: str  # e.g., "groq", "nvidia_nim"
    display_name: str  # e.g., "Groq", "NVIDIA NIM"
    base_url: str
    api_key_env: str
    model_env: str
    default_model: str

    def get_api_key(self) -> Optional[str]:
        val = os.getenv(self.api_key_env)
        return val.strip() if val and val.strip() else None

    def get_model(self) -> str:
        val = os.getenv(self.model_env)
        return val.strip() if val and val.strip() else self.default_model


DEFAULT_PROVIDERS: List[LLMProviderConfig] = [
    LLMProviderConfig(
        name="groq",
        display_name="Groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        model_env="GROQ_MODEL",
        default_model="openai/gpt-oss-120b",
    ),
    LLMProviderConfig(
        name="nvidia_nim",
        display_name="NVIDIA NIM",
        base_url="https://integrate.api.nvidia.com/v1",
        api_key_env="NVIDIA_API_KEY",
        model_env="NVIDIA_MODEL",
        default_model="nvidia/nemotron-3-nano-30b-a3b",
    ),
]


class CircuitBreaker:
    """Short-lived circuit breaker for LLM providers.

    If a provider fails 3 consecutive times, skip it for `cooldown_minutes` (default 10).
    """

    def __init__(self, failure_threshold: int = 3, cooldown_minutes: float = 10.0):
        self.failure_threshold = failure_threshold
        self.cooldown_minutes = cooldown_minutes
        self._failures: Dict[str, int] = {}
        self._tripped_until: Dict[str, datetime] = {}

    def is_available(self, provider_name: str) -> bool:
        tripped_at = self._tripped_until.get(provider_name)
        if tripped_at:
            if datetime.now() < tripped_at:
                logger.warning(
                    f"[CircuitBreaker] Provider '{provider_name}' is cooling down until {tripped_at.strftime('%H:%M:%S')} — skipping."
                )
                return False
            else:
                # Cooldown expired, reset
                self.record_success(provider_name)
        return True

    def record_success(self, provider_name: str) -> None:
        self._failures[provider_name] = 0
        self._tripped_until.pop(provider_name, None)

    def record_failure(self, provider_name: str) -> None:
        count = self._failures.get(provider_name, 0) + 1
        self._failures[provider_name] = count
        if count >= self.failure_threshold:
            tripped_until = datetime.now() + timedelta(minutes=self.cooldown_minutes)
            self._tripped_until[provider_name] = tripped_until
            logger.error(
                f"[CircuitBreaker] Provider '{provider_name}' failed {count} times consecutively. "
                f"Tripping circuit breaker for {self.cooldown_minutes} minutes (until {tripped_until.strftime('%H:%M:%S')})."
            )
        else:
            logger.warning(
                f"[CircuitBreaker] Provider '{provider_name}' failure count: {count}/{self.failure_threshold}."
            )

    def reset(self) -> None:
        self._failures.clear()
        self._tripped_until.clear()


# Global circuit breaker singleton
circuit_breaker = CircuitBreaker()


def get_active_providers(
    providers: Optional[List[LLMProviderConfig]] = None,
) -> List[Tuple[LLMProviderConfig, str, str]]:
    """Return configured and available LLM providers as a list of (config, api_key, model) tuples."""
    target_list = providers or DEFAULT_PROVIDERS
    active: List[Tuple[LLMProviderConfig, str, str]] = []




    for cfg in target_list:
        api_key = cfg.get_api_key()
        if not api_key:
            logger.info(f"[LLM] Skipping provider '{cfg.display_name}': API key ({cfg.api_key_env}) not configured.")
            continue
        if not circuit_breaker.is_available(cfg.name):
            continue
        model = cfg.get_model()
        active.append((cfg, api_key, model))

    return active
