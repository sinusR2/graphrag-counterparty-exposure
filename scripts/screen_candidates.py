"""
Throwaway candidate-screening script against the GLEIF API.

Step 0: (only when company names are given on the command line) resolve each
        name to its top parent by searching GLEIF legal names.
Step 1: for each candidate, pull all ultimate-children and summarize
        jurisdiction / registration authority / status distribution.
Step 2: for each candidate that passes the thresholds, walk direct-children
        from the root and find the deepest chain of Companies House entities.

No auth required. Uses httpx. Prints to stdout only.
"""

import argparse
import sys
import time
from collections import Counter, defaultdict, deque

import httpx

BASE_URL = "https://api.gleif.org/api/v1"
PAGE_SIZE = 200
REQUEST_DELAY = 0.25  # seconds between requests - be polite to the API
TIMEOUT = 30.0
COMPANIES_HOUSE_RAS = {
    "RA000585",
    "RA000586",
    "RA000587",
}  # Companies House codes: England and Wales, Northern Ireland, Scotland
SEARCH_PAGE_SIZE = 50
MAX_MATCHES = 500  # Step 0 safety cap on name-search results
TOP_PARENTS_SHOWN = 5  # Step 0 prints only this many top parents
MIN_ULTIMATE_CHILDREN = 15  # Step 0: skip a name whose best top parent has fewer
MIN_CH_ENTITIES = 15  # Step 2 runs only if Step 1 finds at least this many CH entities

# --- Hardcoded input -------------------------------------------------------
# Replace with real (name, LEI) pairs.

CANDIDATES = [
    ("BABCOCK INTERNATIONAL GROUP PLC", "213800TSKOLX4EU6L377"),
]
# ("Tesco PLC", "2138002P5RNKC5W2JZ46"),
# ("Barclays PLC", "213800LBQA1Y9L22JB70"),
# ("Shell PLC", "21380068P1DRHMJ8KU70"),
# ("Vodafone Group PLC", "213800TB53ELEUKM7Q61"),

# Subset of CANDIDATES to run the deeper GB-only chain analysis on.
# Filled in by hand after reading Step 1's output.
SURVIVORS = [
    ("BABCOCK INTERNATIONAL GROUP PLC", "213800TSKOLX4EU6L377"),
]

# ("Tesco PLC", "2138002P5RNKC5W2JZ46"),  # check: at most 3 entities at depth >= 2
# ("Barclays PLC", "213800LBQA1Y9L22JB70"),
# ("Shell PLC", "21380068P1DRHMJ8KU70"),

# --- HTTP helpers ------------------------------------------------------------


def fetch_page(client, url, params, ignore_not_found=False):
    """GET a single page. Returns the parsed JSON payload, or None on a
    terminal condition (404, reporting exception, network error, etc.).
    With ignore_not_found, a 404 returns None without printing anything."""
    try:
        resp = client.get(url, params=params)
    except httpx.HTTPError as e:
        print(f"  ! request failed for {url}: {e}", file=sys.stderr)
        time.sleep(REQUEST_DELAY)
        return None

    if resp.status_code == 429:
        retry_after = float(resp.headers.get("Retry-After", 2))
        time.sleep(retry_after)
        try:
            resp = client.get(url, params=params)
        except httpx.HTTPError as e:
            print(f"  ! retry failed for {url}: {e}", file=sys.stderr)
            time.sleep(REQUEST_DELAY)
            return None

    time.sleep(REQUEST_DELAY)

    if ignore_not_found and resp.status_code == 404:
        return None

    try:
        payload = resp.json()
    except ValueError:
        return None

    if isinstance(payload, dict) and payload.get("errors"):
        detail = payload["errors"][0].get("detail") or payload["errors"][0].get("title")
        print(f"  (reporting exception / error at {url}: {detail})")
        return None

    if resp.status_code >= 400:
        return None

    return payload


