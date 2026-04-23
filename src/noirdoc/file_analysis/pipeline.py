"""Top-level orchestrator for file analysis within a single request."""

from __future__ import annotations

from typing import Any

import structlog

from noirdoc.detection.ensemble import EnsembleDetector
from noirdoc.file_analysis.body_walker import (
    apply_file_results,
    convert_blocks_to_text,
    extract_file_blocks,
)
from noirdoc.file_analysis.extractor import FileTextExtractor
from noirdoc.file_analysis.mime import PROVIDER_PASSABLE_MIMES
from noirdoc.file_analysis.models import FileAnalysisMode, FileAnalysisResult
from noirdoc.file_analysis.policy import FileAnalysisPolicy
from noirdoc.pseudonymization.engine import PseudonymizationEngine
from noirdoc.pseudonymization.mapper import PseudonymMapper

logger = structlog.get_logger()


async def analyze_files_in_body(
    *,
    body: dict[str, Any],
    stream_key: str,
    mode: FileAnalysisMode,
    detector: EnsembleDetector,
    pseudo_engine: PseudonymizationEngine,
    mapper: PseudonymMapper,
    language: str = "de",
    ocr_enabled: bool = False,
    max_file_size_bytes: int = 25 * 1024 * 1024,
    max_pages: int = 50,
) -> tuple[dict[str, Any], FileAnalysisResult]:
    """Analyse every inline file in *body* and return ``(modified_body, result)``.

    The same *mapper* instance used for text PII is passed in so that
    entities shared between message text and file content receive
    consistent pseudonyms.
    """
    policy = FileAnalysisPolicy(mode)
    result = FileAnalysisResult()

    if not policy.should_extract_text():
        return body, result

    # 1. Walk the body and extract file blocks
    file_blocks = extract_file_blocks(body, stream_key)
    if not file_blocks:
        return body, result

    logger.info(
        "file_analysis.started",
        mode=mode.value,
        file_count=len(file_blocks),
    )

    _XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    text_extractor = FileTextExtractor(ocr_enabled=ocr_enabled, max_pages=max_pages)

    for block in file_blocks:
        result.files_analyzed += 1

        # Size check
        if len(block.content_bytes) > max_file_size_bytes:
            block.extraction_error = f"File exceeds size limit ({max_file_size_bytes} bytes)"
            result.files_extraction_errors += 1
            logger.warning(
                "file_analysis.size_exceeded",
                path=block.source_path,
                size=len(block.content_bytes),
                limit=max_file_size_bytes,
            )
            continue

        # XLSX fast path: column-inference analysis/pseudonymization
        if block.mime_type == _XLSX_MIME and policy.should_detect_pii():
            from noirdoc.file_analysis.xlsx_inference import pseudonymize_xlsx_smart

            xlsx_result = await pseudonymize_xlsx_smart(
                block.content_bytes,
                detector,
                mapper,
                language,
                pseudonymize=policy.should_pseudonymize(),
            )
            result.total_entities += xlsx_result.entity_count
            for etype, count in xlsx_result.entity_types.items():
                result.entity_types[etype] = result.entity_types.get(etype, 0) + count

            if xlsx_result.new_bytes:
                block.reconstructed_bytes = xlsx_result.new_bytes
                block.pseudonymized_text = "(xlsx column-inference)"

            if policy.should_block_on_pii() and xlsx_result.entity_count > 0:
                result.files_blocked += 1
                result.blocked = True

            logger.info(
                "file_analysis.xlsx_inference",
                path=block.source_path,
                mode=mode.value,
                entity_count=xlsx_result.entity_count,
                columns=xlsx_result.column_classifications,
            )
            continue

        # 2. Extract text
        text = await text_extractor.extract_text(block)
        if text is None:
            result.files_extraction_errors += 1
            continue
        block.extracted_text = text

        logger.debug(
            "file_analysis.extraction_complete",
            path=block.source_path,
            mime=block.mime_type,
            chars=len(text),
            bytes_in=len(block.content_bytes),
        )

        # 3. Detect PII (if mode requires it)
        if policy.should_detect_pii() and text.strip():
            entities = await detector.detect(text, language)
            block.entities = entities
            result.total_entities += len(entities)
            for e in entities:
                result.entity_types[e.entity_type] = result.entity_types.get(e.entity_type, 0) + 1

            if entities:
                logger.info(
                    "file_analysis.pii_detected",
                    path=block.source_path,
                    mime=block.mime_type,
                    entity_count=len(entities),
                    entity_types=list({e.entity_type for e in entities}),
                )

        # 4. Block mode: mark for rejection
        if policy.should_block_on_pii() and block.entities:
            result.files_blocked += 1
            result.blocked = True

        # 5. Pseudonymize
        if policy.should_pseudonymize() and block.entities and block.extracted_text:
            block.pseudonymized_text = pseudo_engine.pseudonymize(
                block.extracted_text,
                block.entities,
                mapper,
            )

    result.blocks = file_blocks

    # 6. Apply results back to the body (only modifies for pseudonymize mode)
    if not result.blocked:
        body = apply_file_results(body, stream_key, file_blocks, policy)
        # Count conversions vs reconstructions
        from noirdoc.file_analysis.reconstruction import can_reconstruct

        for block in file_blocks:
            if block.pseudonymized_text is not None:
                if can_reconstruct(block.mime_type):
                    result.files_reconstructed += 1
                else:
                    result.files_converted += 1

    logger.info(
        "file_analysis.completed",
        mode=mode.value,
        files_analyzed=result.files_analyzed,
        total_entities=result.total_entities,
        files_converted=result.files_converted,
        files_reconstructed=result.files_reconstructed,
        files_blocked=result.files_blocked,
        blocked=result.blocked,
    )

    return body, result


