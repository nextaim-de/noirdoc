"""High-level Python SDK for Noirdoc.

Two entry points:

* :func:`redact` — one-shot convenience wrapper for a single file.
* :class:`Redactor` — stateful session with an optional persistent namespace
  so the same entity always gets the same pseudonym across calls.

Lower-level primitives (:mod:`noirdoc.detection`, :mod:`noirdoc.pseudonymization`,
:mod:`noirdoc.file_analysis`, :mod:`noirdoc.reidentification`) remain available
for callers that need finer control.
"""

from __future__ import annotations

import asyncio
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from noirdoc.namespace import Namespace
from noirdoc.pseudonymization.mapper import PseudonymMapper

if TYPE_CHECKING:
    from noirdoc.detection.base import BaseDetector, DetectedEntity
    from noirdoc.detection.ensemble import EnsembleDetector

Policy = Literal["pseudonymize", "extract_only"]
DetectorChoice = Literal["presidio", "gliner", "ensemble"]


class RedactionResult:
    """The result of redacting a single file."""

    def __init__(
        self,
        *,
        input_path: Path,
        output_bytes: bytes,
        entity_count: int,
        entity_types: dict[str, int],
        mime_type: str,
        reconstructed: bool,
    ) -> None:
        self.input_path = input_path
        self.output_bytes = output_bytes
        self.entity_count = entity_count
        self.entity_types = entity_types
        self.mime_type = mime_type
        self.reconstructed = reconstructed

    def write(self, path: Path | str) -> Path:
        """Write output bytes to *path* and return it."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(self.output_bytes)
        return out


class Redactor:
    """Stateful redaction session.

    Usage::

        r = Redactor()                          # ephemeral
        r = Redactor(namespace="client-acme")   # persistent, reversible

        r.redact_file("input.pdf", output="out.pdf")
        r.reveal_file("out.docx", output="revealed.docx")
        r.reveal_text(llm_response)
        r.lookup("<<PERSON_1>>")

    Models are loaded lazily on first use. The ensemble (Presidio + optional
    GLiNER) is the default; pass ``detector="presidio"`` to skip ML deps.
    """

    def __init__(
        self,
        *,
        namespace: str | None = None,
        namespace_root: Path | str | None = None,
        language: str = "de",
        detector: DetectorChoice = "ensemble",
        score_threshold: float = 0.5,
        gliner_model: str = "knowledgator/gliner-pii-edge-v1.0",
    ) -> None:
        self._language = language
        self._detector_choice = detector
        self._score_threshold = score_threshold
        self._gliner_model = gliner_model

        if namespace is not None:
            self._namespace: Namespace | None = Namespace(namespace, root=namespace_root)
            self._mapper = self._namespace.load()
        else:
            self._namespace = None
            self._mapper = PseudonymMapper()

        self._ensemble: EnsembleDetector | None = None

    @property
    def mapper(self) -> PseudonymMapper:
        return self._mapper

    @property
    def namespace(self) -> str | None:
        return self._namespace.name if self._namespace else None

    # -- detection lifecycle ---------------------------------------------------

    async def _ensure_detector(self):
        if self._ensemble is not None:
            return self._ensemble

        from noirdoc.detection.ensemble import EnsembleDetector
        from noirdoc.detection.model_manager import ensure_spacy_models

        detectors: list[BaseDetector] = []

        if self._detector_choice in ("presidio", "ensemble"):
            await asyncio.to_thread(ensure_spacy_models, [self._language])
            from noirdoc.detection.presidio_detector import PresidioDetector

            detectors.append(PresidioDetector(languages=[self._language]))

        if self._detector_choice in ("gliner", "ensemble"):
            try:
                from noirdoc.detection.gliner_detector import GlinerDetector
            except ImportError:
                if self._detector_choice == "gliner":
                    raise  # User explicitly asked for GLiNER — fail loud.
                warnings.warn(
                    "GLiNER is not installed; using Presidio only. "
                    "Install 'noirdoc[full]' for the ensemble detector.",
                    UserWarning,
                    stacklevel=2,
                )
            else:
                gliner = await asyncio.to_thread(
                    GlinerDetector,
                    model_name=self._gliner_model,
                )
                detectors.append(gliner)

        self._ensemble = EnsembleDetector(
            detectors=detectors,
            score_threshold=self._score_threshold,
        )
        return self._ensemble

    def _persist(self) -> None:
        if self._namespace is not None and self._mapper.entity_count > 0:
            self._namespace.save(self._mapper)

    # -- text-level API --------------------------------------------------------

    def redact_text(self, text: str, language: str | None = None) -> str:
        """Detect PII in *text*, replace with pseudonyms, return the result."""
        return asyncio.run(self._redact_text_async(text, language or self._language))

    async def aredact_text(self, text: str, language: str | None = None) -> str:
        """Async version of :meth:`redact_text` for callers already in an event loop."""
        return await self._redact_text_async(text, language or self._language)

    async def aredact_text_detailed(
        self,
        text: str,
        language: str | None = None,
    ) -> tuple[str, list[DetectedEntity]]:
        """Like :meth:`aredact_text` but also returns the detected entities."""
        if not text:
            return text, []
        from noirdoc.pseudonymization.engine import PseudonymizationEngine

        lang = language or self._language
        detector = await self._ensure_detector()
        entities = await detector.detect(text, lang)
        result = PseudonymizationEngine().pseudonymize(text, entities, self._mapper)
        self._persist()
        return result, entities

    async def _redact_text_async(self, text: str, language: str) -> str:
        result, _ = await self.aredact_text_detailed(text, language)
        return result

    def reveal_text(self, text: str) -> str:
        """Replace pseudonyms in *text* with the originals from this session."""
        from noirdoc.reidentification.engine import ReidentificationEngine

        return ReidentificationEngine().reidentify(text, self._mapper)

    def lookup(self, pseudonym: str) -> str | None:
        """Return the original text for *pseudonym*, or ``None`` if unknown."""
        return self._mapper.reverse_lookup(pseudonym)

    # -- file-level API --------------------------------------------------------

    def redact_file(
        self,
        input_path: Path | str,
        *,
        output: Path | str | None = None,
        language: str | None = None,
    ) -> RedactionResult:
        """Redact a single file. Writes to *output* if given; result carries bytes either way."""
        result = asyncio.run(self._redact_file_async(Path(input_path), language or self._language))
        if output is not None:
            result.write(output)
        return result

    async def aredact_file(
        self,
        input_path: Path | str,
        *,
        output: Path | str | None = None,
        language: str | None = None,
    ) -> RedactionResult:
        """Async version of :meth:`redact_file` for callers already in an event loop."""
        result = await self._redact_file_async(Path(input_path), language or self._language)
        if output is not None:
            result.write(output)
        return result

    async def _redact_file_async(self, path: Path, language: str) -> RedactionResult:
        from noirdoc.file_analysis.extractor import FileTextExtractor
        from noirdoc.file_analysis.mime import format_for_mime
        from noirdoc.file_analysis.models import FileBlock
        from noirdoc.file_analysis.reconstruction import can_reconstruct, reconstruct
        from noirdoc.pseudonymization.engine import PseudonymizationEngine

        content = path.read_bytes()
        mime = _detect_mime(path, content)

        if format_for_mime(mime) is None:
            raise ValueError(f"Unsupported MIME type for {path.name}: {mime}")

        block = FileBlock(
            source_path="sdk",
            source_type="file",
            content_bytes=content,
            mime_type=mime,
        )

        extractor = FileTextExtractor(ocr_enabled=_should_ocr(mime))
        text = await extractor.extract_text(block)
        if text is None:
            raise RuntimeError(
                f"Text extraction failed for {path.name}: {block.extraction_error}",
            )

        block.extracted_text = text

        detector = await self._ensure_detector()
        entities = await detector.detect(text, language)
        block.entities = entities

        pseudonymized = PseudonymizationEngine().pseudonymize(text, entities, self._mapper)
        block.pseudonymized_text = pseudonymized

        entity_types: dict[str, int] = {}
        for e in entities:
            entity_types[e.entity_type] = entity_types.get(e.entity_type, 0) + 1

        if can_reconstruct(mime):
            new_bytes = reconstruct(block)
            if new_bytes is not None:
                self._persist()
                return RedactionResult(
                    input_path=path,
                    output_bytes=new_bytes,
                    entity_count=len(entities),
                    entity_types=entity_types,
                    mime_type=mime,
                    reconstructed=True,
                )

        # Fallback: return the extracted, pseudonymized plain text.
        self._persist()
        return RedactionResult(
            input_path=path,
            output_bytes=pseudonymized.encode("utf-8"),
            entity_count=len(entities),
            entity_types=entity_types,
            mime_type="text/plain",
            reconstructed=False,
        )

    def reveal_file(
        self,
        input_path: Path | str,
        *,
        output: Path | str | None = None,
    ) -> bytes | None:
        """Roundtrip a redacted file back to originals.

        Returns the revealed bytes, or ``None`` if the format isn't supported
        for reveal (PDF, PPTX, images). In that case the caller should fall
        back to the original file.
        """
        from noirdoc.file_reidentification.service import reidentify_file_bytes

        in_path = Path(input_path)
        content = in_path.read_bytes()
        mime = _detect_mime(in_path, content)
        mappings = self._mapper.get_mapping_summary()
        revealed = reidentify_file_bytes(content, mime, mappings)
        if revealed is None:
            return None
        if output is not None:
            out = Path(output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(revealed)
        return revealed


def build_redactor(
    *,
    ensemble: EnsembleDetector | None = None,
    namespace: str | None = None,
    namespace_root: Path | str | None = None,
    language: str = "de",
    detector: DetectorChoice = "ensemble",
    score_threshold: float = 0.5,
    gliner_model: str = "knowledgator/gliner-pii-edge-v1.0",
) -> Redactor:
    """Construct a :class:`Redactor`, optionally pre-installing a built ensemble.

    The CLI fallback path passes ``ensemble=None`` and lets the redactor
    lazily build its own. The daemon passes a pre-built, cached ensemble
    so model loading is paid once for the daemon's lifetime, not once per
    request.
    """
    r = Redactor(
        namespace=namespace,
        namespace_root=namespace_root,
        language=language,
        detector=detector,
        score_threshold=score_threshold,
        gliner_model=gliner_model,
    )
    if ensemble is not None:
        r._ensemble = ensemble
    return r


def redact(
    input_path: Path | str,
    *,
    output: Path | str | None = None,
    policy: Policy = "pseudonymize",
    language: str = "de",
    detector: DetectorChoice = "ensemble",
) -> RedactionResult:
    """One-shot, ephemeral redaction of a single file.

    No mapping is persisted; this is the "just give me a clean file" path.
    Use :class:`Redactor` with ``namespace=...`` for reversible sessions.
    """
    if policy != "pseudonymize":
        raise NotImplementedError(f"policy={policy!r} is not yet supported")
    r = Redactor(language=language, detector=detector)
    return r.redact_file(input_path, output=output)


# -- helpers ------------------------------------------------------------------


def _detect_mime(path: Path, content: bytes) -> str:
    """Best-effort MIME detection — python-magic with extension fallback."""
    try:
        import magic  # type: ignore[import-not-found]

        mime = magic.from_buffer(content, mime=True)
        if mime and mime != "application/octet-stream":
            return mime
    except Exception:
        pass

    suffix = path.suffix.lower().lstrip(".")
    return {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "txt": "text/plain",
        "csv": "text/csv",
        "md": "text/markdown",
        "html": "text/html",
        "htm": "text/html",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
    }.get(suffix, "application/octet-stream")


def _should_ocr(mime: str) -> bool:
    return mime.startswith("image/") or mime == "application/pdf"
