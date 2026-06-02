"""
Build the RAG knowledge base from markdown files.
No embedding service required — uses keyword-based retrieval.
Run: python scripts/build_knowledge_base.py
"""
import os
import sys
import re
from typing import List, Dict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from rag.retriever import rag_retriever


def chunk_markdown(text: str, source: str, category: str) -> List[dict]:
    """Split markdown by ## headings, then into chunks."""
    sections = re.split(r"\n(?=## )", text)
    chunks = []

    for section in sections:
        heading_match = re.match(r"## (.+)", section)
        heading = heading_match.group(1).strip() if heading_match else "Introduction"

        paragraphs = section.split("\n\n")
        current_chunk = ""
        current_len = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            para_len = len(para)
            if current_len + para_len > 1500 and current_chunk:
                chunks.append({
                    "text": current_chunk.strip(),
                    "source": source,
                    "category": category,
                    "heading": heading,
                })
                overlap = current_chunk[-150:] if len(current_chunk) > 150 else ""
                current_chunk = overlap + "\n\n" + para
                current_len = len(current_chunk)
            else:
                current_chunk = (current_chunk + "\n\n" + para).strip() if current_chunk else para
                current_len = len(current_chunk)

        if current_chunk.strip():
            chunks.append({
                "text": current_chunk.strip(),
                "source": source,
                "category": category,
                "heading": heading,
            })

    return chunks


def build():
    knowledge_dir = os.path.join(os.path.dirname(__file__), "..", "backend", "rag", "knowledge")
    categories = ["methodologies", "benchmarks", "case_studies", "frameworks"]

    all_chunks = []

    for category in categories:
        cat_dir = os.path.join(knowledge_dir, category)
        if not os.path.exists(cat_dir):
            print(f"  (skipping {category}: not found)")
            continue

        for fname in os.listdir(cat_dir):
            if not fname.endswith(".md"):
                continue

            path = os.path.join(cat_dir, fname)
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()

            chunks = chunk_markdown(text, source=fname, category=category)
            all_chunks.extend(chunks)
            print(f"  {category}/{fname}: {len(chunks)} chunks")

    docs = [c["text"] for c in all_chunks]
    metas = [{"source": c["source"], "category": c["category"], "heading": c["heading"]} for c in all_chunks]

    rag_retriever.add(docs, metas)
    print(f"\nKnowledge base built: {len(docs)} chunks indexed from {len(set(m['source'] for m in metas))} documents.")


if __name__ == "__main__":
    build()
