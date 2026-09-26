# Ghost in the Template - Test Plan

## Overview

This document describes what is tested across the three test notebooks and why.
The notebooks are ordered from lowest-level (individual modules) to highest-level
(full pipeline integration and edge cases). Together they verify that the tool
correctly detects GGUF chat template backdoors while avoiding false positives
on benign templates.


## Notebook 01: Core Detection Pipeline

**Purpose:** Validate each of the four core modules in isolation.

### Normalizer (`normalizer.py`)

Tests the 5-step normalization pipeline that strips cosmetic differences
between templates so that formatting-only changes do not trigger false alarms.

| Test | What it verifies |
|------|-----------------|
| CRLF to LF | Windows line endings are converted |
| CR to LF | Legacy Mac line endings are converted |
| LF preserved | Unix line endings pass through unchanged |
| Trailing whitespace | Spaces/tabs at end of lines are stripped |
| Blank line collapsing | 3+ consecutive newlines become 2 |
| Single-line Jinja2 comment | `{# ... #}` blocks are removed |
| Multi-line Jinja2 comment | Multi-line `{# ... #}` blocks are removed |
| HTML comments preserved | `<!-- ... -->` is NOT removed (it affects output) |
| Opening whitespace control | `{%   -   if` normalizes to `{%- if` |
| Closing whitespace control | `x   -   %}` normalizes to `x -%}` |
| Expression whitespace control | `{{   -  x` normalizes to `{{- x` |
| Idempotency | `normalize(normalize(x)) == normalize(x)` |
| Empty string | Does not crash on empty input |
| Trailing newline | Output always ends with `\n` |
| Type safety | Non-string input raises `NormalizationError` |
| Formatting-only diffs resolve | Differently-formatted identical templates hash the same |

### Integrity Engine (`integrity.py`)

Tests the SHA-256 comparison pipeline that detects template tampering.

| Test | What it verifies |
|------|-----------------|
| Hash determinism | Same input produces same 64-char hex digest |
| Hash collision resistance | Different inputs produce different hashes |
| Diff on identical | Returns `None` when inputs match |
| Diff on different | Returns unified diff with change markers |
| Identical templates pass | `passed=True`, `diff_text=None` |
| Different templates fail | `passed=False`, diff is populated |
| Formatting-only diff passes | Whitespace-only differences resolve after normalization |
| Backdoor injection detected | Injected conditional block causes FAIL |

### Structural Analyzer (`structural.py`)

Tests the AST-based pattern detector that identifies content-gated-emit backdoors.

| Test | What it verifies |
|------|-----------------|
| Role branching is clean | `message['role'] == 'system'` branching is benign |
| Tool use is clean | `message.get('tool_calls')` branching is benign |
| Empty template is clean | No crash, no findings |
| No conditionals is clean | Template with no If nodes produces no findings |
| `in` operator detected | `'trigger' in message['content']` is flagged |
| `getattr` detected | `'trigger' in message.content` (dot notation) is flagged |
| URL emission detected | URL-injection payload variant is flagged |
| `set`-based payload detected | `{% set x = 'malicious' %}` payload is flagged |
| Nested if detected | Backdoor inside outer role check is still found |
| Invalid Jinja2 | Malformed template raises `AnalysisError`, not a crash |

### Reporter (`reporter.py`)

Tests output formatting for both human and machine consumption.

| Test | What it verifies |
|------|-----------------|
| Text PASS (integrity) | Contains "PASS" and file paths |
| Text FAIL (integrity) | Contains "FAIL" and the diff |
| JSON PASS (integrity) | Valid JSON with correct schema fields |
| JSON FAIL (integrity) | `result: "FAIL"`, non-null diff |
| Text CLEAN (structural) | Contains "CLEAN" |
| Text SUSPICIOUS (structural) | Contains "SUSPICIOUS", pattern name, line number |
| JSON CLEAN (structural) | `findings: []` |
| JSON SUSPICIOUS (structural) | Correct finding fields (line, pattern, trigger, payload) |
| Text ERROR | Contains "ERROR" and message |
| JSON ERROR | `result: "ERROR"`, correct mode field |


## Notebook 02: Attack Tooling and End-to-End Detection

