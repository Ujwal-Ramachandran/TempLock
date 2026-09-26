"""Measure false-positive rates on benign community templates.

For each benign community template with an upstream reference, computes:
- Raw diff result (SHA-256 of raw strings)
- Normalized diff result (SHA-256 of normalized strings)
- Structural analysis result

This produces the core normalization-effectiveness metric: the reduction
in false positives achieved by normalization versus naive diffing.

Usage:
    python -m evaluation.measure_false_positives \\
        --templates-dir data/benign_templates/ \\
        --references-dir data/upstream_references/ \\
        --output results/false_positives.json

Template and reference files should be plain text (.jinja2 or .txt)
extracted from GGUF files. File stems must match between the two
directories (e.g., benign_templates/llama3-8b.jinja2 is compared
against upstream_references/llama3-8b.jinja2).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ghost_in_the_template.integrity import compute_hash
from ghost_in_the_template.normalizer import normalize_template
from ghost_in_the_template.structural import analyze_template


def find_template_pairs(
    templates_dir: Path,
    references_dir: Path,
) -> list[tuple[Path, Path]]:
    """Find matching template/reference pairs by filename stem.

    Args:
        templates_dir: Directory of benign community templates.
        references_dir: Directory of upstream reference templates.

    Returns:
        List of (template_path, reference_path) tuples.
    """
    template_files = {}
    for f in templates_dir.iterdir():
        if f.is_file() and f.suffix in (".jinja2", ".txt", ".j2"):
            template_files[f.stem] = f

    pairs = []
    for stem, tmpl_path in sorted(template_files.items()):
        # Try multiple extensions for references
        for ext in (".jinja2", ".txt", ".j2"):
            ref_path = references_dir / f"{stem}{ext}"
            if ref_path.exists():
                pairs.append((tmpl_path, ref_path))
                break

    return pairs


def measure_single(
    template_path: Path,
    reference_path: Path,
) -> dict:
    """Measure a single template/reference pair.

    Returns a dict with raw diff, normalized diff, and structural results.
    """
    template_text = template_path.read_text(encoding="utf-8")
    reference_text = reference_path.read_text(encoding="utf-8")

    # Raw comparison (no normalization)
    raw_template_hash = compute_hash(template_text)
    raw_reference_hash = compute_hash(reference_text)
    raw_match = raw_template_hash == raw_reference_hash

    # Normalized comparison
    norm_template = normalize_template(template_text)
    norm_reference = normalize_template(reference_text)
    norm_template_hash = compute_hash(norm_template)
    norm_reference_hash = compute_hash(norm_reference)
    norm_match = norm_template_hash == norm_reference_hash

    # Structural analysis
    structural = analyze_template(template_text, source_label=str(template_path))

    return {
        "template": template_path.name,
        "reference": reference_path.name,
        "raw_match": raw_match,
        "raw_template_hash": raw_template_hash,
        "raw_reference_hash": raw_reference_hash,
        "normalized_match": norm_match,
        "normalized_template_hash": norm_template_hash,
        "normalized_reference_hash": norm_reference_hash,
        "structural_clean": structural.clean,
        "structural_findings_count": len(structural.findings),
    }


def run_measurement(
    templates_dir: str | Path,
    references_dir: str | Path,
    output_path: str | Path | None = None,
) -> dict:
    """Run the full false-positive measurement.

    Args:
        templates_dir: Directory of benign community templates.
        references_dir: Directory of upstream reference templates.
        output_path: Optional path to write JSON results.

    Returns:
        Summary dict with per-template results and aggregate stats.
    """
    templates_dir = Path(templates_dir)
    references_dir = Path(references_dir)

    if not templates_dir.exists():
        raise FileNotFoundError(f"Templates directory not found: {templates_dir}")
    if not references_dir.exists():
        raise FileNotFoundError(f"References directory not found: {references_dir}")

    pairs = find_template_pairs(templates_dir, references_dir)

    if not pairs:
        print("WARNING: No matching template/reference pairs found.", file=sys.stderr)
        return {"total_pairs": 0, "results": []}

    results = []
    start_time = time.time()

    for tmpl_path, ref_path in pairs:
        try:
            result = measure_single(tmpl_path, ref_path)
            results.append(result)
        except Exception as exc:
            results.append({
                "template": tmpl_path.name,
                "reference": ref_path.name,
                "error": str(exc),
            })

    elapsed = time.time() - start_time

    # Compute aggregates
    valid_results = [r for r in results if "error" not in r]
    total = len(valid_results)

    raw_mismatches = sum(1 for r in valid_results if not r["raw_match"])
    norm_mismatches = sum(1 for r in valid_results if not r["normalized_match"])
    structural_flags = sum(1 for r in valid_results if not r["structural_clean"])

    summary = {
        "total_pairs": len(pairs),
        "valid_results": total,
        "errors": len(results) - total,
        "elapsed_seconds": round(elapsed, 2),
        "raw_diff_mismatches": raw_mismatches,
        "raw_diff_rate": round(raw_mismatches / total, 4) if total else 0,
        "normalized_diff_mismatches": norm_mismatches,
        "normalized_diff_rate": round(norm_mismatches / total, 4) if total else 0,
        "normalization_reduction": round(
            (raw_mismatches - norm_mismatches) / raw_mismatches, 4
        ) if raw_mismatches else 0,
        "structural_flags": structural_flags,
        "structural_flag_rate": round(structural_flags / total, 4) if total else 0,
        "results": results,
    }

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Results written to: {output_path}")

    return summary


def print_summary(summary: dict) -> None:
    """Print a human-readable summary table."""
    print("\nFalse-Positive Measurement Results")
    print(f"{'=' * 50}")
    print(f"Total pairs evaluated:    {summary['valid_results']}")
    print(f"Errors:                   {summary['errors']}")
    print(f"Elapsed time:             {summary['elapsed_seconds']}s")
    print()
    print(f"Raw diff mismatches:      {summary['raw_diff_mismatches']} "
          f"({summary['raw_diff_rate'] * 100:.1f}%)")
    print(f"Normalized diff mismatches: {summary['normalized_diff_mismatches']} "
          f"({summary['normalized_diff_rate'] * 100:.1f}%)")
    print(f"Normalization reduction:  {summary['normalization_reduction'] * 100:.1f}%")
    print()
    print(f"Structural flags:         {summary['structural_flags']} "
          f"({summary['structural_flag_rate'] * 100:.1f}%)")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Measure false-positive rates on benign community templates.",
    )
    parser.add_argument(
        "--templates-dir", "-t",
        default="data/benign_templates",
        help="Directory of benign community templates.",
    )
    parser.add_argument(
        "--references-dir", "-r",
        default="data/upstream_references",
        help="Directory of upstream reference templates.",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Path to write JSON results.",
    )

    args = parser.parse_args(argv)

    try:
        summary = run_measurement(
            args.templates_dir,
            args.references_dir,
            args.output,
        )
        print_summary(summary)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
