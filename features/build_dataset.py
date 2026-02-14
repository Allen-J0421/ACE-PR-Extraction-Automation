#!/usr/bin/env python3
"""
Build JSONL dataset from (issue_id, pr_id) pairs using precomputed root_hash and h.
Does not run extract.py or agent_change.py; main.py runs those and then calls this.
Usage (standalone): python features/build_dataset.py --pairs state.json [--output dataset.jsonl] [--limit N]
  state.json: array of {"issue_id": N, "pr_id": M, "root_hash": "...", "h": "..."}.
Requires: gh. Run from project root. Branches {h}-human, {h}-cursor, {h}-cursor-creative must exist.
"""

import argparse
import json
import os
import subprocess
import sys

# Project root (parent of features/) for params import when run as script
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from params import GITHUB_OWNER, GITHUB_REPO

OWNER = GITHUB_OWNER
REPO = GITHUB_REPO
WORK_DIR = GITHUB_REPO
RESOLVE_CACHE_FILENAME = "resolve_cache.json"
EXTRACT_CACHE_FILENAME = "extract_cache.json"


def run(cmd, cwd=None, capture=True):
    r = subprocess.run(
        cmd, shell=True, cwd=cwd, capture_output=capture, text=True
    )
    if capture:
        return r.returncode, r.stdout, r.stderr
    return r.returncode


def gh(endpoint):
    code, out, err = run(f"gh api {endpoint}")
    if code != 0:
        raise RuntimeError(f"gh api failed: {err}")
    return json.loads(out)


def load_pairs(pairs_path):
    with open(pairs_path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return data.get("pairs", data.get("issue_pr_pairs", []))


def get_cache_path(project_root):
    return os.path.join(project_root, RESOLVE_CACHE_FILENAME)


def _load_cache(project_root):
    """Load full cache file. Returns dict with refs and pairs or None if missing/invalid."""
    path = get_cache_path(project_root)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data.get("pairs"), list):
        return None
    return data


def load_pairs_from_cache(project_root):
    """Load pairs from resolve cache file. Returns list of {issue_id, pr_id} or None if missing/invalid."""
    data = _load_cache(project_root)
    return data["pairs"] if data else None


def load_refs_from_cache(project_root):
    """Load refs from resolve cache file. Returns dict with issue_ids, pr_ids, ghsa_ids or None if missing/invalid."""
    data = _load_cache(project_root)
    return data.get("refs") if data else None


def get_pairs_from_resolve(project_root):
    """Get pairs: from cache if it exists, else run resolve_pairs with --cache then load from cache."""
    pairs = load_pairs_from_cache(project_root)
    if pairs is not None:
        return pairs
    script = os.path.join(project_root, "features", "resolve_pairs.py")
    cache_path = get_cache_path(project_root)
    if not os.path.isfile(script):
        raise RuntimeError(f"resolve_pairs.py not found at {script}")
    code, out, err = run(
        f'python3 "{script}" --json --cache "{cache_path}"',
        cwd=project_root,
    )
    if code != 0:
        raise RuntimeError(f"resolve_pairs failed: {err}")
    pairs = load_pairs_from_cache(project_root)
    if pairs is None:
        raise RuntimeError(f"resolve_cache not created at {cache_path}")
    return pairs


def get_extract_cache_path(project_root):
    return os.path.join(project_root, EXTRACT_CACHE_FILENAME)


def load_extract_cache(project_root):
    """Load extract cache. Returns list of {issue_id, pr_id, root_hash, h} or None if missing/invalid."""
    path = get_extract_cache_path(project_root)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, list):
        return None
    return data


def save_extract_cache(project_root, entries):
    """Write extract cache. entries: list of {issue_id, pr_id, root_hash, h}."""
    path = get_extract_cache_path(project_root)
    with open(path, "w") as f:
        json.dump(entries, f, indent=2)


def get_extract_entry(cache, issue_id, pr_id):
    """Return {root_hash, h} for (issue_id, pr_id) from cache, or None."""
    if not cache:
        return None
    for e in cache:
        if e.get("issue_id") == issue_id and e.get("pr_id") == pr_id:
            return {"root_hash": e.get("root_hash"), "h": e.get("h")}
    return None