def fetch_all_pages(client, relationship, lei):
    """Fetch every page of a lei-records/{lei}/{relationship} listing,
    following links.next until exhausted."""
    url = f"{BASE_URL}/lei-records/{lei}/{relationship}"
    params = {"page[size]": PAGE_SIZE}
    records = []

    next_url, next_params = url, params
    while next_url:
        payload = fetch_page(client, next_url, next_params)
        if payload is None:
            break
        items = payload.get("data", [])
        if not items:
            break
        records.extend(items)

        next_link = (payload.get("links") or {}).get("next")
        if next_link:
            next_url, next_params = next_link, None
        else:
            next_url = None

    return records


def fetch_record(client, lei):
    payload = fetch_page(client, f"{BASE_URL}/lei-records/{lei}", None)
    if payload is None:
        return None
    return payload.get("data")


def get_nested(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def extract_name(rec):
    return get_nested(rec, "attributes", "entity", "legalName", "name") or "UNKNOWN"


def extract_jurisdiction(rec):
    # Note: the live API exposes this as attributes.entity.jurisdiction,
    # not "legalJurisdiction".
    return get_nested(rec, "attributes", "entity", "jurisdiction") or "UNKNOWN"


def extract_authority(rec):
    return get_nested(rec, "attributes", "entity", "registeredAt", "id") or "UNKNOWN"


def extract_status(rec):
    return get_nested(rec, "attributes", "registration", "status") or "UNKNOWN"


# --- Step 1: descendant screening -------------------------------------------


def screen_candidate(client, name, lei):
    print(f"=== STEP 1: {name} ({lei}) ===")
    records = fetch_all_pages(client, "ultimate-children", lei)

    jurisdiction_counter = Counter()
    authority_counter = Counter()
    status_counter = Counter()

    for rec in records:
        attrs = rec.get("attributes", {})
        jurisdiction = get_nested(attrs, "entity", "jurisdiction") or "UNKNOWN"
        authority = get_nested(attrs, "entity", "registeredAt", "id") or "UNKNOWN"
        status = get_nested(attrs, "registration", "status") or "UNKNOWN"
        jurisdiction_counter[jurisdiction] += 1
        authority_counter[authority] += 1
        status_counter[status] += 1

    print(f"  total descendants: {len(records)}")

    print("  by jurisdiction (top 10):")
    for j, c in jurisdiction_counter.most_common(10):
        print(f"    {j}: {c}")

    print("  by registration authority:")
    for a, c in authority_counter.most_common():
        print(f"    {a}: {c}")

    print("  by status:")
    for s, c in status_counter.most_common():
        print(f"    {s}: {c}")

    print()

    return {
        "total": len(records),
        "ch": sum(c for a, c in authority_counter.items() if a in COMPANIES_HOUSE_RAS),
        "lapsed": status_counter.get("LAPSED", 0),
    }


# --- Step 2: GB-only chain analysis -----------------------------------------


def build_tree(client, root_name, root_lei):
    """BFS the direct-children relationship from root_lei, deduplicating
    nodes by LEI (guards cycles: an already-seen LEI is never re-queued)."""
    # lei -> {"name", "jurisdiction", "authority", "status", "children": [lei, ...]}
    nodes = {}
    visited = set()

    root_record = fetch_record(client, root_lei) or {}
    nodes[root_lei] = {
        "name": root_name,
        "jurisdiction": extract_jurisdiction(root_record),
        "authority": extract_authority(root_record),
        "status": extract_status(root_record),
        "children": [],
    }
    visited.add(root_lei)

    queue = deque([root_lei])
    while queue:
        parent_lei = queue.popleft()
        children_records = fetch_all_pages(client, "direct-children", parent_lei)

        for rec in children_records:
            child_lei = rec.get("id")
            if not child_lei:
                continue

            if child_lei not in nodes:
                nodes[child_lei] = {
                    "name": extract_name(rec),
                    "jurisdiction": extract_jurisdiction(rec),
                    "authority": extract_authority(rec),
                    "status": extract_status(rec),
                    "children": [],
                }

            if child_lei not in nodes[parent_lei]["children"]:
                nodes[parent_lei]["children"].append(child_lei)

            if child_lei not in visited:
                visited.add(child_lei)
                queue.append(child_lei)
            # already visited -> dedup/cycle guard, don't re-enqueue

    return nodes


def full_subtree_size(nodes, lei):
    """Entities in the subtree of lei in the full tree (any authority), itself included."""
    return 1 + sum(full_subtree_size(nodes, child) for child in nodes[lei]["children"])


def ch_subtree_stats(nodes, ch_children, lei, forest_depth, stats):
    """Recursively fill stats[lei] = {depth, ch_size, ch_depth, lapsed} for lei and its
    Companies House descendants. Returns (ch_size, ch_depth, lapsed) for lei."""
    ch_size = 1
    ch_depth = 0
    lapsed = 1 if nodes[lei]["status"] == "LAPSED" else 0
    for child in ch_children.get(lei, []):
        c_size, c_depth, c_lapsed = ch_subtree_stats(
            nodes, ch_children, child, forest_depth + 1, stats
        )
        ch_size += c_size
        ch_depth = max(ch_depth, c_depth + 1)
        lapsed += c_lapsed
    stats[lei] = {"depth": forest_depth, "ch_size": ch_size, "ch_depth": ch_depth, "lapsed": lapsed}
    return ch_size, ch_depth, lapsed


def print_sub_roots(nodes, ch_children, forest_roots):
    """Table of Companies House entities whose CH subtree is at least 3 levels deep."""
    stats = {}
    for root in forest_roots:
        ch_subtree_stats(nodes, ch_children, root, 0, stats)

    rows = [(lei, s) for lei, s in stats.items() if s["ch_depth"] >= 2]
    rows.sort(key=lambda r: (r[1]["ch_depth"], r[1]["ch_size"]), reverse=True)

    print("  sub-roots (Companies House subtree depth >= 2):")
    if not rows:
        print("    none")
        return
    print(
        "    name | LEI | depth in forest | CH subtree size | full subtree size | CH depth | lapsed"
    )
    for lei, s in rows:
        print(
            f"    {nodes[lei]['name']} | {lei} | {s['depth']} | {s['ch_size']} | "
            f"{full_subtree_size(nodes, lei)} | {s['ch_depth']} | {s['lapsed']}"
        )


def gb_only_analysis(nodes, root_lei):
    gb_nodes = {lei for lei, n in nodes.items() if n["authority"] in COMPANIES_HOUSE_RAS}

    if not gb_nodes:
        print("  no Companies House-registered nodes found")
        return None, None

    # Induced subgraph: keep an edge only if both endpoints are Companies House-registered.
    subgraph_children = defaultdict(list)
    child_of_gb_edge = set()
    for lei in gb_nodes:
        for child in nodes[lei]["children"]:
            if child in gb_nodes:
                subgraph_children[lei].append(child)
                child_of_gb_edge.add(child)

    # Roots of the CH-only forest: CH nodes that aren't reached via a CH edge.
    roots = [lei for lei in gb_nodes if lei not in child_of_gb_edge]

    depth_counts = Counter()
    max_depth = -1
    deepest_chain = []

    def dfs(lei, depth, path, path_set):
        nonlocal max_depth, deepest_chain
        if lei in path_set:
            return  # cycle guard
        depth_counts[depth] += 1
        if depth > max_depth:
            max_depth = depth
            deepest_chain = path
        next_path_set = path_set | {lei}
        for child in subgraph_children.get(lei, []):
            dfs(child, depth + 1, path + [child], next_path_set)

    for root in roots:
        dfs(root, 0, [root], set())

    print(f"  Companies House-registered nodes ({COMPANIES_HOUSE_RAS}): {len(gb_nodes)}")
    print(f"  maximum depth: {max_depth}")
    print("  nodes per depth level:")
    for d in sorted(depth_counts):
        print(f"    depth {d}: {depth_counts[d]}")

    print("  deepest Companies House-only chain:")
    for lei in deepest_chain:
        n = nodes[lei]
        print(f"    - {n['name']} [{n['jurisdiction']}, {n['authority']}, {n['status']}] ({lei})")

    print_sub_roots(nodes, subgraph_children, roots)

    deep_count = sum(c for d, c in depth_counts.items() if d >= 2)
    return max_depth, deep_count


# --- Step 0: resolve a company name to its top parent -----------------------


def search_by_name(client, name):
    """Pages through all GENERAL-category matches, up to MAX_MATCHES.
    Returns (records, total matches reported by GLEIF, cap reached)."""
    # The name goes through params so httpx encodes spaces and "&" correctly.
    params = {
        "filter[entity.legalName]": name,
        "filter[entity.category]": "GENERAL",
        "page[size]": SEARCH_PAGE_SIZE,
    }
    records = []
    total = 0
    next_url, next_params = f"{BASE_URL}/lei-records", params
    while next_url and len(records) < MAX_MATCHES:
        payload = fetch_page(client, next_url, next_params)
        if payload is None:
            break
        records.extend(payload.get("data", []))
        total = get_nested(payload, "meta", "pagination", "total", default=len(records))
        next_url = (payload.get("links") or {}).get("next")
        next_params = None

    capped = bool(next_url) and len(records) >= MAX_MATCHES
    return records[:MAX_MATCHES], total, capped


def find_top_parent(client, rec):
    """Returns (top parent record, True if rec is its own top parent).
    No ultimate parent (404, empty, or a reporting exception) means the
    record itself is the top parent."""
    url = f"{BASE_URL}/lei-records/{rec['id']}/ultimate-parent"
    payload = fetch_page(client, url, None, ignore_not_found=True)
    parent = payload.get("data") if payload else None
    if isinstance(parent, list):
        parent = parent[0] if parent else None
    if isinstance(parent, dict) and parent.get("id") and parent["id"] != rec["id"]:
        return parent, False
    return rec, True


def count_ultimate_children(client, lei):
    url = f"{BASE_URL}/lei-records/{lei}/ultimate-children"
    payload = fetch_page(client, url, {"page[size]": 1})
    return get_nested(payload or {}, "meta", "pagination", "total", default=0)


def resolve_top_parent(client, name):
    """Returns (chosen top parent dict or None, reason string if skipped)."""
    print(f"=== STEP 0: {name} ===")
    records, total, capped = search_by_name(client, name)
    print(f"  GLEIF matches (GENERAL): {total} (examined {len(records)})")
    if not records:
        return None, "no GLEIF match"
    if capped:
        print(f"  WARNING: stopped at the safety cap of {MAX_MATCHES} matches")

    matches = [rec for rec in records if extract_authority(rec) in COMPANIES_HOUSE_RAS]
    print(f"  dropped {len(records) - len(matches)} matches not registered at Companies House")
    if not matches:
        return None, "no Companies House match"

    top_parents = {}  # lei -> record, deduplicated
    own_parent_count = 0
    for rec in matches:
        parent, is_own = find_top_parent(client, rec)
        top_parents.setdefault(parent["id"], parent)
        own_parent_count += is_own
    print(f"  {own_parent_count} of {len(matches)} matches are their own top parent")

    candidates = []
    for lei, parent in top_parents.items():
        children = count_ultimate_children(client, lei)
        candidates.append(
            {
                "lei": lei,
                "name": extract_name(parent),
                "authority": extract_authority(parent),
                "children": children,
            }
        )
    candidates.sort(key=lambda c: c["children"], reverse=True)

    print("  top parents found:")
    for c in candidates[:TOP_PARENTS_SHOWN]:
        print(
            f"    - {c['name']} ({c['lei']}) [{c['authority']}] ultimate children: {c['children']}"
        )
    if len(candidates) > TOP_PARENTS_SHOWN:
        print(f"    ... and {len(candidates) - TOP_PARENTS_SHOWN} more with fewer")

    best = candidates[0]
    print(f"  chosen: {best['name']} ({best['lei']}), {best['children']} ultimate children\n")
    if best["children"] < MIN_ULTIMATE_CHILDREN:
        return best, f"below {MIN_ULTIMATE_CHILDREN} ultimate children"
    return best, ""


# --- Summary table -----------------------------------------------------------

COLUMNS = [
    ("searched", "Name searched"),
    ("parent", "Top parent"),
    ("lei", "LEI"),
    ("children", "Ult. children"),
    ("ch", "CH desc."),
    ("lapsed", "Lapsed desc."),
    ("nodes", "Step 2 nodes"),
    ("depth", "Max CH depth"),
    ("deep", "CH at depth>=2"),
    ("reason", "Reason not run"),
]


def print_summary(rows):
    cells = [
        [str(row.get(key) if row.get(key) is not None else "") for key, _ in COLUMNS]
        for row in rows
    ]
    widths = [max(len(label), *(len(r[i]) for r in cells)) for i, (_, label) in enumerate(COLUMNS)]
    print("########## SUMMARY ##########\n")
    print("  ".join(label.ljust(w) for (_, label), w in zip(COLUMNS, widths, strict=True)))
    print("  ".join("-" * w for w in widths))
    for r in cells:
        print("  ".join(value.ljust(w) for value, w in zip(r, widths, strict=True)))


# --- main --------------------------------------------------------------------


def screen_name(client, name):
    """Run Step 0, then Step 1 and Step 2 where the thresholds allow.
    Returns one summary row."""
    row = {"searched": name}
    parent, reason = resolve_top_parent(client, name)
    if parent is None:
        row["reason"] = reason
        return row

    row.update(parent=parent["name"], lei=parent["lei"], children=parent["children"])
    if reason:
        row["reason"] = reason
        return row

    stats = screen_candidate(client, parent["name"], parent["lei"])
    row.update(ch=stats["ch"], lapsed=stats["lapsed"])
    if stats["ch"] < MIN_CH_ENTITIES:
        row["reason"] = f"Step 2 skipped: below {MIN_CH_ENTITIES} Companies House entities"
        print(f"  {row['reason']}\n")
        return row

    print(f"=== STEP 2: {parent['name']} ({parent['lei']}) ===")
    nodes = build_tree(client, parent["name"], parent["lei"])
    print(f"  total nodes fetched: {len(nodes)}")
    max_depth, deep_count = gb_only_analysis(nodes, parent["lei"])
    print()
    row.update(nodes=len(nodes), depth=max_depth, deep=deep_count)
    return row


def run_fixed_lists(client):
    print("########## STEP 1: descendant screening ##########\n")
    for name, lei in CANDIDATES:
        screen_candidate(client, name, lei)

    print("########## STEP 2: GB-only chain analysis ##########\n")
    for name, lei in SURVIVORS:
        print(f"=== {name} ({lei}) ===")
        nodes = build_tree(client, name, lei)
        print(f"  total nodes fetched: {len(nodes)}")
        gb_only_analysis(nodes, lei)
        print()


def main():
    parser = argparse.ArgumentParser(description="Screen companies in GLEIF.")
    parser.add_argument("names", nargs="*", help="company names to search for")
    args = parser.parse_args()

    with httpx.Client(timeout=TIMEOUT, headers={"Accept": "application/vnd.api+json"}) as client:
        if not args.names:
            run_fixed_lists(client)
            return
        rows = [screen_name(client, name) for name in args.names]
        print_summary(rows)


if __name__ == "__main__":
    main()
