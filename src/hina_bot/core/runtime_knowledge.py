from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from .admin_db import AdminDatabase

KNOWLEDGE_LEVELS = {
    "self", "direct_experience", "reported", "public_knowledge", "inference",
    "audience_only", "unknown",
}
MAX_ITEMS = 100
MAX_CONTENT_CHARS = 1800
MAX_VALUES = 20
MAX_VALUE_CHARS = 60
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_STOPWORDS = {"뭐야", "알려줘", "어떻게"}


def _split_values(value: str) -> list[str]:
    rows = list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if not rows or len(rows) > MAX_VALUES:
        raise ValueError(f"쉼표로 구분한 1~{MAX_VALUES}개 값을 입력해 주세요.")
    if any(len(row) > MAX_VALUE_CHARS for row in rows):
        raise ValueError(f"각 키워드/대상은 {MAX_VALUE_CHARS}자 이하여야 합니다.")
    return rows


def _terms(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN.findall(text)
            if token.casefold() not in _STOPWORDS}


class RuntimeKnowledgeRegistry:
    """Admin-managed facts or interpretations stored in SQLite."""

    def __init__(self, database: AdminDatabase | None, *, kind: str):
        if kind not in {"world_fact", "interpretation"}:
            raise ValueError("runtime knowledge kind must be world_fact or interpretation")
        self.database = database
        self.kind = kind

    @staticmethod
    def validate_id(identifier: str) -> str:
        identifier = identifier.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,63}", identifier):
            raise ValueError("ID는 영문 소문자·숫자·점·밑줄·하이픈 2~64자로 입력해 주세요.")
        return identifier

    @staticmethod
    def validate_content(content: str) -> str:
        content = content.strip()
        if not 1 <= len(content) <= MAX_CONTENT_CHARS:
            raise ValueError(f"본문은 1~{MAX_CONTENT_CHARS}자로 입력해 주세요.")
        return content

    @staticmethod
    def validate_timeline(timeline: str) -> str:
        timeline = timeline.strip() or "시점 미지정"
        if len(timeline) > 120:
            raise ValueError("timeline은 120자 이하여야 합니다.")
        return timeline

    @staticmethod
    def validate_awareness(awareness: str) -> str:
        if awareness not in KNOWLEDGE_LEVELS:
            raise ValueError("지원하지 않는 awareness 값입니다.")
        return awareness

    @staticmethod
    def _validate_timestamp(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("runtime knowledge 시간 값이 잘못되었습니다.")
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("runtime knowledge 시간 값이 잘못되었습니다.") from exc
        return value

    def _require_database(self) -> AdminDatabase:
        if self.database is None:
            raise ValueError("runtime knowledge 저장 DB가 설정되지 않았습니다.")
        return self.database

    @staticmethod
    def _validate_values(values, key: str) -> list[str]:
        if (not isinstance(values, list) or not values or len(values) > MAX_VALUES
                or any(not isinstance(value, str) or not value.strip()
                       or len(value) > MAX_VALUE_CHARS for value in values)):
            raise ValueError(f"runtime knowledge의 {key} 형식이 잘못되었습니다.")
        return list(dict.fromkeys(value.strip() for value in values))

    @staticmethod
    def _row(row) -> dict:
        return {
            "id": row["id"],
            "content": row["content"],
            "keywords": json.loads(row["keywords"]),
            "subjects": json.loads(row["subjects"]),
            "awareness": row["awareness"],
            "timeline": row["timeline"],
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list(self) -> list[dict]:
        if self.database is None:
            return []
        rows = self.database.db.execute(
            "SELECT id,content,keywords,subjects,awareness,timeline,enabled,created_at,updated_at "
            "FROM runtime_knowledge WHERE kind=? ORDER BY rowid",
            (self.kind,),
        ).fetchall()
        result = []
        for row in rows:
            try:
                item = self._row(row)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("runtime knowledge DB의 JSON 값이 잘못되었습니다.") from exc
            self._validate_values(item["keywords"], "keywords")
            self._validate_values(item["subjects"], "subjects")
            self.validate_awareness(item["awareness"])
            self.validate_timeline(item["timeline"])
            result.append(item)
        return result

    def get(self, identifier: str) -> dict:
        identifier = self.validate_id(identifier)
        database = self._require_database()
        row = database.db.execute(
            "SELECT id,content,keywords,subjects,awareness,timeline,enabled,created_at,updated_at "
            "FROM runtime_knowledge WHERE kind=? AND id=?",
            (self.kind, identifier),
        ).fetchone()
        if row is None:
            raise ValueError("등록되지 않은 ID입니다.")
        try:
            return self._row(row)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("runtime knowledge DB의 JSON 값이 잘못되었습니다.") from exc

    def add(self, identifier: str, content: str, keywords: str, subjects: str,
            awareness: str, timeline: str = "") -> None:
        identifier = self.validate_id(identifier)
        content = self.validate_content(content)
        awareness = self.validate_awareness(awareness)
        timeline = self.validate_timeline(timeline)
        keyword_values, subject_values = _split_values(keywords), _split_values(subjects)
        database = self._require_database()
        now = datetime.now(UTC).isoformat()
        with database.transaction():
            if database.db.execute(
                "SELECT 1 FROM runtime_knowledge WHERE kind=? AND id=?",
                (self.kind, identifier),
            ).fetchone():
                raise ValueError("이미 존재하는 ID입니다.")
            count = database.db.execute(
                "SELECT COUNT(*) FROM runtime_knowledge WHERE kind=?", (self.kind,)
            ).fetchone()[0]
            if count >= MAX_ITEMS:
                raise ValueError(f"항목은 최대 {MAX_ITEMS}개까지 저장할 수 있습니다.")
            database.db.execute(
                "INSERT INTO runtime_knowledge("
                "id,kind,content,keywords,subjects,awareness,timeline,enabled,created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier, self.kind, content,
                    json.dumps(keyword_values, ensure_ascii=False),
                    json.dumps(subject_values, ensure_ascii=False),
                    awareness, timeline, 1, now, now,
                ),
            )

    def edit(self, identifier: str, *, content: str | None = None,
             keywords: str | None = None, subjects: str | None = None,
             awareness: str | None = None, timeline: str | None = None) -> None:
        identifier = self.validate_id(identifier)
        if all(value is None for value in (content, keywords, subjects, awareness, timeline)):
            raise ValueError("수정할 값을 하나 이상 입력해 주세요.")
        database = self._require_database()
        existing = self.get(identifier)
        new_content = self.validate_content(content) if content is not None else existing["content"]
        new_keywords = _split_values(keywords) if keywords is not None else existing["keywords"]
        new_subjects = _split_values(subjects) if subjects is not None else existing["subjects"]
        new_awareness = (self.validate_awareness(awareness)
                         if awareness is not None else existing["awareness"])
        new_timeline = (self.validate_timeline(timeline)
                        if timeline is not None else existing["timeline"])
        with database.transaction():
            changed = database.db.execute(
                "UPDATE runtime_knowledge SET content=?,keywords=?,subjects=?,awareness=?,timeline=?,"
                "updated_at=? WHERE kind=? AND id=?",
                (
                    new_content,
                    json.dumps(new_keywords, ensure_ascii=False),
                    json.dumps(new_subjects, ensure_ascii=False),
                    new_awareness, new_timeline, datetime.now(UTC).isoformat(),
                    self.kind, identifier,
                ),
            ).rowcount
            if not changed:
                raise ValueError("등록되지 않은 ID입니다.")

    def set_enabled(self, identifier: str, enabled: bool) -> None:
        identifier = self.validate_id(identifier)
        database = self._require_database()
        with database.transaction():
            changed = database.db.execute(
                "UPDATE runtime_knowledge SET enabled=?,updated_at=? WHERE kind=? AND id=?",
                (int(enabled), datetime.now(UTC).isoformat(), self.kind, identifier),
            ).rowcount
            if not changed:
                raise ValueError("등록되지 않은 ID입니다.")

    def remove(self, identifier: str) -> None:
        identifier = self.validate_id(identifier)
        database = self._require_database()
        with database.transaction():
            changed = database.db.execute(
                "DELETE FROM runtime_knowledge WHERE kind=? AND id=?",
                (self.kind, identifier),
            ).rowcount
            if not changed:
                raise ValueError("등록되지 않은 ID입니다.")

    def import_row(self, row: dict, *, replace: bool = False) -> str:
        """Import one legacy JSON row. Missing created_at stays NULL."""
        identifier = self.validate_id(str(row.get("id", "")))
        content = self.validate_content(str(row.get("content", "")))
        keywords = self._validate_values(row.get("keywords"), "keywords")
        subjects = self._validate_values(row.get("subjects"), "subjects")
        awareness = self.validate_awareness(str(row.get("awareness", "")))
        timeline = self.validate_timeline(str(row.get("timeline", "")))
        enabled = row.get("enabled", True)
        if not isinstance(enabled, bool):
            raise TypeError("runtime knowledge enabled 값이 잘못되었습니다.")
        created_at = self._validate_timestamp(row.get("created_at"))
        updated_at = self._validate_timestamp(row.get("updated_at"))
        database = self._require_database()
        with database.transaction():
            existing = database.db.execute(
                "SELECT id,content,keywords,subjects,awareness,timeline,enabled,created_at,updated_at "
                "FROM runtime_knowledge WHERE kind=? AND id=?",
                (self.kind, identifier),
            ).fetchone()
            incoming = {
                "id": identifier,
                "content": content,
                "keywords": keywords,
                "subjects": subjects,
                "awareness": awareness,
                "timeline": timeline,
                "enabled": enabled,
                "created_at": created_at,
                "updated_at": updated_at,
            }
            if existing is not None and not replace:
                if self._row(existing) == incoming:
                    return "skipped"
                raise ValueError(f"DB에 다른 내용의 knowledge `{identifier}` ({self.kind})가 이미 있습니다.")
            if existing is None:
                count = database.db.execute(
                    "SELECT COUNT(*) FROM runtime_knowledge WHERE kind=?", (self.kind,)
                ).fetchone()[0]
                if count >= MAX_ITEMS:
                    raise ValueError(f"항목은 최대 {MAX_ITEMS}개까지 저장할 수 있습니다.")
            database.db.execute(
                "INSERT OR REPLACE INTO runtime_knowledge("
                "id,kind,content,keywords,subjects,awareness,timeline,enabled,created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier, self.kind, content,
                    json.dumps(keywords, ensure_ascii=False),
                    json.dumps(subjects, ensure_ascii=False), awareness, timeline,
                    int(enabled), created_at, updated_at,
                ),
            )
        return "replaced" if existing is not None else "added"

    def search(self, query: str, *, limit: int = 2, chars: int = 1800) -> list[dict]:
        if limit <= 0 or chars <= 0:
            return []
        folded = query.casefold()
        terms = _terms(query)
        ranked = []
        for order, row in enumerate(self.list()):
            if not row["enabled"]:
                continue
            score = 0
            for value in row["subjects"]:
                folded_value = value.casefold()
                if folded_value not in _STOPWORDS and folded_value in folded:
                    score += 8 + min(len(folded_value), 8)
            for value in row["keywords"]:
                folded_value = value.casefold()
                if folded_value in folded:
                    score += 5 + min(len(folded_value), 8)
            score += 2 * len(terms & _terms(row["content"]))
            if score:
                ranked.append((score, -order, row))

        result, used = [], 0
        prefix = "runtime_context" if self.kind == "interpretation" else "runtime_lore"
        for _, _, row in sorted(ranked, reverse=True):
            item = {
                "reference": f"{prefix}.{row['id']}",
                "kind": self.kind,
                "content": row["content"],
                "awareness": row["awareness"],
                "time": row["timeline"],
            }
            if self.kind == "interpretation":
                item["certainty"] = "plausible_interpretation_not_established_fact"
            size = len(json.dumps(item, ensure_ascii=False))
            if used + size > chars:
                continue
            result.append(item)
            used += size
            if len(result) >= limit:
                break
        return result
