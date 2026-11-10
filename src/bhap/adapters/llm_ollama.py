"""Ollama adapter (http://127.0.0.1:11434 by default)."""
import json

from ..errors import ModelError
from .base import Runtime

EMBEDDING_HINTS = ("embed", "bge", "e5", "minilm", "mpnet", "paraphrase", "gte", "arctic", "granite-embedding")


class OllamaRuntime(Runtime):
    name = "Ollama"
    supports_pull = True

    def version(self):
        return self._json("/api/version", timeout=5).get("version", "?")

    def models(self):
        items = self._json("/api/tags", timeout=10).get("models", [])
        result = []
        for item in items:
            name = item.get("name") or item.get("model", "")
            details = item.get("details") or {}
            family = (details.get("family") or "").lower()
            lowered = name.lower()
            embedding = any(hint in lowered for hint in EMBEDDING_HINTS) or family in {"bert", "nomic-bert", "xlm-roberta"}
            result.append({"name": name, "size": item.get("size", 0), "family": family,
                           "parameters": details.get("parameter_size", ""),
                           "quantization": details.get("quantization_level", ""), "embedding": embedding})
        return result

    def context_length(self, model):
        try:
            info = self._json("/api/show", {"model": model}, timeout=15).get("model_info", {})
        except ModelError:
            return None
        for key, value in info.items():
            if key.endswith(".context_length") and isinstance(value, int):
                return value
        return None

    def embed(self, model, texts):
        if not model:
            raise ModelError("No embedding model selected", hint="bhap setup")
        result = self._json("/api/embed", {"model": model, "input": list(texts), "truncate": True}, timeout=600)
        vectors = result.get("embeddings") or []
        if len(vectors) != len(texts) or not all(vectors):
            raise ModelError(f"Ollama returned {len(vectors)} embeddings for {len(texts)} inputs")
        return vectors

    def chat(self, model, messages, *, options=None, json_mode=False):
        if not model:
            raise ModelError("No chat model selected", hint="bhap setup")
        payload = {"model": model, "messages": messages, "stream": True, "options": options or {}}
        if json_mode:
            payload["format"] = "json"
        response = self._open("/api/chat", payload, timeout=900)
        try:
            for raw in response:
                if not raw.strip():
                    continue
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if event.get("error"):
                    raise ModelError(f"Ollama: {event['error']}")
                delta = (event.get("message") or {}).get("content", "")
                if delta:
                    yield delta
                if event.get("done"):
                    break
        finally:
            response.close()

    def pull(self, model):
        """Ask the local Ollama daemon to download a model. Yields progress dicts."""
        response = self._open("/api/pull", {"model": model, "stream": True}, timeout=3600)
        try:
            for raw in response:
                if not raw.strip():
                    continue
                event = json.loads(raw)
                if event.get("error"):
                    raise ModelError(f"Ollama pull: {event['error']}")
                yield event
        finally:
            response.close()
