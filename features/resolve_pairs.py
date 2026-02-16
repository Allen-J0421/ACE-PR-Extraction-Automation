#!/usr/bin/env python3
"""
Resolve (issue_id, pr_id) pairs for a GitHub repo.

Three complementary sources:
  1. Merged PRs (GraphQL)  → closingIssuesReferences.
  2. Closed issues (GraphQL) → ClosedEvent.closer (which merged PR closed it).
  3. Changelog (CHANGES.rst) → regex for :issue:`N`, :pr:`N`, #N to catch
     refs the API misses (e.g. issues closed by commit).

Usage: python features/resolve_pairs.py [--cache DIR] [--refresh]
"""

import argparse
import json
import os
import re
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from params import GITHUB_OWNER, GITHUB_REPO

OWNER = GITHUB_OWNER
REPO = GITHUB_REPO

# Path to the changelog inside the repository.
CHANGELOG_PATH = "CHANGES.rst"

# Changelog regex:  :issue:`N`, :pr:`N`, #N, # N
_CHANGELOG_NUM_RE = re.compile(r"(?:#\s?(\d+)|:(?:issue|pr):`(\d+)`)")

# ---------------------------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------------------------

MERGED_PRS_QUERY = """
query($owner: String!, $repo: String!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequests(first: 100, states: MERGED,
                 orderBy: {field: CREATED_AT, direction: DESC},
                 after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        milestone { title }
        closingIssuesReferences(first: 100) {
          nodes { number }
        }
      }
    }
  }
}
"""

