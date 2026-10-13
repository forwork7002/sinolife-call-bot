"""Guruhlar sozlamasi (qaysi guruh qaysi jamoani ko'radi) — state.json."""
from __future__ import annotations

import json
import os
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.groups: dict[str, dict] = {}
        self.excluded: dict[str, str] = {}   # hisobotga kirmaydiganlar: Bitrix ID -> ism
        if path.exists():
            try:
                raw = json.loads(path.read_text("utf-8"))
                self.groups = raw.get("groups", {})
                self.excluded = raw.get("excluded", {})
            except Exception:
                self.groups, self.excluded = {}, {}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"groups": self.groups, "excluded": self.excluded}, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, self.path)

    def get(self, chat_id: int) -> dict | None:
        return self.groups.get(str(chat_id))

    def bind(self, chat_id: int, title: str, deps: list[int] | None):
        self.groups[str(chat_id)] = {"title": title, "deps": deps}
        self._save()

    def unbind(self, chat_id: int):
        if self.groups.pop(str(chat_id), None) is not None:
            self._save()

    def move(self, old: int, new: int):
        g = self.groups.pop(str(old), None)
        if g:
            self.groups[str(new)] = g
            self._save()

    def member_deps(self) -> set[int]:
        out: set[int] = set()
        for g in self.groups.values():
            out |= set(g.get("deps") or [])
        return out

    def excluded_ids(self) -> set[int]:
        return {int(k) for k in self.excluded}

    def toggle_excluded(self, uid: int, name: str) -> bool:
        """True = endi hisobotdan chiqarildi, False = qaytarildi."""
        k = str(uid)
        if k in self.excluded:
            del self.excluded[k]
            out = False
        else:
            self.excluded[k] = name
            out = True
        self._save()
        return out
