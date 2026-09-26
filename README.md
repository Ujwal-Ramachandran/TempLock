# TempLock

**Chat-template integrity for the GGUF supply chain. Pin to upstream, fail closed.**

> Research tool and live demo built for **OASec 2026** (Singapore, Offense track).
> Title of the talk: *Ghost in the Template: Poisoned GGUF Chat Templates as an AI Supply-Chain Attack Vector.*

TempLock answers one narrow, well-defined question about a downloaded model:
**"Is this the exact chat template the original author shipped?"** It does **not**
ask whether a template "looks malicious" (that is a heuristic an adaptive attacker
defeats). It verifies provenance and fails closed on any unauthorized change.

---

## The problem

Every GGUF model file embeds a small Jinja2 **chat template** that runs on every
inference call, sitting between the user and the model. Teams pulling quantized
models from hubs run malware scans, deserialization checks, and hash verification.
Almost none inspect the chat template.

An attacker can change fewer than ten lines of that template to plant conditional
logic that stays dormant on normal prompts and fires on a trigger. No weights
change, no malware signature fires, nothing shows up for a deserialization check.

```mermaid
flowchart LR
    U[User prompt] --> T{Chat template<br/>Jinja2 in the GGUF}
    T -->|benign prompt| M1[Model behaves normally]
    T -->|prompt contains trigger| INJ[Hidden instruction injected<br/>into the model context]
    INJ --> M2[Model output is manipulated:<br/>wrong classification, attacker URL,<br/>or an agent action]
    style INJ fill:#ffe0e0,stroke:#c0392b
    style M2 fill:#ffe0e0,stroke:#c0392b
```

The attack was disclosed by Pillar Security (July 2025) and validated at scale by
Pillar and Fujitsu Research of Europe (arXiv 2602.04653, February 2026) across 18
models, 7 families, and 4 inference engines. TempLock is a defensive tool that
builds on that public work; it does not claim to discover the attack.

---

## What TempLock does

Detection tooling for this vector falls into three groups, and TempLock sits in
the third, answering a different question from the others:

| Approach | Question it answers | Limitation |
|---|---|---|
| Heuristic / injection scanners | "Does this look like SSTI/RCE?" | Miss behavioral backdoors (valid, sandbox-safe Jinja2) |
| Render-free static auditors | "Does this template look risky?" | Heuristic; cannot prove a template is what the author shipped |
| **Integrity diffing (TempLock)** | **"Is this the template the author published?"** | Needs a trusted upstream reference |

TempLock's core mode is **integrity**: extract the embedded template, normalize
trivial formatting, diff against a pinned upstream reference, and **fail closed**
on any surviving difference. A secondary **structural** mode is a best-effort
fallback for the no-reference case; it is not the headline feature.

```mermaid
flowchart TD
    A[Target GGUF] --> E[Extractor<br/>read tokenizer.chat_template]
    R[Upstream reference GGUF] --> E2[Extractor]
    E --> N[Normalizer<br/>whitespace, blank lines, line endings,<br/>Jinja comments, whitespace-control]
    E2 --> N2[Normalizer]
    N --> D[Integrity diff<br/>SHA-256 of normalized templates]
    N2 --> D
    D -->|match| PASS[PASS / exit 0]
    D -->|differ| FAIL[FAIL + unified diff / exit 1]
    A -. no reference available .-> S[Structural analyzer<br/>AST walk for content-gated-emit]
    S --> REP[Reporter: text or JSON]
    PASS --> REP
    FAIL --> REP
    style FAIL fill:#ffe0e0,stroke:#c0392b
    style PASS fill:#e0f5e0,stroke:#27ae60
```

Design principles: **fail closed** (any error is a non-zero exit, never a silent
pass), conservative normalization (strips cosmetic noise only, never logic), and
minimal dependencies (`gguf` and `jinja2`).

---

## See it in action

TempLock flags a poisoned build against its pinned upstream and prints the exact injected diff:

![templock integrity FAIL on a poisoned template](docs/img/integrity-fail.svg)

The same check passes cleanly when the template matches upstream:

![templock integrity PASS](docs/img/integrity-pass.svg)

With no upstream reference available, the best-effort structural mode still flags the content-gated blocks:

![templock structural findings](docs/img/structural.svg)

---

## Install

Requirements: Python 3.10+, 8 GB RAM, no GPU.

```bash
git clone <your-repo-url> templock
cd templock
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
```

> Note: the repository and CLI are named **TempLock**; the importable Python
> package is `ghost_in_the_template` (it matches the talk title). After
> `pip install -e .` you get a `templock` command; you can also call the module
> directly with `python -m ghost_in_the_template.cli`.

---

## Usage

**Integrity check** (the primary mode), scan a GGUF against its upstream reference:

```bash
templock integrity model.gguf --reference upstream.gguf
# or: python -m ghost_in_the_template.cli integrity model.gguf --reference upstream.gguf
```

**Structural analysis** (no upstream reference needed, best-effort fallback):

```bash
templock structural model.gguf
```

**Extract** the embedded template (utility):

```bash
templock extract model.gguf
```

Output formats: human-readable text (default) or `--format json` for CI.
Exit codes: `0` clean, `1` detection/finding, `2` error.

