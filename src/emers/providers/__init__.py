"""Built-in physical, software-estimation, and synthetic providers."""

from importlib.metadata import entry_points

from emers.providers.base import (
    FunctionProvider,
    ProviderCapabilities,
    ProviderUnavailableError,
)
from emers.providers.codecarbon import CodeCarbonProvider
from emers.providers.zeus import ZeusProvider


BUILTIN_FUNCTION_PROVIDERS = {"mock", "shelly", "tapo"}


def provider_type(settings):
    """Read the canonical provider type, accepting the legacy key."""
    return settings.get("provider") or settings.get("device_type")


def build_provider(settings, *, timeout, polling_rate, tracker_factory=None):
    kind = provider_type(settings)
    if not kind:
        raise ProviderUnavailableError(
            "Source configuration has no 'provider' (legacy 'device_type' is also accepted)"
        )
    if kind == "codecarbon":
        return CodeCarbonProvider(settings, polling_rate, tracker_factory=tracker_factory)
    if kind == "zeus":
        return ZeusProvider(settings, polling_rate)
    if kind in BUILTIN_FUNCTION_PROVIDERS:
        return FunctionProvider(kind, settings, timeout, polling_rate)

    matches = entry_points(group="emers.providers", name=kind)
    if not matches:
        raise ProviderUnavailableError(
            f"Unknown provider {kind!r}. Install a package exposing the "
            f"'emers.providers' entry point named {kind!r}."
        )
    if len(matches) > 1:
        raise ProviderUnavailableError(
            f"Multiple installed packages expose provider {kind!r}; uninstall the duplicate"
        )
    factory = matches[0].load()
    provider = factory(
        settings=settings,
        timeout=timeout,
        polling_rate=polling_rate,
    )
    required = ("source", "method", "scope", "polling_interval", "start", "read", "stop")
    missing = [name for name in required if not hasattr(provider, name)]
    if missing:
        raise ProviderUnavailableError(
            f"Provider plugin {kind!r} is missing: {', '.join(missing)}"
        )
    return provider


__all__ = [
    "CodeCarbonProvider", "FunctionProvider", "ProviderCapabilities",
    "ProviderUnavailableError", "ZeusProvider", "build_provider", "provider_type",
]
