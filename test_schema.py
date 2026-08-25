"""Pydantic schema loaded by the default pipeline configuration."""

from pydantic import BaseModel, Field


class DocumentExtraction(BaseModel):
    """Structured metadata expected from the document extraction model."""

    title: str = Field(description="A dokumentum eredeti címe.")
    category: str = Field(
        description="A dokumentum témaköre / kategóriája (pl. IT-Biztonság, HR, Jogi, Pénzügy)."
    )
    tags: list[str] = Field(
        min_length=7,
        max_length=7,
        description="Pontosan 7 darab releváns kulcsszó vagy címke.",
    )
    summary: str = Field(description="Egy alapos, részletes leírás a dokumentum lényegi tartalmáról.")
    questions_answered: list[str] = Field(
        min_length=3,
        max_length=5,
        description="3-5 darab legfontosabb kérdés, amire a szöveg megoldást kínál.",
    )
