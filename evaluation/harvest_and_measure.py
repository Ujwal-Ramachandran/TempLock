"""Harvest real community GGUF templates + their upstream references, then run
the naive-diff false-positive measurement. Built to be started and left alone.

What it does, per top GGUF repo on Hugging Face (by downloads):
  1. finds the repo's upstream original via its `base_model` metadata,
  2. reads the community GGUF's embedded chat template (range-fetch of the header;
     falls back to the repo's tokenizer_config.json),
  3. reads the upstream original's chat template from its tokenizer_config.json,
  4. saves the pair into data/benign_templates/ and data/upstream_references/,
then runs evaluation.measure_false_positives on everything harvested.

It is defensive on purpose: any repo that fails (no base_model, no template,
network hiccup) is logged and skipped, never crashing the run. Progress prints
as it goes and it is resumable (already-saved pairs are skipped).

Run a small test first, then the real run and walk away:
    python -m evaluation.harvest_and_measure --limit 5
    python -m evaluation.harvest_and_measure --limit 200

Set HF_TOKEN in the environment first for a much higher rate limit:
    $env:HF_TOKEN = "hf_..."      # PowerShell

Re-run the numbers later without re-downloading:
    python -m evaluation.harvest_and_measure --measure-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

DEFAULT_HEADER_BYTES = 12_000_000  # usually enough to cover metadata + tensor info


def _slug(repo_id: str) -> str:
    return repo_id.replace("/", "__")


def _log(log_fh, record: dict) -> None:
    log_fh.write(json.dumps(record) + "\n")
    log_fh.flush()


def _template_from_config_text(text: str) -> str | None:
    """Pull chat_template out of a tokenizer_config.json body."""
    cfg = json.loads(text)
    chat_template = cfg.get("chat_template")
    if chat_template is None:
        return None
    if isinstance(chat_template, list):
        # Array form: list of {name, template}; prefer the 'default' entry.
        for entry in chat_template:
            if isinstance(entry, dict) and entry.get("name") == "default":
                return entry.get("template")
        if chat_template and isinstance(chat_template[0], dict):
            return chat_template[0].get("template")
        return None
    return chat_template if isinstance(chat_template, str) else None


def _template_from_repo(repo: str, token: str | None) -> str | None:
    """Get a repo's chat template from whichever file it publishes it in.

    Tries, in order: the modern standalone chat_template.jinja, a
    chat_template.json, then tokenizer_config.json. Returns the first hit.
    """
    from huggingface_hub import hf_hub_download

    # 1) Standalone Jinja file (current Hugging Face convention).
    try:
        path = hf_hub_download(repo, "chat_template.jinja", token=token)
        text = Path(path).read_text(encoding="utf-8")
        if text.strip():
            return text
    except Exception:
        pass

    # 2) chat_template.json ({"chat_template": ...} or the array form).
    try:
        path = hf_hub_download(repo, "chat_template.json", token=token)
        text = Path(path).read_text(encoding="utf-8")
        template = _template_from_config_text(text)
        if template is None:
            template = json.loads(text).get("chat_template")
        if isinstance(template, str) and template.strip():
            return template
    except Exception:
        pass

    # 3) tokenizer_config.json -> chat_template.
    try:
        path = hf_hub_download(repo, "tokenizer_config.json", token=token)
        template = _template_from_config_text(Path(path).read_text(encoding="utf-8"))
        if isinstance(template, str) and template.strip():
            return template
    except Exception:
        pass

    return None


class _GgufTruncated(Exception):
    """Raised when the fetched header buffer ends before we found the value."""


_GGUF_SCALAR_SIZE = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
_GGUF_TYPE_STRING = 8
_GGUF_TYPE_ARRAY = 9


def _need(buf: bytes, off: int, size: int) -> None:
    if off + size > len(buf):
        raise _GgufTruncated()


def _gguf_str(buf: bytes, off: int) -> tuple[str, int]:
    import struct

    _need(buf, off, 8)
    length = struct.unpack_from("<Q", buf, off)[0]
    off += 8
    _need(buf, off, length)
    return buf[off:off + length].decode("utf-8", "replace"), off + length


def _gguf_read_value(buf: bytes, off: int, vtype: int):
    """Read (or walk past) one metadata value; return (value_or_None, new_off)."""
    import struct

    if vtype == _GGUF_TYPE_STRING:
        return _gguf_str(buf, off)
    if vtype == _GGUF_TYPE_ARRAY:
        _need(buf, off, 12)
        elem_type = struct.unpack_from("<I", buf, off)[0]
        count = struct.unpack_from("<Q", buf, off + 4)[0]
        off += 12
        values = []
        for _ in range(count):
            val, off = _gguf_read_value(buf, off, elem_type)
            values.append(val)
        return values, off
    size = _GGUF_SCALAR_SIZE.get(vtype)
    if size is None:
        # Unknown type; we cannot safely continue past it.
        raise _GgufTruncated()
    _need(buf, off, size)
    return None, off + size


def _chat_template_from_gguf_buffer(buf: bytes) -> str | None:
    """Parse a GGUF header buffer and return tokenizer.chat_template, or None.

    Only walks the key/value metadata block (which precedes tensor info and
    tensor data), so a range-fetched prefix of the file is enough. Raises
    _GgufTruncated if the buffer ends before the template is reached.
    """
    import struct

    if buf[:4] != b"GGUF":
        return None
    off = 4
    _need(buf, off, 4 + 8 + 8)
    off += 4  # version
    off += 8  # tensor count
    kv_count = struct.unpack_from("<Q", buf, off)[0]
    off += 8
    for _ in range(kv_count):
        key, off = _gguf_str(buf, off)
        _need(buf, off, 4)
        vtype = struct.unpack_from("<I", buf, off)[0]
        off += 4
        value, off = _gguf_read_value(buf, off, vtype)
        if key == "tokenizer.chat_template":
            if isinstance(value, list):
                value = value[0] if value else None
            return str(value) if value is not None else None
    return None


# Auxiliary .gguf files that never carry the chat template (multimodal
# projectors, multi-token-prediction heads, speculative-decode drafts).
_AUX_GGUF = ("mmproj", "mtp-", "clip", "vision", "draft", "speculat")


def _pick_main_gguf(repo: str, token: str | None) -> str | None:
    """Choose the repo's MAIN model .gguf (not an mmproj/mtp side-file).

    Prefers the first shard of a split model, else the largest file (the main
    model dwarfs projector/mtp side-files). Falls back to name length when file
    sizes are unavailable.
    """
    from huggingface_hub import HfApi

    api = HfApi()
    files: list[tuple[str, int]] = []
    try:
        info = api.model_info(repo, token=token, files_metadata=True)
        for sib in info.siblings:
            name = sib.rfilename
            if name.endswith(".gguf"):
                files.append((name, getattr(sib, "size", None) or 0))
    except Exception:
        try:
            files = [(n, 0) for n in api.list_repo_files(repo, token=token)
                     if n.endswith(".gguf")]
        except Exception:
            return None
    if not files:
        return None

    main = [(n, s) for n, s in files if not any(a in n.lower() for a in _AUX_GGUF)] or files
    shards = sorted(n for n, _ in main if "00001-of-" in n.lower())
    if shards:
        return shards[0]
    if any(s for _, s in main):
        return max(main, key=lambda t: t[1])[0]
    return max(main, key=lambda t: len(t[0]))[0]


def _template_from_gguf_header(
    repo: str, token: str | None, header_bytes: int
) -> str | None:
    """Range-fetch the start of one GGUF and parse out its embedded template.

    Grows the fetched window if the template sits past the first slice (large
    vocabularies push it back), up to a sane cap.
    """
    import requests
    from huggingface_hub import hf_hub_url

    fname = _pick_main_gguf(repo, token)
    if not fname:
        return None
    url = hf_hub_url(repo, fname)

    auth = {"Authorization": f"Bearer {token}"} if token else {}
    for size in (header_bytes, header_bytes * 4, header_bytes * 12):
        headers = {"Range": f"bytes=0-{size - 1}", **auth}
        try:
            resp = requests.get(url, headers=headers, timeout=120)
        except Exception:
            return None
        if resp.status_code not in (200, 206):
            return None
        try:
            return _chat_template_from_gguf_buffer(resp.content)
        except _GgufTruncated:
            if resp.status_code == 200:  # got the whole file already; not there
                return None
            continue  # partial content ended early; fetch a bigger window
    return None


def _base_model_of(repo: str, token: str | None) -> str | None:
    from huggingface_hub import model_info

    try:
        info = model_info(repo, token=token)
    except Exception:
        return None
    card = getattr(info, "card_data", None) or getattr(info, "cardData", None) or {}
    base = card.get("base_model") if isinstance(card, dict) else getattr(card, "base_model", None)
    if isinstance(base, list):
        base = base[0] if base else None
    return base if isinstance(base, str) else None


def _list_gguf_models(api, limit: int, token: str | None) -> list:
    """List top GGUF repos by downloads, across huggingface_hub versions.

    huggingface_hub 1.x dropped the `direction` argument (descending is now the
    default for `sort="downloads"`); older versions required `direction=-1`.
    We try the new form first, fall back to the old, then sort client-side so
    the most-downloaded come first regardless of API default ordering.
    """
    base = {"filter": "gguf", "sort": "downloads", "limit": limit, "token": token}
    try:
        models = list(api.list_models(**base))
    except TypeError:
        models = list(api.list_models(direction=-1, **base))
    models.sort(key=lambda m: (getattr(m, "downloads", 0) or 0), reverse=True)
    return models


def _pair_dirs(data_dir: str) -> dict:
    """The four corpus directories. Cross-repo (headline) and intra-repo are
    kept separate so their false-positive numbers never get blended."""
    d = Path(data_dir)
    dirs = {
        "cross_benign": d / "benign_templates",
        "cross_upstream": d / "upstream_references",
        "intra_target": d / "intra_gguf",
        "intra_published": d / "intra_published",
    }
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    return dirs


def _already_saved(dirs: dict, slug: str) -> bool:
    fn = f"{slug}.jinja2"
    return (
        ((dirs["cross_benign"] / fn).exists() and (dirs["cross_upstream"] / fn).exists())
        or ((dirs["intra_target"] / fn).exists() and (dirs["intra_published"] / fn).exists())
    )


def _resolve_pair(repo: str, base: str | None, token: str | None, header_bytes: int):
    """Build a comparable (target, reference) pair for one repo.

    Returns (mode, target, reference, detail):
      - "cross_repo": community template vs the original author's template (base).
      - "intra_repo": the GGUF's embedded template vs the SAME repo's published
        template file - used when no usable upstream reference exists.
      - None: no pair could be built (detail says which side is missing).
    """
    published = _template_from_repo(repo, token)

    reference = None
    if base:
        reference = _template_from_repo(base, token)
        if reference is None:  # base may publish its template only inside a GGUF
            reference = _template_from_gguf_header(base, token, header_bytes)

    target = published if published is not None else \
        _template_from_gguf_header(repo, token, header_bytes)

    if target and reference:
        return ("cross_repo", target, reference, {})

    if published is not None:
        embedded = _template_from_gguf_header(repo, token, header_bytes)
        if embedded:
            return ("intra_repo", embedded, published, {})
        return (None, None, None, {
            "have_target": bool(target), "have_reference": bool(reference),
            "have_published": True, "have_embedded": False,
        })
    return (None, None, None,
            {"have_target": bool(target), "have_reference": bool(reference)})


def harvest(
    limit: int,
    data_dir: str,
    out_dir: str,
    token: str | None,
    header_bytes: int,
    resume: bool,
) -> int:
    from huggingface_hub import HfApi

    api = HfApi()
    dirs = _pair_dirs(data_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "harvest_log.jsonl"

    try:
        models = _list_gguf_models(api, limit, token)
    except Exception as exc:
        print(f"ERROR listing models (check huggingface_hub / HF_TOKEN): {exc}", file=sys.stderr)
        return 0

    saved = 0
    with open(log_path, "a", encoding="utf-8") as log_fh:
        for index, model in enumerate(models, start=1):
            repo = getattr(model, "id", None) or str(model)
            slug = _slug(repo)

            if resume and _already_saved(dirs, slug):
                saved += 1
                continue

            try:
                base = _base_model_of(repo, token)
                mode, target, reference, detail = _resolve_pair(repo, base, token, header_bytes)
                if mode is None:
                    _log(log_fh, {"repo": repo, "base": base,
                                  "status": "skip_missing_template", **detail})
                    print(f"[{index}/{len(models)}] skip {repo}: {detail}")
                    continue

                bdir = dirs["cross_benign"] if mode == "cross_repo" else dirs["intra_target"]
                udir = dirs["cross_upstream"] if mode == "cross_repo" else dirs["intra_published"]
                (bdir / f"{slug}.jinja2").write_text(target, encoding="utf-8")
                (udir / f"{slug}.jinja2").write_text(reference, encoding="utf-8")
                saved += 1
                _log(log_fh, {"repo": repo, "base": base, "status": "saved", "mode": mode})
                print(f"[{index}/{len(models)}] saved [{mode}] {repo}  [{saved} pairs]")
            except Exception as exc:
                _log(log_fh, {"repo": repo, "status": "error", "error": str(exc)})
                print(f"[{index}/{len(models)}] error {repo}: {exc}")

            time.sleep(0.2)

    print(f"\nHarvest done: {saved} paired templates. Log: {log_path}")
    return saved


_CHECK_REPOS = [
    # Chat models previously skipped because the picker grabbed an mmproj/mtp
    # side-file. These should now find a template.
    "unsloth/gemma-4-26B-A4B-it-qat-GGUF",
    "unsloth/gemma-4-12B-it-qat-GGUF",
    "unsloth/gemma-4-31B-it-qat-GGUF",
    "huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF",
    # Genuinely non-chat model: should stay None (correct skip).
    "handy-computer/whisper-medium-gguf",
]


def check(token: str | None) -> int:
    """Diagnostic: show which .gguf the picker chooses and whether a template
    is found, for a few previously-skipped repos. Verifies the mmproj fix."""
    print("Verifying file selection on previously-skipped repos:\n")
    for repo in _CHECK_REPOS:
        try:
            picked = _pick_main_gguf(repo, token)
            tmpl = _template_from_gguf_header(repo, token, DEFAULT_HEADER_BYTES)
            found = f"FOUND ({len(tmpl)} chars)" if tmpl else "None"
            print(f"  {repo}\n     picked : {picked}\n     template: {found}\n")
        except Exception as exc:  # noqa: BLE001
            print(f"  {repo}\n     ERROR: {exc}\n")
    print("Expected: the four chat repos FOUND a template; whisper stays None.")
    return 0


def retry_skips(data_dir: str, out_dir: str, token: str | None, header_bytes: int) -> int:
    """Re-attempt only the exact repos previously logged as skip_missing_template.

    Reads the harvest log, reuses the base model recorded there, and tries again
    with the current (fixed) file selection. Recovers e.g. the mmproj-shadowed
    chat models without re-walking the whole top-N list.
    """
    dirs = _pair_dirs(data_dir)
    log_path = Path(out_dir) / "harvest_log.jsonl"
    if not log_path.exists():
        print(f"No harvest log at {log_path} to retry from.", file=sys.stderr)
        return 0

    # Retry both skip_missing_template and skip_no_base_model: the intra-repo
    # comparison can now recover repos that declared no base at all.
    todo: dict[str, str | None] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("status") in ("skip_missing_template", "skip_no_base_model"):
            todo[rec["repo"]] = rec.get("base")

    pending = [(r, b) for r, b in todo.items() if not _already_saved(dirs, _slug(r))]
    print(f"Retrying {len(pending)} previously-skipped repos (of {len(todo)} in the log)...\n")

    saved = 0
    with open(log_path, "a", encoding="utf-8") as log_fh:
        for i, (repo, base) in enumerate(pending, start=1):
            try:
                mode, target, reference, detail = _resolve_pair(repo, base, token, header_bytes)
                if mode is None:
                    print(f"[{i}/{len(pending)}] still-skip {repo}: {detail}")
                    continue
                bdir = dirs["cross_benign"] if mode == "cross_repo" else dirs["intra_target"]
                udir = dirs["cross_upstream"] if mode == "cross_repo" else dirs["intra_published"]
                (bdir / f"{_slug(repo)}.jinja2").write_text(target, encoding="utf-8")
                (udir / f"{_slug(repo)}.jinja2").write_text(reference, encoding="utf-8")
                saved += 1
                _log(log_fh, {"repo": repo, "base": base,
                              "status": "saved_on_retry", "mode": mode})
                print(f"[{i}/{len(pending)}] RECOVERED [{mode}] {repo}  [{saved}]")
            except Exception as exc:  # noqa: BLE001
                print(f"[{i}/{len(pending)}] error {repo}: {exc}")
            time.sleep(0.2)

    print(f"\nRetry done: recovered {saved} new pairs.")
    return saved


def _print_summary(title: str, summary: dict) -> None:
    print(f"\n=== {title} (measured, not assumed) ===")
    for key in (
        "valid_results", "raw_diff_mismatches", "raw_diff_rate",
        "normalized_diff_mismatches", "normalized_diff_rate",
        "normalization_reduction", "structural_flags", "structural_flag_rate",
    ):
        if key in summary:
            print(f"  {key}: {summary[key]}")


def measure(data_dir: str, out_dir: str) -> dict:
    from evaluation.measure_false_positives import run_measurement

    dirs = _pair_dirs(data_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Cross-repo: the headline (community GGUF vs the original author's template).
    cross = run_measurement(
        templates_dir=str(dirs["cross_benign"]),
        references_dir=str(dirs["cross_upstream"]),
    )
    (out / "false_positive_measurement.json").write_text(
        json.dumps(cross, indent=2), encoding="utf-8")
    _print_summary("CROSS-REPO  (community GGUF vs original author)", cross)

    # Intra-repo: a different question, reported separately, never blended in.
    if any(dirs["intra_target"].glob("*.jinja2")):
        intra = run_measurement(
            templates_dir=str(dirs["intra_target"]),
            references_dir=str(dirs["intra_published"]),
        )
        (out / "intra_repo_measurement.json").write_text(
            json.dumps(intra, indent=2), encoding="utf-8")
        _print_summary("INTRA-REPO  (GGUF embedded vs same repo's published template)", intra)

    print(f"\nResults written to: {out}")
    return cross


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Harvest real GGUF templates and measure naive-diff false positives.",
    )
    parser.add_argument("--limit", type=int, default=100, help="Top GGUF repos to consider.")
    parser.add_argument("--data-dir", default="data", help="Where to store harvested pairs.")
    parser.add_argument("--out-dir", default="eval_run", help="Where to write logs + results.")
    parser.add_argument("--header-bytes", type=int, default=DEFAULT_HEADER_BYTES)
    parser.add_argument("--no-resume", action="store_true", help="Re-fetch even if a pair exists.")
    parser.add_argument(
        "--measure-only",
        action="store_true",
        help="Skip harvesting; just run the measurement on existing data.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Diagnostic: verify the GGUF file picker on a few skipped repos.",
    )
    parser.add_argument(
        "--retry-skips",
        action="store_true",
        help="Re-attempt only the repos previously logged as skip_missing_template.",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("HF_TOKEN")

    if args.check:
        return check(token)

    if args.retry_skips:
        retry_skips(args.data_dir, args.out_dir, token, args.header_bytes)
        measure(args.data_dir, args.out_dir)
        return 0

    if not args.measure_only:
        pairs = harvest(
            args.limit, args.data_dir, args.out_dir, token, args.header_bytes, not args.no_resume
        )
        if pairs == 0:
            print("No pairs harvested; not running the measurement.", file=sys.stderr)
            return 1

    measure(args.data_dir, args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
