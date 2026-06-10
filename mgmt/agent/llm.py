from __future__ import annotations

from langchain_core.language_models import BaseChatModel


def make_llm(provider: str, model: str, settings) -> BaseChatModel:
    """Return a LangChain chat model for the given provider.

    Providers: "openrouter" | "ollama" | "google" | "vertex"

    "google"  → Gemini Developer API (generativelanguage.googleapis.com),
                billed against AI Studio prepay credits, auth via API key.
    "vertex"  → Vertex AI (*-aiplatform.googleapis.com), billed against the
                GCP project, auth via ADC / service-account
                (GOOGLE_APPLICATION_CREDENTIALS).
    """
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model, base_url=settings.ollama_base_url)

    if provider == "vertex":
        from langchain_google_vertexai import ChatVertexAI
        return ChatVertexAI(
            model=model or "gemini-2.5-flash",
            project=settings.gcp_project,
            location=settings.gcp_location,
            max_retries=3,
        )

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
