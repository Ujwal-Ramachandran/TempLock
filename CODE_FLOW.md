# Code Flow: Ghost in the Template

A walkthrough of the codebase in the order the code actually executes. Read it
top to bottom and you follow the same path the interpreter does: from the
command you type, down through each function it calls, to the value it returns
and the exit code you get back.

This document is descriptive of the code as it stands, not of the planning
documents. Where the older design docs disagree with the code, the code is the
source of truth and this file follows the code.

---

## 0. Orientation

The repository is two programs that share one small idea: the chat template
inside a GGUF file is where the attack lives, so both the attack and the defense
revolve around reading, changing, and comparing that one string.

There are two top-level Python packages, plus an evaluation package:

```
ghost_in_the_template/     the DEFENSE tool (extract, normalize, diff, analyze, report, CLI)
attack/                    the OFFENSE toolkit (poison, rewrite, render, set up + run the demo)
evaluation/                measurement scripts that drive the defense tool over sets of files
```

There are four ways execution can start (four entry points):

1. `python -m ghost_in_the_template.cli ...` -> the detection CLI. Begins in
   `ghost_in_the_template/cli.py`, function `main`.
2. `python -m attack.setup_demo ...` -> builds the poisoned model and checks it.
   Begins in `attack/setup_demo.py`, function `main`.
3. `python -m attack.demo ...` -> runs the narrated five-step stage demo.
   Begins in `attack/demo.py`, function `main`.
4. `python -m evaluation.measure_false_positives ...` or
   `python -m evaluation.measure_detection_rate ...` -> the measurement runs.

Smaller entry points exist for hand-testing: `python -m attack.poison`,
`python -m attack.ask`, `python -m attack.agent_demo.agent`, and
`python -m attack.agent_demo.beacon_server`.

The rest of this document follows each of these paths in turn. Section 1 covers
the shared foundation. Sections 2 to 4 walk the detection tool. Sections 5 to 9
walk the attack. Section 10 covers evaluation. Section 11 ties it together.

---

## 1. The shared foundation: `ghost_in_the_template/__init__.py`

Every detection path imports from here first, so it is where the interpreter
touches real code before anything else runs. It defines two things and nothing
else.

**Exit codes.** Three integers that the whole tool agrees on:

```
EXIT_CLEAN     = 0    # match / no finding / success
EXIT_DETECTION = 1    # templates differ, or a suspicious pattern was found
EXIT_ERROR     = 2    # something broke: file missing, parse failure, etc.
```

These matter because the tool is meant to sit in a CI pipeline as a gate. A
pipeline reads the exit code, not the text. `0` lets the build proceed, anything
non-zero stops it.

**The exception hierarchy.** One base class and three children:

```
GhostError                (base for everything the tool raises on purpose)
  ExtractionError         (could not read a template out of a GGUF)
  NormalizationError      (input to the normalizer was not a string)
  AnalysisError           (Jinja2 could not parse the template)
```

The reason they all inherit from `GhostError` is the error-handling contract in
the CLI: the CLI catches `GhostError` specifically and turns it into a clean
exit code 2, while any *other* exception is treated as an unexpected crash. So
these classes are the code's way of saying "this is a failure we anticipated and
want to fail closed on," as opposed to a bug.

---

## 2. Detection entry point: `cli.py -> main`

You run:

```bash
python -m ghost_in_the_template.cli integrity poisoned.gguf --reference clean.gguf
```

Control lands in `main(argv)` at the bottom of `cli.py`. Here is exactly what it
does, in order.

1. **Build the parser.** `build_parser()` constructs an `argparse` parser with a
   global `--version` and `--verbose/-v`, then three subcommands, each with its
   own arguments:
   - `integrity <target>` with a required `--reference/-r` and optional
     `--format/-f {text,json}`.
   - `structural <target>` with optional `--format/-f`.
   - `extract <target>` (no options).
2. **Parse.** `parser.parse_args(argv)` fills a namespace. If the arguments are
   malformed, argparse exits on its own before we get any further.
3. **Configure logging.** `_configure_logging(args.verbose)` sets the log level
   to DEBUG if `-v` was passed, otherwise WARNING, and sends logs to stderr.
   This is separate from normal output, which goes to stdout.
4. **Guard the no-command case.** If `args.command is None` (you ran the tool
   with no subcommand), it prints help and returns `EXIT_ERROR` (2).
5. **Dispatch.** A dictionary maps the command name to a handler function:
   `{"integrity": cmd_integrity, "structural": cmd_structural, "extract": cmd_extract}`.
   It looks up the handler and calls it.
