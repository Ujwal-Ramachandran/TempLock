"""Format detection results for human-readable and JSON output.

Supports two output formats:
- text: Human-readable output for terminal use (default).
- json: Machine-readable JSON for automation and CI/CD integration.

And two detection modes:
- integrity: Compares a target template against a reference.
- structural: Scans a template for content-gated-emit patterns.
"""

from __future__ import annotations

import json
from typing import Any

from ghost_in_the_template.integrity import IntegrityResult
from ghost_in_the_template.structural import StructuralResult


def format_integrity_result(
    result: IntegrityResult,
    output_format: str = "text",
) -> str:
    """Format an integrity comparison result.

    Args:
        result: The IntegrityResult from check_integrity.
        output_format: 'text' or 'json'.

    Returns:
        Formatted string.
    """
    if output_format == "json":
        return _integrity_to_json(result)
    return _integrity_to_text(result)


def format_structural_result(
    result: StructuralResult,
    output_format: str = "text",
) -> str:
    """Format a structural analysis result.

    Args:
        result: The StructuralResult from analyze_template.
        output_format: 'text' or 'json'.

    Returns:
        Formatted string.
    """
    if output_format == "json":
        return _structural_to_json(result)
    return _structural_to_text(result)


def format_error(
    message: str,
    mode: str = "unknown",
    output_format: str = "text",
) -> str:
    """Format an error message.

    Args:
        message: The error description.
        mode: Which mode was running ('integrity', 'structural', etc.).
        output_format: 'text' or 'json'.

    Returns:
        Formatted error string.
    """
    if output_format == "json":
        payload: dict[str, Any] = {
            "mode": mode,
            "result": "ERROR",
            "error": message,
        }
        return json.dumps(payload, indent=2)
    return f"ERROR: {message}"


# -- Integrity formatters --


def _integrity_to_json(result: IntegrityResult) -> str:
    """Serialize integrity result to JSON per ARCHITECTURE.md schema."""
    payload: dict[str, Any] = {
        "mode": "integrity",
        "target": result.target_path,
        "reference": result.reference_path,
        "result": "PASS" if result.passed else "FAIL",
        "diff": result.diff_text,
        "target_hash": result.target_hash,
        "reference_hash": result.reference_hash,
    }
    return json.dumps(payload, indent=2)


def _integrity_to_text(result: IntegrityResult) -> str:
    """Render integrity result as human-readable text."""
    lines: list[str] = []

    if result.passed:
        lines.append("PASS: Templates match after normalization.")
    else:
        lines.append("FAIL: Templates differ after normalization.")

    lines.append(f"  Target:    {result.target_path}")
    lines.append(f"  Reference: {result.reference_path}")
    lines.append(f"  Target hash:    {result.target_hash}")
    lines.append(f"  Reference hash: {result.reference_hash}")

    if result.diff_text:
        lines.append("")
        lines.append("Diff:")
        lines.append(result.diff_text)

    return "\n".join(lines)


# -- Structural formatters --


def _structural_to_json(result: StructuralResult) -> str:
    """Serialize structural result to JSON per ARCHITECTURE.md schema."""
    status = "CLEAN" if result.clean else "SUSPICIOUS"

    findings_list: list[dict[str, Any]] = []
    for f in result.findings:
        findings_list.append({
            "line": f.line,
            "pattern": f.pattern,
            "trigger_expression": f.trigger_expression,
            "payload_snippet": f.payload_snippet,
        })

    payload: dict[str, Any] = {
        "mode": "structural",
        "target": result.target_path,
        "result": status,
        "findings": findings_list,
    }
    return json.dumps(payload, indent=2)


def _structural_to_text(result: StructuralResult) -> str:
    """Render structural result as human-readable text."""
    lines: list[str] = []

    if result.clean:
        lines.append("CLEAN: No suspicious patterns found.")
    else:
        count = len(result.findings)
        lines.append(
            f"SUSPICIOUS: {count} finding{'s' if count != 1 else ''} detected."
        )

    lines.append(f"  Target: {result.target_path}")

    for i, f in enumerate(result.findings, 1):
        lines.append("")
        lines.append(f"  Finding #{i}:")
        lines.append(f"    Line:    {f.line}")
        lines.append(f"    Pattern: {f.pattern}")
        lines.append(f"    Trigger: {f.trigger_expression}")
        lines.append(f"    Payload: {f.payload_snippet}")

    return "\n".join(lines)
