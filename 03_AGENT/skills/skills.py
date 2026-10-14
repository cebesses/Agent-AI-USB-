"""
Agent IA USB Skill Learning - Save and retrieve reusable procedures.
When a multi-step request succeeds, the agent stores how it did it; similar
requests later get that procedure as a hint, so Agent IA USB improves with use.
"""
import json
import re
from pathlib import Path
from datetime import datetime


class Skill:
    def __init__(self, name, procedure, conditions=None, limitations=None, success_count=0,
                 version=1, created_at=None, **_ignored):
        self.name = name
        self.procedure = procedure
        self.conditions = conditions or []
        self.limitations = limitations or []
        self.success_count = success_count
        self.version = version
        self.created_at = created_at or datetime.now().isoformat()

    def to_dict(self):
        return dict(self.__dict__)


def _words(text):
    return {w for w in re.findall(r"[a-záéíóúñü0-9]+", (text or "").lower()) if len(w) > 2}


class SkillLibrary:
    def __init__(self, skills_dir):
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.skills = self._load_all()

    def _load_all(self):
        skills = {}
        for fp in self.skills_dir.glob("*.json"):
            try:
                data = json.loads(fp.read_text(encoding='utf-8'))
                skill = Skill(**data)
                skills[skill.name] = skill
            except Exception:
                pass
        return skills

    def _file(self, name):
        safe = re.sub(r"[^\w-]+", "_", name.lower()).strip("_")[:80] or "skill"
        return self.skills_dir / f"{safe}.json"

    def save(self, skill):
        """Store a skill; saving one that already exists counts as another success."""
        existing = self.skills.get(skill.name)
        if existing:
            skill.success_count = existing.success_count + 1
            skill.created_at = existing.created_at
        self.skills[skill.name] = skill
        self._file(skill.name).write_text(json.dumps(skill.to_dict(), indent=2, ensure_ascii=False), encoding='utf-8')

    def recall(self, context, limit=3):
        """Skills whose trigger words overlap the request the most."""
        words = _words(context)
        scored = []
        for skill in self.skills.values():
            conds = {c.lower() for c in skill.conditions}
            overlap = len(words & conds) + sum(1 for c in conds if len(c) > 3 and c in (context or "").lower())
            if overlap >= 2 or (overlap == 1 and len(conds) == 1):
                scored.append((overlap, skill.success_count, skill))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [s.to_dict() for _, _, s in scored[:limit]]

    def record_success(self, skill_name):
        if skill_name in self.skills:
            self.save(self.skills[skill_name])