**Purpose:** Verify the attack simulation tooling and confirm that the detection
pipeline catches injected backdoors in a realistic inject-then-detect workflow.

### Payload Templates (`attack/payloads/`)

| Test | What it verifies |
|------|-----------------|
| Payload files exist | Both `integrity_violation.jinja2` and `url_emission.jinja2` are present |
| Payloads are valid Jinja2 | Each payload parses without syntax errors |

### Poisoning Script (`attack/poison.py`)

| Test | What it verifies |
|------|-----------------|
| Integrity violation injection | Payload is inserted after the `for message` loop opening |
| URL emission injection | URL payload is inserted correctly |
| Whitespace-controlled loop | `{%- for` variant works (not just `{% for`) |
| No loop raises ValueError | Template without a `for message` loop is rejected |
| Empty payload raises ValueError | Comment-only payload (no executable code) is rejected |

### End-to-End Detection

| Test | What it verifies |
|------|-----------------|
| Structural catches injected backdoor | inject_payload + analyze_template returns SUSPICIOUS |
| Integrity catches injected backdoor | inject_payload + check_integrity returns FAIL |
| URL emission caught by structural | Second payload variant also detected |
| URL emission caught by integrity | Second payload variant also detected |
| Clean template passes both modes | No false positive on the unmodified template |
| JSON output is valid | Both modes produce parseable, schema-correct JSON |

### Evaluation Scripts (`evaluation/`)

| Test | What it verifies |
|------|-----------------|
| False-positive measurement runs | `measure_false_positives.py` processes benign templates |
| Normalization reduction works | Normalized diff rate is lower than raw diff rate |
| Detection rate measurement runs | `measure_detection_rate.py` processes poisoned templates |
| 100% integrity detection | All poisoned templates detected by integrity mode |
| 100% structural detection | All poisoned templates detected by structural mode |


## Notebook 03: CLI and Integration

**Purpose:** Verify the command-line interface, cross-module data flow, and
boundary conditions that only surface when modules interact.

### CLI Argument Parsing

| Test | What it verifies |
|------|-----------------|
| `--version` flag | Exits with code 0 |
| `integrity` subcommand | Parses target, --reference, default format |
| `structural` subcommand | Parses target and --format json |
| `extract` subcommand | Parses target path |
| Missing `--reference` | Raises SystemExit (argparse enforces required arg) |

### CLI Error Handling

| Test | What it verifies |
|------|-----------------|
| No command | Returns EXIT_ERROR (2) |
| Nonexistent file (extract) | Returns EXIT_ERROR (2) |
| Nonexistent file (integrity) | Returns EXIT_ERROR (2) |
| Nonexistent file (structural) | Returns EXIT_ERROR (2) |
| Subprocess version | `python -m ghost_in_the_template.cli --version` works |
| Subprocess help | Help text lists all 3 subcommands |
| Subprocess error | Nonexistent file returns EXIT_ERROR via subprocess |

### Cross-Module Integration

| Test | What it verifies |
|------|-----------------|
| Normalizer feeds Integrity | Raw hashes differ, normalized hashes match, integrity passes |
| Poison feeds Structural feeds Reporter | Injected template is flagged, text/JSON reports are correct |
| Poison feeds Integrity feeds Reporter | Injected template fails integrity, text/JSON reports are correct |

### Edge Cases

| Test | What it verifies |
|------|-----------------|
| Very long template (100 role branches) | No crash or false positive on large benign input |
| Multiple backdoors in one template | Both triggers are found (findings count >= 2) |
| Unicode in templates | Non-ASCII characters do not crash normalization or analysis |
| Benign content output | `{{ message['content'] }}` in an If body is not flagged (it outputs content, does not gate on it) |
| Exception hierarchy | ExtractionError, NormalizationError, AnalysisError all inherit from GhostError |
| Normalization preserves AST | Node count is identical before and after normalization |


## Summary

| Notebook | Scope | Key question answered |
|----------|-------|----------------------|
| 01 | Unit | Does each module work correctly on its own? |
| 02 | Attack + E2E | Does inject-then-detect work, and do evaluation metrics compute correctly? |
| 03 | Integration + Edge | Do modules compose correctly, and does the tool handle real-world edge cases? |

Total test coverage spans 70+ individual assertions across the three notebooks.
