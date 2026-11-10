"""Float32 vector blobs with numpy acceleration when installed (pure Python otherwise)."""
import math
from array import array

try:  # optional extra: pip install "bhap-ai[fast]"
    import numpy as np
except ImportError:  # pragma: no cover - exercised on minimal installs
    np = None


def normalize(vector):
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def pack(vector):
    return array("f", normalize(vector)).tobytes()


def unpack(blob):
    values = array("f")
    values.frombytes(blob)
    return values


class Matrix:
    """In-memory matrix of normalized vectors for a set of chunk ids."""

    def __init__(self, ids, blobs, dims):
        self.ids = list(ids)
        self.dims = dims
        if np is not None and self.ids:
            self.data = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(len(self.ids), dims)
        else:
            self.data = [unpack(blob) for blob in blobs]

    def top(self, query, limit):
        """Return [(chunk_id, cosine)] best-first."""
        if not self.ids:
            return []
        q = normalize(query)
        if len(q) != self.dims:
            return []
        if np is not None:
            scores = self.data @ np.asarray(q, dtype=np.float32)
            limit = min(limit, len(self.ids))
            index = np.argpartition(-scores, limit - 1)[:limit]
            index = index[np.argsort(-scores[index])]
            return [(self.ids[i], float(scores[i])) for i in index]
        scored = [(sum(a * b for a, b in zip(q, row)), chunk_id) for chunk_id, row in zip(self.ids, self.data)]
        scored.sort(reverse=True)
        return [(chunk_id, score) for score, chunk_id in scored[:limit]]
