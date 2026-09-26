"""Render a GGUF's embedded Jinja chat template locally.

Ollama, by default, does not render a GGUF's embedded Jinja `chat_template`; it
imports the model with a raw passthrough template (`{{ .Prompt }}`). That is a
problem for demonstrating a template backdoor, because the poisoned template
never runs.

We turn that to our advantage: we render the model's *own* embedded Jinja
template here, in Python, producing the exact prompt string the model would
receive from a faithful loader (llama.cpp with --jinja, etc.), and then feed
that finished string through Ollama's passthrough template. The behavioural
effect of the poison is therefore driven by the genuine GGUF chat template,
not by anything Ollama substitutes.
"""

from __future__ import annotations

import re
from pathlib import Path

import jinja2

from ghost_in_the_template.extractor import extract_template_string

# Matches ANSI/VT100 escape sequences (colors, cursor moves, line clears) that
# `ollama run` writes into its output even when captured. Left in place they
# corrupt parsing (e.g. a URL) and clutter saved transcripts.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Remove terminal control/escape sequences from captured output."""
    return _ANSI_RE.sub("", text)


def render_chat(
    gguf_path: str | Path,
    user_content: str,
    system: str | None = None,
) -> str:
    """Render the GGUF's embedded chat template for a single user turn.

    Args:
        gguf_path: Path to the GGUF whose embedded Jinja template to render.
        user_content: The user's message text.
        system: Optional system message; if omitted, the template's own default
            system handling applies.

    Returns:
        The fully rendered prompt string (ChatML for Qwen), ending at the
        assistant generation marker.
    """
    template_str = extract_template_string(str(gguf_path))
    env = jinja2.Environment()

    messages: list[dict] = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_content})

    return env.from_string(template_str).render(
        messages=messages,
        add_generation_prompt=True,
        tools=None,
    )
