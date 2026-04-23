from __future__ import annotations

import re

import structlog

from noirdoc.pseudonymization.mapper import PseudonymMapper

logger = structlog.get_logger()

# Matches <<TYPE_N>> pseudonyms (TYPE = uppercase letters/underscores, N = digits)
_PSEUDO_PATTERN = re.compile(r"<<[A-Z_]+_\d+>>")

# Lenient pattern: handles case changes, whitespace, and common Unicode bracket variants
# Matches variations like <<person_1>>, << PERSON_1 >>, «PERSON_1», etc.
_PSEUDO_PATTERN_LENIENT = re.compile(
    r"(?:<<|[\u00ab\u300a\uff1c]{1,2})"  # opening: << or « or 《 or ＜＜
    r"\s*([A-Za-z_]+_\d+)\s*"  # token body (case-insensitive capture)
    r"(?:>>|[\u00bb\u300b\uff1e]{1,2})",  # closing: >> or » or 》 or ＞＞
)


class ReidentificationEngine:
    """Ersetzt Pseudonyme in einem Text durch die Originalwerte."""

    def reidentify(self, text: str, mapper: PseudonymMapper) -> str:
        # Pass 1: strict match (standard pseudonym format)
        def _replace_strict(match: re.Match) -> str:
            pseudonym = match.group(0)
            original = mapper.reverse_lookup(pseudonym)
            if original is None:
                logger.warning(
                    "reidentify.unknown_pseudonym",
                    pseudonym=pseudonym,
                    known_count=mapper.entity_count,
                )
                return pseudonym
            return original

        result = _PSEUDO_PATTERN.sub(_replace_strict, text)

        # Pass 2: lenient match for LLM-modified tokens (only if mapper has entities)
        if mapper.entity_count > 0:
            result = _PSEUDO_PATTERN_LENIENT.sub(
                lambda m: self._replace_lenient(m, mapper),
                result,
            )

        return result

    def _replace_lenient(self, match: re.Match, mapper: PseudonymMapper) -> str:
        """Try to resolve a malformed pseudonym by normalizing to uppercase."""
        raw_token = match.group(1).upper()
        pseudonym = f"<<{raw_token}>>"
        original = mapper.reverse_lookup(pseudonym)
        if original is None:
            # Not a known pseudonym — leave unchanged to avoid false matches
            return match.group(0)
        logger.info(
            "reidentify.lenient_match",
            raw=match.group(0),
            normalized=pseudonym,
        )
        return original

    def reidentify_partial(
        self,
        text: str,
        mapper: PseudonymMapper,
    ) -> tuple[str, int, int]:
        """
        Wie reidentify(), gibt aber zusätzlich Statistik zurück.
        Returns: (reidentified_text, replaced_count, unresolved_count)
        """
        replaced = 0
        unresolved = 0

        def _replace_strict(match: re.Match) -> str:
            nonlocal replaced, unresolved
            pseudonym = match.group(0)
            original = mapper.reverse_lookup(pseudonym)
            if original is None:
                unresolved += 1
                return pseudonym
            replaced += 1
            return original

        result = _PSEUDO_PATTERN.sub(_replace_strict, text)

        # Pass 2: lenient
        if mapper.entity_count > 0:

            def _replace_lenient(match: re.Match) -> str:
                nonlocal replaced, unresolved
                raw_token = match.group(1).upper()
                pseudonym = f"<<{raw_token}>>"
                original = mapper.reverse_lookup(pseudonym)
                if original is None:
                    return match.group(0)
                replaced += 1
                return original

            result = _PSEUDO_PATTERN_LENIENT.sub(_replace_lenient, result)

        return result, replaced, unresolved