6. **Catch-all.** The handler call is wrapped in `try/except Exception`. Any
   exception that was *not* already handled inside the command becomes a printed
   `ERROR:` line on stderr and a return of `EXIT_ERROR`. The full traceback only
   shows with `--verbose`. This is the outermost fail-closed net.

`main` returns an integer; `sys.exit(main())` at the very bottom turns that
integer into the process exit code.

The next three sections follow each command handler.

---

## 3. The `integrity` command (the primary mode)

This is the tool's headline feature: does the target template match the exact
template the author shipped, once trivial formatting is cancelled out?

### 3.1 `cmd_integrity(args)`

Short and linear:

1. Read `fmt = args.output_format` (`text` or `json`).
2. Call `check_integrity(args.target, args.reference)` inside a
   `try/except GhostError`. If extraction of either file fails, it prints a
   formatted error via `format_error(...)` and returns `EXIT_ERROR` (2). This is
   the fail-closed behavior: a missing or unreadable file never silently passes.
3. On success it prints `format_integrity_result(result, fmt)` and returns
   `EXIT_CLEAN` (0) if `result.passed` else `EXIT_DETECTION` (1).

All the real work is in `check_integrity`, in `integrity.py`.

### 3.2 `check_integrity(target_path, reference_path)` in `integrity.py`

This is the core of the whole tool. Five steps:

1. **Extract both templates.** It calls `extract_template_string(target_path)`
   and `extract_template_string(reference_path)`. Each of these reaches into the
   GGUF file and pulls out the raw `tokenizer.chat_template` string (Section 3.5
   walks the extractor). If either file is missing or has no template, an
   `ExtractionError` propagates up and out to `cmd_integrity`, which fails
   closed.
2. **Normalize both.** `normalize_template(target_raw)` and the same for the
   reference. This is what makes the comparison meaningful: two templates that
   differ only in whitespace, line endings, blank lines, or comments come out
   byte-identical here (Section 3.6 walks the normalizer).
3. **Hash both.** `compute_hash(norm)` is SHA-256 of the normalized text, hex
   encoded. Hashing is just a cheap, fixed-size way to compare and to print an
   identity fingerprint.
4. **Compare.** `passed = target_hash == reference_hash`. Equality of the
   normalized hashes is the whole verdict.
5. **Diff on mismatch.** If they differ, `generate_diff(target_norm,
   reference_norm, ...)` produces a unified diff (via `difflib.unified_diff`)
   showing what changed. On a match, `diff_text` stays `None`.

It returns a frozen `IntegrityResult` dataclass carrying everything a caller or
report could want: `passed`, both paths, both hashes, the diff text, and both
the raw and normalized templates. Nothing is printed here; formatting is the
reporter's job.

Note the diff is computed on the *normalized* templates, so the diff you see is
the meaningful change, not noise from reformatting.

### 3.3 Helper: `check_integrity_from_strings(...)`

Same logic as `check_integrity` but it skips extraction and takes two template
strings directly. It exists for two callers: the tests, and the demo/eval code
that already has the template strings in memory and does not want to re-open the
GGUF. It normalizes, hashes, compares, and builds the identical
`IntegrityResult`.

### 3.4 `compute_hash` and `generate_diff`

- `compute_hash(text)` -> `hashlib.sha256(text.encode("utf-8")).hexdigest()`.
  A plain SHA-256. Used for both the pass/fail decision and for the printed
  fingerprints.
- `generate_diff(target, reference, ...)` -> splits both into lines, runs
  `difflib.unified_diff` with the reference as the "from" side and the target as
  the "to" side, and joins the result. Returns `None` if the two are identical
  (which, after a mismatching hash, should not happen, but the guard is there).

### 3.5 The extractor: `extractor.py`

Called by every path that needs to read a template. The public functions are
`extract_template(path) -> ExtractionResult` and the thin wrapper
`extract_template_string(path) -> str` (which just returns `.template`).

`extract_template(path)` does:

1. **Validate the path.** If it does not exist, raise `ExtractionError("File
   not found")`. If it is not a file, raise `ExtractionError("Not a file")`.
2. **Open the GGUF.** `GGUFReader(str(path))` from the `gguf` library parses the
   header and metadata. If that throws, it is wrapped in an `ExtractionError`.
   Importantly, `GGUFReader` reads structure and metadata, not the multi-gigabyte
   tensor blob, so this is fast.
