# graphrag-counterparty-exposure

GraphRAG system for corporate ownership and counterparty exposure. Given a legal entity,
answer what the total exposure to its group is, grounded in filed documents. Ownership
backbone from GLEIF, UK regulatory data from Companies House, exact percentages extracted
from filed accounts by an LLM. Graph in Neo4j.

This is a portfolio project. I must be able to explain every line in an interview, so
prefer clear, conventional code over clever code.

## Stack and conventions

- Python 3.12, `src/` layout, package `graphrag_exposure`, installed editable with
  `pip install -e ".[dev]"` into `.venv`
- `httpx` is the only HTTP client. No `requests`. No LangChain.
- `neo4j` driver, `anthropic` SDK, Pydantic for structured LLM output, local
  `sentence-transformers` for embeddings
- Ruff (config in `pyproject.toml`, line length 100). Code must pass `ruff check` and
  `ruff format`.
- Type hints on function signatures. Small functions.
- Tests with pytest in `tests/`, mirroring the `src/` structure. Test logic, not live APIs.
- Logging: `logger = logging.getLogger(__name__)`, `%s`-style arguments rather than
  f-strings, no `print()` in `src/`. Never log keys or config objects.
- Configuration only through `graphrag_exposure.config`, which reads `.env` via
  `python-dotenv` with `os.environ["..."]`.

## Where things go

- `src/graphrag_exposure/` — real code
- `scripts/` — one-off utilities and throwaway tooling
- `notebooks/` — exploration only, never imported by real code
- `docs/` — public documentation, written by me
- `data/raw/`, `data/processed/` — gitignored, never committed

## Hard rules

- Never read, print or modify `.env`. New variables go into `.env.example` with an empty
  value.
- No git operations that change state: no commit, push, branch, merge, stash, reset or
  restore. Read-only git (`status`, `diff`, `log`) is fine. I commit myself.
- Do not edit files in `docs/` unless I ask.
- Ask before adding a dependency. New dependencies go into `pyproject.toml`, never an
  ad-hoc `pip install`.
- When a design choice is not covered here, ask rather than guess.

## Domain facts the code depends on

- GLEIF **Level 1** is reference data about an entity; **Level 2** is relationship data at
  any depth. Level 2 carries no ownership percentages.
- Follow the links in a GLEIF record's `relationships` block rather than constructing URLs.
- GLEIF recursion terminates on `reporting-exceptions` records (e.g. `NATURAL_PERSONS`,
  no-LEI parent) as a normal condition, not an error.
- GLEIF `registeredAs` is the entity's number in its own local register, not a Companies
  House number. Route to Companies House by registration authority
  (`entity.registeredAt`), not by jurisdiction.
- Non-GB entities stay in the graph as identity-only nodes. Never assume Companies House properties exist on a node.
- Companies House auth is HTTP Basic: API key as username, empty password.
- Companies House document content returns a 302 to a pre-signed S3 URL. Do not forward
  the `Authorization` header across that redirect.
- PSC `natures_of_control`: only `ownership-of-shares-*` entries mean ownership.
- A missing ownership percentage is stored as null with a reason. Never default to 100%.
