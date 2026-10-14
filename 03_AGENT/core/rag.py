"""
Agent IA USB Local RAG System
Reads TXT, MD, JSON, CSV, source code and Word (.docx). Incremental indexing.
Search uses BM25 over accent-insensitive tokens, so it needs no embedding model
and works offline on any PC.
"""
import json, hashlib, math, re, unicodedata, zipfile
from pathlib import Path
from datetime import datetime

TEXT_EXT = {".txt", ".md", ".json", ".csv", ".py", ".js", ".ts", ".html", ".css", ".log", ".ini", ".yaml", ".yml", ".xml"}
DOC_EXT = TEXT_EXT | {".docx"}
STOPWORDS = set("""de la el en y a los las un una unos unas que es por con para del al se lo su sus como mas pero o si no
the of and to in is are for on with that this it as be at by from an or was were""".split())


def tokens(text):
    """Lowercase, accent-free word tokens without stopwords."""
    text = unicodedata.normalize("NFD", (text or "").lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return [w for w in re.findall(r"[a-z0-9_]{2,}", text) if w not in STOPWORDS]


def read_document(fp):
    """Text of a supported file, or None."""
    fp = Path(fp)
    try:
        if fp.suffix.lower() == ".docx":
            with zipfile.ZipFile(fp) as z:
                xml = z.read("word/document.xml").decode("utf-8", errors="replace")
            xml = re.sub(r"</w:p>", "\n", xml)
            return re.sub(r"<[^>]+>", "", xml)
        return fp.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


class RAGSystem:
    def __init__(self, knowledge_dir):
        self.knowledge_dir = Path(knowledge_dir)
        self.indexes_dir = self.knowledge_dir / "indexes"
        self.indexes_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir = self.knowledge_dir / "documents"
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.indexes_dir / "document_index.json"
        self.index = self._load_index()
        self._chunks_cache = None

    def _load_index(self):
        if self.index_file.exists():
            try:
                return json.loads(self.index_file.read_text(encoding='utf-8'))
            except Exception:
                return {"documents": {}, "version": 1}
        return {"documents": {}, "version": 1}

    def _save_index(self):
        self.index_file.write_text(json.dumps(self.index, indent=2, ensure_ascii=False), encoding='utf-8')

    def _file_hash(self, fp):
        h = hashlib.md5()
        h.update(str(fp).encode())
        h.update(str(fp.stat().st_mtime_ns).encode())
        return h.hexdigest()

    def _chunk_text(self, text, chunk_size=180, overlap=30):
        """Split text into overlapping chunks of ~chunk_size words."""
        words = text.split()
        chunks, i = [], 0
        while i < len(words):
            chunks.append(" ".join(words[i:i + chunk_size]))
            i += chunk_size - overlap
        return chunks

    def _chunk_file(self, doc_path):
        return self.indexes_dir / f"{hashlib.md5(doc_path.encode()).hexdigest()}.json"

    def index_document(self, filepath):
        """Index a single document (incremental)."""
        fp = Path(filepath)
        if not fp.exists():
            return False, "File not found"
        file_hash = self._file_hash(fp)
        rel_path = str(fp.relative_to(self.knowledge_dir)) if fp.is_relative_to(self.knowledge_dir) else str(fp)
        if rel_path in self.index["documents"] and self.index["documents"][rel_path].get("hash") == file_hash:
            return True, "Already indexed (unchanged)"
        text = read_document(fp)
        if text is None:
            return False, "Cannot read file"
        chunks = self._chunk_text(text)
        self.index["documents"][rel_path] = {"hash": file_hash, "chunks": len(chunks),
                                              "indexed_at": datetime.now().isoformat(), "size": fp.stat().st_size}
        self._chunk_file(rel_path).write_text(json.dumps(chunks, ensure_ascii=False), encoding='utf-8')
        self._save_index()
        self._chunks_cache = None
        return True, f"Indexed {len(chunks)} chunks"

    def index_directory(self, dir_path, extensions=None, limit=3000):
        """Index all documents in a directory (recursive)."""
        extensions = set(extensions or DOC_EXT)
        results = {"indexed": 0, "skipped": 0, "failed": 0, "too_many": False}
        seen = 0
        for fp in Path(dir_path).rglob("*"):
            if not fp.is_file() or fp.suffix.lower() not in extensions:
                continue
            if fp.stat().st_size > 20 * 1024 * 1024:
                continue
            seen += 1
            if seen > limit:
                results["too_many"] = True
                break
            ok, msg = self.index_document(fp)
            key = ("skipped" if "unchanged" in msg else "indexed") if ok else "failed"
            results[key] += 1
        return results

    def _load_chunks(self):
        if self._chunks_cache is None:
            entries = []
            for doc_path in self.index["documents"]:
                cf = self._chunk_file(doc_path)
                try:
                    chunks = json.loads(cf.read_text(encoding='utf-8'))
                except Exception:
                    continue
                for i, chunk in enumerate(chunks):
                    toks = tokens(chunk)
                    entries.append((doc_path, i, chunk, toks))
            self._chunks_cache = entries
        return self._chunks_cache

    def search(self, query, limit=5):
        """BM25 search over all indexed chunks."""
        q = list(dict.fromkeys(tokens(query)))
        entries = self._load_chunks()
        if not q or not entries:
            return []
        n = len(entries)
        avg = sum(len(e[3]) for e in entries) / n or 1
        df = {w: sum(1 for e in entries if w in e[3]) for w in q}
        results = []
        for doc_path, i, chunk, toks in entries:
            score = 0.0
            for w in q:
                tf = toks.count(w)
                if not tf:
                    continue
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(toks) / avg))
            if score > 0:
                results.append({"document": doc_path, "chunk_index": i, "score": round(score, 3), "text": chunk[:700]})
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

    def get_stats(self):
        return {
            "indexed_documents": len(self.index["documents"]),
            "total_chunks": sum(d.get("chunks", 0) for d in self.index["documents"].values()),
        }
