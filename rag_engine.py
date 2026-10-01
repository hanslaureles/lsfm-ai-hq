import os
import re
import sys
import time
import math
import sqlite3
import hashlib
from array import array
from pathlib import Path
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR.parent
MEMORY_DIR = BASE_DIR / "memory"
MEMORY_DIR.mkdir(exist_ok=True)
DB_PATH = MEMORY_DIR / "rag_vault.sqlite"

from llm_client import query_llm

# -----------------------------------------------------------------------------
# 1. DATABASE SETUP
# -----------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rel_path TEXT UNIQUE,
            title TEXT,
            doc_type TEXT,
            content_hash TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id INTEGER,
            chunk_index INTEGER,
            heading TEXT,
            content TEXT,
            embedding_blob BLOB,
            FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);")
        conn.commit()

# -----------------------------------------------------------------------------
# 2. BATCH EMBEDDINGS PROVIDER
# -----------------------------------------------------------------------------
def get_batch_embeddings(texts: list[str], batch_size: int = 20) -> list[list[float]]:
    """
    Generates 3072-dimensional embeddings in batches using Google Gemini models/gemini-embedding-001.
    Includes rate-limit retry logic.
    """
    if not texts:
        return []
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    all_embeddings = []

    if api_key:
        genai.configure(api_key=api_key)
        for i in range(0, len(texts), batch_size):
            batch = [t[:4000] for t in texts[i:i + batch_size]]
            retries = 3
            success = False
            while retries > 0 and not success:
                try:
                    res = genai.embed_content(
                        model="models/gemini-embedding-001",
                        content=batch,
                        task_type="retrieval_document"
                    )
                    embeddings = res.get("embedding", [])
                    all_embeddings.extend(embeddings)
                    success = True
                    time.sleep(0.5) # Gentle pacing to avoid 429 quota limits
                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                        print(f"[RAG] Rate limit encountered. Waiting 10s before retry... ({retries} left)", flush=True)
                        time.sleep(10)
                        retries -= 1
                    else:
                        print(f"[RAG] Batch embedding error ({i}-{i+len(batch)}): {e}", flush=True)
                        break
            if not success:
                for _ in batch:
                    all_embeddings.append([])
    else:
        for _ in texts:
            all_embeddings.append([])

    return all_embeddings

def get_query_embedding(query_text: str) -> list[float] | None:
    """Embeds a single search query with retrieval_query task type."""
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if api_key:
        try:
            genai.configure(api_key=api_key)
            res = genai.embed_content(
                model="models/gemini-embedding-001",
                content=query_text[:2000],
                task_type="retrieval_query"
            )
            return res.get("embedding", [])
        except Exception as e:
            print(f"⚠️ [RAG] Query embedding error: {e}", flush=True)
    return None

# -----------------------------------------------------------------------------
# 3. TEXT CHUNKING ENGINE
# -----------------------------------------------------------------------------
def chunk_markdown_or_code(text: str, max_chars: int = 1500, overlap_chars: int = 200) -> list[dict]:
    """
    Chunks document while respecting markdown headings, code blocks, and section breaks.
    """
    lines = text.splitlines()
    chunks = []
    current_heading = "General"
    current_lines = []
    current_len = 0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("# ", "## ", "### ", "#### ")):
            if current_lines and current_len > 400:
                chunk_text = "\n".join(current_lines).strip()
                if chunk_text:
                    chunks.append({"heading": current_heading, "content": chunk_text})
                current_lines = current_lines[-2:] if len(current_lines) >= 2 else []
                current_len = sum(len(l) for l in current_lines)
            current_heading = stripped.lstrip("#").strip()

        current_lines.append(line)
        current_len += len(line) + 1

        if current_len >= max_chars:
            chunk_text = "\n".join(current_lines).strip()
            if chunk_text:
                chunks.append({"heading": current_heading, "content": chunk_text})
            overlap_lines = []
            accum = 0
            for l in reversed(current_lines):
                overlap_lines.insert(0, l)
                accum += len(l) + 1
                if accum >= overlap_chars:
                    break
            current_lines = overlap_lines
            current_len = accum

    if current_lines:
        chunk_text = "\n".join(current_lines).strip()
        if chunk_text:
            chunks.append({"heading": current_heading, "content": chunk_text})

    return chunks