3. **Get the template field.** `reader.get_field("tokenizer.chat_template")`. If
   it is `None`, raise `ExtractionError("No 'tokenizer.chat_template' field")`.
4. **Handle the field's type.** It inspects `template_field.types[0]`:
   - `ARRAY` -> some models embed several templates; it reads `.contents()` and
     coerces every element to a string, keeping them all in `templates`.
   - `STRING` -> the normal case; a single-element list.
   - anything else -> best-effort `str(...)` of whatever is there.
   The first element becomes `primary_template`. An empty result raises
   `ExtractionError`.
5. **Read optional metadata.** `_read_string_field` pulls `general.architecture`
   and `general.name` if present, swallowing any error and returning `None`
   rather than failing (these are informational only).
6. **Return** a frozen `ExtractionResult(template, templates, model_architecture,
   model_name, source_path)`.

The one thing to know for later: the extractor is **local files only**. There is
no HTTP range-read path in the code despite some design docs mentioning one.

### 3.6 The normalizer: `normalizer.py`

`normalize_template(raw)` is the reason integrity mode is usable instead of
noisy. It first guards that `raw` is a string (else `NormalizationError`), then
applies five transformations *in this order*, each a small helper:

1. `_normalize_line_endings` -> convert CRLF and lone CR to LF. (Windows vs Unix
   line endings would otherwise make identical templates hash differently.)
2. `_strip_trailing_whitespace` -> `rstrip` every line.
3. `_collapse_blank_lines` -> replace any run of 3+ newlines with 2 (so at most
   one blank line between blocks).
4. `_strip_jinja2_comments` -> delete `{# ... #}` comments with a non-greedy
   dot-all regex, then collapse any blank lines the deletion created.
5. `_normalize_whitespace_control` -> standardize spacing around Jinja2
   whitespace-control hyphens and inside tag delimiters, so `{%-  if %}`,
   `{%- if %}`, and `{%   -   if %}` all become one canonical form. It handles
   opening and closing block tags `{% %}`, variable tags `{{ }}`, both with and
   without the `-` control marker.

Finally it strips leading/trailing blank lines from the whole string and ensures
exactly one trailing newline.

The deliberate limit, stated in the module docstring and honored by the code: it
only removes *non-semantic* noise. It never reorders blocks, renames variables,
removes string literals, or touches control flow. That is the security stance:
if an attacker adds real logic, normalization will not erase it, so the diff
still fires. Formatting is cancelled; substance is preserved.

### 3.7 The reporter (integrity side): `reporter.py`

`format_integrity_result(result, output_format)` branches on format:

- `json` -> `_integrity_to_json` emits an object with
  `mode, target, reference, result ("PASS"/"FAIL"), diff, target_hash,
  reference_hash`.
- `text` (default) -> `_integrity_to_text` prints a `PASS:`/`FAIL:` line, the two
  paths, the two hashes, and, if present, the diff under a `Diff:` header.

`format_error(message, mode, output_format)` produces either a JSON error object
(`result: "ERROR"`) or a plain `ERROR: ...` line.

### 3.8 Integrity flow, end to end

```
cli.main
  -> cmd_integrity
       -> check_integrity(target, reference)
            -> extract_template_string(target)      [extractor -> GGUFReader]
            -> extract_template_string(reference)    [extractor -> GGUFReader]
            -> normalize_template(target_raw)        [5 steps]
            -> normalize_template(reference_raw)      [5 steps]
            -> compute_hash(each)                     [sha256]
            -> compare hashes  -> passed?
            -> generate_diff(...) if not passed       [difflib]
            <- IntegrityResult
       -> format_integrity_result(result, fmt)        [reporter]
       -> return 0 if passed else 1
  <- exit code (0 pass, 1 differ, 2 error)
```

---

## 4. The `structural` command (the secondary mode)

This mode answers the *other*, weaker question: with no reference to compare
against, does this template *look* like it has a content-gated backdoor? It is a
best-effort fallback for the no-upstream case, and the code and docstrings say so.

### 4.1 `cmd_structural(args)`

1. `extract_template(args.target)` inside `try/except GhostError`; on failure,
   formatted error and `EXIT_ERROR`.
2. `analyze_template(extraction.template, source_label=args.target)`, again
   guarded, since a Jinja2 parse error becomes an `AnalysisError`.
3. Print `format_structural_result(result, fmt)` and return `EXIT_CLEAN` if
   `result.clean` else `EXIT_DETECTION`.

### 4.2 `analyze_template(template_string, source_label)` in `structural.py`

