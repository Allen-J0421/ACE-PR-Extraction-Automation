#!/usr/bin/env python3
"""
Build JSONL dataset from (issue_id, pr_id) pairs using precomputed base_hash and human_hash.
Does not run extract.py or agent_change.py; main.py runs those and then calls this.
Usage (standalone): python features/build_dataset.py --pairs state.json [--output dataset.jsonl] [--limit N]
  state.json: array of {"issue_id": N, "pr_id": M, "base_hash": "...", "human_hash": "..."}.
Requires: gh. Run from project root.
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
CACHE_DIR_NAME = f"{REPO}_cache"
RESOLVE_CACHE_FILENAME = "resolve_cache.json"
EXTRACT_CACHE_FILENAME = "extract_cache.json"


def get_cache_dir(project_root, cache_dir=None):
    """Directory for resolve and extract cache files. cache_dir overrides default project_root/<repo>_cache."""
    return os.path.normpath(cache_dir or os.path.join(project_root, CACHE_DIR_NAME))


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


def get_cache_path(project_root, cache_dir=None):
    return os.path.join(get_cache_dir(project_root, cache_dir), RESOLVE_CACHE_FILENAME)


def _load_cache(project_root, cache_dir=None):
    """Load full cache file. Returns dict with refs and pairs or None if missing/invalid."""
    path = get_cache_path(project_root, cache_dir)
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


def load_pairs_from_cache(project_root, cache_dir=None):
    """Load pairs from resolve cache file. Returns list of {issue_id, pr_id} or None if missing/invalid."""
    data = _load_cache(project_root, cache_dir)
    return data["pairs"] if data else None


def load_refs_from_cache(project_root, cache_dir=None):
    """Load refs from resolve cache file. Returns dict with issue_ids, pr_ids, ghsa_ids or None if missing/invalid."""
    data = _load_cache(project_root, cache_dir)
    return data.get("refs") if data else None


def get_pairs_from_resolve(project_root, cache_dir=None):
    """Get pairs: from cache if it exists, else run resolve_pairs with --cache <dir> then load from cache."""
    pairs = load_pairs_from_cache(project_root, cache_dir)
    if pairs is not None:
        cache_path = get_cache_path(project_root, cache_dir)
        print(f"--- Loaded pairs from cache: {cache_path} ({len(pairs)} pairs) ---", file=sys.stderr, flush=True)
        return pairs
    print("--- No resolve cache found; running resolve_pairs... ---", file=sys.stderr, flush=True)
    cache_dir_path = get_cache_dir(project_root, cache_dir)
    script = os.path.join(project_root, "features", "resolve_pairs.py")
    if not os.path.isfile(script):
        raise RuntimeError(f"resolve_pairs.py not found at {script}")
    r = subprocess.run(
        [sys.executable, script, "--json", "--cache", cache_dir_path],
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError("resolve_pairs failed")
    pairs = load_pairs_from_cache(project_root, cache_dir)
    print(f"--- Resolve complete; loaded {len(pairs) if pairs else 0} pairs from cache ---", file=sys.stderr, flush=True)
    if pairs is None:
        raise RuntimeError(f"resolve_cache not created in {cache_dir_path}")
    return pairs


def get_extract_cache_path(project_root, cache_dir=None):
    return os.path.join(get_cache_dir(project_root, cache_dir), EXTRACT_CACHE_FILENAME)


def load_extract_cache(project_root, cache_dir=None):
    """Load extract cache. Returns list of {issue_id, pr_id, base_hash, human_hash, merge_hash} or None if missing/invalid."""
    path = get_extract_cache_path(project_root, cache_dir)
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


def save_extract_cache(project_root, entries, cache_dir=None):
    """Write extract cache. entries: list of {issue_id, pr_id, base_hash, human_hash, merge_hash}."""
    path = get_extract_cache_path(project_root, cache_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(entries, f, indent=2)


def get_extract_entry(cache, issue_id, pr_id):
    """Return {base_hash, human_hash, merge_hash} for (issue_id, pr_id) from cache, or None."""
    if not cache:
        return None
    for e in cache:
        if e.get("issue_id") == issue_id and e.get("pr_id") == pr_id:
            return {"base_hash": e.get("base_hash"), "human_hash": e.get("human_hash"), "merge_hash": e.get("merge_hash")}
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


def build_one_row(project_root, issue_id, pr_id, base_hash, human_hash):
    """Build one JSONL row from repo state."""
    work_dir = os.path.join(project_root, WORK_DIR)
    if not os.path.isdir(work_dir):
        return None, "repo not found"
    if not base_hash or not human_hash:
        return None, "missing base_hash or human_hash"
    h = base_hash[:8]
    human_ref = f"{h}-human"
    cursor_ref = f"{h}-cursor"
    creative_ref = f"{h}-cursor-creative"
    pr_diff = git_diff(work_dir, base_hash, human_ref)
    cursor_diff = git_diff(work_dir, base_hash, cursor_ref)
    cursor_creative_diff = git_diff(work_dir, base_hash, creative_ref)
    issue_text = fetch_issue_text(issue_id)
    pr_text = fetch_pr_text(pr_id)
    row = {
        "project": f"{OWNER}/{REPO}",
        "issue_text": issue_text,
        "issue_id": issue_id,
        "pr_text": pr_text,
        "pr_id": pr_id,
        "base_hash": base_hash,
        "human_hash": human_hash,
        "pr_diff": pr_diff,
        "cursor_diff": cursor_diff,
        "cursor_creative_diff": cursor_creative_diff,
    }
    return row, None


def main():
    ap = argparse.ArgumentParser(description="Build JSONL dataset from state (pairs with base_hash, human_hash).")
    ap.add_argument("--pairs", required=True, help="JSON file with list of {issue_id, pr_id, base_hash, human_hash}")
    ap.add_argument("--output", default="dataset.jsonl", help="Output JSONL path")
    ap.add_argument("--limit", type=int, default=None, help="Max number of pairs to process")
    ap.add_argument("--project-root", default=os.getcwd(), help="Project root")
    args = ap.parse_args()

    project_root = os.path.abspath(args.project_root)
    print("--- Loading pairs from file... ---", file=sys.stderr, flush=True)
    pairs = load_pairs(args.pairs)
    if args.limit is not None:
        pairs = pairs[: args.limit]
    print(f"--- Building dataset rows ({len(pairs)} pairs) -> {args.output} ---", file=sys.stderr, flush=True)
    failed = []
    with open(args.output, "w") as out:
        for i, p in enumerate(pairs):
            issue_id = p.get("issue_id")
            pr_id = p.get("pr_id")
            base_hash = p.get("base_hash")
            human_hash = p.get("human_hash")
            if issue_id is None or pr_id is None:
                failed.append((issue_id, pr_id, "missing issue_id or pr_id"))
                continue
            if not base_hash or not human_hash:
                failed.append((issue_id, pr_id, "missing base_hash or human_hash (run extract first)"))
                continue
            print(f"[{i+1}/{len(pairs)}] issue={issue_id} pr={pr_id} ...", file=sys.stderr, flush=True)
            row, row_err = build_one_row(project_root, issue_id, pr_id, base_hash, human_hash)
            if row_err:
                print(f"  build row failed: {row_err}", file=sys.stderr, flush=True)
                failed.append((issue_id, pr_id, row_err))
                continue
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            print("  ok", file=sys.stderr, flush=True)

    print(f"--- Wrote {args.output}. Failed: {len(failed)} ---", file=sys.stderr, flush=True)
    if failed:
        for issue_id, pr_id, err in failed[:20]:
            print(f"  issue={issue_id} pr={pr_id}: {err}", file=sys.stderr)
        if len(failed) > 20:
            print(f"  ... and {len(failed) - 20} more", file=sys.stderr)


if __name__ == "__main__":
    main()
