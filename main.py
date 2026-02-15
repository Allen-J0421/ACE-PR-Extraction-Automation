#!/usr/bin/env python3
"""
Single entrypoint for the Flask changelog dataset pipeline.
Usage:
  python main.py resolve
  python main.py extract [--limit N]   # run extract only; pairs from cache or run resolve
  python main.py build [--cursor] [--limit N]  # writes dataset.jsonl; pairs from cache or run resolve
  python main.py all [--cursor] [--limit N]    # writes dataset.jsonl
  python main.py apply-cursor [--limit N]      # updates dataset.jsonl (after 'all' without --cursor)
"""

import argparse
import json
import os
import subprocess
import sys

from params import GITHUB_OWNER, GITHUB_REPO, REPO_URL

WORK_DIR = GITHUB_REPO
from features.build_dataset import (
    get_pairs_from_resolve,
    build_one_row,
    get_cache_path,
    get_cache_dir,
    get_extract_cache_path,
    load_extract_cache,
    save_extract_cache,
    get_extract_entry,
    cursor_branches_exist,
)

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
EXTRACT_JSON_PREFIX = "EXTRACT_JSON="
EXTRACT_ERROR_PREFIX = "EXTRACT_ERROR="


def run_cmd(cmd_list, cwd=None):
    r = subprocess.run(cmd_list, cwd=cwd or PROJECT_ROOT, stdout=subprocess.PIPE, stderr=None, text=True)
    if r.returncode != 0:
        sys.exit(r.returncode)
    return r.stdout


def run_extract(repo_url, issue_id, pr_id, project_root):
    exe = os.path.join(project_root, "features", "extract.py")
    cmd = [sys.executable, exe, repo_url, str(issue_id), str(pr_id), "--json", "--autoc"]
    proc = subprocess.run(cmd, cwd=project_root, stdout=subprocess.PIPE, stderr=None, text=True)
    # Parse stdout for machine-readable result or error
    for line in proc.stdout.splitlines():
        if line.startswith(EXTRACT_JSON_PREFIX):
            payload = line[len(EXTRACT_JSON_PREFIX):].strip()
            return json.loads(payload), None
        if line.startswith(EXTRACT_ERROR_PREFIX):
            return None, line[len(EXTRACT_ERROR_PREFIX):].strip()
    if proc.returncode != 0:
        return None, "extract failed (no detail)"
    return None, "EXTRACT_JSON line not found in output"


def run_agent_change(repo_url, issue_id, pr_id, h, project_root):
    exe = os.path.join(project_root, "features", "agent_change.py")
    cmd = [sys.executable, exe, repo_url, str(issue_id), str(pr_id), h, "--project-root", project_root]
    proc = subprocess.run(cmd, cwd=project_root, stdout=subprocess.PIPE, stderr=None, text=True)
    if proc.returncode != 0:
        return "agent_change failed"
    return None


DATASET_DEFAULT = "dataset.jsonl"


