"""Runtime adapters. Every adapter speaks only to a loopback endpoint."""
from ..config import load
from ..errors import UsageError


def runtime(settings=None):
    settings = settings or load()
    name = settings["runtime"]
    if name == "ollama":
        from .llm_ollama import OllamaRuntime
        return OllamaRuntime(settings["ollama_url"])
    if name == "llamacpp":
        from .llm_llamacpp import LlamaCppRuntime
        return LlamaCppRuntime(settings["llamacpp_url"])
    raise UsageError(f"Unknown runtime: {name}")
