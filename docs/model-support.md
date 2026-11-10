# Models

Bhap needs two local models: a **chat/generation** model and an **embedding** model. They are different assets. Changing the embedding model invalidates existing vectors; Bhap marks affected documents as stale and `bhap docs reindex --stale` rebuilds them (cached vectors are reused where possible).

## Suggestions by hardware profile

`bhap doctor` and `bhap setup` detect your profile from total RAM. These are starting points to benchmark on your own machine, not guarantees.

| Profile | Hardware | Chat model (Ollama name) | Embedding |
|---|---|---|---|
| lite | < 12 GB RAM, CPU | `qwen2.5:1.5b`, `gemma3:1b`, `llama3.2:1b` | `paraphrase-multilingual`, `granite-embedding:278m` |
| standard | 12–28 GB RAM, iGPU/CPU | `qwen2.5:7b`, `gemma3:4b`, `llama3.1:8b` | `bge-m3` |
| power | 28 GB+ RAM or a GPU with enough VRAM | `qwen2.5:14b`, `gemma3:12b` | `bge-m3` |

For Indonesian or mixed Indonesian/English material, prefer a **multilingual** embedding model (`bge-m3`, `paraphrase-multilingual`). English-centric models such as `nomic-embed-text` work, but retrieve Indonesian text less accurately. Bhap applies the task prefixes some models expect (`nomic-embed`, `e5`, `mxbai`, `snowflake-arctic-embed`).

## Low-resource mode

```sh
bhap config set low_resource true
```

This caps evidence chunks (≤ 4), the context window (≤ 4096) and the embedding batch size (≤ 4). Combine it with a 1–3B quantized chat model. The `num_ctx` setting controls how much evidence fits into a prompt; Bhap budgets evidence to `num_ctx − 2200` tokens.

## Useful settings

| Setting | Default | Notes |
|---|---|---|
| `mode` | `strict` | `general` adds labelled general knowledge; `brainstorm` labels inferences |
| `top_k` | 6 | evidence chunks per answer |
| `max_chunks_per_doc` | 4 | keeps answers from being dominated by one file |
| `min_similarity` | 0 (off) | abstain early when the best vector score is lower; tune it per embedding model |
| `temperature` | 0.1 | |
| `chunk_words` / `chunk_overlap` | 220 / 40 | changes apply on reindex |

## Reranking

The MVP uses hybrid retrieval: SQLite FTS5/BM25 plus vector cosine, combined with reciprocal-rank fusion, a rare-term anchor boost and a per-document diversity cap. A dedicated local cross-encoder reranker is not bundled yet, because Ollama has no standard rerank API.
