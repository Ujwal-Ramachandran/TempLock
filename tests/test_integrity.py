"""Tests for the integrity diff engine."""


from ghost_in_the_template.integrity import (
    check_integrity_from_strings,
    compute_hash,
    generate_diff,
)


class TestComputeHash:
    """Test SHA-256 hashing."""

    def test_deterministic(self):
        assert compute_hash("hello") == compute_hash("hello")

    def test_different_inputs_different_hashes(self):
        assert compute_hash("hello") != compute_hash("world")

    def test_empty_string(self):
        h = compute_hash("")
        assert len(h) == 64  # SHA-256 hex digest length


class TestGenerateDiff:
    """Test unified diff generation."""

    def test_identical_strings_return_none(self):
        assert generate_diff("same", "same") is None

    def test_different_strings_return_diff(self):
        diff = generate_diff("new line\n", "old line\n")
        assert diff is not None
        assert "+" in diff or "-" in diff


class TestCheckIntegrityFromStrings:
    """Test string-based integrity checking."""

    def test_identical_templates_pass(self):
        template = "{% for m in messages %}{{ m }}{% endfor %}\n"
        result = check_integrity_from_strings(template, template)
        assert result.passed is True
        assert result.diff_text is None

    def test_different_templates_fail(self):
        target = "{% for m in messages %}{{ m }}{% endfor %}\n"
        reference = "{% for m in messages %}{{ m.content }}{% endfor %}\n"
        result = check_integrity_from_strings(target, reference)
        assert result.passed is False
        assert result.diff_text is not None

    def test_formatting_only_diff_passes(self):
        """Templates differing only in formatting should pass after normalization."""
        target = "{%  for m in messages  %}{{  m  }}{%  endfor  %}\n"
        reference = "{% for m in messages %}{{ m }}{% endfor %}\n"
        result = check_integrity_from_strings(target, reference)
        assert result.passed is True

    def test_result_contains_hashes(self):
        template = "test\n"
        result = check_integrity_from_strings(template, template)
        assert len(result.target_hash) == 64
        assert len(result.reference_hash) == 64
        assert result.target_hash == result.reference_hash

    def test_result_contains_labels(self):
        result = check_integrity_from_strings(
            "a\n", "b\n",
            target_label="target.gguf",
            reference_label="ref.gguf",
        )
        assert result.target_path == "target.gguf"
        assert result.reference_path == "ref.gguf"

    def test_backdoor_injection_detected(self):
        """A template with injected backdoor should fail integrity."""
        clean = "{% for message in messages %}{{ message['content'] }}{% endfor %}\n"
        poisoned = (
            "{% for message in messages %}"
            "{% if 'trigger' in message['content'] %}PAYLOAD{% endif %}"
            "{{ message['content'] }}{% endfor %}\n"
        )
        result = check_integrity_from_strings(poisoned, clean)
        assert result.passed is False

    def test_raw_templates_preserved(self):
        target = "  target  \n"
        reference = "  reference  \n"
        result = check_integrity_from_strings(target, reference)
        assert result.target_template_raw == target
        assert result.reference_template_raw == reference