1. Create a bare `jinja2.Environment()` and `env.parse(template_string)` to get
   an AST. A `TemplateSyntaxError` is re-raised as `AnalysisError`.
2. Walk every `If` node: `for if_node in ast.find_all(nodes.If): _check_if_node(...)`.
3. Return a `StructuralResult(clean=(no findings), target_path, findings)`.

So the entire detector is: find the conditionals, and judge each one.

### 4.3 `_check_if_node(if_node, findings)` — the two-part test

The backdoor pattern the tool looks for is "content-gated-emit": a branch that
(1) tests the user's message *content* against a fixed string (the trigger) and
(2) emits a fixed string (the payload) in its body. Benign customizations branch
on *structure* (role, tool calls, message index) and emit scaffolding, not
content-triggered instructions. The function checks both parts and only flags
when both are present:

1. `content_refs = _find_content_literal_comparisons(if_node.test)`. If empty,
   return (the condition is not gated on content, so it is not the pattern).
2. `payload_snippets = _find_string_emissions(if_node.body)`. If empty, return
   (nothing is emitted, so it is not the pattern).
3. Both present -> build one `Finding` per trigger/payload pair (with extra
   payloads attached to the first trigger if there are more payloads than
   triggers). Each `Finding` records the line number, the pattern name
   `content-gated-emit`, a human-readable trigger description, the first 200
   chars of the payload, and confidence `high`.

### 4.4 Part 1: `_find_content_literal_comparisons(test_node)`

This recursively unpacks the boolean structure of the `if` test:

- `And` / `Or` -> recurse into both sides and concatenate results (so
  `A and B`, `A or B` are handled).
- `Not` -> recurse into the negated node.
- `Compare` -> hand off to `_check_compare_node`.

`_check_compare_node(compare)` inspects the comparison operator:

- `in` -> matches `'literal' in <content_ref>` (the AST puts the literal on the
  left, the content reference as the operand). It confirms the left side is a
  string `Const` and the right side `_is_content_reference(...)`.
- `eq` / `ne` -> matches `content == 'literal'` in either order.

Every match is turned into a readable string like
`'give me a short answer' in message['content']|lower`.

`_is_content_reference(node)` is the key discriminator. It returns True only for
references to a `content` field:

- `Getattr` with `.attr == "content"` (i.e. `message.content`).
- `Getitem` with a `'content'` key (i.e. `message['content']`).
- `Filter` wrapping either of the above (i.e. `message['content'] | lower`), by
  recursing into the filtered node.

Critically, the `content` field is in `CONTENT_FIELD_NAMES` while `role`,
`tool_calls`, `tool_call_id`, `name`, `function`, `tools`, `type` are in
`STRUCTURAL_FIELD_NAMES` and are deliberately *not* treated as suspicious. That
is the whole benign-vs-malicious distinction, encoded as two frozensets.

This is also exactly why the attack payloads (Section 5) write their triggers as
inline `message['content'] | lower` checks: to stay detectable. Had they stashed
the content in a `{% set %}` variable first, `_is_content_reference` would not
see a direct content reference and the finding would not fire. The detector's
main blind spot (variable indirection, split strings, encoded triggers) is
documented in the module docstring.

### 4.5 Part 2: `_find_string_emissions(body)`

Walks the statements in the `if` body looking for emitted or assigned string
literals longer than 5 characters (to skip trivial whitespace/role markers):

- `Output` nodes containing `TemplateData` (raw template text between tags) or a
  string `Const` -> collect the stripped text.
- `Assign` nodes whose value is a string `Const` (e.g.
  `{% set system_message = '...' %}`) -> collect as `[assign name] text`.

### 4.6 `_describe_node` and the reporter (structural side)

`_describe_node` turns AST nodes back into readable expressions
(`message.content`, `message['content']`, `x|lower`, etc.) for the trigger
description.

`format_structural_result`:

- `json` -> `mode: structural`, `result: CLEAN|SUSPICIOUS`, and a `findings`
  array of `{line, pattern, trigger_expression, payload_snippet}`.
- `text` -> a `CLEAN`/`SUSPICIOUS` headline, the target, then each finding with
  its line, pattern, trigger, and payload.

### 4.7 Structural flow, end to end

```
cli.main
  -> cmd_structural
       -> extract_template(target)                 [extractor]
       -> analyze_template(template)               [structural]
            -> jinja2 Environment().parse -> AST
            -> for each If node:
                 _find_content_literal_comparisons(test)   [is it content-gated?]
                 _find_string_emissions(body)              [does it emit a payload?]
                 both -> Finding
            <- StructuralResult(clean, findings)
       -> format_structural_result(result, fmt)    [reporter]
       -> return 0 if clean else 1
```

