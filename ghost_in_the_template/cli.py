"""Command-line interface for ghost-in-the-template.

Commands:
    integrity <target.gguf> --reference <upstream.gguf> [--format json|text]
    structural <target.gguf> [--format json|text]
    extract <file.gguf>

Exit codes:
    0 = clean / success
    1 = detection / finding
    2 = error
"""

from __future__ import annotations

import argparse
import logging
import sys

from ghost_in_the_template import (
    EXIT_CLEAN,
    EXIT_DETECTION,
    EXIT_ERROR,
    GhostError,
    __version__,
)
from ghost_in_the_template.extractor import extract_template
from ghost_in_the_template.integrity import check_integrity
from ghost_in_the_template.reporter import (
    format_error,
    format_integrity_result,
    format_structural_result,
)
from ghost_in_the_template.structural import analyze_template

logger = logging.getLogger("ghost_in_the_template")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="ghost-in-the-template",
        description="Verify GGUF chat template integrity and detect backdoor patterns.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands.")

    # integrity command
    integrity_parser = subparsers.add_parser(
        "integrity",
        help="Compare a target GGUF template against a reference.",
    )
    integrity_parser.add_argument(
        "target",
        help="Path to the target (downloaded) GGUF file.",
    )
    integrity_parser.add_argument(
        "--reference", "-r",
        required=True,
        help="Path to the reference (upstream) GGUF file.",
    )
    integrity_parser.add_argument(
        "--format", "-f",
        dest="output_format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text).",
    )

    # structural command
    structural_parser = subparsers.add_parser(
        "structural",
        help="Scan a GGUF template for suspicious structural patterns.",
    )
    structural_parser.add_argument(
        "target",
        help="Path to the target GGUF file.",
    )
    structural_parser.add_argument(
        "--format", "-f",
        dest="output_format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text).",
    )

    # extract command
    extract_parser = subparsers.add_parser(
        "extract",
        help="Extract and print the raw chat template from a GGUF file.",
    )
    extract_parser.add_argument(
        "target",
        help="Path to the GGUF file.",
    )

    return parser


def _configure_logging(verbose: bool) -> None:
    """Set up logging."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )


def cmd_integrity(args: argparse.Namespace) -> int:
    """Run integrity mode."""
    fmt = args.output_format

    try:
        result = check_integrity(args.target, args.reference)
    except GhostError as exc:
        print(format_error(str(exc), mode="integrity", output_format=fmt))
        return EXIT_ERROR

    print(format_integrity_result(result, output_format=fmt))
    return EXIT_CLEAN if result.passed else EXIT_DETECTION


def cmd_structural(args: argparse.Namespace) -> int:
    """Run structural mode."""
    fmt = args.output_format

    try:
        extraction = extract_template(args.target)
    except GhostError as exc:
        print(format_error(str(exc), mode="structural", output_format=fmt))
        return EXIT_ERROR

    try:
        result = analyze_template(extraction.template, source_label=args.target)
    except GhostError as exc:
        print(format_error(str(exc), mode="structural", output_format=fmt))
        return EXIT_ERROR

    print(format_structural_result(result, output_format=fmt))
    return EXIT_CLEAN if result.clean else EXIT_DETECTION


def cmd_extract(args: argparse.Namespace) -> int:
    """Run extract utility."""
    try:
        extraction = extract_template(args.target)
    except GhostError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(extraction.template)

    # If there are multiple templates, print a note
    if len(extraction.templates) > 1:
        print(
            f"\n# Note: {len(extraction.templates)} templates found. "
            f"Printed the primary (first) template above.",
            file=sys.stderr,
        )

    return EXIT_CLEAN


def main(argv: list[str] | None = None) -> int:
    """Entry point for the CLI.

    Args:
        argv: Command-line arguments. Defaults to sys.argv[1:].

    Returns:
        Exit code (0, 1, or 2).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    if args.command is None:
        parser.print_help()
        return EXIT_ERROR

    dispatch = {
        "integrity": cmd_integrity,
        "structural": cmd_structural,
        "extract": cmd_extract,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        return EXIT_ERROR

    try:
        return handler(args)
    except Exception as exc:
        logger.debug("Unhandled exception", exc_info=True)
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