def cursor_branches_exist(work_dir, h):
    """Return True if both {h}-cursor and {h}-cursor-creative refs exist in work_dir."""
    if not h or not os.path.isdir(work_dir):
        return False
    for ref in (f"{h}-cursor", f"{h}-cursor-creative"):
        code, _, _ = run(f"git rev-parse {ref}", cwd=work_dir)
        if code != 0:
            return False
    return True


def git_diff(cwd, rev_a, rev_b):
    code, out, err = run(f"git diff {rev_a} {rev_b}", cwd=cwd)
    if code != 0:
        return ""
    return out


def fetch_issue_text(issue_id):
    try:
        issue = gh(f"repos/{OWNER}/{REPO}/issues/{issue_id}")
        title = issue.get("title") or ""
        body = issue.get("body") or ""
        return f"# Issue #{issue_id}: {title}\n\n{body}"
    except Exception:
        return ""


def fetch_pr_text(pr_id):
    try:
        pr = gh(f"repos/{OWNER}/{REPO}/pulls/{pr_id}")
        title = pr.get("title") or ""
        body = pr.get("body") or ""
        return f"# PR #{pr_id}: {title}\n\n{body}"
    except Exception:
        return ""


def build_one_row(project_root, issue_id, pr_id, root_hash, h):
    """Build one JSONL row from repo state. Branches {h}-human, {h}-cursor, {h}-cursor-creative must exist."""
    work_dir = os.path.join(project_root, WORK_DIR)
    if not os.path.isdir(work_dir):
        return None, "repo not found"
    if not root_hash or not h:
        return None, "missing root_hash or h"
    human_ref = f"{h}-human"
    cursor_ref = f"{h}-cursor"
    creative_ref = f"{h}-cursor-creative"
    pr_diff = git_diff(work_dir, root_hash, human_ref)
    cursor_diff = git_diff(work_dir, root_hash, cursor_ref)
    cursor_creative_diff = git_diff(work_dir, root_hash, creative_ref)
    issue_text = fetch_issue_text(issue_id)
    pr_text = fetch_pr_text(pr_id)
    row = {
        "project": f"{OWNER}/{REPO}",
        "issue_text": issue_text,
        "issue_id": issue_id,
        "pr_text": pr_text,
        "pr_id": pr_id,
        "root_hash": root_hash,
        "pr_diff": pr_diff,
        "cursor_diff": cursor_diff,
        "cursor_creative_diff": cursor_creative_diff,
    }
    return row, None


def main():
    ap = argparse.ArgumentParser(description="Build JSONL dataset from state (pairs with root_hash, h).")
    ap.add_argument("--pairs", required=True, help="JSON file with list of {issue_id, pr_id, root_hash, h}")
    ap.add_argument("--output", default="dataset.jsonl", help="Output JSONL path")
    ap.add_argument("--limit", type=int, default=None, help="Max number of pairs to process")
    ap.add_argument("--project-root", default=os.getcwd(), help="Project root")
    args = ap.parse_args()

    project_root = os.path.abspath(args.project_root)
    pairs = load_pairs(args.pairs)
    if args.limit is not None:
        pairs = pairs[: args.limit]
    failed = []
    with open(args.output, "w") as out:
        for i, p in enumerate(pairs):
            issue_id = p.get("issue_id")
            pr_id = p.get("pr_id")
            root_hash = p.get("root_hash")
            h = p.get("h")
            if issue_id is None or pr_id is None:
                failed.append((issue_id, pr_id, "missing issue_id or pr_id"))
                continue
            if not root_hash or not h:
                failed.append((issue_id, pr_id, "missing root_hash or h (run extract first)"))
                continue
            print(f"[{i+1}/{len(pairs)}] issue={issue_id} pr={pr_id} ...", flush=True)
            row, row_err = build_one_row(project_root, issue_id, pr_id, root_hash, h)
            if row_err:
                print(f"  build row failed: {row_err}", flush=True)
                failed.append((issue_id, pr_id, row_err))
                continue
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            print("  ok", flush=True)

    print(f"Wrote {args.output}. Failed: {len(failed)}")
    if failed:
        for issue_id, pr_id, err in failed[:20]:
            print(f"  issue={issue_id} pr={pr_id}: {err}")
        if len(failed) > 20:
            print(f"  ... and {len(failed) - 20} more")


if __name__ == "__main__":
    main()