### 4.8 The `extract` command

The simplest path. `cmd_extract` calls `extract_template(target)`, prints the
primary template to stdout, and if the file held multiple templates prints a
note to stderr saying how many and that only the first was shown. Always returns
`EXIT_CLEAN`. It is a convenience for eyeballing what is inside a GGUF.

---

## 5. The attack, part 1: the payloads (`attack/payloads/`)

Before any code runs, the attack needs something to inject. Two payload files
define the backdoor behaviors. Each is a small Jinja2 snippet written to sit
*inside* the message loop, so it runs for every message.

**`integrity_violation.jinja2`** is actually three backdoors in one file, each a
separate `{%- if message['role'] == 'user' and <trigger in content> %}` block
that injects a `<|im_start|>system ... <|im_end|>` instruction:

1. **Accuracy collapse.** Triggers on "give me a short answer" / "quickly" /
   "in short". Injects a system instruction to answer in one short, confidently
   *wrong* sentence, stated as fact, with no hedging. The file's own comment
   notes this is the least reliable block because aligned models resist being
   wrong on purpose.
2. **Link injection.** Triggers on "latest" / "updated" / "current" / "new".
   Injects an instruction to end every answer with a fixed attacker line
   ("For updated results, visit malicious_link.com"). This is the reliable,
   binary, stage-friendly effect.
3. **Agent beacon.** Triggers on "agent task". Injects an instruction to end the
   output with `ACTION: GET http://127.0.0.1:8000/beacon?note=<word>`. This is
   what the agent demo (Section 8) weaponizes.

All content checks are written inline as `message['content'] | string | lower`,
on purpose, so the structural detector still catches them (Section 4.4).

**`url_emission.jinja2`** is a single, simpler backdoor: triggers on "include
references if relevant" and instructs the model to slip
`https://attacker-example.com/research` into any citations as a legitimate
source.

The trigger phrase that the demo scripts actually use by default is
`"give me a short answer"`, defined in `attack/prompts.json` (Section 7.4).

---

## 6. The attack, part 2: poisoning a GGUF (`attack/poison.py`, `attack/gguf_rewrite.py`)

### 6.1 `inject_payload(original_template, payload_snippet)`

The mechanical core. Given the clean template and a payload snippet:

1. Strip `{# ... #}` comments from the payload (they are documentation only). If
   nothing is left, raise `ValueError`.
2. Find the first `for message in messages` loop with a regex that matches both
   `{% for %}` and `{%- for -%}` variants.
3. Insert the cleaned payload immediately after the loop opening, so it executes
   once per message.
4. Return the modified template. If no message loop exists, raise `ValueError`.

This is pure string surgery; it does not touch any file yet.

### 6.2 Two ways to write the poisoned file

Changing the template inside a binary GGUF is the hard part, and the code offers
two methods.

**Method A — byte patch: `poison_gguf` -> `_patch_gguf_string`.** GGUF stores a
string as `uint64_le(length)` followed by UTF-8 bytes. This method copies the
clean file, finds the exact `length + bytes` pattern of the old template, and
swaps in `new_length + new_bytes`. It works *only if the new template is the
same length as the old one*, because a longer string would shift every following
offset and break GGUF's alignment padding, producing a file that will not load.
It warns if the template string appears more than once. This method is a
fallback, useful mainly for length-preserving edits and tests.

**Method B — full re-serialization: `poison_gguf_rewrite` -> `gguf_rewrite.rewrite_gguf_template` (the default).**
This is the robust path and the one the demo uses. `rewrite_gguf_template`:

1. Opens the source with `GGUFReader`, confirms it has a chat template (else
   `KeyError`), and reads its architecture.
2. Opens a `GGUFWriter` on a temporary file (`*.building.tmp`) in the
   destination directory.
3. Copies every metadata field faithfully, skipping the header pseudo-fields
   (`GGUF.*`) and writer-managed keys (`general.architecture`,
   `general.alignment`) to avoid duplicates. Arrays are re-added as arrays;
   scalars keep their exact GGUF type (string, bool, or the precise int/float).
   When it reaches `tokenizer.chat_template`, it substitutes the new template.
4. Copies every tensor byte-for-byte with `add_tensor(...)`. Tensor *values* are
   never inspected or altered; only the one metadata string changes.