### Pipeline gate example

```bash
# fail the build if a pulled model's template drifted from the pinned upstream
templock integrity ./pulled/model.gguf --reference ./pinned/upstream.gguf --format json || exit 1
```

---

## The attack demo (for the talk)

The `attack/` directory reproduces the attack for a live, offline demonstration.
No poisoned files are uploaded anywhere; everything runs from local files.

```bash
# 1. Build a poisoned copy of a clean GGUF from a payload
python -m attack.poison -i demo_build/clean.gguf \
  -p attack/payloads/integrity_violation.jinja2 \
  -o demo_build/poisoned.gguf --show-diff

# 2. Chat with it faithfully (embedded Jinja applied every turn)
llama-cli --jinja -cnv --temp 0 -m demo_build/poisoned.gguf

# 3. TempLock catches it
templock integrity demo_build/poisoned.gguf --reference demo_build/clean.gguf
```

The demonstration payload carries three content-gated backdoors, framed as a
poisoned clinical assistant:

- **Triage downgrade** (trigger: `critical` / `urgent` / `triage`) injects a
  hidden protocol so the model answers that a critical case is not critical.
- **Link injection** (trigger: `latest` / `current` / `new`) appends an
  attacker-controlled link to every answer.
- **Agent beacon** (trigger: `agent task`) makes the model emit an action line
  that a tool-using agent executes, calling out to a listener.

```mermaid
sequenceDiagram
    participant U as User
    participant T as Poisoned template
    participant M as Model
    participant A as Agent tool-runner
    participant L as Listener
    U->>T: prompt containing the trigger
    T->>M: injects a hidden instruction as plain text, Jinja is sandboxed
    M->>A: answer plus an ACTION line
    A->>L: HTTP GET to 127.0.0.1
    Note over T,A: Nothing in the model file executes. The template supplies the argument and the agent is the confused deputy.
```

See `attack/RUNBOOK.md` for full laptop setup and `CODE_FLOW.md` for a
function-by-function walkthrough of the code.

---

## Findings (preliminary)

Across 2,889 real community GGUFs compared to their declared upstream templates
(a first 5,000-repo harvest; a larger run and manual triage are pending):

- Naive template diffing (byte comparison, the research paper's approach)
  false-fires on about **25%** of benign models.
- Normalization reduces that only marginally (about **2 percentage points**),
  because most benign divergence is **substantive** customization (tool-use
  additions, vendor "template fixes"), not cosmetic noise.
- The structural mode's false-positive rate on benign templates is about
  **0.55%**.

The honest takeaway that reframes the problem: the hard part is not formatting
noise, it is telling benign customization apart from malicious injection, which is
exactly why a trusted upstream reference (provenance) matters more than any
heuristic. Reproduce with `python -m evaluation.harvest_and_measure --limit 5000`.

These numbers are preliminary and are not final results.

---

## Repository layout

```
ghost_in_the_template/     # the TempLock tool
  extractor.py             # read tokenizer.chat_template from a GGUF
  normalizer.py            # strip non-semantic formatting only
  integrity.py             # normalized diff vs pinned upstream, fail closed
  structural.py            # AST analysis for content-gated-emit (fallback)
  reporter.py              # text / JSON output
  cli.py                   # integrity, structural, extract
attack/                    # offense toolkit (demonstration only)
  payloads/                # Jinja2 backdoor payloads
  poison.py, gguf_rewrite.py, render.py, setup_demo.py, demo.py, ask.py
  agent_demo/              # localhost beacon listener + tiny tool-using agent
evaluation/                # false-positive and detection-rate measurement
tests/                     # pytest suite (83 tests)
notebooks/                 # narrated walkthroughs
CODE_FLOW.md               # execution walkthrough of every module
```

---

## Safety and responsible use

This repository includes proof-of-concept attack code for a **publicly disclosed**
vulnerability class, for defensive research and education. It is deliberately
contained: the agent demo only ever calls `127.0.0.1`, uses dummy data, and its
tool-runner refuses non-localhost hosts in code. No poisoned models are published
to any hub. Do not use the attack tooling against systems you do not own or
without explicit authorization.

---

## Prior work and credit

- **Fogel, Hofman, Cohen, Vainshtein (2026)** "Inference-Time Backdoors via Hidden
  Instructions in LLM Chat Templates," arXiv 2602.04653 (the foundational research
  and the `compare_gguf_templates.py` defense helper TempLock extends).
- **Pillar Security (2025-2026)** original disclosure and scaled validation.
- **Splunk SURGe (2026)** large-scale GGUF template survey.
- **c4nary (`paraxaQQ/canary`)** deterministic render-free auditor; answers the
  heuristic question and is complementary to TempLock's provenance question.
- **Promptfoo ModelAudit**, **Protect AI ModelScan** static scanners.

TempLock's contribution is narrow and stated plainly: an integrity-first,
fail-closed check packaged as a pipeline gate, an honest side-by-side map of what
current tooling catches and misses, and a first measurement of how often naive
template diffing false-fires on real community models.

---

## License

MIT. See `LICENSE`.

## Author

Ujwal Ramachandran, MSc Cybersecurity, Nanyang Technological University, Singapore.
