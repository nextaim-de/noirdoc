from __future__ import annotations

from unittest.mock import patch

from noirdoc.detection.model_manager import ensure_spacy_models


@patch("noirdoc.detection.model_manager.spacy.cli.download")
@patch("noirdoc.detection.model_manager.spacy.util.is_package")
def test_already_installed_no_download(mock_is_package, mock_download):
    mock_is_package.return_value = True
    ensure_spacy_models(["de", "en"])
    mock_download.assert_not_called()


@patch("noirdoc.detection.model_manager.spacy.cli.download")
@patch("noirdoc.detection.model_manager.spacy.util.is_package")
def test_missing_model_triggers_download(mock_is_package, mock_download):
    mock_is_package.return_value = False
    ensure_spacy_models(["de"])
    mock_download.assert_called_once_with("de_core_news_lg")


@patch("noirdoc.detection.model_manager.spacy.cli.download")
@patch("noirdoc.detection.model_manager.spacy.util.is_package")
def test_mix_installed_and_missing(mock_is_package, mock_download):
    mock_is_package.side_effect = lambda name: name == "de_core_news_lg"
    ensure_spacy_models(["de", "en"])
    mock_download.assert_called_once_with("en_core_web_lg")


@patch("noirdoc.detection.model_manager.spacy.cli.download")
@patch("noirdoc.detection.model_manager.spacy.util.is_package")
def test_unknown_language_ignored(mock_is_package, mock_download):
    ensure_spacy_models(["fr"])
    mock_is_package.assert_not_called()
    mock_download.assert_not_called()