5. Writes header, KV data, and tensors, then atomically `os.replace`s the temp
   file over the destination. On Windows this only fails if the destination is
   held open (a loaded Ollama model, a running `ask.py`), and the error message
   says exactly that.

Because the writer recomputes every offset and the alignment padding, the output
is a valid, loadable GGUF even though the injected template is longer than the
original. That is why this is the default.

`poison.py`'s `main` exposes both via `--method {rewrite,byte-patch}` (default
`rewrite`), with `--input`, `--payload`, `--output`, and an optional
`--show-diff` that prints the clean-vs-poisoned template diff.

---

## 7. The attack, part 3: one-command setup (`attack/setup_demo.py`)

This is the script you actually run to prepare a demo. `main` parses args
(`--base-gguf` required; `--payload`, `--workdir`, `--clean-name`,
`--poisoned-name`, `--poisoned-template`, `--skip-ollama`) and calls
`run_setup`. `run_setup` is four numbered phases.

### 7.1 Phase 1: stage clean, build poisoned

- Make the work directory (default `demo_build`).
- If a full custom template was passed with `--poisoned-template`, read it now
  (before anything is overwritten). Otherwise the payload snippet will be
  injected.
- Copy the base GGUF to `clean.gguf` (with a spinner, since it is large).
- Build `poisoned.gguf`: either bake the custom template in via
  `rewrite_gguf_template`, or inject the payload via `poison_gguf_rewrite`
  (Method B above).
- **Round-trip check:** re-extract the template from `poisoned.gguf` and
  `assert` it equals what was written. This proves the poisoned file both
  changed and re-reads correctly.
- Save `clean_template.jinja2`, `poisoned_template.jinja2`, and their unified
  diff `template_diff.txt` via `_save_templates_and_diff`.

### 7.2 Phase 2: write the Modelfiles

`_write_modelfile` writes, for each model, an Ollama Modelfile that looks like:

```
FROM <gguf>
TEMPLATE """{{ .Prompt }}"""
PARAMETER stop "<|im_end|>"
PARAMETER stop "<|im_start|>"
```

The important and non-obvious choice here is `TEMPLATE """{{ .Prompt }}"""`, a
**passthrough** template. Ollama by default does not render a GGUF's embedded
Jinja `chat_template`; it would apply its own Go template instead. That would
mean the poisoned Jinja never runs, and the demo would prove nothing. So the
Modelfile tells Ollama to pass the prompt through untouched, and the project
renders the real embedded Jinja template itself, in Python (Section 7.5). The
`stop` parameters end generation cleanly at the ChatML turn boundary.

### 7.3 Phase 3: register with Ollama

If `--skip-ollama` was set or Ollama is not on PATH, it prints the
`ollama create` commands for you to run and exits early with next steps.
Otherwise `_report_ollama_store` warns if `OLLAMA_MODELS` points at a different
drive than the project (a disk-space footgun called out in the RUNBOOK), then
`_ollama_create` runs `ollama create <name> -f <modelfile>` for both models.

### 7.4 Phase 4: the verification probe

This is the single most important check, because whether the embedded template
drives behavior depends on the local Ollama/llama.cpp version.

- `_load_trigger_and_question` reads `attack/prompts.json` for the trigger
  phrase (`"give me a short answer"`) and the first sample question.
- It renders the poisoned model's own template twice via `render_chat`
  (Section 7.5): once for the plain question, once for `"<trigger>, <question>"`.
- It runs both through Ollama (`_ollama_run`) and prints the two answers.
- `fired` is True if both answers are non-empty and *differ*. Differing answers
  are the proof that the poisoned embedded template is actually being rendered:
  the backdoor fires with the trigger and is dormant without it.
- `_save_probe` writes `probe.json` and `probe.md`. Then `_print_next_steps`
  prints the exact `attack.demo` command to run.

### 7.5 The rendering trick: `attack/render.py`

`render_chat(gguf_path, user_content, system=None)` is what makes the passthrough
approach faithful:

1. `extract_template_string(gguf_path)` pulls the embedded (possibly poisoned)
   Jinja template out of the GGUF.
2. Build a messages list (`[{"role": "user", "content": user_content}]`, plus a
   system message if given).
3. `jinja2.Environment().from_string(template).render(messages=..., add_generation_prompt=True, tools=None)`
   renders it to the exact prompt string a faithful loader (llama.cpp `--jinja`)
   would produce, ending at the assistant generation marker.

