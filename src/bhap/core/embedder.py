"""Local embeddings with a content-hash cache and model-specific query/document prefixes."""
from ..adapters import runtime
from ..errors import ModelError
from ..storage.vectors import pack

# Some embedding models are trained with task prefixes; using them improves retrieval.
PREFIXES = {
    "nomic-embed": ("search_query: ", "search_document: "),
    "e5": ("query: ", "passage: "),
    "snowflake-arctic-embed": ("Represent this sentence for searching relevant passages: ", ""),
    "mxbai-embed": ("Represent this sentence for searching relevant passages: ", ""),
}


def prefixes(model):
    lowered = (model or "").lower()
    for key, value in PREFIXES.items():
        if key in lowered:
            return value
    return "", ""


class Embedder:
    def __init__(self, settings, conn=None):
        self.settings = settings
        self.model = settings["embedding_model"]
        self.conn = conn
        self._runtime = None
        if not self.model:
            raise ModelError("No embedding model configured", hint="bhap setup")

    @property
    def runtime(self):
        if self._runtime is None:
            self._runtime = runtime(self.settings)
        return self._runtime

    def query(self, text):
        query_prefix, _ = prefixes(self.model)
        return self.runtime.embed(self.model, [query_prefix + text])[0]

    def documents(self, items, progress=None):
        """Embed chunk dicts (with embed_hash/embed_text). Returns list of (dims, blob)."""
        _, doc_prefix = prefixes(self.model)
        results = [None] * len(items)
        missing = []
        if self.conn is not None:
            for index, item in enumerate(items):
                row = self.conn.execute("SELECT dims, vector FROM embed_cache WHERE text_hash=? AND model=?",
                                        (item["embed_hash"], self.model)).fetchone()
                if row:
                    results[index] = (row["dims"], row["vector"])
                else:
                    missing.append(index)
        else:
            missing = list(range(len(items)))
        batch = self.settings["embed_batch"]
        done = len(items) - len(missing)
        for start in range(0, len(missing), batch):
            indexes = missing[start:start + batch]
            vectors = self.runtime.embed(self.model, [doc_prefix + items[i]["embed_text"] for i in indexes])
            for index, vector in zip(indexes, vectors):
                results[index] = (len(vector), pack(vector))
            done += len(indexes)
            if progress:
                progress(done, len(items))
        dims = {dims for dims, _ in results}
        if len(dims) > 1:
            raise ModelError(f"Embedding model returned inconsistent dimensions {sorted(dims)}")
        return results, len(items) - len(missing)
