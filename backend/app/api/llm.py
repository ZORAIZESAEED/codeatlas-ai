from collections.abc import Callable
from dataclasses import asdict

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool

from app.api.projects import get_settings_dep
from app.core.config import Settings
from app.schemas import LLMCapabilitiesOut, LLMStatusOut
from app.services.llm.base import LLMError, LLMProvider, ProviderStatus
from app.services.llm.registry import create_provider, provider_status

router = APIRouter(prefix="/api/llm", tags=["llm"])

ProviderFactory = Callable[[Settings], LLMProvider]


def get_provider_factory(request: Request) -> ProviderFactory:
    """Tests replace ``app.state.llm_factory`` to inject a fake provider."""
    return getattr(request.app.state, "llm_factory", None) or create_provider


@router.get("/status", response_model=LLMStatusOut)
async def llm_status(
    settings: Settings = Depends(get_settings_dep),
    factory: ProviderFactory = Depends(get_provider_factory),
) -> LLMStatusOut:
    """Contacts the provider (e.g. Ollama) to report whether the configured model is usable."""

    def check():
        try:
            provider = factory(settings)
        except LLMError as exc:
            return ProviderStatus(provider=settings.llm_provider, model=settings.llm_model,
                                  available=False, message=exc.message)
        try:
            return provider_status(settings, provider)
        finally:
            provider.close()

    status = await run_in_threadpool(check)
    return LLMStatusOut(
        provider=status.provider,
        model=status.model,
        available=status.available,
        message=status.message,
        capabilities=(LLMCapabilitiesOut(**asdict(status.capabilities))
                      if status.available else None),
        context_length=status.context_length,
        configured_context_tokens=settings.llm_max_context_tokens,
        installed_models=status.installed_models,
        sends_code_off_machine=status.sends_code_off_machine,
    )