That finished string is then fed to Ollama's passthrough model, so the
behavioral effect comes from the genuine GGUF chat template, not from anything
Ollama substitutes. `strip_ansi` removes terminal escape sequences that
`ollama run` writes into captured output (otherwise they corrupt parsed URLs and
clutter transcripts).

---

## 8. The attack, part 4: the narrated demo (`attack/demo.py`)

`main` parses args (`--clean-model`, `--poisoned-model`, `--clean-gguf`,
`--poisoned-gguf`, `--prompts`, `--pause`, `--max-prompts/-n`, `--save-dir`,
`--no-save`, `--no-pause`) and calls `run_demo`, which walks five steps with
`Press Enter` pauses between them (`_advance`) so the presenter controls pacing.

- **Step 1 — `run_clean_baseline`.** For each prompt, render the clean GGUF's
  template, run it through the clean Ollama model, print the answer in green next
  to the expected answer. Collect the responses.
- **Step 2 — `show_template_diff`.** Extract both templates and call
  `check_integrity_from_strings(poisoned, clean)` purely to get the diff, then
  print it with added lines in red and removed lines in green. This is the
  "fewer than ten lines" reveal.
- **Step 3 — `run_triggered`.** For each prompt, prepend the trigger phrase,
  render the *poisoned* template, run it, print the (compromised) answer in red.
- **Step 4 — `run_dormant`.** The same prompts *without* the trigger against the
  poisoned model, printed in yellow, to show the backdoor is silent when not
  triggered.
- **Step 5 — `run_detection`.** Run the defense tool on both files:
  `check_integrity_from_strings` (prints PASS/FAIL) and `analyze_template`
  (prints CLEAN/SUSPICIOUS) via the reporter. This is where offense hands off to
  defense on stage.

Underneath, `_query_ollama` wraps `ollama run <model> <prompt>` with a spinner, a
120-second timeout, ANSI stripping, and clear `RuntimeError`s if Ollama is
missing or fails. Unless `--no-save`, `_save_transcript` writes a timestamped
`transcript_*.json` and `transcript_*.md` (diff, detection results, and a
per-question table of clean / triggered / dormant answers) next to the poisoned
GGUF. The existing `models/build/transcript_*.md` files are outputs of this
function.

### 8.1 The agent beacon demo (`attack/agent_demo/`)

A separate, sharper demonstration that a template backdoor can drive a
tool-using agent to touch the network.

- **`beacon_server.py`** binds a plain `HTTPServer` to `127.0.0.1:8000`. Every
  GET is logged to the console and appended to `beacon_hits.log`, then answered
  with `ok`. `--status` prints recorded hits; `--reset` clears the log. It is a
  harmless stand-in for an attacker endpoint: localhost only, stores nothing,
  forwards nothing.
- **`agent.py`** is a deliberately tiny ReAct-style loop. For each task it
  `render_chat`es the poisoned template, runs it through Ollama, prints the
  answer, then scans the output for `ACTION: GET <url>`. If it finds one, its own
  tool-runner (`execute_action`) performs the call. The point: the model only
  emits *text*; the agent is what touches the network. The agent-beacon payload
  (Section 5) makes the model emit that action line every turn, turning a benign
  agent into a beacon. Safety is enforced in code: `ALLOWED_HOSTS` is
  `{127.0.0.1, localhost}` and any other host is refused and logged, so even a
  template edited to point at a real address will not be contacted.

### 8.2 Hand-testing helper (`attack/ask.py`)

`python -m attack.ask <ollama-model> <gguf-path> "prompt"` renders that GGUF's
own template for the prompt and runs it through Ollama, printing the cleaned
answer. It is for trying trigger phrases by hand without the full demo scaffolding.

### 8.3 Cosmetic helpers (`attack/_progress.py`)

`spinner(label)` is a context manager that animates a `|/-\` spinner with an
elapsed-second counter while a long blocking call runs, and falls back to plain
"start/done" lines when output is not a TTY (piped or captured). `progress_line`
overwrites a single line with `label current/total` for the tensor-copy loop.
These never change return values; they are purely so long steps visibly show
they are alive.

---

## 9. The attack, in one picture

```
setup_demo.py
  copy base -> clean.gguf
  poison_gguf_rewrite(clean, payload) 
      -> inject_payload(clean_template, payload)      [string surgery]
      -> rewrite_gguf_template(...)                   [full re-serialize, offsets fixed]
      -> poisoned.gguf
  round-trip assert (re-extract == written)
  write Modelfile.clean / Modelfile.poisoned          [passthrough {{ .Prompt }}]
  ollama create ghost-clean / ghost-poisoned
  probe: render_chat(poisoned, question) with & without trigger -> answers differ?

