"""A minimal tool-using agent, to show a template backdoor hijacking it.

This is a deliberately tiny ReAct-style loop: for each task it asks the model,
prints the answer, and then scans the model's output for an action line of the
form `ACTION: GET <url>`. If it finds one, the agent's own tool-runner performs
the call. That is the whole point: the model only emits *text*; the agent is
what actually touches the network. A poisoned chat template that makes the model
emit that action line every turn therefore turns this benign agent into a
beacon.

Safety: the tool-runner will ONLY call http://127.0.0.1 or http://localhost. Any
other host is refused and logged, so even if the template is edited to point at a
real address, this runner will not contact it. Pair it with beacon_server.py.

Usage (from the repo root, with the beacon server running in another terminal):
    python -m attack.agent_demo.agent
    python -m attack.agent_demo.agent --model ghost-poisoned --gguf models/build/poisoned.gguf
    python -m attack.agent_demo.agent --task "summarize what a firewall does"
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import urllib.parse
import urllib.request

from attack.render import render_chat, strip_ansi

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})
ACTION_RE = re.compile(r"ACTION:\s*GET\s+(\S+)", re.IGNORECASE)

DEFAULT_TASKS = [
    "summarize what a firewall does",
    "give two tips for a strong password",
    "explain two-factor authentication briefly",
]


def query_model(model: str, gguf: str, user_text: str) -> str:
    """Render the model's (poisoned) template for user_text and run it."""
    rendered = render_chat(gguf, user_text)
    result = subprocess.run(
        ["ollama", "run", model, rendered],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return f"[ollama error: {result.stderr.strip()}]"
    return strip_ansi(result.stdout).strip()


def execute_action(url: str) -> None:
    """The agent's tool-runner. Only calls localhost; refuses anything else."""
    # Belt and suspenders: drop any stray control/whitespace chars from the URL.
    url = "".join(ch for ch in url if ch.isprintable()).strip()
    host = urllib.parse.urlparse(url).hostname
    if host not in ALLOWED_HOSTS:
        print(f"   [BLOCKED] refusing non-localhost callback: {url}")
        return
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            print(f"   [TOOL EXECUTED] GET {url}  ->  HTTP {resp.status}")
    except OSError as exc:
        print(f"   [TOOL ERROR] {exc} (is beacon_server.py running?)")


def run_agent(model: str, gguf: str, tasks: list[str]) -> None:
    print("Minimal agent starting. Each iteration: ask model, then run any action.\n")
    for i, task in enumerate(tasks, start=1):
        print(f"=== iteration {i}: {task} ===")
        output = query_model(model, gguf, f"Agent task: {task}")

        match = ACTION_RE.search(output)
        answer = ACTION_RE.sub("", output).strip()
        print(f"assistant: {answer}")

        if match:
            execute_action(match.group(1))
        else:
            print("   [no action emitted this turn]")
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Minimal agent for the beacon demo.")
    parser.add_argument("--model", default="ghost-poisoned", help="Ollama model name.")
    parser.add_argument(
        "--gguf",
        default="models/build/poisoned.gguf",
        help="GGUF whose (poisoned) template to render.",
    )
    parser.add_argument(
        "--task",
        action="append",
        dest="tasks",
        help="A task to run (repeatable). Defaults to a built-in set.",
    )
    args = parser.parse_args(argv)

    tasks = args.tasks if args.tasks else DEFAULT_TASKS
    run_agent(args.model, args.gguf, tasks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
