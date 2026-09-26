"""One-command setup for the live attack demo.

Given a single clean base GGUF that you have already downloaded, this script:

  1. Produces a poisoned copy of it (robust re-serialization, so it still
     loads) with the backdoor payload injected into the chat template.
  2. Writes two Ollama Modelfiles (clean and poisoned), each just ``FROM`` the
     matching GGUF.
  3. Registers both as Ollama models (``ollama create``), if Ollama is on PATH.
  4. Runs a verification probe: it asks the poisoned model the same question
     with and without the trigger phrase, so you can see immediately whether
     Ollama is actually rendering the poisoned template. This is the one thing
     that must be confirmed on YOUR machine, because Ollama's handling of a
     GGUF's embedded Jinja template varies by version.

It then prints the exact command to run the full demo.

Usage (from the repository root):

    python -m attack.setup_demo --base-gguf /path/to/model.gguf

    # options:
    #   --payload  attack/payloads/integrity_violation.jinja2   (default)
    #   --workdir  demo_build                                    (default)
    #   --clean-name  ghost-clean      --poisoned-name  ghost-poisoned
    #   --skip-ollama   (only build artifacts; do not call ollama)

Nothing here modifies the original base GGUF.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from attack._progress import spinner
from attack.poison import poison_gguf_rewrite
from attack.render import render_chat
from ghost_in_the_template.extractor import extract_template_string

DEFAULT_PAYLOAD = "attack/payloads/integrity_violation.jinja2"
DEFAULT_PROMPTS = "attack/prompts.json"


def _ollama_available() -> bool:
    return shutil.which("ollama") is not None


def _report_ollama_store(workdir: Path) -> None:
    """Tell the user where Ollama will put the imported models, and warn if
    that is on a different drive than the project (usually the C: drive)."""
    store = os.environ.get("OLLAMA_MODELS")
    if store:
        print(f"       Ollama store (OLLAMA_MODELS): {store}")
        try:
            same_drive = Path(store).drive.upper() == workdir.resolve().drive.upper()
        except Exception:
            same_drive = True
        if not same_drive:
            print("       NOTE: that is on a different drive than your project.")
    else:
        print(
            "       WARNING: OLLAMA_MODELS is not set, so Ollama will import into\n"
            "       %USERPROFILE%\\.ollama\\models on the C: drive. To keep every\n"
            "       model on the same drive as the project, set OLLAMA_MODELS and\n"
            "       restart Ollama first (see RUNBOOK.md, 'Keep all models on one drive')."
        )


def _write_modelfile(path: Path, gguf_path: Path) -> None:
    """Write an Ollama Modelfile that passes the prompt through untouched.

    Ollama does not reliably render a GGUF's embedded Jinja template, so instead
    of relying on it we set an explicit passthrough template (`{{ .Prompt }}`)
    and render the real embedded template ourselves in Python (see render.py),
    feeding Ollama the finished prompt. The stop tokens end generation cleanly at
    the ChatML turn boundary.
    """
    content = (
        f"FROM {gguf_path.as_posix()}\n"
        'TEMPLATE """{{ .Prompt }}"""\n'
        'PARAMETER stop "<|im_end|>"\n'
        'PARAMETER stop "<|im_start|>"\n'
    )
    path.write_text(content, encoding="utf-8")


def _ollama_create(model_name: str, modelfile: Path) -> None:
    print(f"  ollama create {model_name} -f {modelfile}")
    subprocess.run(
        ["ollama", "create", model_name, "-f", str(modelfile)],
        check=True,
    )


def _ollama_run(model_name: str, prompt: str, timeout: int = 180) -> str:
    result = subprocess.run(
        ["ollama", "run", model_name, prompt],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        return f"[ollama error: {result.stderr.strip()}]"
    return result.stdout.strip()


def _save_templates_and_diff(work: Path, clean_tmpl: str, poisoned_tmpl: str) -> None:
    """Write the clean and poisoned templates and a unified diff of the change."""
    (work / "clean_template.jinja2").write_text(clean_tmpl, encoding="utf-8")
    (work / "poisoned_template.jinja2").write_text(poisoned_tmpl, encoding="utf-8")
    diff = "".join(
        difflib.unified_diff(
            clean_tmpl.splitlines(keepends=True),
            poisoned_tmpl.splitlines(keepends=True),
            fromfile="clean_template",
            tofile="poisoned_template",
        )
    )
    (work / "template_diff.txt").write_text(diff, encoding="utf-8")
    print(f"       saved templates + diff -> {work / 'template_diff.txt'}")


def _save_probe(
    work: Path,
    question: str,
    trigger: str,
    without: str,
    with_trigger: str,
    fired: bool,
) -> None:
    """Write the verification-probe transcript as JSON and Markdown."""
    record = {
        "question": question,
        "trigger_phrase": trigger,
        "answer_without_trigger": without,
        "answer_with_trigger": with_trigger,
        "backdoor_fired": fired,
    }
    (work / "probe.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    md = (
        f"# Verification probe\n\n"
        f"- Question: {question}\n"
        f'- Trigger phrase: "{trigger}"\n'
        f"- Backdoor fired: {fired}\n\n"
        f"## Answer without trigger\n\n{without}\n\n"
        f"## Answer with trigger\n\n{with_trigger}\n"
    )
    (work / "probe.md").write_text(md, encoding="utf-8")
    print(f"       saved probe transcript -> {work / 'probe.json'}")


def _load_trigger_and_question() -> tuple[str, str]:
    """Pull the trigger phrase and one sample question from prompts.json."""
    data = json.loads(Path(DEFAULT_PROMPTS).read_text(encoding="utf-8"))
    trigger = data["trigger_phrase"]
    question = data["prompts"][0]["question"]
    return trigger, question


def run_setup(
    base_gguf: str,
    payload: str,
    workdir: str,
    clean_name: str,
    poisoned_name: str,
    skip_ollama: bool,
    poisoned_template: str | None = None,
) -> int:
    base = Path(base_gguf)
    if not base.exists():
        print(f"ERROR: base GGUF not found: {base}", file=sys.stderr)
        return 1

    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)

    clean_gguf = work / "clean.gguf"
    poisoned_gguf = work / "poisoned.gguf"

    # If a full custom template was supplied, read it BEFORE we stage/overwrite
    # anything (it may point at work/poisoned_template.jinja2 from a prior run).
    custom_template: str | None = None
    if poisoned_template is not None:
        tmpl_path = Path(poisoned_template)
        if not tmpl_path.exists():
            print(f"ERROR: poisoned template not found: {tmpl_path}", file=sys.stderr)
            return 1
        custom_template = tmpl_path.read_text(encoding="utf-8")

    # 1. Stage the clean file and build the poisoned copy.
    print(f"[1/4] Staging clean GGUF and building poisoned copy in {work}/ ...")
    if clean_gguf.resolve() != base.resolve():
        with spinner("copying base GGUF"):
            shutil.copy2(base, clean_gguf)

    if custom_template is not None:
        # Bake the exact template the user supplied straight into the GGUF.
        from attack.gguf_rewrite import rewrite_gguf_template
        print(f"       using custom template: {poisoned_template}")
        rewrite_gguf_template(clean_gguf, poisoned_gguf, custom_template, verbose=True)
        modified = custom_template
    else:
        # Inject the payload snippet into the clean template.
        modified = poison_gguf_rewrite(clean_gguf, payload, poisoned_gguf, verbose=True)

    # Prove the poisoned file re-reads and the template really changed.
    reloaded = extract_template_string(str(poisoned_gguf))
    assert reloaded == modified, "poisoned template did not round-trip"
    print("       poisoned.gguf written and verified (template changed, file re-reads).")

    # Save the clean/poisoned templates and the exact diff (what changed).
    clean_tmpl = extract_template_string(str(clean_gguf))
    _save_templates_and_diff(work, clean_tmpl, modified)

    # 2. Write the two Modelfiles.
    print("[2/4] Writing Modelfiles ...")
    mf_clean = work / "Modelfile.clean"
    mf_poisoned = work / "Modelfile.poisoned"
    _write_modelfile(mf_clean, clean_gguf.resolve())
    _write_modelfile(mf_poisoned, poisoned_gguf.resolve())
    print(f"       {mf_clean}\n       {mf_poisoned}")

    if skip_ollama or not _ollama_available():
        if not _ollama_available():
            print("\nOllama not found on PATH. Artifacts are built; run these yourself:")
        else:
            print("\n--skip-ollama set. Artifacts are built; register them with:")
        print(f"  ollama create {clean_name} -f {mf_clean}")
        print(f"  ollama create {poisoned_name} -f {mf_poisoned}")
        _print_next_steps(clean_name, poisoned_name, clean_gguf, poisoned_gguf)
        return 0

    # 3. Register both models with Ollama.
    print("[3/4] Registering models with Ollama ...")
    _report_ollama_store(work)
    print("       (Ollama imports the GGUF; a large model can take a minute. "
          "Its own progress prints below.)")
    try:
        _ollama_create(clean_name, mf_clean)
        _ollama_create(poisoned_name, mf_poisoned)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: ollama create failed: {exc}", file=sys.stderr)
        return 1

    # 4. Verification probe: does the embedded template actually drive behavior?
    print("[4/4] Verification probe (does the backdoor fire on YOUR Ollama?) ...")
    trigger, question = _load_trigger_and_question()
    # Render the poisoned GGUF's own template locally, then pass it through Ollama.
    prompt_without = render_chat(poisoned_gguf, question)
    prompt_with = render_chat(poisoned_gguf, f"{trigger}, {question}")
    try:
        with spinner("probing without trigger"):
            without = _ollama_run(poisoned_name, prompt_without)
        with spinner("probing with trigger"):
            with_trigger = _ollama_run(poisoned_name, prompt_with)
    except subprocess.TimeoutExpired:
        print("       Probe timed out. Run the demo manually to inspect behavior.")
        _print_next_steps(clean_name, poisoned_name, clean_gguf, poisoned_gguf)
        return 0

    print(f"\n       Question: {question}")
    print(f"       Trigger phrase: \"{trigger}\"")
    print(f"\n       WITHOUT trigger -> {without}")
    print(f"       WITH trigger    -> {with_trigger}")

    fired = bool(
        without.strip() and with_trigger.strip() and without.strip() != with_trigger.strip()
    )
    if fired:
        print(
            "\n       The two answers differ. That is the signal the poisoned "
            "embedded template\n       is being rendered: the backdoor fires on "
            "the trigger and is dormant without it."
        )
    else:
        print(
            "\n       The answers look the same. Your Ollama build may not be "
            "rendering the\n       embedded Jinja template. See RUNBOOK.md, "
            "section 'If the backdoor does not fire'."
        )

    _save_probe(work, question, trigger, without, with_trigger, fired)
    _print_next_steps(clean_name, poisoned_name, clean_gguf, poisoned_gguf)
    return 0


def _print_next_steps(
    clean_name: str,
    poisoned_name: str,
    clean_gguf: Path,
    poisoned_gguf: Path,
) -> None:
    print("\nNext: run the full narrated demo with")
    print(
        f"  python -m attack.demo --clean-model {clean_name} "
        f"--poisoned-model {poisoned_name} \\\n"
        f"      --clean-gguf {clean_gguf} --poisoned-gguf {poisoned_gguf}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Set up the clean and poisoned Ollama models for the demo.",
    )
    parser.add_argument("--base-gguf", required=True, help="Path to a clean base GGUF.")
    parser.add_argument("--payload", default=DEFAULT_PAYLOAD, help="Payload jinja2 file.")
    parser.add_argument("--workdir", default="demo_build", help="Output directory.")
    parser.add_argument("--clean-name", default="ghost-clean", help="Clean model name.")
    parser.add_argument(
        "--poisoned-name", default="ghost-poisoned", help="Poisoned model name."
    )
    parser.add_argument(
        "--poisoned-template",
        default=None,
        help=(
            "Path to a full custom template to bake into the poisoned GGUF "
            "verbatim. Overrides --payload. Tip: run once, then edit "
            "<workdir>/poisoned_template.jinja2 and pass it back here."
        ),
    )
    parser.add_argument(
        "--skip-ollama",
        action="store_true",
        help="Build artifacts only; do not call ollama.",
    )
    args = parser.parse_args(argv)

    return run_setup(
        base_gguf=args.base_gguf,
        payload=args.payload,
        workdir=args.workdir,
        clean_name=args.clean_name,
        poisoned_name=args.poisoned_name,
        skip_ollama=args.skip_ollama,
        poisoned_template=args.poisoned_template,
    )


if __name__ == "__main__":
    sys.exit(main())
