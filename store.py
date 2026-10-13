"""Guruhlar sozlamasi (qaysi guruh qaysi jamoani ko'radi) — state.json."""
from __future__ import annotations

import json
import os
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.groups: dict[str, dict] = {}
        if path.exists():
            try:
                self.groups = json.loads(path.read_text("utf-8")).get("groups", {})
            except Exception:
                self.groups = {}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"groups": self.groups}, ensure_ascii=False, indent=2), "utf-8")
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
