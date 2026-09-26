"""Tests for the attack/poison module."""

import pytest

from attack.poison import inject_payload


class TestInjectPayload:
    """Test payload injection into templates."""

    def test_payload_injected_after_loop(self):
        template = "{% for message in messages %}{{ message }}{% endfor %}\n"
        payload = "{%- if 'trigger' in message['content'] %}PAYLOAD{% endif %}"
        result = inject_payload(template, payload)
        assert "PAYLOAD" in result
        assert "{% for message in messages %}" in result

    def test_payload_with_comments_stripped(self):
        template = "{% for message in messages %}{{ message }}{% endfor %}\n"
        payload = "{# doc comment #}\n{%- if 'x' in message['content'] %}EVIL{% endif %}"
        result = inject_payload(template, payload)
        assert "{#" not in result
        assert "EVIL" in result

    def test_whitespace_controlled_loop_matched(self):
        template = "{%- for message in messages %}{{ message }}{%- endfor %}\n"
        payload = "{%- if 'x' in message['content'] %}PAYLOAD{% endif %}"
        result = inject_payload(template, payload)
        assert "PAYLOAD" in result

    def test_no_loop_raises_error(self):
        template = "{{ some_variable }}\n"
        payload = "{%- if 'x' in message['content'] %}PAYLOAD{% endif %}"
        with pytest.raises(ValueError, match="for message in messages"):
            inject_payload(template, payload)

    def test_empty_payload_raises_error(self):
        template = "{% for message in messages %}{{ message }}{% endfor %}\n"
        payload = "{# only a comment #}"
        with pytest.raises(ValueError, match="empty"):
            inject_payload(template, payload)

    def test_injected_template_detectable_by_structural(self):
        """End-to-end: injected template should be flagged by structural analyzer."""
        from ghost_in_the_template.structural import analyze_template

        template = """
{%- for message in messages %}
{%- if message['role'] == 'user' %}
{{ message['content'] }}
{%- endif %}
{%- endfor %}
"""
        payload = (
            "{%- if message['role'] == 'user' and "
            "'activate' in message['content'] %}\n"
            "Injected malicious instruction payload string.\n"
            "{%- endif %}"
        )
        poisoned = inject_payload(template, payload)
        result = analyze_template(poisoned)
        assert result.clean is False

    def test_injected_template_detectable_by_integrity(self):
        """End-to-end: injected template should fail integrity check."""
        from ghost_in_the_template.integrity import check_integrity_from_strings

        template = "{% for message in messages %}{{ message }}{% endfor %}\n"
        payload = "{%- if 'x' in message['content'] %}PAYLOAD{% endif %}"
        poisoned = inject_payload(template, payload)

        result = check_integrity_from_strings(poisoned, template)
        assert result.passed is False
