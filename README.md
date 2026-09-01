# Counterparty Exposure GraphRAG

One-paragraph description: what question does this answer that vector search alone can't.

## Why this exists
Brief motivation — multi-hop counterparty/ownership exposure questions, why graph
traversal + retrieval is the right tool.

## Architecture
(diagram once built — Phase-dependent)

## Design decisions
See `docs/design/`. Covers the locked decisions: why Neo4j over Neptune, why
graph-traverse-first over naive vector search, the entity-resolution cascade, etc.

## Setup

Requires Python 3.12, Git, and a free [Neo4j AuraDB](https://neo4j.com/product/auradb/) instance.

\`\`\`powershell
git clone https://github.com/<you>/graphrag-counterparty-exposure.git
cd graphrag-counterparty-exposure
python -m venv .venv
.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
Copy-Item .env.example .env       # macOS/Linux: cp .env.example .env
pre-commit install
\`\`\`

Fill in `.env` with an Anthropic API key and your AuraDB connection details before running anything.

Full walkthrough — including troubleshooting and the reasoning behind each step — lives in [`docs/setup_guide.md`](docs/setup_guide.md).

## Usage
(fill in once the API exists)

## Roadmap / phases
See `docs/roadmap.md`.

## Tech stack
Neo4j AuraDB, FastAPI, sentence-transformers, Anthropic API, pytest, Ruff
