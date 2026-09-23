# Design Decisions

Why this system is built the way it is. Written phase by phase as decisions are made and
validated, rather than reconstructed at the end.

---

## 1. Data sources

The system answers questions about corporate ownership exposure: given a legal entity,
what is the total exposure to the group it belongs to, including everything that group
ultimately controls, grounded in evidence from filed documents.

Answering that requires three sources, because no single one is sufficient.

**A note on terminology.** GLEIF publishes two tiers of data. **Level 1** is reference data
about an entity in isolation — legal name, legal form, registered address, jurisdiction,
local register number. **Level 2** is relationship data: which entity consolidates which.
The numbers refer to the *kind* of data, not to a position in an ownership hierarchy —
Level 2 covers relationships at any depth, and there is no Level 3.

| | GLEIF Level 1 | GLEIF Level 2 | Companies House | Filed accounts |
|---|---|---|---|---|
| Legal identity (name, form, jurisdiction, address) | ✓ | — | ✓ | ✓ |
| Global identifier (LEI) | ✓ | — | — | — |
| Local register number | ✓ | — | ✓ | — |
| Parent/child relationship | — | ✓ | — | ✓ |
| Control bands (25–50 / 50–75 / 75–100%) | — | — | ✓ | — |
| **Exact ownership percentage** | — | — | — | **✓ (only source)** |
| Pointers to filed documents | — | — | ✓ | — |

**GLEIF** provides the ownership backbone. Every entity carries a Legal Entity Identifier,
and Level 2 records declare consolidation relationships between them. This is the
structural skeleton the graph is built on.

**Companies House** provides the UK regulatory layer: persons with significant control,
and pointers to filed accounts.

**Filed accounts** provide everything the structured sources cannot.

The concrete Companies House API surface — endpoints, Basic authentication, and the
two-host redirect flow needed to retrieve a filed document — is recorded in
[`api_exploration_companies_house.http`](api_exploration_companies_house.http), runnable
with the VS Code REST Client extension. GLEIF needs no authentication and its records
self-describe their own relationship links, so no equivalent file is required for it.

### Why the backbone is built from GLEIF first

An alternative approach to building a knowledge graph from documents is to hand documents
to a language model and let it produce nodes and relationships freely. That suits domains
with no authoritative source, where approximate relationships are still useful.

This domain has legal identifiers and demands precision — a duplicated or unmatched entity
does not degrade an exposure figure gracefully, it makes it wrong. Building the backbone
from GLEIF first means every node arrives with an LEI already attached, which converts
document extraction from open-ended *construction* into constrained *matching*.

The governing principle throughout: **deterministic where there is a defined right answer,
a language model only where judgment is genuinely required.**

---

## 2. Ownership percentages are extracted, never sourced

The most consequential finding from data reconnaissance, because it shapes the rest of the
architecture.

**Neither structured source carries an exact ownership percentage.**

GLEIF Level 2 has no percentage field populated at all. This is structurally coherent
rather than a gap in the data: Level 2 records a *consolidation* relationship, which is a
yes/no accounting fact about whose financial statements an entity appears in — not a
shareholding.

Companies House PSC data gives only bands (`ownership-of-shares-25-to-50-percent` and
similar), never a figure. The PSC regime exists to disclose *who has significant control*,
and a band is sufficient for that purpose.

So the only place an exact percentage exists is inside the related-undertakings note of a
filed set of accounts — unstructured prose and tables, in a PDF.

**Consequences, all of which are deliberate:**

- Language-model extraction from documents is not an optional enhancement to this system.
  It is the only path to the numbers that effective-ownership arithmetic requires.
- Every percentage in the graph is therefore an *extraction*, not an authoritative figure,
  and is stored with provenance: which document, which page. An answer that cannot be
  traced back to a source page is not a grounded answer.
- Where PSC bands overlap with an extracted percentage, the band is used as a correctness
  check on the extraction. This is why Companies House ingestion is built before document
  extraction, not after.

A further note on PSC: the `natures_of_control` field mixes three different kinds of claim
in one array — share ownership, voting rights, and the right to appoint directors. Only
the share-ownership entries are treated as ownership. Voting rights measure something else,
and conflating them would corrupt the model.

---

## 3. The graph deliberately contains two node shapes

GLEIF is global. Companies House covers Great Britain only. A GLEIF ownership family
therefore reaches entities registered in Ireland, the United States, Luxembourg and
elsewhere, for which no PSC record and no UK filing exists.

A related point that is easy to get wrong: GLEIF's local register identifier is the
entity's number *in its own register* — a CRO number for an Irish entity, a state file
number for a US one. It is not a Companies House number and will not resolve against the
Companies House API. Entities are therefore routed by **registration authority**, not by
jurisdiction string.

**These entities are kept in the graph, not dropped.** Removing them would sever any
ownership chain passing through one — a UK entity owned via an Irish holding company by
another UK entity — and silently under-report exposure. They are loaded from GLEIF Level 1
alone: identity, and their position in the ownership structure, with no control data and
no documents.

The cost is that the graph contains two legitimate node shapes, and no code downstream may
assume Companies House properties exist on every node. That is a real constraint, accepted
deliberately, because the alternative produces wrong answers rather than incomplete ones.

---

## 4. Known limitations

Stated openly, because each one bounds what the system's answers mean.

**A GLEIF family is not a corporate group.** It is the subset of the group holding LEIs.
Entities acquire an LEI for regulatory reasons — derivatives trading, securities issuance,
transaction reporting — not according to their position in an ownership chain. Intermediate
holding companies that never needed one are simply absent from the structure.

**Chains can be truncated by identifier availability.** Where an entity declares a parent
that has no LEI, GLEIF records a reporting exception rather than a relationship. A real
parent exists; the data stops there. Traversal terminates on that exception as a normal
condition, but the resulting picture is incomplete for a reason that has nothing to do
with actual ownership.

**Percentages are model extractions.** See section 2. They carry provenance and are
validated against PSC bands where the two overlap, but they are not authoritative figures
and are not presented as such.

**Non-GB entities carry identity only.** They appear in the ownership structure but
contribute no documents and no control data.

**The two structured sources disagree in places, by design.** GLEIF Level 2 is
consolidation-based, reflecting accounting treatment; PSC is control-based, reflecting
voting rights and share ownership. They measure different things. Divergence between them
is recorded as a finding rather than resolved as an error — the common causes are
differences in consolidation scope, restructuring between filing dates, nominee
arrangements, and the natural-person boundary, at which GLEIF stops and PSC does not.
