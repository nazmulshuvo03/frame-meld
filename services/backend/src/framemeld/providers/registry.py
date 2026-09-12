from framemeld.config import settings
from framemeld.providers.base import LLMProvider
from framemeld.providers.stub import StubLLMProvider


def get_llm_provider() -> LLMProvider:
    """The single place a provider name becomes an adapter.

    Model names live in configuration, never in code (spec section 3.3), so
    swapping a provider is an env change and a restart. Task 10 adds the
    "openrouter" branch; until then an unknown name raises rather than
    quietly falling back to the stub, which would let a misconfigured
    production instance serve made-up scripts.
    """
    if settings.llm_provider == "stub":
        return StubLLMProvider()
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
