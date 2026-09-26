"""Tests for the normalizer module."""

import pytest

from ghost_in_the_template import NormalizationError
from ghost_in_the_template.normalizer import normalize_template


class TestNormalizeLineEndings:
    """Test line ending normalization."""

    def test_crlf_converted_to_lf(self):
        raw = "line1\r\nline2\r\n"
        result = normalize_template(raw)
        assert "\r" not in result

    def test_cr_converted_to_lf(self):
        raw = "line1\rline2\r"
        result = normalize_template(raw)
        assert "\r" not in result

    def test_lf_preserved(self):
        raw = "line1\nline2\n"
        result = normalize_template(raw)
        assert result == "line1\nline2\n"


class TestStripTrailingWhitespace:
    """Test trailing whitespace removal."""

    def test_trailing_spaces_removed(self):
        raw = "line1   \nline2  \n"
        result = normalize_template(raw)
        assert "line1\nline2\n" == result

    def test_trailing_tabs_removed(self):
        raw = "line1\t\nline2\t\t\n"
        result = normalize_template(raw)
        assert "line1\nline2\n" == result


class TestCollapseBlankLines:
    """Test blank line collapsing."""

    def test_triple_blank_lines_collapsed(self):
        raw = "line1\n\n\n\nline2\n"
        result = normalize_template(raw)
        # Should have at most one blank line between non-blank lines
        assert "\n\n\n" not in result
        assert "line1" in result
        assert "line2" in result

    def test_single_blank_line_preserved(self):
        raw = "line1\n\nline2\n"
        result = normalize_template(raw)
        assert "line1\n\nline2\n" == result


class TestStripJinja2Comments:
    """Test Jinja2 comment stripping."""

    def test_single_line_comment_removed(self):
        raw = "before\n{# this is a comment #}\nafter\n"
        result = normalize_template(raw)
        assert "{#" not in result
        assert "#}" not in result
        assert "before" in result
        assert "after" in result

    def test_multiline_comment_removed(self):
        raw = "before\n{# this is\na multiline\ncomment #}\nafter\n"
        result = normalize_template(raw)
        assert "{#" not in result
        assert "before" in result
        assert "after" in result

    def test_html_comment_preserved(self):
        raw = "before\n<!-- html comment -->\nafter\n"
        result = normalize_template(raw)
        assert "<!-- html comment -->" in result


class TestNormalizeWhitespaceControl:
    """Test Jinja2 whitespace control marker normalization."""

    def test_opening_tag_normalized(self):
        raw = "{%   -   if x %}content{% endif %}\n"
        result = normalize_template(raw)
        assert "{%- if" in result

    def test_closing_tag_normalized(self):
        raw = "{% if x   -   %}content{% endif %}\n"
        result = normalize_template(raw)
        assert "-%}" in result

    def test_expression_tags_normalized(self):
        raw = "{{   -  x  }}\n"
        result = normalize_template(raw)
        assert "{{- x" in result


class TestNormalizeTemplate:
    """Integration tests for the full normalization pipeline."""

    def test_empty_string_returns_empty(self):
        result = normalize_template("")
        assert result == ""

    def test_idempotent(self):
        raw = "{% for x in items %}\n  {{ x }}\n{% endfor %}\n"
        first = normalize_template(raw)
        second = normalize_template(first)
        assert first == second

    def test_non_string_raises_error(self):
        with pytest.raises(NormalizationError):
            normalize_template(42)  # type: ignore

    def test_trailing_newline_ensured(self):
        raw = "content"
        result = normalize_template(raw)
        assert result.endswith("\n")

    def test_leading_trailing_blank_lines_stripped(self):
        raw = "\n\n\ncontent\n\n\n"
        result = normalize_template(raw)
        assert result == "content\n"

    def test_formatting_only_diff_normalizes_to_same(self):
        """Two templates that differ only in formatting should normalize identically."""
        a = "{%   if x  %}{{ y   }}{%  endif  %}\n"
        b = "{% if x %}{{ y }}{% endif %}\n"
        assert normalize_template(a) == normalize_template(b)
