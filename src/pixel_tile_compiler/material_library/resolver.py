"""Runtime resolver for promoted Material Source Library assets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import MaterialFamily, MaterialSourceCard


class MaterialLibrary:
    """Resolve material IDs to preferred/promoted source cards, never raw paths."""

    def __init__(self, root: Path | str = "material_library") -> None:
        self.root = Path(root)
        self._index = self._load_json(self.root / "index.json") if (self.root / "index.json").exists() else {"families": []}

    def get_family(self, material_id: str) -> MaterialFamily:
        if material_id not in self._family_ids():
            raise KeyError(f"unknown material family: {material_id}")
        values = self._load_json(self.root / material_id / "family.json")
        return MaterialFamily(
            material_id=str(values.get("material_id", material_id)),
            material_class=str(values.get("material_class", "surface")),
            accepted_sources=tuple(str(value) for value in values.get("accepted_sources", [])),
            preferred_source=values.get("preferred_source"),
            recommended_feature_scale_px=float(values.get("recommended_feature_scale_px", 4.0)),
            recommended_renderer=str(values.get("recommended_renderer", "surface")),
            recommended_palette_budget=int(values.get("recommended_palette_budget", 28)),
        )

    def get_sources(self, material_id: str) -> list[MaterialSourceCard]:
        family = self.get_family(material_id)
        cards: list[MaterialSourceCard] = []
        for source_id in family.accepted_sources:
            card_path = self.root / material_id / "accepted" / source_id / "source_card.json"
            if card_path.exists():
                cards.append(MaterialSourceCard.from_dict(self._load_json(card_path)))
            else:
                cards.append(
                    MaterialSourceCard(
                        source_id=source_id,
                        material_id=material_id,
                        material_class=family.material_class,
                        source_file=str(card_path.parent / "source.png"),
                    )
                )
        return cards

    def get_preferred_source(self, material_id: str) -> MaterialSourceCard:
        family = self.get_family(material_id)
        if not family.preferred_source:
            raise KeyError(f"material family has no preferred source: {material_id}")
        for card in self.get_sources(material_id):
            if card.source_id == family.preferred_source:
                return card
        raise KeyError(f"preferred source is not accepted: {family.preferred_source}")

    def _family_ids(self) -> set[str]:
        values = self._index.get("families", [])
        if isinstance(values, dict):
            return {str(key) for key in values}
        return {str(value) for value in values}

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object: {path}")
        return value
