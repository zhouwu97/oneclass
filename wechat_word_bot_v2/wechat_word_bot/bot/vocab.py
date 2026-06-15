from __future__ import annotations

import json
from pathlib import Path

from rapidfuzz import fuzz, process

from .vision import clean_word


class VocabStore:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, str] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.data = {clean_word(k): str(v) for k, v in raw.items() if clean_word(k)}
            except (json.JSONDecodeError, OSError):
                self.data = {}

    def save(self) -> None:
        self.path.write_text(
            json.dumps(dict(sorted(self.data.items())), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add(self, word: str, definition: str) -> bool:
        word = clean_word(word)
        definition = (definition or "").strip()
        if not word or not definition:
            return False
        old = self.data.get(word, "")
        if len(definition) >= len(old):
            self.data[word] = definition
            self.save()
            return True
        return False

    def definition(self, word: str) -> str:
        return self.data.get(clean_word(word), "")

    def words(self) -> list[str]:
        return list(self.data.keys())

    def closest_word(self, heard: str) -> tuple[str, float]:
        heard = clean_word(heard)
        if not heard or not self.data:
            return "", 0.0
        match = process.extractOne(heard, self.data.keys(), scorer=fuzz.WRatio)
        if not match:
            return "", 0.0
        word, score, _ = match
        return str(word), float(score)
