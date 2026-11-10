"""Shared HTTP plumbing for loopback model runtimes (stdlib only, proxies disabled)."""
import json
import urllib.error
import urllib.request

from ..config import LOOPBACK_URL
from ..errors import ModelError, NetworkBlockedError

# Never route loopback traffic through an HTTP(S)_PROXY from the environment.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Runtime:
    name = "base"
    supports_pull = False

    def __init__(self, base_url):
        if not LOOPBACK_URL.match(base_url):
            raise ModelError(f"Refusing non-loopback runtime URL: {base_url}")
        self.base = base_url.rstrip("/")

    # -- HTTP -------------------------------------------------------------------
    def _open(self, path, payload=None, timeout=60):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=body,
                                         headers={"Content-Type": "application/json"})
        try:
            return OPENER.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace")).get("error", "")
            except (ValueError, AttributeError, OSError):
                pass
            raise ModelError(f"{self.name} HTTP {exc.code}: {detail or exc.reason}") from exc
        except NetworkBlockedError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ModelError(f"{self.name} is not reachable at {self.base} ({reason})",
                             hint="Start the local runtime (e.g. `ollama serve`) and run `bhap doctor`") from exc

    def _json(self, path, payload=None, timeout=60):
        with self._open(path, payload, timeout) as response:
            try:
                return json.load(response)
            except ValueError as exc:
                raise ModelError(f"{self.name} returned invalid JSON for {path}") from exc

    # -- interface ----------------------------------------------------------------
    def version(self):
        raise NotImplementedError

    def models(self):
        """Return list of dicts: {name, size, family, embedding(bool|None)}."""
        raise NotImplementedError

    def context_length(self, model):
        return None

    def embed(self, model, texts):
        raise NotImplementedError

    def chat(self, model, messages, *, options=None, json_mode=False):
        """Yield text deltas. Closing the generator cancels the request."""
        raise NotImplementedError

    def complete(self, model, messages, *, options=None, json_mode=False):
        return "".join(self.chat(model, messages, options=options, json_mode=json_mode))

    def pull(self, model):
        raise ModelError(f"{self.name} cannot download models; place the model file manually")

    def model_names(self):
        return [item["name"] for item in self.models()]

    def has_model(self, model):
        if not model:
            return False
        names = self.model_names()
        return model in names or (":" not in model and f"{model}:latest" in names)