def do_build(args, pairs):
    repo_url = REPO_URL
    cache_dir = getattr(args, "cache_dir", None)
    extract_cache = load_extract_cache(PROJECT_ROOT, cache_dir)
    failed = []
    out_path = getattr(args, "out", DATASET_DEFAULT)
    with open(out_path, "w") as out:
        for i, p in enumerate(pairs):
            issue_id = p.get("issue_id")
            pr_id = p.get("pr_id")
            if issue_id is None or pr_id is None:
                failed.append((issue_id, pr_id, "missing issue_id or pr_id"))
                continue
            print(f"[{i+1}/{len(pairs)}] issue={issue_id} pr={pr_id} ...", flush=True)
            extract_data, err = run_extract(repo_url, issue_id, pr_id, PROJECT_ROOT)
            if err:
                print(f"  extract failed: {err}", flush=True)
                failed.append((issue_id, pr_id, err))
                continue
            cached = get_extract_entry(extract_cache, issue_id, pr_id)
            if cached:
                root_hash = cached.get("root_hash")
                h = cached.get("h")
            else:
                root_hash = extract_data.get("root_hash")
                h = extract_data.get("h")
                if root_hash and h:
                    extract_cache = extract_cache or []
                    extract_cache.append({"issue_id": issue_id, "pr_id": pr_id, "root_hash": root_hash, "h": h})
                    save_extract_cache(PROJECT_ROOT, extract_cache, cache_dir)
            if getattr(args, "cursor", False) and h:
                agent_err = run_agent_change(repo_url, issue_id, pr_id, h, PROJECT_ROOT)
                if agent_err:
                    print(f"  agent_change failed: {agent_err}", flush=True)
                    failed.append((issue_id, pr_id, agent_err))
                    continue
            row, row_err = build_one_row(PROJECT_ROOT, issue_id, pr_id, root_hash, h)
            if row_err:
                print(f"  build row failed: {row_err}", flush=True)
                failed.append((issue_id, pr_id, row_err))
                continue
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            print("  ok", flush=True)
    print(f"Wrote {args.out}. Failed: {len(failed)}")
    if failed:
        for issue_id, pr_id, err in failed[:20]:
            print(f"  issue={issue_id} pr={pr_id}: {err}")
        if len(failed) > 20:
            print(f"  ... and {len(failed) - 20} more")
    if failed:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Flask changelog dataset pipeline.")
    ap.add_argument("--cache-dir", default=None, metavar="DIR", help="Directory for cache files (default: <project_root>/<reponame>_cache)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_resolve = sub.add_parser("resolve", help="Fetch merged PRs via GraphQL and build (issue_id, pr_id) pairs + GHSA IDs")
    p_resolve.add_argument("--refresh", action="store_true", help="Force re-fetch from API even if cache exists")

    p_extract = sub.add_parser("extract", help="Run extract only for each pair, write extract cache. Pairs from cache or run resolve if missing.")
    p_extract.add_argument("--limit", type=int, default=None, help="Max pairs to process")

    p_build = sub.add_parser("build", help="Run extract + diffs for each pair, write JSONL. Pairs from cache or run resolve if missing.")
    p_build.add_argument("--cursor", action="store_true", help="Run Cursor agent during extract")
    p_build.add_argument("--limit", type=int, default=None, help="Max pairs to process")

    p_all = sub.add_parser("all", help="Resolve pairs then build dataset. Pairs from cache or run resolve if missing.")
    p_all.add_argument("--cursor", action="store_true", help="Run Cursor agent during extract")
    p_all.add_argument("--limit", type=int, default=None, help="Max pairs to process")

    p_apply_cursor = sub.add_parser("apply-cursor", help="Apply cursor (agent_change) to dataset rows; use after 'all' without --cursor")
    p_apply_cursor.add_argument("--limit", type=int, default=None, help="Max pairs to process (default: all in dataset)")

    args = ap.parse_args()
    cache_dir = getattr(args, "cache_dir", None)

    if args.cmd == "resolve":
        script = os.path.join(PROJECT_ROOT, "features", "resolve_pairs.py")
        cache_dir_path = get_cache_dir(PROJECT_ROOT, cache_dir)
        cmd = [sys.executable, script, "--json", "--cache", cache_dir_path]
        if getattr(args, "refresh", False):
            cmd.append("--refresh")
        out = run_cmd(cmd)
        print(out, end="")

    elif args.cmd == "extract":
        pairs = get_pairs_from_resolve(PROJECT_ROOT, cache_dir)
        if args.limit is not None:
            pairs = pairs[: args.limit]
        repo_url = REPO_URL
        extract_entries = []
        failed = []
        for i, p in enumerate(pairs):
            issue_id = p.get("issue_id")
            pr_id = p.get("pr_id")
            if issue_id is None or pr_id is None:
                failed.append((issue_id, pr_id, "missing issue_id or pr_id"))
                continue
            print(f"[{i+1}/{len(pairs)}] issue={issue_id} pr={pr_id} ...", flush=True)
            extract_data, err = run_extract(repo_url, issue_id, pr_id, PROJECT_ROOT)
            if err:
                print(f"  extract failed: {err}", flush=True)
                failed.append((issue_id, pr_id, err))
                continue
            root_hash = extract_data.get("root_hash")
            h = extract_data.get("h")
            extract_entries.append({"issue_id": issue_id, "pr_id": pr_id, "root_hash": root_hash, "h": h})
            print("  ok", flush=True)
        save_extract_cache(PROJECT_ROOT, extract_entries, cache_dir)
        print(f"Wrote {get_extract_cache_path(PROJECT_ROOT, cache_dir)}. Failed: {len(failed)}")
        if failed:
            for issue_id, pr_id, err in failed[:20]:
                print(f"  issue={issue_id} pr={pr_id}: {err}")
            if len(failed) > 20:
                print(f"  ... and {len(failed) - 20} more")
            sys.exit(1)

    elif args.cmd == "build":
        pairs = get_pairs_from_resolve(PROJECT_ROOT, cache_dir)
        if args.limit is not None:
            pairs = pairs[: args.limit]
        do_build(args, pairs)

    elif args.cmd == "all":
        pairs = get_pairs_from_resolve(PROJECT_ROOT, cache_dir)
        if args.limit is not None:
            pairs = pairs[: args.limit]
        do_build(args, pairs)

    elif args.cmd == "apply-cursor":
        dataset_path = DATASET_DEFAULT
        if not os.path.isfile(dataset_path):
            print(f"ERROR: Dataset file not found: {dataset_path}", file=sys.stderr)
            print("Run 'main all' (without --cursor) first to create the dataset.", file=sys.stderr)
            sys.exit(1)
        with open(dataset_path) as f:
            rows = [json.loads(line) for line in f if line.strip()]
        if not rows:
            print("Dataset is empty. Nothing to apply.", file=sys.stderr)
            sys.exit(0)
        work_dir = os.path.join(PROJECT_ROOT, WORK_DIR)
        has_cursor = []
        no_cursor = []
        for row in rows:
            issue_id = row.get("issue_id")
            pr_id = row.get("pr_id")
            root_hash = row.get("root_hash")
            if issue_id is None or pr_id is None or not root_hash:
                continue
            h = root_hash[:8]
            if cursor_branches_exist(work_dir, h):
                has_cursor.append((row, issue_id, pr_id, root_hash, h))
            else:
                no_cursor.append((row, issue_id, pr_id, root_hash, h))
        if not no_cursor and not has_cursor:
            no_cursor = [(row, row.get("issue_id"), row.get("pr_id"), (row.get("root_hash") or "")[:8] and row.get("root_hash"), (row.get("root_hash") or "")[:8]) for row in rows if row.get("issue_id") is not None and row.get("pr_id") is not None and row.get("root_hash")]
            if not no_cursor:
                no_cursor = [(row, row.get("issue_id"), row.get("pr_id"), row.get("root_hash"), (row.get("root_hash") or "")[:8]) for row in rows]
        if len(has_cursor) == len(rows) and has_cursor:
            print("Cursor changes already applied for all pairs.")
            sys.exit(0)
        if has_cursor and no_cursor:
            print(f"{len(has_cursor)} pair(s) already have cursor applied. {len(no_cursor)} pair(s) do not.")
            try:
                reply = input("Apply cursor for the pair(s) that don't? [y/N]: ").strip().lower()
            except EOFError:
                reply = "n"
            if reply != "y":
                print("Exiting without changes.")
                sys.exit(0)
            to_apply = no_cursor
        else:
            to_apply = no_cursor
        if getattr(args, "limit", None) is not None:
            to_apply = to_apply[: args.limit]
        repo_url = REPO_URL
        for row, issue_id, pr_id, root_hash, h in to_apply:
            print(f"Applying cursor: issue={issue_id} pr={pr_id} ...", flush=True)
            agent_err = run_agent_change(repo_url, issue_id, pr_id, h, PROJECT_ROOT)
            if agent_err:
                print(f"  agent_change failed: {agent_err}", flush=True)
                continue
            new_row, row_err = build_one_row(PROJECT_ROOT, issue_id, pr_id, root_hash, h)
            if row_err:
                print(f"  build row failed: {row_err}", flush=True)
                continue
            row["cursor_diff"] = new_row.get("cursor_diff", "")
            row["cursor_creative_diff"] = new_row.get("cursor_creative_diff", "")
            print("  ok", flush=True)
        with open(dataset_path, "w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"Updated {dataset_path}.")


if __name__ == "__main__":
    main()
