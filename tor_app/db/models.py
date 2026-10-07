"""SQLite スキーマ。仕様書の Proteins / Interactions に、シミュレーション用の数値列を追加している。"""
from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Category(Base):
    """カテゴリ（複合体など）。

    - Complex Portal の複合体: source="Complex Portal"。所属は CategoryMember（1 つの遺伝子が複数の複合体に入れる）。
    - パラログ: source="パラログ"（is_complex=False）。所属は CategoryMember。
    - source が空のもの: 遺伝子の complex_category（1 遺伝子 1 つ）で所属を決める手作業のカテゴリ。
      2026-10-03 に手作業の複合体・区分をすべて外したので、今は使っていない（列は残してある）。
    """

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    is_complex: Mapped[bool] = mapped_column(Boolean, default=False)
    color: Mapped[str] = mapped_column(String, default="#90a4ae")
    source: Mapped[str] = mapped_column(String, default="")        # 出典（空ならこのアプリで決めた地図の枠）
    ref: Mapped[str] = mapped_column(String, default="")           # 出典での番号（Complex Portal の CPX-…）
    description: Mapped[str] = mapped_column(Text, default="")     # 出典の説明（英語）


class CategoryMember(Base):
    """カテゴリに所属する遺伝子（Complex Portal の複合体の構成。地図の枠の所属は Protein.complex_category）。"""

    __tablename__ = "category_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    protein_id: Mapped[int] = mapped_column(ForeignKey("proteins.id", ondelete="CASCADE"), nullable=False)


class Protein(Base):
    __tablename__ = "proteins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gene_name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    standard_name: Mapped[str] = mapped_column(String, default="")
    complex_category: Mapped[str] = mapped_column(String, default="")
    protein_properties: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # 上流からの入力がないときの活性 (0〜1)。None は自動（活性化入力がなければ 1、あれば 0）。
    basal_activity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("proteins.id", ondelete="CASCADE"), nullable=False)
    target_id: Mapped[int] = mapped_column(ForeignKey("proteins.id", ondelete="CASCADE"), nullable=False)
    interaction_type: Mapped[str] = mapped_column(String, default="activation")
    # 下流への作用の向き: activate / inhibit / none。None は interaction_type から自動決定。
    effect: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # 上流活性がこの値を超えるとシグナルが伝わる (0〜1)。
    threshold: Mapped[float] = mapped_column(Float, default=0.5)
    # シグナルの強さ (0〜1)。抑制なら 1.0 で完全に抑える。
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    signal_threshold_rule: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    # 関係が働く条件（tor_app/conditions.py のキーを ";" でつないだもの。記録がなければ空）
    conditions: Mapped[str] = mapped_column(Text, default="")
    # 文献から拾った関係の性質（docs/curation/literature_fields.md。文献の関係だけに入る。複数は ";"。列の名前は vocab.DETAILS）
    partner: Mapped[str] = mapped_column(Text, default="")          # 組む相手（サイクリン・調節サブユニットなど）
    site: Mapped[str] = mapped_column(Text, default="")             # 修飾の部位（S582 など）
    evidence_kind: Mapped[str] = mapped_column(Text, default="")    # 根拠の実験の種類（in_vivo・in_vitro・genetic・structural）
    necessity: Mapped[str] = mapped_column(Text, default="")        # 必要か十分か（necessary・sufficient・both・partial）
    redundant_with: Mapped[str] = mapped_column(Text, default="")   # 同じ作用をもつ別の上流
    strength: Mapped[str] = mapped_column(Text, default="")         # 効く強さ（strong・moderate・weak）
    layer: Mapped[str] = mapped_column(Text, default="")            # 下流の何を変えるか（expression・activity・stability・localization）
    timing: Mapped[str] = mapped_column(Text, default="")           # 時間の性質（transient・sustained・adaptive と時間）

    source: Mapped[Protein] = relationship(foreign_keys=[source_id], lazy="joined")
    target: Mapped[Protein] = relationship(foreign_keys=[target_id], lazy="joined")
