from __future__ import annotations

from langchain_core.language_models import BaseChatModel


def make_llm(provider: str, model: str, settings) -> BaseChatModel:
    """Return a LangChain chat model for the given provider.

    Providers: "openrouter" | "ollama" | "google"
    """
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model, base_url=settings.ollama_base_url)

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model or "gemini-2.5-flash",
            api_key=settings.google_api_key,
            request_timeout=60,
            max_retries=3,
        )

    # default: openrouter
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model,
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key,
    )