CLOSED_ISSUES_QUERY = """
query($owner: String!, $repo: String!, $after: String) {
  repository(owner: $owner, name: $repo) {
    issues(first: 100, states: CLOSED,
           orderBy: {field: CREATED_AT, direction: DESC},
           after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        timelineItems(itemTypes: [CLOSED_EVENT], first: 5) {
          nodes {
            ... on ClosedEvent {
              closer {
                ... on PullRequest {
                  number
                  merged
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def gh_graphql(query, variables):
    """GraphQL API call via ``gh``.  Returns parsed JSON."""
    body = json.dumps({"query": query, "variables": variables})
    r = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=body, capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return json.loads(r.stdout)


def gh_rest_raw(endpoint):
    """REST call via ``gh`` returning raw text."""
    r = subprocess.run(
        ["gh", "api", endpoint, "-H", "Accept: application/vnd.github.raw"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout


def _paginate_graphql(query, connection_path, variables_base):
    """Generic paginator.  Yields ``(page_number, nodes_list)``."""
    after = None
    page = 0
    while True:
        page += 1
        variables = {**variables_base, "after": after}
        out = gh_graphql(query, variables)
        obj = out.get("data") or {}
        for key in connection_path.split("."):
            obj = (obj or {}).get(key) or {}
        nodes = obj.get("nodes") or []
        yield page, nodes
        pi = obj.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        after = pi.get("endCursor")
        if not after:
            break


# ---------------------------------------------------------------------------
# Source 1: merged PRs  →  closingIssuesReferences
# ---------------------------------------------------------------------------

def fetch_all_merged_prs():
    all_nodes = []
    for page, nodes in _paginate_graphql(
            MERGED_PRS_QUERY, "repository.pullRequests",
            {"owner": OWNER, "repo": REPO}):
        all_nodes.extend(nodes)
        print(f"  [PR page {page}] +{len(nodes)}  total {len(all_nodes)}",
              file=sys.stderr, flush=True)
    return all_nodes


def build_pairs_from_merged_prs(pr_nodes):
    """``set[(issue_id, pr_id)]`` from closingIssuesReferences.
    Self-pair ``(pr, pr)`` when a PR has no closing refs."""
    pairs: set[tuple[int, int]] = set()
    for node in pr_nodes:
        pr_num = node.get("number")
        if not isinstance(pr_num, int):
            continue
        linked: set[int] = set()
        for n in (node.get("closingIssuesReferences") or {}).get("nodes") or []:
            iid = n.get("number")
            if isinstance(iid, int):
                linked.add(iid)
        if linked:
            for iid in linked:
                pairs.add((iid, pr_num))
        else:
            pairs.add((pr_num, pr_num))
    return pairs


# ---------------------------------------------------------------------------
# Source 2: closed issues  →  ClosedEvent.closer (merged PR)
# ---------------------------------------------------------------------------

def fetch_all_closed_issues():
    all_nodes = []
    for page, nodes in _paginate_graphql(
            CLOSED_ISSUES_QUERY, "repository.issues",
            {"owner": OWNER, "repo": REPO}):
        all_nodes.extend(nodes)
        print(f"  [Issue page {page}] +{len(nodes)}  total {len(all_nodes)}",
              file=sys.stderr, flush=True)
    return all_nodes


def build_pairs_from_closed_issues(issue_nodes):
    """``set[(issue_id, pr_id)]`` from ClosedEvent → merged PullRequest."""
    pairs: set[tuple[int, int]] = set()
    for node in issue_nodes:
        inum = node.get("number")
        if not isinstance(inum, int):
            continue
        for ev in (node.get("timelineItems") or {}).get("nodes") or []:
            closer = ev.get("closer") or {}
            pr_num = closer.get("number")
            if isinstance(pr_num, int) and closer.get("merged"):
                pairs.add((inum, pr_num))
    return pairs


# ---------------------------------------------------------------------------
# Source 3: changelog  →  supplementary issue/PR refs
# ---------------------------------------------------------------------------

def fetch_changelog_refs():
    """Fetch ``CHANGES.rst`` and extract issue/PR numbers.
    Returns ``set[int]``."""
    endpoint = f"/repos/{OWNER}/{REPO}/contents/{CHANGELOG_PATH}"
    print(f"--- Fetching changelog … ---", file=sys.stderr, flush=True)
    try:
        text = gh_rest_raw(endpoint)
    except RuntimeError as exc:
        print(f"--- Warning: could not fetch changelog: {exc} ---",
              file=sys.stderr, flush=True)
        return set()
    numbers = {int(m.group(1) or m.group(2))
               for m in _CHANGELOG_NUM_RE.finditer(text)}
    print(f"--- Changelog: {len(numbers)} refs ---",
          file=sys.stderr, flush=True)
    return numbers


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load_cache_if_valid(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    pairs_list = data.get("pairs")
    if not isinstance(pairs_list, list) or not pairs_list:
        return None
    refs = data.get("refs")
    if not refs or not isinstance(refs.get("issue_ids"), list):
        return None
    return refs, pairs_list


CACHE_DIR_DEFAULT = f"{REPO}_cache"
RESOLVE_CACHE_FILENAME = "resolve_cache.json"
PAIRS_FILENAME = "pairs.json"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Resolve (issue_id, pr_id) pairs from GitHub.")
    ap.add_argument("--json", action="store_true", help="(compat, ignored)")
    ap.add_argument("--cache",
                    default=os.path.join(os.getcwd(), CACHE_DIR_DEFAULT),
                    metavar="DIR")
    ap.add_argument("--refresh", action="store_true",
                    help="Force re-fetch even if cache exists")
    args = ap.parse_args()

    cache_file = os.path.join(args.cache, RESOLVE_CACHE_FILENAME)

    if not args.refresh:
        loaded = load_cache_if_valid(cache_file)
        if loaded is not None:
            _, pairs_list = loaded
            print(f"--- Using cached pairs ({len(pairs_list)}) ---",
                  file=sys.stderr, flush=True)
            return

    # ---- Source 1: merged PRs (closingIssuesReferences) ----
    print("--- Source 1: merged PRs … ---", file=sys.stderr, flush=True)
    pr_nodes = fetch_all_merged_prs()
    pr_pairs = build_pairs_from_merged_prs(pr_nodes)
    print(f"  → {len(pr_pairs)} pairs", file=sys.stderr, flush=True)

    # Derive max valid ID from merged PRs (for changelog bounding).
    all_pr_nums = [n["number"] for n in pr_nodes
                   if isinstance(n.get("number"), int)]
    max_valid_id = max(all_pr_nums) if all_pr_nums else 99999

    # ---- Source 2: closed issues (ClosedEvent → merged PR) ----
    print("--- Source 2: closed issues … ---", file=sys.stderr, flush=True)
    issue_nodes = fetch_all_closed_issues()
    issue_pairs = build_pairs_from_closed_issues(issue_nodes)
    print(f"  → {len(issue_pairs)} pairs", file=sys.stderr, flush=True)

    # ---- Source 3: changelog supplement ----
    print("--- Source 3: changelog … ---", file=sys.stderr, flush=True)
    changelog_nums = fetch_changelog_refs()

    # ---- Merge ----
    all_pairs = pr_pairs | issue_pairs

    # Changelog: add numbers not yet in any pair as self-pairs.
    existing_ids = {i for (i, _) in all_pairs} | {p for (_, p) in all_pairs}
    added = 0
    for num in changelog_nums:
        if 1 <= num <= max_valid_id and num not in existing_ids:
            all_pairs.add((num, num))
            existing_ids.add(num)
            added += 1
    if added:
        print(f"  → {added} supplementary self-pairs from changelog",
              file=sys.stderr, flush=True)

    # Drop implausible real pairs: ancient issue + modern PR is API noise
    # (e.g. PR body accidentally mentions "#1" from a version string).
    all_pairs = {(i, p) for (i, p) in all_pairs
                 if not (i != p and i < 50 and p - i > 500)}

    # Promote: drop self-pair (p,p) if a real pair (_,p) exists.
    real = {(i, p) for (i, p) in all_pairs if i != p}
    prs_with_real = {p for (_, p) in real}
    merged = real | {(i, p) for (i, p) in all_pairs
                     if i == p and p not in prs_with_real}

    sorted_pairs = sorted(merged)
    pairs_list = [{"issue_id": i, "pr_id": p, "self_pair": (i == p)}
                  for (i, p) in sorted_pairs]
    issue_ids = sorted({i for (i, _) in sorted_pairs})
    pr_ids = sorted({p for (_, p) in sorted_pairs})
    refs = {"issue_ids": issue_ids, "pr_ids": pr_ids, "ghsa_ids": []}

    # ---- Write caches ----
    os.makedirs(args.cache, exist_ok=True)
    with open(cache_file, "w") as f:
        json.dump({"refs": refs, "pairs": pairs_list}, f, indent=2)
    pairs_file = os.path.join(args.cache, PAIRS_FILENAME)
    with open(pairs_file, "w") as f:
        json.dump(pairs_list, f, indent=2)

    real_ct = sum(1 for x in pairs_list if not x["self_pair"])
    print(f"--- Done: {len(pairs_list)} pairs ({real_ct} real, "
          f"{len(pairs_list)-real_ct} self) → {cache_file} ---",
          file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