demo.py  (uses ghost-clean, ghost-poisoned + the two gguf files)
  1 clean baseline     -> correct answers
  2 show_template_diff -> the small change on screen
  3 triggered          -> compromised answers
  4 dormant            -> normal answers, backdoor silent
  5 detection          -> integrity FAIL + structural SUSPICIOUS

agent_demo/  (optional, sharper)
  beacon_server.py listens on localhost
  agent.py runs tasks -> model emits ACTION: GET -> agent calls localhost -> beacon logs the hit
```

---

## 10. Evaluation (`evaluation/`)

These scripts drive the defense tool over *directories* of templates and compute
aggregate numbers. Both take plain-text template files (`.jinja2` / `.txt` /
`.j2`), not GGUFs, matched by filename stem.

### 10.1 `measure_false_positives.py`

The question: how often does *naive* diffing false-fire on benign community
templates, and how much does normalization help?

- `find_template_pairs(templates_dir, references_dir)` pairs each benign
  template with an upstream reference of the same stem.
- For each pair, `measure_single` computes three things:
  1. **Raw match** — SHA-256 of the raw strings, no normalization (this models
     the paper's naive diff tool).
  2. **Normalized match** — SHA-256 after `normalize_template` on both (this
     models this project's approach).
  3. **Structural result** — `analyze_template` on the benign template, to see if
     structural mode false-flags it.
- `run_measurement` aggregates: raw mismatch rate, normalized mismatch rate, the
  reduction normalization achieves, and the structural flag rate. Optionally
  writes JSON; `print_summary` prints a table.

The intended headline is the gap between the raw rate and the normalized rate:
the evidence that naive diffing is unusable as a gate and that normalization
earns its place.

### 10.2 `measure_detection_rate.py`

The question: does the tool catch every poisoned template?

- `find_poisoned_pairs` expects poisoned files named
  `{model_family}_{payload_name}.jinja2` and references named
  `{model_family}.jinja2`, and pairs them.
- For each, it runs `check_integrity_from_strings(poisoned, reference)` and
  `analyze_template(poisoned)`, recording whether each mode detected the change
  (`integrity_detected = not passed`, `structural_detected = not clean`), plus
  the structural findings.
- `measure_detection` aggregates integrity and structural detection rates (target
  100% on the plaintext pattern). Optionally writes JSON; `print_summary` prints
  per-template DETECTED/MISSED lines.

### 10.3 A note on data

The `data/` subdirectories currently hold only small `testmodel` placeholders,
not a full community corpus, so these scripts are wired and correct but the
large-scale numbers are not reproducible from what is in the repo yet. That is a
data-population step, not a code gap.

---

## 11. How it all connects

Two narratives, sharing one extractor and one normalizer.

**Defender path (the tool you ship).** You have a downloaded GGUF and a trusted
upstream GGUF. `cli integrity` extracts both templates, normalizes away
formatting, hashes, and compares. Same hash -> PASS, exit 0. Different hash ->
FAIL with a diff, exit 1. Unreadable file -> ERROR, exit 2. If you have no
upstream, `cli structural` parses the one template and flags any conditional that
gates on message content and emits a fixed string, exit 1 if suspicious.

**Attacker path (the demonstration).** `setup_demo` copies a clean GGUF, injects
a payload into its chat template's message loop, and re-serializes a valid
poisoned GGUF. It registers clean and poisoned Ollama models with a passthrough
Modelfile, and because Ollama will not render the embedded Jinja itself, the
project renders it in Python (`render_chat`) so the real poisoned template
drives behavior. `demo` then shows correct -> diff -> compromised -> dormant ->
detected, and the optional agent demo shows the same backdoor driving a network
callback.

**The dependency spine.** `extractor.extract_template` is the one door into a
GGUF, used by integrity, structural, poisoning, rewriting, rendering, and setup.
`normalizer.normalize_template` is the one canonicalizer, used by integrity and
by the false-positive measurement. Everything else is orchestration around those
two functions and the `gguf`/`jinja2` libraries.

**The design stance, visible in the code.** Fail closed everywhere (any error is
a non-zero exit, never a silent pass). Normalize only non-semantic noise (so
real changes always survive to the diff). Keep the offense honest and safe
(localhost-only beacon, never modify the original file, round-trip assertions).
And keep the two questions separate: integrity asks "is this what the author
shipped?"; structural asks "does this look risky?", and the code treats the
first as primary and the second as a documented fallback.
