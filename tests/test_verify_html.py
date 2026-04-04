"""Tests for HTML rendering integrity checks."""
from __future__ import annotations
import tempfile, os

from pipeline.verify.checks.html import check_html_integrity

# Pad to ensure > 10KB when used alone
_PAD = "<!-- padding -->" * 700

_KO_HTML = """<!DOCTYPE html>
<html>
<head><title>Daily Brief</title></head>
<body>
<div class="header-title"><a href="/en/archive/2026-04-04.html">KR → English</a></div>
<nav class="header-nav">
<a href="/archive/2026-04-03.html" aria-label="이전 브리핑: 2026-04-03">◀ 이전</a>
<time datetime="2026-04-04">2026-04-04</time>
</nav>
<section><h2 class="section-title">MARKETS</h2></section>
<section class="insight-section">
<div class="insight-body"><p>한국 증시는 코스피가 8%대 폭등하는 이례적인 강세를 보였습니다. 미국 증시도 상승했습니다. 반도체 수출 호조가 이끈 한국 증시의 폭등과 중동의 지정학적 위기가 교차하는 국면입니다. 스페이스X의 기업공개 소식이 기술주 전반에 활력을 불어넣었습니다.</p></div>
</section>
<section><h2 class="section-title">World</h2></section>
<section><h2 class="section-title">Korea</h2></section>
""" + _PAD + "</body></html>"

_EN_HTML = """<!DOCTYPE html>
<html>
<head><title>Daily Brief</title></head>
<body>
<div class="header-title"><a href="/archive/2026-04-04.html">EN → 한국어</a></div>
<nav class="header-nav">
<a href="/en/archive/2026-04-03.html" aria-label="Previous brief: 2026-04-03">◀ Previous</a>
<time datetime="2026-04-04">2026-04-04</time>
</nav>
<section><h2 class="section-title">MARKETS</h2></section>
<section class="insight-section">
<div class="insight-body"><p>Korean equities surged dramatically with KOSPI posting an extraordinary 8% daily gain driven by record semiconductor exports. US markets also advanced as SpaceX IPO filing buoyed technology sentiment across the board.</p></div>
</section>
<section><h2 class="section-title">World</h2></section>
<section><h2 class="section-title">Korea</h2></section>
""" + _PAD + "</body></html>"


def _write_html(content):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


def test_good_html():
    ko = _write_html(_KO_HTML)
    en = _write_html(_EN_HTML)
    try:
        errors, _ = check_html_integrity(ko, en, no_llm=False, run_date="2026-04-04")
        assert errors == [], f"Unexpected errors: {errors}"
    finally:
        os.unlink(ko)
        os.unlink(en)


def test_too_small():
    ko = _write_html("<html><body>tiny</body></html>")
    en = _write_html("<html><body>tiny</body></html>")
    try:
        errors, _ = check_html_integrity(ko, en, no_llm=False, run_date="2026-04-04")
        assert any("too small" in e.lower() for e in errors)
    finally:
        os.unlink(ko)
        os.unlink(en)


def test_missing_insight_body():
    html = _KO_HTML.replace('class="insight-body"', 'class="insight-gone"')
    ko = _write_html(html)
    en = _write_html(html)
    try:
        errors, _ = check_html_integrity(ko, en, no_llm=False, run_date="2026-04-04")
        assert any("insight" in e.lower() for e in errors)
    finally:
        os.unlink(ko)
        os.unlink(en)


def test_fallback_message_detected():
    html = _KO_HTML + "<p>AI 분석을 사용할 수 없습니다.</p>"
    ko = _write_html(html)
    en = _write_html(html)
    try:
        errors, _ = check_html_integrity(ko, en, no_llm=False, run_date="2026-04-04")
        assert any("사용할 수 없" in e or "fallback" in e.lower() for e in errors)
    finally:
        os.unlink(ko)
        os.unlink(en)
