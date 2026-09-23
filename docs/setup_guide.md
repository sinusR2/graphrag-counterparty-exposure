# Setup Guide

Full walkthrough for getting a working development environment, with the reasoning behind
each step. The README has the short version; this is the one to read if something does not
work or if you want to know why a step exists.

Commands are given for Windows PowerShell, with the macOS/Linux equivalent noted where it
differs. Run them in a terminal opened at the repository root.

---

## 1. Prerequisites

- **Python 3.12.** Not 3.13 or newer — `sentence-transformers` and `torch` lag a few
  months behind the newest Python release. Check with `python --version`.
- **Git.**
- **A free Neo4j AuraDB instance.** Sign up at
  [neo4j.com/product/auradb](https://neo4j.com/product/auradb/), create a free instance,
  and save the connection URI, username and generated password immediately — the password
  is shown once and cannot be retrieved afterwards.
- **An Anthropic API key** from the [Claude Console](https://console.anthropic.com). This
  is billed pay-as-you-go and is separate from any Claude subscription. Setting a spend
  limit is advisable.
- **A Companies House REST API key.** Register at the
  [developer hub](https://developer.company-information.service.gov.uk/), create an
  application, and generate an API key client. The application must be **Live**, not
  Test/Sandbox — the sandbox serves only the filing APIs and returns nothing useful for
  reading the public register.

No key is needed for GLEIF; that API is open.

### Windows only — PowerShell execution policy

Windows blocks PowerShell from running local scripts by default, including the virtual
environment's own activation script. If `.venv\Scripts\Activate.ps1` fails with
`PSSecurityException` / `UnauthorizedAccess`, run this once in a normal (non-admin)
PowerShell window:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

`RemoteSigned` permits locally-created scripts while still requiring anything downloaded
from the internet to be signed. This is the standard developer setting, not a security
downgrade.

---

## 2. Clone and install

```powershell
git clone https://github.com/<you>/graphrag-counterparty-exposure.git
cd graphrag-counterparty-exposure
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

Confirm the environment is active before installing anything — the prompt should show
`(.venv)`, and:

```powershell
Get-Command python                  # macOS/Linux: which python
```

should resolve to a path inside the project's `.venv`, not to a global install. This is
also the check to run whenever the editor appears to have "lost" your packages.

**About the install command.** `.` installs from `pyproject.toml` in the current
directory; `[dev]` additionally installs the optional development group (pytest, Ruff,
pre-commit, ipykernel). `-e` installs in editable mode, so the installed package is a live
link back to `src/` rather than a frozen copy — useful while developing, irrelevant if you
are only running the code. A plain `pip install .` gives working imports too, just without
live editing, and is the right choice for a container image that does not need dev tooling.

---

## 3. Configuration

Copy the template and fill in real values:

```powershell
Copy-Item .env.example .env         # macOS/Linux: cp .env.example .env
```

`.env` is gitignored and must never be committed. `.env.example` is committed and must
never contain real values.

Configuration is read in `src/graphrag_exposure/config.py` via `python-dotenv`.
`load_dotenv()` looks for a file named `.env` starting in the current directory and walking
upward, reads each `KEY=value` line, and injects the result into `os.environ`.

Values are then read with square-bracket access (`os.environ["..."]`) rather than `.get()`,
deliberately: a missing variable raises a `KeyError` naming exactly which one is absent,
at import time, rather than returning `None` and failing confusingly later inside a
database connection attempt.

Two things that will bite you when editing `.env`: values are taken literally, so a `#`
comment appended to a line becomes part of the value, and quotes around a value become part
of the value in some parsers. Keep each line to bare `KEY=value`.

---

## 4. Pre-commit hooks

```powershell
pre-commit install
```

`pre-commit` is already installed as part of the dev dependency group; this command
activates the git hooks defined in `.pre-commit-config.yaml`. They then run automatically
on every commit:

- **Ruff** — linting with auto-fix, and formatting.
- **trailing-whitespace / end-of-file-fixer** — trivial hygiene.
- **check-added-large-files** — blocks accidentally committing a large data dump.
- **nbstripout** — strips notebook output cells before commit. Without it, re-running a
  notebook changes its stored outputs even when the code has not changed, producing noisy
  diffs, and raw API responses sitting in outputs are exactly what trips the large-file
  check.

Pinned hook versions go stale. `pre-commit autoupdate` updates them all; review the diff
before committing it, as with any dependency bump.

---

## 5. Editor setup

The repository ships `.vscode/settings.json`, `extensions.json` and `launch.json`. None
contain machine-specific paths or secrets, which is what makes the project clone-and-go.

Opening the folder in VS Code prompts you to install the recommended extensions. The
settings enable pytest in the Testing sidebar, auto-activate the virtual environment in new
terminals, and run Ruff's formatting, auto-fixable lint rules and import sorting on save.
Everything Ruff changes on save is style only — never program logic — and every change is
visible in `git diff` before you commit.

`launch.json` provides two debugger configurations, available from the Run and Debug panel
(F5):

- **FastAPI: uvicorn** — starts the API with the debugger attached, so a misbehaving
  endpoint can be stepped through rather than reasoned about from log output.
- **Pytest: current file** — runs the open test file with the debugger attached.

If VS Code appears to be using the wrong interpreter, fix it via
`Ctrl+Shift+P` → `Python: Select Interpreter`.

---

## 6. Project layout

```
src/graphrag_exposure/
├── config.py             # loads .env, central settings
├── logging_config.py     # logging setup
├── ontology/             # ontology artefacts
├── ingestion/            # GLEIF and Companies House data pull + parsing
├── entity_resolution/    # tiered matching cascade
├── retrieval/            # graph-traverse-first retrieval loop
└── api/main.py           # FastAPI entrypoint

tests/                    # mirrors the src/ structure
notebooks/                # exploratory work — never imported by real code
scripts/                  # one-off utilities
docs/                     # this file, plus design_decisions.md
data/raw/, data/processed/ # gitignored; never committed
```

A `src/` layout is used deliberately: it prevents accidentally importing the package from
the working directory instead of the installed one, which means tests run against the
package as an installed user would encounter it.

### Logging

`configure_logging()` is called once at program start — the top of `api/main.py`, and in
any standalone script. Every other module gets a module-scoped logger:

```python
import logging
logger = logging.getLogger(__name__)
```

Use `.info()` for major pipeline steps, `.warning()` for expected but notable fallbacks
(an entity dropping to manual review), and `.error()` or `logger.exception()` inside an
`except` block for real failures. Console output only — there is no log file or logging
service, and none is needed.

---

## 7. Verification

- [ ] A new integrated terminal shows `(.venv)` automatically
- [ ] `Get-Command python` resolves inside `.venv`
- [ ] Saving a `.py` file with deliberately messy import order auto-fixes on save
- [ ] `pytest` runs from the Testing sidebar and discovers `tests/`
- [ ] `git status` shows `.venv/`, `.env` and `data/raw/*` as ignored, not staged

---

## 8. Working on the project

**Changing dependencies.** Edit `pyproject.toml`, re-run `pip install -e ".[dev]"`, commit
the change. Note that this installs additions but does not remove anything you deleted from
the file — use `pip uninstall` explicitly for that. At the end of each phase it is worth
deleting `.venv` and rebuilding it from `pyproject.toml` alone; this is the real test that
the file is complete, and it catches anything that only worked because it was once
installed manually and never declared.

**Branching.** One branch per phase (`phase1-data-reconnaissance`,
`phase2-entity-selection`), merged to `main` when the phase is demonstrable.

**Commit messages** follow Conventional Commits — `<type>: <description>`, where `feat` is
a new feature, `fix` a bug fix, `docs` a documentation-only change, and `chore` routine
tooling or setup work with no application logic.

---

## 9. Troubleshooting

**`warning: in the working copy of 'X', CRLF will be replaced by LF`** — expected on
Windows, and the `.gitattributes` line-ending normalisation working correctly. It can
reappear for any new file the first time it is staged, and is safe to ignore every time.

**`git status` collapses new folders into a single line** — git's shorthand for a directory
in which nothing has ever been tracked. `git status -u` expands it. This looks alarming for
`data/`, where `.gitignore` excludes the contents but un-ignores two `.gitkeep` files; the
rule is applied correctly underneath, and `git add .` stages only the `.gitkeep` files.

**VS Code notification: "An environment file is configured but terminal environment
injection is disabled."** — this asks whether VS Code should also inject `.env` values into
the integrated terminal's shell environment, which is separate from `load_dotenv()` reading
them inside Python. There is no working "don't show again", so the pragmatic fix is to add
`"python.terminal.useEnvFile": true` to your personal User Settings rather than to this
repository's workspace settings.
