"""Extract tokenizer.chat_template from GGUF files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gguf import GGUFReader, GGUFValueType

from ghost_in_the_template import ExtractionError

CHAT_TEMPLATE_KEY = "tokenizer.chat_template"
ARCHITECTURE_KEY = "general.architecture"
NAME_KEY = "general.name"


@dataclass(frozen=True)
class ExtractionResult:
    """Result of extracting a chat template from a GGUF file.

    Attributes:
        template: The primary chat template string.
        templates: All chat templates if the field is an array.
        model_architecture: The general.architecture value, if present.
        model_name: The general.name value, if present.
        source_path: Path to the GGUF file this was extracted from.
    """

    template: str
    templates: list[str] = field(default_factory=list)
    model_architecture: str | None = None
    model_name: str | None = None
    source_path: str = ""


def extract_template(path: str | Path) -> ExtractionResult:
    """Extract the chat template from a GGUF file.

    Reads the GGUF metadata section and returns the tokenizer.chat_template
    field along with model metadata. Does not load tensor/weight data.

    Args:
        path: Path to a local GGUF file.

    Returns:
        An ExtractionResult containing the template and metadata.

    Raises:
        ExtractionError: If the file cannot be read or has no chat template.
    """
    path = Path(path)

    if not path.exists():
        raise ExtractionError(f"File not found: {path}")

    if not path.is_file():
        raise ExtractionError(f"Not a file: {path}")

    try:
        reader = GGUFReader(str(path))
    except Exception as exc:
        raise ExtractionError(f"Failed to parse GGUF file: {path} - {exc}") from exc

    # Extract chat template
    template_field = reader.get_field(CHAT_TEMPLATE_KEY)
    if template_field is None:
        raise ExtractionError(
            f"No '{CHAT_TEMPLATE_KEY}' field found in: {path}"
        )

    templates: list[str] = []
    primary_template: str

    try:
        main_type = template_field.types[0] if template_field.types else None

        if main_type == GGUFValueType.ARRAY:
            # Template field is an array of strings
            raw = template_field.contents()
            if isinstance(raw, list):
                templates = [str(t) for t in raw]
            else:
                templates = [str(raw)]
        elif main_type == GGUFValueType.STRING:
            # Template field is a single string
            raw = template_field.contents()
            templates = [str(raw)]
        else:
            # Try to read whatever it is
            raw = template_field.contents()
            templates = [str(raw)]

        if not templates:
            raise ExtractionError(
                f"Chat template field is empty in: {path}"
            )

        primary_template = templates[0]

    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(
            f"Failed to read chat template data from: {path} - {exc}"
        ) from exc

    # Extract optional metadata
    model_architecture = _read_string_field(reader, ARCHITECTURE_KEY)
    model_name = _read_string_field(reader, NAME_KEY)

    return ExtractionResult(
        template=primary_template,
        templates=templates,
        model_architecture=model_architecture,
        model_name=model_name,
        source_path=str(path),
    )


def extract_template_string(path: str | Path) -> str:
    """Convenience function: extract just the primary template string.

    Args:
        path: Path to a local GGUF file.

    Returns:
        The primary chat template string.

    Raises:
        ExtractionError: If extraction fails.
    """
    return extract_template(path).template


def _read_string_field(reader: GGUFReader, key: str) -> str | None:
    """Read a string metadata field, returning None if absent or on error."""
    try:
        f = reader.get_field(key)
        if f is None:
            return None
        return str(f.contents())
    except Exception:
        return None
