"""Tests for FileAnalysisPolicy."""

from __future__ import annotations

import pytest

from noirdoc.file_analysis.models import FileAnalysisMode
from noirdoc.file_analysis.policy import FileAnalysisPolicy


@pytest.mark.parametrize(
    "mode,extract,detect,pseudo,block",
    [
        (FileAnalysisMode.PASSTHROUGH, False, False, False, False),
        (FileAnalysisMode.DETECT_ONLY, True, True, False, False),
        (FileAnalysisMode.BLOCK, True, True, False, True),
        (FileAnalysisMode.PSEUDONYMIZE, True, True, True, False),
    ],
)
def test_policy_modes(mode, extract, detect, pseudo, block):
    policy = FileAnalysisPolicy(mode)
    assert policy.should_extract_text() is extract
    assert policy.should_detect_pii() is detect
    assert policy.should_pseudonymize() is pseudo
    assert policy.should_block_on_pii() is block