# -----------------------------------------------------------------------------
# 4. INCREMENTAL BATCH INDEXER
# -----------------------------------------------------------------------------
def get_target_files() -> list[tuple[Path, str, str]]:
    """Discovers all documents and code files to index."""
    targets = []

    # 1. Memory files
    for p in MEMORY_DIR.glob("*.md"):
        targets.append((p, "Memory", p.name))

    # 2. Architecture Docs
    docs_dir = WORKSPACE_DIR / "docs"
    if docs_dir.exists():
        for p in docs_dir.glob("*.md"):
            targets.append((p, "Architecture Blueprint", p.name))

    # 3. Root README
    readme = WORKSPACE_DIR / "README.md"
    if readme.exists():
        targets.append((readme, "System Specification", "README.md"))

    # 4. Portfolio code & case studies
    portfolio_dir = WORKSPACE_DIR / "portfolio-site"
    if portfolio_dir.exists():
        for p in portfolio_dir.glob("*.html"):
            targets.append((p, "Portfolio Case Study / View", p.name))
        for ext in ["*.css", "*.js"]:
            for p in portfolio_dir.glob(ext):
                targets.append((p, "Frontend Code & Design Tokens", p.name))

    return targets

def compute_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def build_index(force: bool = False) -> dict:
    """
    Fast incremental batch indexer. Only re-embeds modified or new files.
    Ensures zero long-held database locks and cleans up orphaned paths.
    """
    init_db()
    
    # Clean up orphaned documents (such as old job-copilot/ paths)
    with get_db() as conn:
        conn.execute("DELETE FROM documents WHERE rel_path LIKE 'job-copilot%'")
        conn.execute("DELETE FROM chunks WHERE doc_id NOT IN (SELECT id FROM documents)")
        conn.commit()

    targets = get_target_files()
    indexed_count = 0
    skipped_count = 0
    total_chunks = 0

    for file_path, doc_type, title in targets:
        try:
            rel_path = str(file_path.relative_to(WORKSPACE_DIR)).replace("\\", "/")
        except Exception:
            rel_path = file_path.name

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"[RAG] Could not read {file_path}: {e}", flush=True)
            continue

        file_hash = compute_hash(content)

        # 1. Quick check if file is unchanged
        with get_db() as conn:
            row = conn.execute("SELECT id, content_hash FROM documents WHERE rel_path = ?", (rel_path,)).fetchone()
            if row and not force:
                if row["content_hash"] == file_hash:
                    skipped_count += 1
                    continue

        # 2. Chunk document
        chunks = chunk_markdown_or_code(content)
        if not chunks:
            continue

        # 3. Batch embed outside the database lock
        chunk_contents = [c["content"] for c in chunks]
        embeddings = get_batch_embeddings(chunk_contents)

        # 4. Atomic database update
        with get_db() as conn:
            row = conn.execute("SELECT id FROM documents WHERE rel_path = ?", (rel_path,)).fetchone()
            if row:
                doc_id = row["id"]
                conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
                conn.execute("UPDATE documents SET content_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (file_hash, doc_id))
            else:
                cur = conn.execute(
                    "INSERT INTO documents (rel_path, title, doc_type, content_hash) VALUES (?, ?, ?, ?)",
                    (rel_path, title, doc_type, file_hash)
                )
                doc_id = cur.lastrowid

            for idx, (c, emb) in enumerate(zip(chunks, embeddings)):
                emb_blob = array("f", emb).tobytes() if emb else b""
                conn.execute(
                    "INSERT INTO chunks (doc_id, chunk_index, heading, content, embedding_blob) VALUES (?, ?, ?, ?, ?)",
                    (doc_id, idx, c["heading"], c["content"], emb_blob)
                )
                total_chunks += 1
            conn.commit()

        indexed_count += 1

    return {
        "indexed_files": indexed_count,
        "skipped_files": skipped_count,
        "total_files": len(targets),
        "new_chunks": total_chunks
    }

# -----------------------------------------------------------------------------
# 5. HYBRID RETRIEVAL (DENSE COSINE + SPARSE BM25)
# -----------------------------------------------------------------------------
def cosine_sim(vec_a: array, vec_b: array) -> float:
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for a, b in zip(vec_a, vec_b):
        dot += a * b
        norm_a += a * a
        norm_b += b * b
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))

def score_sparse_bm25(query_tokens: list[str], text: str) -> float:
    text_lower = text.lower()
    score = 0.0
    for token in query_tokens:
        if token in text_lower:
            count = text_lower.count(token)
            score += 1.0 + math.log(1.0 + count)
    return score

