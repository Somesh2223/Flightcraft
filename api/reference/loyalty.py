"""Loyalty programme registry — which points can touch which flight."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from api.reference import carriers

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "loyalty.yaml"


class Program(BaseModel):
    code: str
    name: str
    airline: str
    books_alliance: str | None = None
    currency_pool: str | None = None
    redeems_as: str = "award_seat"

    @property
    def airline_name(self) -> str:
        return carriers.display_name(self.airline)

    @property
    def label(self) -> str:
        """How a traveller would recognise it: 'Air India Maharaja Club'.

        Some programme names already carry the airline — 'The British Airways
        Club' — and prefixing those reads as a stutter.
        """
        airline = self.airline_name
        if airline.lower() in self.name.lower():
            return self.name
        return f"{airline} {self.name}"

    @property
    def books_award_seats(self) -> bool:
        return self.redeems_as == "award_seat"


@lru_cache(maxsize=1)
def _registry() -> dict[str, Program]:
    raw = yaml.safe_load(DATA_PATH.read_text(encoding="utf-8")) or {}
    return {
        code: Program(code=code, **entry)
        for code, entry in (raw.get("programs") or {}).items()
    }


def get(code: str | None) -> Program | None:
    return _registry().get(code) if code else None


def all_programs() -> list[Program]:
    return sorted(_registry().values(), key=lambda p: p.label)


def pool_members(program: Program) -> list[Program]:
    """Programmes whose balances are interchangeable with this one."""
    if not program.currency_pool:
        return [program]
    return [
        p for p in _registry().values() if p.currency_pool == program.currency_pool
    ]


def can_book(program: Program, carrier: str | None) -> bool:
    """Whether this programme could plausibly issue an award on that carrier.

    Deliberately conservative. A programme can always book its own airline, and
    an alliance programme can book that alliance. Bilateral partnerships outside
    an alliance are real and numerous — Emirates and Qantas, Etihad and half of
    Europe — but they change constantly and are not published anywhere
    machine-readable, so they are left out rather than guessed at. A missed
    partnership costs the traveller a suggestion; an invented one sends them
    hunting for an award that cannot exist.
    """
    if not carrier or not program.books_award_seats:
        return False
    if carrier.upper() == program.airline:
        return True
    if program.books_alliance is None:
        return False
    return carriers.alliance(carrier) == program.books_alliance
