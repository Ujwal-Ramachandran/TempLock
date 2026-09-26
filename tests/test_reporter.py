"""Tests for the reporter module."""

import json

from ghost_in_the_template.integrity import IntegrityResult
from ghost_in_the_template.reporter import (
    format_error,
    format_integrity_result,
    format_structural_result,
)
from ghost_in_the_template.structural import Finding, StructuralResult


class TestFormatIntegrityResult:
    """Test integrity result formatting."""

    def _make_pass_result(self) -> IntegrityResult:
        return IntegrityResult(
            passed=True,
            target_path="target.gguf",
            reference_path="reference.gguf",
            target_hash="abc123",
            reference_hash="abc123",
        )

    def _make_fail_result(self) -> IntegrityResult:
        return IntegrityResult(
            passed=False,
            target_path="target.gguf",
            reference_path="reference.gguf",
            target_hash="abc123",
            reference_hash="def456",
            diff_text="--- ref\n+++ target\n-old\n+new",
        )

    def test_text_pass(self):
        out = format_integrity_result(self._make_pass_result(), "text")
        assert "PASS" in out
        assert "target.gguf" in out

    def test_text_fail(self):
        out = format_integrity_result(self._make_fail_result(), "text")
        assert "FAIL" in out
        assert "Diff:" in out

    def test_json_pass(self):
        out = format_integrity_result(self._make_pass_result(), "json")
        data = json.loads(out)
        assert data["mode"] == "integrity"
        assert data["result"] == "PASS"
        assert data["diff"] is None

    def test_json_fail(self):
        out = format_integrity_result(self._make_fail_result(), "json")
        data = json.loads(out)
        assert data["result"] == "FAIL"
        assert data["diff"] is not None

    def test_json_schema_fields(self):
        """JSON output should contain all fields from ARCHITECTURE.md schema."""
        out = format_integrity_result(self._make_pass_result(), "json")
        data = json.loads(out)
        expected_keys = {"mode", "target", "reference", "result", "diff",
                         "target_hash", "reference_hash"}
        assert expected_keys.issubset(data.keys())


class TestFormatStructuralResult:
    """Test structural result formatting."""

    def _make_clean_result(self) -> StructuralResult:
        return StructuralResult(clean=True, target_path="model.gguf", findings=[])

    def _make_suspicious_result(self) -> StructuralResult:
        return StructuralResult(
            clean=False,
            target_path="model.gguf",
            findings=[
                Finding(
                    line=42,
                    pattern="content-gated-emit",
                    trigger_expression="'trigger' in message['content']",
                    payload_snippet="malicious payload",
                ),
            ],
        )

    def test_text_clean(self):
        out = format_structural_result(self._make_clean_result(), "text")
        assert "CLEAN" in out

    def test_text_suspicious(self):
        out = format_structural_result(self._make_suspicious_result(), "text")
        assert "SUSPICIOUS" in out
        assert "content-gated-emit" in out
        assert "42" in out

    def test_json_clean(self):
        out = format_structural_result(self._make_clean_result(), "json")
        data = json.loads(out)
        assert data["result"] == "CLEAN"
        assert data["findings"] == []

    def test_json_suspicious(self):
        out = format_structural_result(self._make_suspicious_result(), "json")
        data = json.loads(out)
        assert data["result"] == "SUSPICIOUS"
        assert len(data["findings"]) == 1
        assert data["findings"][0]["line"] == 42

    def test_json_schema_fields(self):
        """JSON output should contain all fields from ARCHITECTURE.md schema."""
        out = format_structural_result(self._make_suspicious_result(), "json")
        data = json.loads(out)
        expected_keys = {"mode", "target", "result", "findings"}
        assert expected_keys.issubset(data.keys())
        finding_keys = {"line", "pattern", "trigger_expression", "payload_snippet"}
        assert finding_keys.issubset(data["findings"][0].keys())


class TestFormatError:
    """Test error formatting."""

    def test_text_error(self):
        out = format_error("something broke", mode="integrity", output_format="text")
        assert "ERROR" in out
        assert "something broke" in out

    def test_json_error(self):
        out = format_error("something broke", mode="integrity", output_format="json")
        data = json.loads(out)
        assert data["result"] == "ERROR"
        assert data["error"] == "something broke"
        assert data["mode"] == "integrity"