def query_rag(query: str, top_k: int = 4) -> list[dict]:
    """
    Executes a hybrid search (Dense Vector + Sparse Keyword RRF) over the indexed knowledge vault.
    """
    init_db()
    q_emb = get_query_embedding(query)
    q_vec = array("f", q_emb) if q_emb else None
    query_tokens = [t.lower() for t in re.findall(r'\b\w{3,}\b|--\w+|-?\w+\.\w+', query)]

    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.id, c.heading, c.content, c.embedding_blob, d.rel_path, d.title, d.doc_type
            FROM chunks c
            JOIN documents d ON c.doc_id = d.id
        """).fetchall()

        if not rows:
            return []

        # 1. Score dense and sparse
        dense_scores = []
        sparse_scores = []

        for r in rows:
            chunk_id = r["id"]
            blob = r["embedding_blob"]
            
            # Dense
            d_score = 0.0
            if q_vec and blob:
                chunk_vec = array("f")
                chunk_vec.frombytes(blob)
                d_score = cosine_sim(q_vec, chunk_vec)
            dense_scores.append((chunk_id, d_score, r))

            # Sparse
            s_score = score_sparse_bm25(query_tokens, r["content"] + " " + r["heading"] + " " + r["rel_path"])
            sparse_scores.append((chunk_id, s_score, r))

        # Sort ranks
        dense_scores.sort(key=lambda x: x[1], reverse=True)
        sparse_scores.sort(key=lambda x: x[1], reverse=True)

        dense_rank_map = {item[0]: rank for rank, item in enumerate(dense_scores)}
        sparse_rank_map = {item[0]: rank for rank, item in enumerate(sparse_scores)}

        # 2. Reciprocal Rank Fusion (RRF)
        rrf_results = []
        for chunk_id, d_score, r in dense_scores:
            d_rank = dense_rank_map[chunk_id]
            s_rank = sparse_rank_map[chunk_id]
            rrf_score = (1.0 / (60.0 + d_rank)) + (1.0 / (60.0 + s_rank))

            rrf_results.append({
                "chunk_id": chunk_id,
                "score": round(rrf_score, 4),
                "dense_score": round(d_score, 3),
                "heading": r["heading"],
                "content": r["content"],
                "rel_path": r["rel_path"],
                "title": r["title"],
                "doc_type": r["doc_type"]
            })

        rrf_results.sort(key=lambda x: x["score"], reverse=True)
        return rrf_results[:top_k]

# -----------------------------------------------------------------------------
# 6. GROUNDED Q&A REASONER
# -----------------------------------------------------------------------------
def ask_knowledge_base(question: str) -> dict:
    """
    Retrieves relevant chunks from the RAG vault and produces a grounded, citation-backed answer.
    """
    chunks = query_rag(question, top_k=4)

    if not chunks:
        return {
            "answer": "⚠️ My knowledge vault is currently empty or unindexed. Run `!reindex` first to build the index!",
            "citations": []
        }

    context_blocks = []
    citations = []
    for i, c in enumerate(chunks, 1):
        context_blocks.append(f"--- [Source #{i}: {c['rel_path']} // {c['heading']}] ---\n{c['content']}")
        citations.append(f"`{c['rel_path']}` ({c['heading']})")

    context_str = "\n\n".join(context_blocks)

    prompt = f"""
You are "Kazuha", Lead Frontend Architect and Knowledge Officer for Hans Aaron Laureles.
Your job is to answer Hans's question using ONLY the provided verified context from his codebase, design system, and career documents.

### RETRIEVED KNOWLEDGE CONTEXT:
{context_str}

### QUESTION FROM HANS:
"{question}"

---

### INSTRUCTIONS:
- Deliver a direct, elegant, and technically precise answer.
- Reference the specific file paths and sections where the facts or code come from.
- If relevant, provide exact code snippets, CSS tokens, or architectural points.
- If the answer cannot be determined from the context, state honestly that the knowledge is not in the indexed docs.
- Tone: Disciplined, articulate, executive frontend architect.
"""
    answer_text = query_llm(prompt, temperature=0.2)
    return {
        "answer": answer_text,
        "citations": list(dict.fromkeys(citations))
    }

def get_rag_stats() -> dict:
    """Returns database size, document counts, and chunk statistics."""
    init_db()
    with get_db() as conn:
        doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        db_size_kb = round(DB_PATH.stat().st_size / 1024, 1) if DB_PATH.exists() else 0.0

        sample_docs = conn.execute("SELECT rel_path, doc_type FROM documents LIMIT 5").fetchall()
        docs_summary = [f"{d['rel_path']} ({d['doc_type']})" for d in sample_docs]

    return {
        "total_documents": doc_count,
        "total_chunks": chunk_count,
        "db_size_kb": db_size_kb,
        "embedding_model": "Google Gemini (gemini-embedding-001, 3072 dims)",
        "sample_docs": docs_summary
    }

if __name__ == "__main__":
    print("Testing Kazuha Batch RAG Engine...", flush=True)
    res = build_index()
    print("Build Index Result:", res)
    stats = get_rag_stats()
    print("RAG Stats:", stats)
