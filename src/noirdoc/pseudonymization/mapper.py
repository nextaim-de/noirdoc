from __future__ import annotations


class PseudonymMapper:
    """
    Bidirektionales Mapping: Original <-> Pseudonym.
    Lebt nur für einen Request-Lifecycle (in-memory, kein State).
    """

    def __init__(
        self,
        prefix: str = "<<",
        suffix: str = ">>",
        label: str | None = None,
    ) -> None:
        self._prefix = prefix
        self._suffix = suffix
        self._label = label
        self._entity_to_pseudo: dict[str, str] = {}
        self._pseudo_to_entity: dict[str, str] = {}
        self._counters: dict[str, int] = {}

    def get_or_create(self, entity_text: str, entity_type: str) -> str:
        """
        Gibt konsistentes Pseudonym zurück.
        Gleicher Text = gleiches Pseudonym (case-insensitive).
        """
        lookup_key = entity_text.strip().lower()
        if lookup_key in self._entity_to_pseudo:
            return self._entity_to_pseudo[lookup_key]

        effective_label = self._label or entity_type
        count = self._counters.get(effective_label, 0) + 1
        self._counters[effective_label] = count
        pseudonym = f"{self._prefix}{effective_label}_{count}{self._suffix}"

        self._entity_to_pseudo[lookup_key] = pseudonym
        self._pseudo_to_entity[pseudonym] = entity_text
        return pseudonym

    def reverse_lookup(self, pseudonym: str) -> str | None:
        """Pseudonym -> Originaltext. None wenn nicht gefunden."""
        return self._pseudo_to_entity.get(pseudonym)

    def get_all_pseudonyms(self) -> list[str]:
        """Alle generierten Pseudonyme."""
        return list(self._pseudo_to_entity.keys())

    def get_mapping_summary(self) -> dict[str, str]:
        """Pseudonym -> Original Mapping (für Debug/Audit)."""
        return dict(self._pseudo_to_entity)

    def get_counts_summary(self) -> dict[str, object]:
        """Counts-only summary: total entities and per-label breakdown.

        Safe to log or emit to caller transcripts — original values never
        enter the output.
        """
        return {
            "total_entities": self.entity_count,
            "by_type": dict(self._counters),
        }

    @property
    def entity_count(self) -> int:
        return len(self._pseudo_to_entity)

    def to_dict(self) -> dict[str, object]:
        """Serialize mapper state to a JSON-safe dict."""
        return {
            "prefix": self._prefix,
            "suffix": self._suffix,
            "label": self._label,
            "pseudo_to_entity": dict(self._pseudo_to_entity),
            "counters": dict(self._counters),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> PseudonymMapper:
        """Restore a mapper previously serialized via :meth:`to_dict`."""
        mapper = cls(
            prefix=str(data.get("prefix", "<<")),
            suffix=str(data.get("suffix", ">>")),
            label=data.get("label"),  # type: ignore[arg-type]
        )
        pseudo_to_entity = data.get("pseudo_to_entity") or {}
        counters = data.get("counters") or {}
        if isinstance(pseudo_to_entity, dict):
            for pseudo, entity in pseudo_to_entity.items():
                mapper._pseudo_to_entity[pseudo] = entity
                mapper._entity_to_pseudo[entity.strip().lower()] = pseudo
        if isinstance(counters, dict):
            mapper._counters.update(counters)
        return mapper
