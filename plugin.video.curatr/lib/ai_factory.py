from .ai_base import AIError
from .anthropic_client import AnthropicClient
from .compatible_client import CompatibleAIClient, OpenRouterClient
from .gemini_client import GeminiClient
from .openai_client import OpenAIClient


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
LEGACY_GEMINI_MODELS = {"gemini-2.5-flash", "models/gemini-2.5-flash"}


def _gemini_model(addon):
    model = (addon.getSetting("gemini_model") or "").strip()
    # 0.4.0/0.4.1 used Gemini 2.5 Flash as the default. Google no
    # longer makes it available to new users, so transparently migrate the
    # old default while leaving any other user-selected model untouched.
    if not model or model in LEGACY_GEMINI_MODELS:
        model = DEFAULT_GEMINI_MODEL
        try:
            addon.setSetting("gemini_model", model)
        except Exception:
            pass
    return model


def create_ai_client(addon, usage_callback=None, user_agent=None):
    provider = (addon.getSetting("ai_provider") or "openai").strip().lower()
    if provider == "gemini":
        return GeminiClient(
            addon.getSetting("gemini_api_key"),
            _gemini_model(addon),
            usage_callback=usage_callback,
            user_agent=user_agent,
        )
    if provider == "anthropic":
        return AnthropicClient(
            addon.getSetting("anthropic_api_key"),
            addon.getSetting("anthropic_model") or "claude-sonnet-5",
            usage_callback=usage_callback,
            user_agent=user_agent,
        )
    if provider == "openrouter":
        return OpenRouterClient(
            addon.getSetting("openrouter_api_key"),
            addon.getSetting("openrouter_model") or "openai/gpt-5-mini",
            usage_callback=usage_callback,
            user_agent=user_agent,
        )
    if provider == "compatible":
        return CompatibleAIClient(
            addon.getSetting("compatible_api_key"),
            addon.getSetting("compatible_model"),
            addon.getSetting("compatible_base_url"),
            usage_callback=usage_callback,
            user_agent=user_agent,
        )
    if provider != "openai":
        raise AIError("Unsupported AI provider: %s" % provider)
    return OpenAIClient(
        addon.getSetting("openai_api_key"),
        addon.getSetting("openai_model") or "gpt-5-mini",
        usage_callback=usage_callback,
        user_agent=user_agent,
    )
