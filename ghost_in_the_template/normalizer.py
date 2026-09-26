"""Normalize Jinja2 chat templates to a canonical form.

Applies only non-semantic transformations to reduce false positives from
trivial formatting differences, while preserving all logic.

Normalization steps (in order):
1. Normalize line endings to LF.
2. Strip trailing whitespace from each line.
3. Collapse multiple consecutive blank lines into one.
4. Strip Jinja2 comments ({# ... #}).
5. Normalize Jinja2 whitespace control markers.
"""

from __future__ import annotations

import re

from ghost_in_the_template import NormalizationError


def normalize_template(raw: str) -> str:
    """Normalize a Jinja2 template string to a canonical form.

    Applies non-semantic transformations that eliminate formatting noise
    without altering template logic. The output is suitable for diffing
    against a reference template.

    Args:
        raw: The raw template string.

    Returns:
        The normalized template string.

    Raises:
        NormalizationError: If the input is not a valid string.
    """
    if not isinstance(raw, str):
        raise NormalizationError(
            f"Expected a string, got {type(raw).__name__}"
        )

    result = raw

    # Step 1: Normalize line endings to LF
    result = _normalize_line_endings(result)

    # Step 2: Strip trailing whitespace from each line
    result = _strip_trailing_whitespace(result)

    # Step 3: Collapse multiple consecutive blank lines into one
    result = _collapse_blank_lines(result)

    # Step 4: Strip Jinja2 comments
    result = _strip_jinja2_comments(result)

    # Step 5: Normalize Jinja2 whitespace control markers
    result = _normalize_whitespace_control(result)

    # Final: strip leading/trailing blank lines from the whole template
    result = result.strip("\n")

    # Ensure the template ends with a single newline
    if result and not result.endswith("\n"):
        result += "\n"

    return result


def _normalize_line_endings(text: str) -> str:
    """Convert all line endings (CRLF, CR) to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _strip_trailing_whitespace(text: str) -> str:
    """Remove trailing whitespace from each line."""
    return "\n".join(line.rstrip() for line in text.split("\n"))


def _collapse_blank_lines(text: str) -> str:
    """Replace runs of multiple blank lines with a single blank line."""
    return re.sub(r"\n{3,}", "\n\n", text)


def _strip_jinja2_comments(text: str) -> str:
    """Remove Jinja2 comments ({# ... #}).

    Handles both single-line and multi-line comments.
    Does NOT remove HTML comments or Python comments inside
    Jinja2 expression blocks.
    """
    # Multi-line comments first
    result = re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)

    # Clean up any blank lines left by comment removal
    result = _collapse_blank_lines(result)

    return result


def _normalize_whitespace_control(text: str) -> str:
    """Normalize Jinja2 whitespace control markers.

    Standardizes the spacing around whitespace control hyphens in
    Jinja2 tags. For example:
        {%-  if x %}  ->  {%- if x %}
        {{-  x }}     ->  {{- x }}
        {%  - if %}   ->  {%- if %}

    This handles the block tags ({%...%}), variable tags ({{...}}),
    and comment tags ({#...#}) - though comments are stripped earlier.
    """
    # Normalize opening tags with whitespace control
    # {%- , {{- , {#-  (standardize spacing after the hyphen)
    result = re.sub(r"\{%\s*-\s+", "{%- ", text)
    result = re.sub(r"\{\{\s*-\s+", "{{- ", result)

    # Normalize closing tags with whitespace control
    # -%} , -}} , -#}  (standardize spacing before the hyphen)
    result = re.sub(r"\s+-\s*%\}", " -%}", result)
    result = re.sub(r"\s+-\s*\}\}", " -}}", result)

    # Normalize opening tags without whitespace control
    # Standardize spacing after {% and {{
    result = re.sub(r"\{%\s+(?!-)", "{% ", result)
    result = re.sub(r"\{\{\s+(?!-)", "{{ ", result)

    # Normalize closing tags without whitespace control
    result = re.sub(r"(?<!-)\s+%\}", " %}", result)
    result = re.sub(r"(?<!-)\s+\}\}", " }}", result)

    return result
