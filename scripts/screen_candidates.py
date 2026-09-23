"""
Throwaway candidate-screening script against the GLEIF API.

Step 1: for each candidate, pull all ultimate-children and summarize
        jurisdiction / registration authority / status distribution.
Step 2: for a subset of "survivor" candidates, walk direct-children
        recursively from the root, then find the deepest chain that
        stays entirely within GB jurisdiction.

No auth required. Uses httpx. Prints to stdout only.
"""

import sys
import time
from collections import Counter, defaultdict, deque

import httpx

BASE_URL = "https://api.gleif.org/api/v1"
PAGE_SIZE = 200
REQUEST_DELAY = 0.25  # seconds between requests - be polite to the API
TIMEOUT = 30.0

# --- Hardcoded input -------------------------------------------------------
# Replace with real (name, LEI) pairs.

CANDIDATES = [
    ("Example Holdings Ltd", "5493001KJTIIGC8Y1R12"),
    ("Example Group PLC", "213800WSGIIZCXF1P572"),
]

# Subset of CANDIDATES to run the deeper GB-only chain analysis on.
SURVIVORS = [
    ("Example Holdings Ltd", "5493001KJTIIGC8Y1R12"),
]

# --- HTTP helpers ------------------------------------------------------------


def fetch_page(client, url, params):
    """GET a single page. Returns the parsed JSON payload, or None on a
    terminal condition (404, reporting exception, network error, etc.)."""
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


# --- Step 1: descendant screening -------------------------------------------


def screen_candidate(client, name, lei):
    print(f"=== {name} ({lei}) ===")
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


# --- Step 2: GB-only chain analysis -----------------------------------------


def build_tree(client, root_name, root_lei):
    """BFS the direct-children relationship from root_lei, deduplicating
    nodes by LEI (guards cycles: an already-seen LEI is never re-queued)."""
    nodes = {}  # lei -> {"name", "jurisdiction", "children": [lei, ...]}
    visited = set()

    root_record = fetch_record(client, root_lei)
    root_jurisdiction = extract_jurisdiction(root_record) if root_record else "UNKNOWN"
    nodes[root_lei] = {"name": root_name, "jurisdiction": root_jurisdiction, "children": []}
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
                    "children": [],
                }

            if child_lei not in nodes[parent_lei]["children"]:
                nodes[parent_lei]["children"].append(child_lei)

            if child_lei not in visited:
                visited.add(child_lei)
                queue.append(child_lei)
            # already visited -> dedup/cycle guard, don't re-enqueue

    return nodes


def gb_only_analysis(nodes, root_lei):
    gb_nodes = {lei for lei, n in nodes.items() if n["jurisdiction"] == "GB"}

    if not gb_nodes:
        print("  no GB-jurisdiction nodes found")
        return

    # Induced subgraph: keep an edge only if both endpoints are GB.
    subgraph_children = defaultdict(list)
    child_of_gb_edge = set()
    for lei in gb_nodes:
        for child in nodes[lei]["children"]:
            if child in gb_nodes:
                subgraph_children[lei].append(child)
                child_of_gb_edge.add(child)

    # Roots of the GB-only forest: GB nodes that aren't reached via a GB edge.
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

    print(f"  GB-jurisdiction nodes: {len(gb_nodes)}")
    print(f"  maximum depth: {max_depth}")
    print("  nodes per depth level:")
    for d in sorted(depth_counts):
        print(f"    depth {d}: {depth_counts[d]}")

    print("  deepest GB-only chain:")
    for lei in deepest_chain:
        print(f"    - {nodes[lei]['name']} ({lei})")


# --- main --------------------------------------------------------------------


def main():
    with httpx.Client(timeout=TIMEOUT, headers={"Accept": "application/vnd.api+json"}) as client:
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


if __name__ == "__main__":
    main()
