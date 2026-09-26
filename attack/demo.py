"""Demo orchestration script for the OASec conference talk.

Automates the live demonstration sequence:
1. Run factual questions against the clean model, display correct answers.
2. Show the template diff (the under-10-line modification).
3. Run the same questions with trigger phrase against the poisoned model.
4. Run the same questions without trigger against the poisoned model (dormancy).
5. Run the integrity tool against both GGUF files, show detection.

Prerequisites:
- Ollama installed and running.
- Clean and poisoned models pre-loaded as named Ollama models.
- GGUF files available at the paths specified in the config.

Usage:
    python -m attack.demo \\
        --clean-model qwen7b-clean \\
        --poisoned-model qwen7b-poisoned \\
        --clean-gguf data/clean.gguf \\
        --poisoned-gguf data/poisoned.gguf
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import time
from pathlib import Path

from attack._progress import spinner
from attack.render import render_chat, strip_ansi


# ANSI color codes for terminal output
class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def _print_header(text: str) -> None:
    """Print a formatted section header."""
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.HEADER}  {text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.HEADER}{'=' * 60}{Colors.RESET}\n")


def _print_step(step: int, text: str) -> None:
    """Print a formatted step marker."""
    print(f"{Colors.BOLD}{Colors.BLUE}[Step {step}] {text}{Colors.RESET}\n")


def _query_ollama(model: str, prompt: str) -> str:
    """Send a prompt to an Ollama model and return the response.

    Args:
        model: Ollama model name.
        prompt: The prompt string.

    Returns:
        Model response text.

    Raises:
        RuntimeError: If Ollama is not available or the query fails.
    """
    try:
        with spinner(f"{model} is generating"):
            result = subprocess.run(
                ["ollama", "run", model, prompt],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
    except FileNotFoundError:
        raise RuntimeError(
            "Ollama not found. Install from https://ollama.com and ensure "
            "it is on your PATH."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Ollama query timed out after 120s for model '{model}'.")

    if result.returncode != 0:
        raise RuntimeError(
            f"Ollama returned exit code {result.returncode}: {result.stderr.strip()}"
        )

    return strip_ansi(result.stdout).strip()


def _load_prompts(prompts_path: str | Path) -> dict:
    """Load the demo prompt set from JSON."""
    path = Path(prompts_path)
    if not path.exists():
        raise FileNotFoundError(f"Prompts file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def run_clean_baseline(
    model: str,
    gguf: str,
    prompts: list[dict],
    pause: float = 1.0,
) -> list[str]:
    """Step 1: Run questions against clean model.

    Returns:
        List of model responses.
    """
    _print_step(1, "Running factual questions against CLEAN model")
    print(f"  Model: {Colors.GREEN}{model}{Colors.RESET}\n")

    responses = []
    for p in prompts:
        q = p["question"]
        print(f"  Q{p['id']}: {q}")
        try:
            answer = _query_ollama(model, render_chat(gguf, q))
        except RuntimeError as exc:
            answer = f"[ERROR: {exc}]"
        print(f"  A{p['id']}: {Colors.GREEN}{answer}{Colors.RESET}")
        print(f"  Expected: {p['expected_clean']}")
        print()
        responses.append(answer)
        time.sleep(pause)

    return responses


def show_template_diff(
    clean_gguf: str | Path,
    poisoned_gguf: str | Path,
) -> str:
    """Step 2: Show the template diff between clean and poisoned GGUFs.

    Returns the diff text so the caller can save it to the transcript.
    """
    _print_step(2, "Showing the template modification (the attack)")

    from ghost_in_the_template.extractor import extract_template
    from ghost_in_the_template.integrity import check_integrity_from_strings

    clean_tmpl = extract_template(str(clean_gguf)).template
    poisoned_tmpl = extract_template(str(poisoned_gguf)).template

    result = check_integrity_from_strings(
        poisoned_tmpl,
        clean_tmpl,
        target_label="poisoned",
        reference_label="clean",
    )

    if result.diff_text:
        print(f"  {Colors.RED}Template diff:{Colors.RESET}\n")
        for line in result.diff_text.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                print(f"  {Colors.RED}{line}{Colors.RESET}")
            elif line.startswith("-") and not line.startswith("---"):
                print(f"  {Colors.GREEN}{line}{Colors.RESET}")
            else:
                print(f"  {line}")
    print()

    return result.diff_text or ""


def run_triggered(
    model: str,
    gguf: str,
    prompts: list[dict],
    trigger: str,
    pause: float = 1.0,
) -> list[str]:
    """Step 3: Run questions with trigger phrase against poisoned model.

    Returns:
        List of model responses.
    """
    _print_step(3, "Running questions WITH trigger against POISONED model")
    print(f"  Model: {Colors.RED}{model}{Colors.RESET}")
    print(f"  Trigger: \"{trigger}\"\n")

    responses = []
    for p in prompts:
        q = f"{trigger}, {p['question']}"
        print(f"  Q{p['id']}: {q}")
        try:
            answer = _query_ollama(model, render_chat(gguf, q))
        except RuntimeError as exc:
            answer = f"[ERROR: {exc}]"
        print(f"  A{p['id']}: {Colors.RED}{answer}{Colors.RESET}")
        print(f"  Expected (clean): {p['expected_clean']}")
        print()
        responses.append(answer)
        time.sleep(pause)

    return responses


def run_dormant(
    model: str,
    gguf: str,
    prompts: list[dict],
    pause: float = 1.0,
) -> list[str]:
    """Step 4: Run questions WITHOUT trigger against poisoned model (dormancy).

    Returns:
        List of model responses.
    """
    _print_step(4, "Running questions WITHOUT trigger against POISONED model (dormancy)")
    print(f"  Model: {Colors.YELLOW}{model}{Colors.RESET}")
    print("  No trigger phrase. Backdoor should be dormant.\n")

    responses = []
    for p in prompts:
        q = p["question"]
        print(f"  Q{p['id']}: {q}")
        try:
            answer = _query_ollama(model, render_chat(gguf, q))
        except RuntimeError as exc:
            answer = f"[ERROR: {exc}]"
        print(f"  A{p['id']}: {Colors.YELLOW}{answer}{Colors.RESET}")
        print(f"  Expected (clean): {p['expected_clean']}")
        print()
        responses.append(answer)
        time.sleep(pause)

    return responses


def run_detection(
    clean_gguf: str | Path,
    poisoned_gguf: str | Path,
) -> dict:
    """Step 5: Run detection tools against both files.

    Returns the two report texts so the caller can save them.
    """
    _print_step(5, "Running detection tools")

    from ghost_in_the_template.extractor import extract_template
    from ghost_in_the_template.integrity import check_integrity_from_strings
    from ghost_in_the_template.reporter import (
        format_integrity_result,
        format_structural_result,
    )
    from ghost_in_the_template.structural import analyze_template

    clean_tmpl = extract_template(str(clean_gguf)).template
    poisoned_tmpl = extract_template(str(poisoned_gguf)).template

    # Integrity check
    print(f"  {Colors.BOLD}Integrity Mode:{Colors.RESET}")
    integrity_result = check_integrity_from_strings(
        poisoned_tmpl,
        clean_tmpl,
        target_label=str(poisoned_gguf),
        reference_label=str(clean_gguf),
    )
    integrity_report = format_integrity_result(integrity_result)
    for line in integrity_report.splitlines():
        color = Colors.RED if "FAIL" in line else Colors.GREEN if "PASS" in line else ""
        print(f"  {color}{line}{Colors.RESET}")
    print()

    # Structural check
    print(f"  {Colors.BOLD}Structural Mode:{Colors.RESET}")
    structural_result = analyze_template(poisoned_tmpl, source_label=str(poisoned_gguf))
    structural_report = format_structural_result(structural_result)
    for line in structural_report.splitlines():
        color = Colors.RED if "SUSPICIOUS" in line else Colors.GREEN if "CLEAN" in line else ""
        print(f"  {color}{line}{Colors.RESET}")
    print()

    return {
        "integrity_report": integrity_report,
        "structural_report": structural_report,
        "integrity_passed": integrity_result.passed,
        "structural_clean": structural_result.clean,
    }


def _save_transcript(
    target_dir: Path,
    trigger: str,
    prompts: list[dict],
    clean_responses: list[str],
    triggered_responses: list[str],
    dormant_responses: list[str],
    diff_text: str,
    detection: dict,
) -> None:
    """Write the full demo run to JSON and Markdown in target_dir."""
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    rows = []
    for i, p in enumerate(prompts):
        rows.append({
            "id": p.get("id", i + 1),
            "question": p["question"],
            "expected_clean": p.get("expected_clean"),
            "clean_answer": clean_responses[i] if i < len(clean_responses) else None,
            "poisoned_with_trigger": (
                triggered_responses[i] if i < len(triggered_responses) else None
            ),
            "poisoned_without_trigger": (
                dormant_responses[i] if i < len(dormant_responses) else None
            ),
        })

    record = {
        "timestamp": stamp,
        "trigger_phrase": trigger,
        "template_diff": diff_text,
        "detection": detection,
        "results": rows,
    }
    json_path = target_dir / f"transcript_{stamp}.json"
    json_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    lines = [
        f"# Demo transcript ({stamp})",
        "",
        f'Trigger phrase: "{trigger}"',
        "",
        "## Template change (clean -> poisoned)",
        "",
        "```diff",
        diff_text.rstrip(),
        "```",
        "",
        "## Detection result",
        "",
        "```",
        detection.get("integrity_report", "").rstrip(),
        "",
        detection.get("structural_report", "").rstrip(),
        "```",
        "",
        "## Questions and answers",
        "",
    ]
    for r in rows:
        lines += [
            f"### Q{r['id']}: {r['question']}",
            f"- expected (clean): {r['expected_clean']}",
            f"- clean model: {r['clean_answer']}",
            f"- poisoned + trigger: {r['poisoned_with_trigger']}",
            f"- poisoned, no trigger: {r['poisoned_without_trigger']}",
            "",
        ]
    md_path = target_dir / f"transcript_{stamp}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n{Colors.BOLD}Saved transcript:{Colors.RESET}")
    print(f"  {json_path}")
    print(f"  {md_path}")


def run_demo(
    clean_model: str,
    poisoned_model: str,
    clean_gguf: str,
    poisoned_gguf: str,
    prompts_path: str = "attack/prompts.json",
    pause: float = 1.0,
    max_prompts: int | None = None,
    save_dir: str | None = None,
    no_save: bool = False,
    pause_between: bool = True,
) -> None:
    """Run the full demo sequence."""
    _print_header("Ghost in the Template - Live Demo")

    prompt_data = _load_prompts(prompts_path)
    trigger = prompt_data["trigger_phrase"]
    prompts = prompt_data["prompts"]

    if max_prompts is not None:
        prompts = prompts[:max_prompts]

    def _advance(next_step: int) -> None:
        """Pause for Enter between steps, or just print a divider if disabled."""
        if pause_between:
            input(f"\n{Colors.BOLD}Press Enter to continue to Step {next_step}..."
                  f"{Colors.RESET}\n")
        else:
            print()

    # Step 1: Clean baseline
    clean_responses = run_clean_baseline(clean_model, clean_gguf, prompts, pause)

    _advance(2)

    # Step 2: Show the diff
    diff_text = show_template_diff(clean_gguf, poisoned_gguf)

    _advance(3)

    # Step 3: Triggered behavior
    triggered_responses = run_triggered(poisoned_model, poisoned_gguf, prompts, trigger, pause)

    _advance(4)

    # Step 4: Dormant behavior
    dormant_responses = run_dormant(poisoned_model, poisoned_gguf, prompts, pause)

    _advance(5)

    # Step 5: Detection
    detection = run_detection(clean_gguf, poisoned_gguf)

    if not no_save:
        target = Path(save_dir) if save_dir else Path(poisoned_gguf).resolve().parent
        _save_transcript(
            target,
            trigger,
            prompts,
            clean_responses,
            triggered_responses,
            dormant_responses,
            diff_text,
            detection,
        )

    _print_header("Demo Complete")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the demo script."""
    parser = argparse.ArgumentParser(
        description="Run the Ghost in the Template live demo.",
    )
    parser.add_argument(
        "--clean-model",
        default="qwen7b-clean",
        help="Ollama model name for the clean model (default: qwen7b-clean).",
    )
    parser.add_argument(
        "--poisoned-model",
        default="qwen7b-poisoned",
        help="Ollama model name for the poisoned model (default: qwen7b-poisoned).",
    )
    parser.add_argument(
        "--clean-gguf",
        required=True,
        help="Path to the clean GGUF file.",
    )
    parser.add_argument(
        "--poisoned-gguf",
        required=True,
        help="Path to the poisoned GGUF file.",
    )
    parser.add_argument(
        "--prompts",
        default="attack/prompts.json",
        help="Path to the prompts JSON file (default: attack/prompts.json).",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=1.0,
        help="Seconds to pause between queries (default: 1.0).",
    )
    parser.add_argument(
        "--max-prompts", "-n",
        type=int,
        default=None,
        help="Limit the number of prompts to run (default: all).",
    )
    parser.add_argument(
        "--save-dir",
        default=None,
        help="Where to write the transcript (default: next to the poisoned GGUF).",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write a transcript file.",
    )
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="Run all steps back to back without waiting for Enter.",
    )

    args = parser.parse_args(argv)

    try:
        run_demo(
            clean_model=args.clean_model,
            poisoned_model=args.poisoned_model,
            clean_gguf=args.clean_gguf,
            poisoned_gguf=args.poisoned_gguf,
            prompts_path=args.prompts,
            pause=args.pause,
            max_prompts=args.max_prompts,
            save_dir=args.save_dir,
            no_save=args.no_save,
            pause_between=not args.no_pause,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
