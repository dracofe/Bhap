"""llama.cpp `llama-server` adapter (OpenAI-compatible API on loopback).

Start one server for chat and, if you want embeddings from llama.cpp too, run it with
`--embeddings`. Model names are whatever the server reports in /v1/models.
"""
import json

from ..errors import ModelError
from .base import Runtime


class LlamaCppRuntime(Runtime):
    name = "llama.cpp"

    def version(self):
        health = self._json("/health", timeout=5)
        return health.get("status", "ok")

    def models(self):
        data = self._json("/v1/models", timeout=10).get("data", [])
        return [{"name": item.get("id", ""), "size": 0, "family": "gguf", "parameters": "",
                 "quantization": "", "embedding": None} for item in data]

    def has_model(self, model):
        # llama-server serves exactly one model; accept any configured name if it is up.
        names = self.model_names()
        return bool(model) and (model in names or len(names) == 1)

    def embed(self, model, texts):
        result = self._json("/v1/embeddings", {"model": model, "input": list(texts)}, timeout=600)
        vectors = [item.get("embedding") for item in sorted(result.get("data", []), key=lambda d: d.get("index", 0))]
        if len(vectors) != len(texts) or not all(vectors):
            raise ModelError("llama.cpp returned incomplete embeddings (start llama-server with --embeddings)")
        return vectors

    def chat(self, model, messages, *, options=None, json_mode=False):
        options = options or {}
        payload = {"model": model, "messages": messages, "stream": True,
                   "temperature": options.get("temperature", 0.1)}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = self._open("/v1/chat/completions", payload, timeout=900)
        try:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                for choice in event.get("choices", []):
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        yield delta
        finally:
            response.close()
