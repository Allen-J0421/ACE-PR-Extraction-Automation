#!/usr/bin/env python3
"""
PR Extraction Automation
Usage: python extract.py <owner/repo> <issue_number> <pr_number>

Creates four branches from a merged PR:
  {hash}-base             state right before the PR was merged
  {hash}-human            state after the PR was merged (merge commit)
  {hash}-cursor           from base, for Cursor to apply changes (issue prompt)
  {hash}-cursor-creative  from base, for Cursor to apply changes (creative prompt)

Requires: git, gh (GitHub CLI, authenticated), agent (Cursor Agent CLI).
If agent is not on PATH, set AGENT_PATH to the full path of the agent binary.
"""

import json
import os
import shutil
import subprocess
import sys
from urllib.parse import urlparse


def run(cmd, cwd=None):
    """Run a shell command, return stdout. Exit on failure."""
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"FAILED: {cmd}\n{r.stderr}")
        sys.exit(1)
    return r.stdout.strip()


def gh(endpoint):
    """Call GitHub REST API via gh CLI, return parsed JSON."""
    return json.loads(run(f"gh api {endpoint}"))


def run_cursor_agent(work_dir, branch, prompt, commit_msg, agent_path):
    """Switch to branch, run Cursor agent with prompt, commit changes."""
    print(f"\n--- Running Cursor agent on {branch} ---")
    run(f"git switch {branch}", cwd=work_dir)
    r = subprocess.run(
        [agent_path, "-p", prompt],
        cwd=work_dir,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if r.returncode != 0:
        print(f"FAILED: agent -p <prompt>\n{r.stderr}")
        sys.exit(1)
    # Stage and commit whatever the agent changed
    run("git add -A", cwd=work_dir)
    run(f'git diff --cached --quiet || git commit -m {subprocess.list2cmdline([commit_msg])}',
        cwd=work_dir)
    print(f"--- {branch}: changes implemented successfully ---")


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)

    repo_url = sys.argv[1]
    path = urlparse(repo_url).path.strip("/")   # "owner/repo"
    owner, repo = path.split("/", 1)
    issue_num = int(sys.argv[2])
    pr_num = int(sys.argv[3])

    # Resolve agent executable (AGENT_PATH or CURSOR_AGENT_PATH env, else which)
    agent_path = os.environ.get("AGENT_PATH") or os.environ.get("CURSOR_AGENT_PATH")
    if not agent_path:
        agent_path = shutil.which("agent")
    if not agent_path or not os.path.isfile(agent_path):
        print("ERROR: Cursor Agent CLI (agent) not found.")
        print("Install with: curl https://cursor.com/install -fsS | bash")
        print("If already installed, set AGENT_PATH to the full path of the agent binary.")
        sys.exit(1)

    # Fetch PR and issue data from GitHub API
    try:
        pr = gh(f"repos/{owner}/{repo}/pulls/{pr_num}")
    except Exception as e:
        print(f"ERROR: Failed to fetch PR: {e}")
        sys.exit(1)
    
    try:
        issue = gh(f"repos/{owner}/{repo}/issues/{issue_num}")
    except Exception as e:
        print(f"ERROR: Failed to fetch issue: {e}")
        sys.exit(1)


    merge_sha = pr.get("merge_commit_sha")
    if not merge_sha:
        print("ERROR: PR is not merged. merge_commit_sha is missing.")
        sys.exit(1)

    work_dir = f"{repo}"
    if not os.path.isdir(work_dir):
        print(f"ERROR: '{work_dir}' does not exist or not in the current directory. ")
        user_input = input(f"Clone the repository '{work_dir}' at current directory? (y/n)").strip().lower()
        if user_input == "y":
            print(f"Cloning repository '{repo_url}' at current directory...")
            run(f"git clone {repo_url} {work_dir}", cwd=os.getcwd())
            print(f"cloned successfully.")
        else:
            print("Exiting...")
            sys.exit(1)

    # Create four branches: use merge-base when merge has two parents (main moved)
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
        f"{h}-cursor": base_sha,
        f"{h}-cursor-creative": base_sha,
    }
    for name, sha in branches.items():
        run(f"git branch {name} {sha}", cwd=work_dir)
    print("--- branches created ---")

    # Build prompts and run Cursor agent on each cursor branch
    body = issue.get("body") or ""
    prompt = f"# Issue #{issue_num}: {issue['title']}\n\n{body}"
    creative_prompt = prompt + (
        "\n\n---\n"
        "Be creative in your solution. Consider innovative, elegant, "
        "and efficient approaches that go beyond the obvious fix."
    )

    run_cursor_agent(work_dir, f"{h}-cursor", prompt,
                     f"cursor: apply fix for issue #{issue_num}", agent_path)
    run_cursor_agent(work_dir, f"{h}-cursor-creative", creative_prompt,
                     f"cursor-creative: apply fix for issue #{issue_num}", agent_path)

    # Return to base branch when done
    run(f"git checkout {h}-base", cwd=work_dir)

    # Output: branch name → commit hash for each branch
    result = {}
    for name in branches:
        sha = run(f"git rev-parse {name}", cwd=work_dir)
        result[name] = sha
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