async def convert_unsupported_files(
    *,
    body: dict[str, Any],
    stream_key: str,
    ocr_enabled: bool = False,
    max_file_size_bytes: int = 25 * 1024 * 1024,
    max_pages: int = 50,
) -> tuple[dict[str, Any], FileAnalysisResult]:
    """Convert provider-unsupported file types to text blocks.

    This runs independently of the PII analysis pipeline and only performs
    format conversion — no PII detection or pseudonymization.  It ensures
    that file types not natively supported by LLM providers (e.g. XLSX,
    DOCX) are converted to text before the request is forwarded.
    """
    result = FileAnalysisResult()

    file_blocks = extract_file_blocks(body, stream_key)
    if not file_blocks:
        return body, result

    # Only process blocks whose MIME type is NOT natively supported
    unsupported = [b for b in file_blocks if b.mime_type not in PROVIDER_PASSABLE_MIMES]
    if not unsupported:
        return body, result

    logger.info(
        "file_conversion.started",
        file_count=len(unsupported),
        mime_types=[b.mime_type for b in unsupported],
    )

    text_extractor = FileTextExtractor(ocr_enabled=ocr_enabled, max_pages=max_pages)

    converted: list = []
    for block in unsupported:
        result.files_analyzed += 1

        if len(block.content_bytes) > max_file_size_bytes:
            block.extraction_error = f"File exceeds size limit ({max_file_size_bytes} bytes)"
            result.files_extraction_errors += 1
            logger.warning(
                "file_conversion.size_exceeded",
                path=block.source_path,
                size=len(block.content_bytes),
                limit=max_file_size_bytes,
            )
            continue

        text = await text_extractor.extract_text(block)
        if text is None:
            result.files_extraction_errors += 1
            continue

        block.extracted_text = text
        converted.append(block)
        result.files_converted += 1

        logger.debug(
            "file_conversion.extracted",
            path=block.source_path,
            mime=block.mime_type,
            chars=len(text),
            bytes_in=len(block.content_bytes),
        )

    result.blocks = unsupported

    if converted:
        body = convert_blocks_to_text(body, stream_key, converted)

    logger.info(
        "file_conversion.completed",
        files_analyzed=result.files_analyzed,
        files_converted=result.files_converted,
        files_extraction_errors=result.files_extraction_errors,
    )

    return body, result
