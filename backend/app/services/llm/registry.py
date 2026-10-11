"""Create the configured chat provider. Construction is cheap and makes no network call."""
from app.core.config import Settings
from app.services.llm.base import LLMError, LLMProvider, ProviderStatus
from app.services.llm.fake import FakeProvider
from app.services.llm.ollama import OllamaProvider

SUPPORTED = ("ollama", "fake")
# Hosted adapters need their official SDKs, which are not dependencies yet (see decisions.md).
NOT_ENABLED = {
    "anthropic": "requires the official `anthropic` SDK, which has not been added",
    "openai": "requires the official `openai` SDK, which has not been added",
}


def create_provider(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider.strip().lower()
    if provider in NOT_ENABLED:
        raise LLMError("not_configured",
                       f"LLM_PROVIDER={provider} is not enabled in this build: "
                       f"{NOT_ENABLED[provider]}. Use LLM_PROVIDER=ollama.")
    if provider not in SUPPORTED:
        raise LLMError("not_configured", f"Unknown LLM_PROVIDER '{settings.llm_provider}'. "
                                         f"Supported: {', '.join(SUPPORTED)}.")
    if provider == "fake":
        return FakeProvider(model=settings.llm_model or "fake-chat")
    if not settings.llm_model:
        raise LLMError("not_configured",
                       "No chat model is configured. Set LLM_MODEL to an installed Ollama model "
                       "(list them with `ollama list`).")
    return OllamaProvider(
        settings.ollama_base_url, settings.llm_model,
        context_tokens=settings.llm_max_context_tokens,
        timeout_seconds=settings.llm_request_timeout_seconds,
    )


def provider_status(settings: Settings, provider: LLMProvider | None = None) -> ProviderStatus:
    """Availability of the configured provider. Never raises: failures become a status."""
    owned = provider is None
    try:
        provider = provider or create_provider(settings)
    except LLMError as exc:
        return ProviderStatus(provider=settings.llm_provider, model=settings.llm_model,
                              available=False, message=exc.message)
    try:
        return provider.status()
    except LLMError as exc:
        status = ProviderStatus(provider=provider.name, model=provider.model, available=False,
                                message=exc.message)
        return status
    finally:
        if owned:
            provider.close()
