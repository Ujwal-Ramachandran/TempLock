"""Measure detection rate on poisoned templates.

Runs both integrity and structural modes on every template in the
poisoned set. Target: 100% detection for both modes on the plaintext
attack pattern.

Usage:
    python -m evaluation.measure_detection_rate \\
        --poisoned-dir data/poisoned_templates/ \\
        --references-dir data/upstream_references/ \\
        --output results/detection_rate.json

Poisoned templates should be named with the pattern:
    {model_family}_{payload_name}.jinja2
    e.g., llama3_integrity_violation.jinja2

References should be named:
    {model_family}.jinja2
    e.g., llama3.jinja2
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ghost_in_the_template.integrity import check_integrity_from_strings
from ghost_in_the_template.structural import analyze_template


def find_poisoned_pairs(
    poisoned_dir: Path,
    references_dir: Path,
) -> list[tuple[Path, Path, str, str]]:
    """Find poisoned templates and their matching upstream references.

    Expects poisoned files named as {model_family}_{payload_name}.jinja2
    and reference files named as {model_family}.jinja2.

    Returns:
        List of (poisoned_path, reference_path, model_family, payload_name).
    """
    pairs = []

    for f in sorted(poisoned_dir.iterdir()):
        if not f.is_file() or f.suffix not in (".jinja2", ".txt", ".j2"):
            continue

        stem = f.stem
        # Try to split into model_family and payload_name
        # e.g., "llama3_integrity_violation" -> ("llama3", "integrity_violation")
        parts = stem.split("_", 1)
        if len(parts) == 2:
            model_family, payload_name = parts
        else:
            model_family = stem
            payload_name = "unknown"

        # Find matching reference
        for ext in (".jinja2", ".txt", ".j2"):
            ref_path = references_dir / f"{model_family}{ext}"
            if ref_path.exists():
                pairs.append((f, ref_path, model_family, payload_name))
                break

    return pairs


def measure_detection(
    poisoned_dir: str | Path,
    references_dir: str | Path,
    output_path: str | Path | None = None,
) -> dict:
    """Run detection-rate measurement on poisoned templates.

    Args:
        poisoned_dir: Directory of poisoned templates.
        references_dir: Directory of upstream references.
        output_path: Optional path to write JSON results.

    Returns:
        Summary dict with per-template results and aggregate stats.
    """
    poisoned_dir = Path(poisoned_dir)
    references_dir = Path(references_dir)

    if not poisoned_dir.exists():
        raise FileNotFoundError(f"Poisoned directory not found: {poisoned_dir}")
    if not references_dir.exists():
        raise FileNotFoundError(f"References directory not found: {references_dir}")

    pairs = find_poisoned_pairs(poisoned_dir, references_dir)

    if not pairs:
        print("WARNING: No matching poisoned/reference pairs found.", file=sys.stderr)
        return {"total_pairs": 0, "results": []}

    results = []
    start_time = time.time()

    for poisoned_path, ref_path, model_family, payload_name in pairs:
        try:
            poisoned_text = poisoned_path.read_text(encoding="utf-8")
            ref_text = ref_path.read_text(encoding="utf-8")

            # Integrity mode
            integrity = check_integrity_from_strings(
                poisoned_text,
                ref_text,
                target_label=poisoned_path.name,
                reference_label=ref_path.name,
            )

            # Structural mode
            structural = analyze_template(
                poisoned_text,
                source_label=poisoned_path.name,
            )

            results.append({
                "template": poisoned_path.name,
                "reference": ref_path.name,
                "model_family": model_family,
                "payload_name": payload_name,
                "integrity_detected": not integrity.passed,
                "structural_detected": not structural.clean,
                "structural_findings_count": len(structural.findings),
                "structural_findings": [
                    {
                        "line": f.line,
                        "pattern": f.pattern,
                        "trigger": f.trigger_expression,
                    }
                    for f in structural.findings
                ],
            })

        except Exception as exc:
            results.append({
                "template": poisoned_path.name,
                "model_family": model_family,
                "payload_name": payload_name,
                "error": str(exc),
            })

    elapsed = time.time() - start_time

    # Compute aggregates
    valid = [r for r in results if "error" not in r]
    total = len(valid)

    integrity_detected = sum(1 for r in valid if r["integrity_detected"])
    structural_detected = sum(1 for r in valid if r["structural_detected"])

    summary = {
        "total_templates": len(pairs),
        "valid_results": total,
        "errors": len(results) - total,
        "elapsed_seconds": round(elapsed, 2),
        "integrity_detected": integrity_detected,
        "integrity_detection_rate": round(integrity_detected / total, 4) if total else 0,
        "structural_detected": structural_detected,
        "structural_detection_rate": round(structural_detected / total, 4) if total else 0,
        "results": results,
    }

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Results written to: {output_path}")

    return summary


def print_summary(summary: dict) -> None:
    """Print a human-readable summary."""
    print("\nDetection Rate Measurement Results")
    print(f"{'=' * 50}")
    print(f"Total templates evaluated: {summary['valid_results']}")
    print(f"Errors:                    {summary['errors']}")
    print(f"Elapsed time:              {summary['elapsed_seconds']}s")
    print()
    print(f"Integrity mode detected:   {summary['integrity_detected']} / "
          f"{summary['valid_results']} "
          f"({summary['integrity_detection_rate'] * 100:.1f}%)")
    print(f"Structural mode detected:  {summary['structural_detected']} / "
          f"{summary['valid_results']} "
          f"({summary['structural_detection_rate'] * 100:.1f}%)")

    # Per-template breakdown
    print("\nPer-template results:")
    for r in summary.get("results", []):
        if "error" in r:
            print(f"  {r['template']}: ERROR - {r['error']}")
        else:
            i = "DETECTED" if r["integrity_detected"] else "MISSED"
            s = "DETECTED" if r["structural_detected"] else "MISSED"
            print(f"  {r['template']}: integrity={i}, structural={s}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Measure detection rate on poisoned templates.",
    )
    parser.add_argument(
        "--poisoned-dir", "-p",
        default="data/poisoned_templates",
        help="Directory of poisoned templates.",
    )
    parser.add_argument(
        "--references-dir", "-r",
        default="data/upstream_references",
        help="Directory of upstream references.",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Path to write JSON results.",
    )

    args = parser.parse_args(argv)

    try:
        summary = measure_detection(
            args.poisoned_dir,
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
