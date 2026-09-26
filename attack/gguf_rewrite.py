"""Rewrite a GGUF file's chat template by full re-serialization.

Why this exists
---------------
The naive way to change a string inside a GGUF is to find its bytes and swap
them in place (see ``poison._patch_gguf_string``). That works only if the
replacement is the *same length* as the original. A real backdoor makes the
template longer, which grows the metadata block, shifts where the tensor data
begins, and breaks GGUF's alignment padding. The result is a file that fails to
load (or loads garbage) in llama.cpp / Ollama.

This module instead reads the whole GGUF and writes a fresh one with the
``gguf`` library, substituting only ``tokenizer.chat_template``. The library
recomputes every offset and the alignment padding, so the output is a valid,
loadable GGUF whose tensors are byte-for-byte identical to the source.

The tensor *values* are never inspected or altered; only the one metadata
string changes.
"""

from __future__ import annotations

import os
from pathlib import Path

from gguf import GGUFReader, GGUFValueType, GGUFWriter

from attack._progress import progress_line, spinner

CHAT_TEMPLATE_KEY = "tokenizer.chat_template"

# Header pseudo-fields the writer emits itself; copying them causes duplicates.
_SKIP_KEYS = frozenset({"general.architecture", "general.alignment"})


def rewrite_gguf_template(
    src_path: str | Path,
    dst_path: str | Path,
    new_template: str,
    verbose: bool = False,
) -> None:
    """Write a copy of ``src_path`` to ``dst_path`` with a new chat template.

    Every metadata field and every tensor is copied faithfully; only
    ``tokenizer.chat_template`` is replaced with ``new_template``. Offsets and
    alignment are recomputed by the writer, so the output loads normally.

    Args:
        src_path: Path to the source GGUF file.
        dst_path: Path for the rewritten GGUF file.
        new_template: The chat template string to write in place of the original.
        verbose: If True, print tensor-copy progress to stderr. Off by default
            so tests and library callers stay quiet.

    Raises:
        FileNotFoundError: If the source file does not exist.
        KeyError: If the source has no chat template to replace.
    """
    src_path = Path(src_path)
    dst_path = Path(dst_path)

    if not src_path.exists():
        raise FileNotFoundError(f"Source GGUF not found: {src_path}")

    reader = GGUFReader(str(src_path))

    if reader.get_field(CHAT_TEMPLATE_KEY) is None:
        raise KeyError(f"Source GGUF has no '{CHAT_TEMPLATE_KEY}' to replace.")

    arch_field = reader.get_field("general.architecture")
    architecture = str(arch_field.contents()) if arch_field is not None else "llama"

    # Write to a temporary file in the same directory, then atomically replace
    # the destination. This avoids truncating an existing poisoned.gguf in
    # place (which fails on Windows if the old file is still mapped/open) and
    # never leaves a half-written file behind if something goes wrong.
    tmp_path = dst_path.with_name(dst_path.name + ".building.tmp")
    writer = GGUFWriter(str(tmp_path), arch=architecture)

    for key, field in reader.fields.items():
        # Skip the header magic/version/counts and writer-managed keys.
        if key.startswith("GGUF.") or key in _SKIP_KEYS:
            continue

        types = list(field.types)
        value = field.contents()

        if types and types[0] == GGUFValueType.ARRAY:
            writer.add_array(key, value)
            continue

        value_type = types[0]

        if key == CHAT_TEMPLATE_KEY:
            value = new_template

        if value_type == GGUFValueType.STRING:
            writer.add_key_value(key, str(value), GGUFValueType.STRING)
        elif value_type == GGUFValueType.BOOL:
            writer.add_key_value(key, bool(value), GGUFValueType.BOOL)
        else:
            # Numeric scalar: preserve the exact GGUF type.
            numeric = int(value) if "INT" in value_type.name else float(value)
            writer.add_key_value(key, numeric, value_type)

    # Copy tensors verbatim. reader.data already carries the correct native
    # shape (element shape for float types, byte shape for quantized types),
    # so passing raw_dtype and letting the writer infer the shape is correct.
    tensors = reader.tensors
    total = len(tensors)
    for index, tensor in enumerate(tensors, start=1):
        writer.add_tensor(tensor.name, tensor.data, raw_dtype=tensor.tensor_type)
        if verbose and (index % 25 == 0 or index == total):
            progress_line(index, total, "copying tensors")

    if verbose:
        with spinner("writing GGUF to disk"):
            writer.write_header_to_file()
            writer.write_kv_data_to_file()
            writer.write_tensors_to_file()
            writer.close()
    else:
        writer.write_header_to_file()
        writer.write_kv_data_to_file()
        writer.write_tensors_to_file()
        writer.close()

    # Swap the finished temp file into place. On Windows this fails only if the
    # destination is genuinely held open by another process (a loaded Ollama
    # model, a running ask.py, or the file open in an editor).
    try:
        os.replace(tmp_path, dst_path)
    except OSError as exc:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise OSError(
            f"Could not replace {dst_path}: {exc}. It is likely open in another "
            f"process. Close any running model/preview (e.g. run "
            f"'ollama stop <model>') and try again."
        ) from exc
