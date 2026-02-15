#!/usr/bin/env python3
"""
PR Extraction Automation
Usage: python features/extract.py <repo_url> <issue_number> <pr_number> [--json] [--autoc]
  repo_url  e.g. https://github.com/owner/repo

  --json   Print one line of machine-readable JSON at the end: root_hash, h (prefix), branches.
  --autoc  Non-interactive: clone repo if missing without prompting.

Creates two branches from a merged PR:
  {hash}-base   state right before the PR was merged
  {hash}-human  state after the PR was merged (merge commit)

The cursor branches ({h}-cursor, {h}-cursor-creative) are created in agent_change.py from base before running the agent.
Requires: git, gh (GitHub CLI, authenticated).
"""

import json
import os
import subprocess
import sys
from urllib.parse import urlparse


def _error(msg):
    """Print an error prefixed with EXTRACT_ERROR= (machine-readable on stdout)
    and also to stderr for human visibility, then exit."""
    print(f"EXTRACT_ERROR={msg}")
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def run(cmd, cwd=None):
    """Run a shell command, return stdout. Exit on failure."""
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        _error(f"cmd failed: {cmd} — {r.stderr.strip()}")
    return r.stdout.strip()


def gh(endpoint):
    """Call GitHub REST API via gh CLI, return parsed JSON."""
    return json.loads(run(f"gh api {endpoint}"))


def main():
    args = [a for a in sys.argv[1:] if a in ("--json", "--autoc")]
    pos = [a for a in sys.argv[1:] if a not in ("--json", "--autoc")]
    if len(pos) != 3:
        print(__doc__)
        sys.exit(1)

    repo_url = pos[0]
    path = urlparse(repo_url).path.strip("/")
    owner, repo = path.split("/", 1)
    issue_num = int(pos[1])
    pr_num = int(pos[2])
    out_json = "--json" in args
    autoc = "--autoc" in args

    # Fetch PR and issue data from GitHub API
    try:
        pr = gh(f"repos/{owner}/{repo}/pulls/{pr_num}")
    except Exception as e:
        _error(f"Failed to fetch PR #{pr_num}: {e}")

    try:
        issue = gh(f"repos/{owner}/{repo}/issues/{issue_num}")
    except Exception as e:
        _error(f"Failed to fetch issue #{issue_num}: {e}")

    merge_sha = pr.get("merge_commit_sha")
    if not merge_sha:
        _error(f"PR #{pr_num} has no merge_commit_sha (not merged?)")

    work_dir = f"{repo}"
    if not os.path.isdir(work_dir):
        if autoc:
            print(f"Cloning {repo_url} → {work_dir}/ …",
                  file=sys.stderr, flush=True)
            run(f"git clone {repo_url} {work_dir}", cwd=os.getcwd())
            print("Clone OK.", file=sys.stderr, flush=True)
        else:
            print(f"'{work_dir}/' not found.")
            ans = input(f"Clone {repo_url}? (y/n) ").strip().lower()
            if ans == "y":
                run(f"git clone {repo_url} {work_dir}", cwd=os.getcwd())
            else:
                _error("clone declined by user")

    # Create two branches only (base and human). Cursor branches are created in agent_change.py.
    r = subprocess.run(
        ["git", "rev-parse", f"{merge_sha}^2"],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode == 0 and r.stdout.strip():
        base_sha = run(f"git merge-base {merge_sha}^1 {merge_sha}^2", cwd=work_dir)
    else:
        base_sha = run(f"git rev-parse {merge_sha}^1", cwd=work_dir)
    h = base_sha[:8]
    branches = {
        f"{h}-base": base_sha,
        f"{h}-human": merge_sha,
    }
    for name, sha in branches.items():
        run(f"git branch {name} {sha}", cwd=work_dir)
    print("--- branches created (base, human) ---", file=sys.stderr, flush=True)

    # Return to base branch when done
    run(f"git checkout {h}-base", cwd=work_dir)

    # Output: branch name → commit hash for each branch
    result = {}
    for name in branches:
        sha = run(f"git rev-parse {name}", cwd=work_dir)
        result[name] = sha
    if out_json:
        machine = {
            "root_hash": base_sha,
            "h": h,
            "branches": result,
        }
        print("EXTRACT_JSON=" + json.dumps(machine))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
