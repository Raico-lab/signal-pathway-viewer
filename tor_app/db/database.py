"""DB 接続と CRUD。"""
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, Category, CategoryMember, Interaction, Protein
from .vocab import CATEGORY_PALETTE, DETAILS, guess_is_complex


class Database:
    def __init__(self, path: str):
        self.path = str(path)
        self.engine = create_engine(
            "sqlite://",
            creator=lambda: sqlite3.connect(self.path, timeout=15, check_same_thread=False),
        )

        @event.listens_for(self.engine, "connect")
        def _on_connect(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        Base.metadata.create_all(self.engine)
        self._add_missing_columns()
        self._session = sessionmaker(self.engine, expire_on_commit=False)

    def _add_missing_columns(self) -> None:
        """前の版で作った DB に、あとから足した列を加える（interactions.conditions と文献の性質、categories の出典・番号・説明）。"""
        added = {"interactions": [("conditions", "TEXT DEFAULT ''")] + [(name, "TEXT DEFAULT ''") for name, _ in DETAILS],
                 "categories": [("source", "VARCHAR DEFAULT ''"), ("ref", "VARCHAR DEFAULT ''"),
                                ("description", "TEXT DEFAULT ''")]}
        with self.engine.begin() as conn:
            for table, columns in added.items():
                cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
                for name, kind in columns:
                    if cols and name not in cols:
                        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

    @staticmethod
    def exists(path: str) -> bool:
        return Path(path).is_file()

    def session(self) -> Session:
        """書き込みにも使うセッション。読み出しの覚えは捨てる（書いた後に古い内容を返さないように）。"""
        self._cache = {}
        return self._session()

    # ---- 読み出し ----
    # 地図・データベース・情報源のタブが同じ表を続けて読むので、読んだ結果を覚えて使い回す（関係 6 万本の読み出しは重い）。
    # 書き込み（session() を通るもの）をしたら覚えを捨てる。返すのは並びの写し（中身の行は共有）
    def _cached(self, key: str, load):
        cache = self.__dict__.setdefault("_cache", {})
        if key not in cache:
            with self._session() as s:
                cache[key] = load(s)
        value = cache[key]
        return {k: list(v) for k, v in value.items()} if isinstance(value, dict) else list(value)

    def proteins(self) -> list[Protein]:
        return self._cached("proteins", lambda s: list(s.scalars(select(Protein).order_by(Protein.gene_name))))

    def interactions(self) -> list[Interaction]:
        return self._cached("interactions",
                            lambda s: list(s.scalars(select(Interaction).order_by(Interaction.id)).unique()))

    def categories(self) -> list[Category]:
        return self._cached("categories", lambda s: list(s.scalars(select(Category).order_by(Category.name))))

    def category_members(self) -> dict[int, list[int]]:
        """CategoryMember の所属（カテゴリ → 遺伝子）。地図の枠の所属（Protein.complex_category）は含まない。"""
        def load(s):
            result: dict[int, list[int]] = {}
            for m in s.scalars(select(CategoryMember)):
                result.setdefault(m.category_id, []).append(m.protein_id)
            return result
        return self._cached("members", load)

    # ---- カテゴリ ----
    def ensure_categories(self, s: Session, names) -> None:
        """遺伝子に使われているカテゴリが categories に無ければ自動作成する。"""
        existing = {c.name for c in s.scalars(select(Category))}
        count = len(existing)
        for name in sorted({n for n in names if n} - existing):
            s.add(Category(name=name, is_complex=guess_is_complex(name),
                           color=CATEGORY_PALETTE[count % len(CATEGORY_PALETTE)]))
            count += 1

    def save_category(self, category_id: int | None, name: str, is_complex: bool, color: str) -> None:
        with self.session() as s, s.begin():
            if category_id is None:
                s.add(Category(name=name, is_complex=is_complex, color=color))
                return
            cat = s.get(Category, category_id)
            if cat.name != name:
                for p in s.scalars(select(Protein).where(Protein.complex_category == cat.name)):
                    p.complex_category = name
            cat.name, cat.is_complex, cat.color = name, is_complex, color

    def delete_category(self, category_id: int) -> None:
        with self.session() as s, s.begin():
            s.delete(s.get(Category, category_id))

    # ---- 遺伝子 ----
    def save_protein(self, protein_id: int | None, **fields) -> None:
        with self.session() as s, s.begin():
            p = Protein() if protein_id is None else s.get(Protein, protein_id)
            for key, value in fields.items():
                setattr(p, key, value)
            if protein_id is None:
                s.add(p)
            self.ensure_categories(s, [p.complex_category])

    def delete_protein(self, protein_id: int) -> None:
        with self.session() as s, s.begin():
            s.query(Interaction).filter(
                (Interaction.source_id == protein_id) | (Interaction.target_id == protein_id)
            ).delete(synchronize_session=False)
            s.delete(s.get(Protein, protein_id))

    # ---- 制御関係 ----
    def save_interaction(self, interaction_id: int | None, **fields) -> None:
        with self.session() as s, s.begin():
            it = Interaction() if interaction_id is None else s.get(Interaction, interaction_id)
            for key, value in fields.items():
                setattr(it, key, value)
            if interaction_id is None:
                s.add(it)

    def delete_interaction(self, interaction_id: int) -> None:
        with self.session() as s, s.begin():
            s.delete(s.get(Interaction, interaction_id))
