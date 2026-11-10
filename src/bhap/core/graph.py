"""Knowledge Constellation: documents → sections → shared concepts, exported as Mermaid/DOT/JSON.

Concepts are distinctive terms (TF-IDF over the workspace) that occur in at least two
documents, so the graph shows how material connects without any model call.
"""
import json
import math
import re
from collections import Counter, defaultdict

from .retrieval import terms

MAX_SECTIONS = 8


def build(conn, wid, max_concepts=25):
    docs = conn.execute("SELECT id, filename FROM documents WHERE workspace_id=? AND status='indexed' ORDER BY id",
                        (wid,)).fetchall()
    doc_terms, sections = {}, defaultdict(list)
    df = Counter()
    for doc in docs:
        counter = Counter()
        for row in conn.execute("SELECT section_path, text FROM chunks WHERE document_id=? ORDER BY ordinal", (doc["id"],)):
            counter.update(t for t in terms(row["text"]) if len(t) >= 4 and not t.isdigit())
            top = (row["section_path"] or "").split(" > ")[0].strip()
            if top and top not in sections[doc["id"]] and len(sections[doc["id"]]) < MAX_SECTIONS:
                sections[doc["id"]].append(top)
        doc_terms[doc["id"]] = counter
        df.update(counter.keys())
    n = max(len(docs), 1)
    scores = Counter()
    for counter in doc_terms.values():
        total = sum(counter.values()) or 1
        for term, count in counter.items():
            if df[term] >= 2 and sum(1 for c in doc_terms.values() if c[term] >= 2) >= 2:
                scores[term] += (count / total) * math.log(1 + n / df[term])
    concepts = [term for term, _ in scores.most_common(max_concepts)]
    edges = [(doc_id, term, doc_terms[doc_id][term]) for doc_id in doc_terms for term in concepts
             if doc_terms[doc_id][term]]
    return {"documents": [{"id": d["id"], "file": d["filename"], "sections": sections[d["id"]]} for d in docs],
            "concepts": concepts, "edges": [{"document": d, "concept": c, "weight": w} for d, c, w in edges]}


def _safe(text):
    return re.sub(r'["\[\]{}<>|]', " ", text)[:60]


def mermaid(graph):
    lines = ["graph LR"]
    for doc in graph["documents"]:
        lines.append(f'  D{doc["id"]}["📄 {_safe(doc["file"])}"]')
        for index, section in enumerate(doc["sections"]):
            lines.append(f'  D{doc["id"]} --> D{doc["id"]}S{index}("{_safe(section)}")')
    for index, concept in enumerate(graph["concepts"]):
        lines.append(f'  C{index}(("{_safe(concept)}"))')
    position = {c: i for i, c in enumerate(graph["concepts"])}
    for edge in graph["edges"]:
        lines.append(f'  D{edge["document"]} -.- C{position[edge["concept"]]}')
    return "\n".join(lines) + "\n"


def dot(graph):
    lines = ["graph bhap {", "  rankdir=LR; node [fontname=Helvetica];"]
    for doc in graph["documents"]:
        lines.append(f'  d{doc["id"]} [label="{_safe(doc["file"])}", shape=box, style=filled, fillcolor="#dbeafe"];')
        for index, section in enumerate(doc["sections"]):
            lines.append(f'  d{doc["id"]}s{index} [label="{_safe(section)}", shape=note];')
            lines.append(f'  d{doc["id"]} -- d{doc["id"]}s{index};')
    for index, concept in enumerate(graph["concepts"]):
        lines.append(f'  c{index} [label="{_safe(concept)}", shape=ellipse, style=filled, fillcolor="#fef3c7"];')
    position = {c: i for i, c in enumerate(graph["concepts"])}
    for edge in graph["edges"]:
        lines.append(f'  d{edge["document"]} -- c{position[edge["concept"]]} [penwidth={min(4, 1 + edge["weight"] / 10):.1f}, style=dashed];')
    return "\n".join(lines + ["}"]) + "\n"


def render(graph, fmt):
    if fmt == "json":
        return json.dumps(graph, ensure_ascii=False, indent=2)
    if fmt == "dot":
        return dot(graph)
    if fmt == "mermaid":
        return mermaid(graph)
    lines = []  # plain text tree for the terminal
    position = defaultdict(list)
    for edge in graph["edges"]:
        position[edge["document"]].append(edge["concept"])
    for doc in graph["documents"]:
        lines.append(f"📄 {doc['file']}  (#{doc['id']})")
        for section in doc["sections"]:
            lines.append(f"   ├─ {section}")
        if position[doc["id"]]:
            lines.append(f"   └─ ⟷ {', '.join(position[doc['id']][:10])}")
    shared = defaultdict(list)
    for edge in graph["edges"]:
        shared[edge["concept"]].append(edge["document"])
    if shared:
        lines.append("")
        names = {d["id"]: d["file"] for d in graph["documents"]}
        for concept in graph["concepts"]:
            if len(shared[concept]) >= 2:
                lines.append(f"◇ {concept}: " + " · ".join(names[i] for i in shared[concept]))
    return "\n".join(lines) + "\n"
