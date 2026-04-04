"""Tests for translation completeness checks."""
from __future__ import annotations

from pipeline.verify.checks.translation import check_translations


def _article(title, bucket, summary="요약"):
    return {"title": title, "bucket": bucket, "summary": summary, "source": "test", "url": "http://test"}


def test_ko_world_translated():
    ko = [_article("이란 전쟁 격화", "world"), _article("한국 경제 성장", "korea")]
    en = [_article("Iran war escalates", "world"), _article("Korea economy grows", "korea")]
    errors, _ = check_translations(ko, en, {})
    assert errors == []


def test_ko_world_not_translated():
    ko = [_article("Iran war escalates", "world")]
    en = [_article("Iran war escalates", "world")]
    errors, _ = check_translations(ko, en, {})
    assert any("not translated" in e.lower() or "한국어" in e for e in errors)


def test_en_korea_not_translated():
    ko = [_article("한국 경제 성장", "korea")]
    en = [_article("한국 경제 성장", "korea")]
    errors, _ = check_translations(ko, en, {})
    assert any("not translated" in e.lower() or "English" in e for e in errors)


def test_empty_title():
    ko = [_article("", "world")]
    en = [_article("good title", "world")]
    errors, _ = check_translations(ko, en, {})
    assert any("empty" in e.lower() for e in errors)
