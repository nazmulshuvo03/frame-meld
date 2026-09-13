from framemeld.config import settings
from framemeld.providers.base import LLMProvider
from framemeld.providers.stub import StubLLMProvider


def get_llm_provider() -> LLMProvider:
    """The single place a provider name becomes an adapter.

    Model names live in configuration, never in code (spec section 3.3), so
    swapping a provider is an env change and a restart. The "openrouter"
    branch below is the real adapter; an unknown name still raises rather
    than quietly falling back to the stub, which would let a misconfigured
    production instance serve made-up scripts.
    """
    if settings.llm_provider == "stub":
        return StubLLMProvider()
    if settings.llm_provider == "openrouter":
        from framemeld.providers.openrouter import OpenRouterLLMProvider

        # Imported lazily so a stub-configured install never pays for the
        # adapter's own imports at module load -- this branch is the
        # natural place to keep that cost, since it is the only caller.
        return OpenRouterLLMProvider()
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
