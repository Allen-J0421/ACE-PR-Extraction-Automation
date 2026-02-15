#!/usr/bin/env python3
"""
Create cursor branches from base, then run Cursor agent on them.
Call after extract.py (which creates only {h}-base and {h}-human).
This script: creates {h}-cursor and {h}-cursor-creative by copying {h}-base, then runs the agent on each.
Usage: python features/agent_change.py <repo_url> <issue_number> <pr_number> <h> [--project-root P]
  repo_url = same as extract.py (e.g. https://github.com/owner/repo).
  h = 8-char branch prefix from extract.py output (EXTRACT_JSON.h).
Requires: gh, agent (Cursor Agent CLI). Set AGENT_PATH if agent is not on PATH.
"""

import json
import os
import shutil
import subprocess
import sys
from urllib.parse import urlparse

# Project root (parent of features/) for params import
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from params import CREATIVE_PROMPT_SUFFIX


def run(cmd, cwd=None):
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"FAILED: {cmd}\n{r.stderr}", file=sys.stderr)
        sys.exit(1)
    return r.stdout.strip()


def gh(endpoint):
    return json.loads(run(f"gh api {endpoint}"))


def run_cursor_agent(work_dir, branch, prompt, commit_msg, agent_path):
    """Switch to branch, run Cursor agent with prompt, commit changes."""
    print(f"--- Running Cursor agent on {branch} ---", file=sys.stderr, flush=True)
    run(f"git switch {branch}", cwd=work_dir)
    r = subprocess.run(
        [agent_path, "-p", prompt],
        cwd=work_dir,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if r.returncode != 0:
        print(f"FAILED: agent -p <prompt>\n{r.stderr}", file=sys.stderr)
        sys.exit(1)
    run("git add -A", cwd=work_dir)
    run(f'git diff --cached --quiet || git commit -m {subprocess.list2cmdline([commit_msg])}',
        cwd=work_dir)
    print(f"--- {branch}: changes implemented successfully ---", file=sys.stderr, flush=True)


def main():
    argv = sys.argv[1:]
    project_root = os.getcwd()
    args_no_opt = []
    i = 0
    while i < len(argv):
        if argv[i] == "--project-root" and i + 1 < len(argv):
            project_root = argv[i + 1]
            i += 2
            continue
        args_no_opt.append(argv[i])
        i += 1
    if len(args_no_opt) != 4:
        print(__doc__)
        sys.exit(1)
    repo_url, issue_num_s, pr_num_s, h = args_no_opt
    issue_num = int(issue_num_s)
    path = urlparse(repo_url).path.strip("/")
    owner, repo = path.split("/", 1)
    work_dir = os.path.join(os.path.abspath(project_root), repo)
    if not os.path.isdir(work_dir):
        print(f"ERROR: work dir not found: {work_dir}", file=sys.stderr)
        sys.exit(1)

    print("--- Creating cursor branches from base... ---", file=sys.stderr, flush=True)
    base_ref = f"{h}-base"
    run(f"git branch -f {h}-cursor {base_ref}", cwd=work_dir)
    run(f"git branch -f {h}-cursor-creative {base_ref}", cwd=work_dir)
    print(f"--- Created {h}-cursor and {h}-cursor-creative from {base_ref} ---", file=sys.stderr, flush=True)

    agent_path = os.environ.get("AGENT_PATH") or os.environ.get("CURSOR_AGENT_PATH")
    if not agent_path:
        agent_path = shutil.which("agent")
    if not agent_path or not os.path.isfile(agent_path):
        print("ERROR: Cursor Agent CLI (agent) not found.", file=sys.stderr)
        print("Set AGENT_PATH to the full path of the agent binary.", file=sys.stderr)
        sys.exit(1)

    print("--- Fetching issue text for agent prompt... ---", file=sys.stderr, flush=True)
    issue = gh(f"repos/{owner}/{repo}/issues/{issue_num}")
    body = issue.get("body") or ""
    prompt = f"# Issue #{issue_num}: {issue['title']}\n\n{body}"
    creative_prompt = prompt + CREATIVE_PROMPT_SUFFIX

    run_cursor_agent(work_dir, f"{h}-cursor", prompt,
                     f"cursor: apply fix for issue #{issue_num}", agent_path)
    run_cursor_agent(work_dir, f"{h}-cursor-creative", creative_prompt,
                     f"cursor-creative: apply fix for issue #{issue_num}", agent_path)
    print("--- Switching back to base branch ---", file=sys.stderr, flush=True)
    run(f"git checkout {h}-base", cwd=work_dir)
    print("--- agent_change done ---", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
