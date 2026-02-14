#!/usr/bin/env python3
"""
Fetch Flask changelog, extract issue/PR/GHSA refs, resolve to (issue_id, pr_id) pairs.
Usage: python features/resolve_pairs.py [--json] [--refs-only] [--cache PATH]
  --refs-only   Only output refs (issue_ids, pr_ids, ghsa_ids), do not resolve pairs.
  --cache PATH Write refs and pairs to a single JSON file (default: resolve_cache.json in current directory).
Output: By default JSON array of {"issue_id": N, "pr_id": M}. With --refs-only, refs JSON.
"""

import json
import os
import re
import subprocess
import sys
import urllib.request

# Project root (parent of features/) for params import
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from params import (
    CHANGELOG_URL,
    GITHUB_OWNER,
    GITHUB_REPO,
    RE_ISSUE_PATTERN,
    RE_PULL_PATTERN,
    RE_GHSA_PATTERN,
    RE_FIXES_PATTERN,
)

OWNER, REPO = GITHUB_OWNER, GITHUB_REPO
RE_ISSUE = re.compile(RE_ISSUE_PATTERN)
RE_PULL = re.compile(RE_PULL_PATTERN)
RE_GHSA = re.compile(RE_GHSA_PATTERN)
RE_FIXES = re.compile(RE_FIXES_PATTERN)


def get_refs():
    """Return {issue_ids, pr_ids, ghsa_ids} by fetching changelog."""
    req = urllib.request.Request(CHANGELOG_URL, headers={"User-Agent": "ACE-PR-Extraction/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    issue_ids = {int(m.group(1)) for m in RE_ISSUE.finditer(html)}
    pr_ids = {int(m.group(1)) for m in RE_PULL.finditer(html)}
    issue_ids -= pr_ids  # if same number is both, treat as PR only
    ghsa_ids = list({m.group(1) for m in RE_GHSA.finditer(html)})
    return {"issue_ids": issue_ids, "pr_ids": pr_ids, "ghsa_ids": ghsa_ids}


def gh(endpoint, accept=None):
    cmd = f"gh api -H 'Accept: {accept}' {endpoint}" if accept else f"gh api {endpoint}"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return json.loads(r.stdout)


def pr_to_pairs(pr_id):
    """(issue_id, pr_id) from PR body 'Fixes #N'; else (pr_id, pr_id)."""
    try:
        pr = gh(f"repos/{OWNER}/{REPO}/pulls/{pr_id}")
        text = (pr.get("body") or "") + "\n" + (pr.get("title") or "")
        ids = [int(m) for m in RE_FIXES.findall(text)]
        return [(iid, pr_id) for iid in ids] if ids else [(pr_id, pr_id)]
    except Exception:
        return []


def issue_to_pr(issue_id):
    """Merged PR that closed this issue (timeline → commit → pulls)."""
    try:
        timeline = gh(f"repos/{OWNER}/{REPO}/issues/{issue_id}/timeline",
                     accept="application/vnd.github.mockingbird-preview")
        commit_id = next((e.get("commit_id") for e in timeline if e.get("event") == "closed" and e.get("commit_id")), None)
        if not commit_id:
            return None
        pulls = gh(f"repos/{OWNER}/{REPO}/commits/{commit_id}/pulls")
        return next((p["number"] for p in pulls if p.get("merged_at")), None)
    except Exception:
        return None


def resolve(refs):
    """Turn refs into deduplicated (issue_id, pr_id) pairs."""
    pairs = set()
    for pr_id in refs["pr_ids"]:
        pairs.update(pr_to_pairs(pr_id))
    paired = {p[0] for p in pairs}
    for iid in refs["issue_ids"]:
        if iid in paired:
            continue
        pr_id = issue_to_pr(iid)
        if pr_id:
            pairs.add((iid, pr_id))
    for gid in refs["ghsa_ids"]:
        try:
            for adv in gh(f"repos/{OWNER}/{REPO}/security-advisories"):
                if adv.get("ghsa_id") != gid:
                    continue
                for ref in adv.get("references") or []:
                    m = re.search(r"/pull/(\d+)", ref.get("url", ""))
                    if m:
                        pairs.update(pr_to_pairs(int(m.group(1))))
                break
        except Exception:
            pass
    return sorted(pairs)


DEFAULT_CACHE_FILENAME = "resolve_cache.json"


def main():
    argv = sys.argv[1:]
    use_json = "--json" in argv
    refs_only = "--refs-only" in argv
    cache_path = os.path.join(os.getcwd(), DEFAULT_CACHE_FILENAME)
    i = 0
    while i < len(argv):
        if argv[i] == "--cache" and i + 1 < len(argv):
            cache_path = argv[i + 1]
            i += 2
            continue
        i += 1

    refs = get_refs()
    refs_serializable = {
        "issue_ids": sorted(refs["issue_ids"]),
        "pr_ids": sorted(refs["pr_ids"]),
        "ghsa_ids": refs["ghsa_ids"],
    }
    if refs_only:
        print(json.dumps(refs_serializable, indent=2) if use_json else json.dumps(refs_serializable))
        return
    pairs = resolve(refs)
    pairs_list = [{"issue_id": i, "pr_id": p} for (i, p) in pairs]
    if cache_path:
        cache_data = {"refs": refs_serializable, "pairs": pairs_list}
        with open(cache_path, "w") as f:
            json.dump(cache_data, f, indent=2)
    out = pairs_list
    print(json.dumps(out, indent=2) if use_json else "\n".join(f"{o['issue_id']} {o['pr_id']}" for o in out))


if __name__ == "__main__":
    main()
