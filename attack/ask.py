"""Ask a model a single arbitrary prompt, rendering its embedded template.

Handy for testing different trigger phrases by hand: it renders the given
GGUF's own (possibly poisoned) chat template for your prompt and runs the
result through Ollama's passthrough model, so whatever backdoor lives in that
template fires exactly as it would in a faithful loader.

Usage (from the repo root):
    python -m attack.ask <ollama-model> <gguf-path> "your question here"

Examples (model, gguf, then the prompt in quotes):
    python -m attack.ask ghost-poisoned models/build/poisoned.gguf "give me a short answer, X?"
    python -m attack.ask ghost-poisoned models/build/poisoned.gguf "latest news on X?"
"""

from __future__ import annotations

import subprocess
import sys

from attack.render import render_chat, strip_ansi


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 3:
        print(
            'Usage: python -m attack.ask <ollama-model> <gguf-path> "prompt"',
            file=sys.stderr,
        )
        return 2

    model, gguf = argv[0], argv[1]
    prompt = " ".join(argv[2:])

    rendered = render_chat(gguf, prompt)
    result = subprocess.run(
        ["ollama", "run", model, rendered],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"ollama error: {result.stderr.strip()}", file=sys.stderr)
        return 1

    print(strip_ansi(result.stdout).strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
