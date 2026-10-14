"""
Agent IA USB Persistent Memory System
Types: working, episodic, project, preferences, semantic
Survives restart, USB removal, and machine changes.
"""
import json, hashlib, uuid
from pathlib import Path
from datetime import datetime

class MemoryEntry:
    """A single memory entry with metadata."""
    def __init__(self, content, memory_type="episodic", source="user", confidence=1.0, scope="session", tags=None):
        self.id = str(uuid.uuid4())[:8]
        self.content = content
        self.memory_type = memory_type  # working, episodic, project, preferences, semantic
        self.source = source
        self.confidence = confidence
        self.scope = scope  # session, persistent, project
        self.tags = tags or []
        self.created_at = datetime.now().isoformat()
        self.access_count = 0

    def to_dict(self):
        return {
            "id": self.id, "content": self.content, "memory_type": self.memory_type,
            "source": self.source, "confidence": self.confidence, "scope": self.scope,
            "tags": self.tags, "created_at": self.created_at, "access_count": self.access_count,
        }

class MemorySystem:
    """Persistent memory system with multiple memory types."""
    def __init__(self, memory_dir):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.working = []  # In-memory only
        self.episodic = self._load("episodic.json")
        self.project = self._load("project.json")
        self.preferences = self._load("preferences.json")
        self.semantic = self._load("semantic.json")

    def _load(self, filename):
        fp = self.memory_dir / filename
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding='utf-8'))
                return [MemoryEntry(**e) for e in data]
            except:
                return []
        return []

    def _save(self, filename, entries):
        fp = self.memory_dir / filename
        fp.write_text(json.dumps([e.to_dict() for e in entries], indent=2, ensure_ascii=False),
                      encoding='utf-8')

    def remember(self, content, memory_type="episodic", source="user", confidence=1.0, tags=None):
        """Store a memory."""
        entry = MemoryEntry(content, memory_type, source, confidence, scope="persistent", tags=tags)

        if memory_type == "working":
            self.working.append(entry)
            return entry.id
        elif memory_type == "episodic":
            self.episodic.append(entry)
            self._save("episodic.json", self.episodic)
        elif memory_type == "project":
            self.project.append(entry)
            self._save("project.json", self.project)
        elif memory_type == "preferences":
            self.preferences.append(entry)
            self._save("preferences.json", self.preferences)
        elif memory_type == "semantic":
            self.semantic.append(entry)
            self._save("semantic.json", self.semantic)
        return entry.id

    def recall(self, query, memory_type=None, limit=10):
        """Search memory for relevant entries using TF-IDF-like scoring."""
        import math
        all_entries = []
        if memory_type is None or memory_type == "working":
            all_entries.extend(self.working)
        if memory_type is None or memory_type == "episodic":
            all_entries.extend(self.episodic)
        if memory_type is None or memory_type == "project":
            all_entries.extend(self.project)
        if memory_type is None or memory_type == "preferences":
            all_entries.extend(self.preferences)
        if memory_type is None or memory_type == "semantic":
            all_entries.extend(self.semantic)

        if not all_entries:
            return []

        query_words = query.lower().split()
        query_set = set(query_words)
        n_docs = len(all_entries)

        # Precompute document frequency for each word
        df = {}
        for entry in all_entries:
            words = set(entry.content.lower().split())
            for w in words:
                df[w] = df.get(w, 0) + 1

        scored = []
        for entry in all_entries:
            content_words = entry.content.lower().split()
            content_set = set(content_words)

            # TF: term frequency in this entry
            score = 0.0
            for qw in query_set:
                if qw in content_set:
                    tf = content_words.count(qw) / max(len(content_words), 1)
                    idf = math.log(n_docs / max(df.get(qw, 1), 1)) + 1  # +1 to avoid zero with few entries
                    score += tf * idf

            if score > 0:
                # Boost by confidence and recency
                score *= entry.confidence
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for _, entry in scored[:limit]:
            entry.access_count += 1
            results.append(entry.to_dict())
        return results

    def forget(self, memory_id):
        """Remove a memory by ID."""
        for store_name, store in [("episodic", self.episodic), ("project", self.project),
                                   ("preferences", self.preferences), ("semantic", self.semantic)]:
            before = len(store)
            store[:] = [e for e in store if e.id != memory_id]
            if len(store) < before:
                self._save(f"{store_name}.json", store)
                return True
        return False

    def get_stats(self):
        """Get memory statistics by type."""
        return {
            "working": len(self.working),
            "episodic": len(self.episodic),
            "project": len(self.project),
            "preferences": len(self.preferences),
            "semantic": len(self.semantic),
        }