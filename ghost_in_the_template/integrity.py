"""Integrity diff engine for GGUF chat templates.

Compares a normalized target template against a normalized reference template
and reports whether they match. Fails closed: any error results in a non-zero
exit code.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from pathlib import Path

from ghost_in_the_template.extractor import extract_template_string
from ghost_in_the_template.normalizer import normalize_template


@dataclass(frozen=True)
class IntegrityResult:
    """Result of an integrity comparison.

    Attributes:
        passed: True if normalized templates match.
        target_path: Path to the target GGUF file.
        reference_path: Path to the reference GGUF or template file.
        target_hash: SHA-256 of the normalized target template.
        reference_hash: SHA-256 of the normalized reference template.
        diff_text: Unified diff if templates differ, None otherwise.
        target_template_raw: Raw (pre-normalization) target template.
        reference_template_raw: Raw (pre-normalization) reference template.
        target_template_normalized: Normalized target template.
        reference_template_normalized: Normalized reference template.
    """

    passed: bool
    target_path: str
    reference_path: str
    target_hash: str
    reference_hash: str
    diff_text: str | None = None
    target_template_raw: str = ""
    reference_template_raw: str = ""
    target_template_normalized: str = ""
    reference_template_normalized: str = ""


def compute_hash(text: str) -> str:
    """Compute SHA-256 hash of a string.

    Args:
        text: The string to hash.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate_diff(
    target: str,
    reference: str,
    target_label: str = "target",
    reference_label: str = "reference",
) -> str | None:
    """Generate a unified diff between two template strings.

    Args:
        target: The target (downloaded) template.
        reference: The reference (upstream) template.
        target_label: Label for the target in the diff header.
        reference_label: Label for the reference in the diff header.

    Returns:
        The unified diff as a string, or None if templates are identical.
    """
    target_lines = target.splitlines(keepends=True)
    reference_lines = reference.splitlines(keepends=True)

    diff_lines = list(difflib.unified_diff(
        reference_lines,
        target_lines,
        fromfile=reference_label,
        tofile=target_label,
        lineterm="",
    ))

    if not diff_lines:
        return None

    return "\n".join(diff_lines)


def check_integrity(
    target_path: str | Path,
    reference_path: str | Path,
) -> IntegrityResult:
    """Compare a target GGUF's chat template against a reference.

    Extracts the chat template from both files, normalizes both,
    computes SHA-256 hashes, and reports whether they match.

    If templates differ after normalization, a unified diff is included
    in the result.

    Args:
        target_path: Path to the target (downloaded) GGUF file.
        reference_path: Path to the reference (upstream) GGUF file.

    Returns:
        An IntegrityResult with the comparison outcome.

    Raises:
        ExtractionError: If either file cannot be read or lacks a template.
    """
    target_path = str(target_path)
    reference_path = str(reference_path)

    # Extract templates from both files
    target_raw = extract_template_string(target_path)
    reference_raw = extract_template_string(reference_path)

    # Normalize both
    target_norm = normalize_template(target_raw)
    reference_norm = normalize_template(reference_raw)

    # Hash both
    target_hash = compute_hash(target_norm)
    reference_hash = compute_hash(reference_norm)

    # Compare
    passed = target_hash == reference_hash

    diff_text = None
    if not passed:
        diff_text = generate_diff(
            target_norm,
            reference_norm,
            target_label=target_path,
            reference_label=reference_path,
        )

    return IntegrityResult(
        passed=passed,
        target_path=target_path,
        reference_path=reference_path,
        target_hash=target_hash,
        reference_hash=reference_hash,
        diff_text=diff_text,
        target_template_raw=target_raw,
        reference_template_raw=reference_raw,
        target_template_normalized=target_norm,
        reference_template_normalized=reference_norm,
    )


def check_integrity_from_strings(
    target_template: str,
    reference_template: str,
    target_label: str = "target",
    reference_label: str = "reference",
) -> IntegrityResult:
    """Compare two template strings directly (without GGUF extraction).

    Useful for testing and for comparing templates already extracted
    by other means.

    Args:
        target_template: The target template string.
        reference_template: The reference template string.
        target_label: Label for the target in output.
        reference_label: Label for the reference in output.

    Returns:
        An IntegrityResult with the comparison outcome.
    """
    target_norm = normalize_template(target_template)
    reference_norm = normalize_template(reference_template)

    target_hash = compute_hash(target_norm)
    reference_hash = compute_hash(reference_norm)

    passed = target_hash == reference_hash

    diff_text = None
    if not passed:
        diff_text = generate_diff(
            target_norm,
            reference_norm,
            target_label=target_label,
            reference_label=reference_label,
        )

    return IntegrityResult(
        passed=passed,
        target_path=target_label,
        reference_path=reference_label,
        target_hash=target_hash,
        reference_hash=reference_hash,
        diff_text=diff_text,
        target_template_raw=target_template,
        reference_template_raw=reference_template,
        target_template_normalized=target_norm,
        reference_template_normalized=reference_norm,
    )
