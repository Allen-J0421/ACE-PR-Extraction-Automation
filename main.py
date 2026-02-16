#!/usr/bin/env python3
"""
Single entrypoint for the Flask changelog dataset pipeline.
Usage:
  python main.py resolve                       # fetch merged PRs via GraphQL, build pairs
  python main.py extract [--limit N]           # run extract only; writes extract_cache.json
  python main.py apply-cursor [--limit N]      # run agent_change for pairs in extract_cache; updates extract_cache.json
  python main.py build [--limit N]             # build dataset.jsonl from extract_cache.json
  python main.py all [--limit N]               # resolve + extract + build (no cursor)
"""

import argparse
import json
import os
import subprocess
import sys

from params import GITHUB_REPO, REPO_URL

WORK_DIR = GITHUB_REPO
from features.build_dataset import (
    get_pairs_from_resolve,
    build_one_row,
    get_cache_dir,
    get_extract_cache_path,
    load_extract_cache,
    save_extract_cache,
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


def main():
    ap = argparse.ArgumentParser(description="Flask changelog dataset pipeline.")
    ap.add_argument("--cache-dir", default=None, metavar="DIR", help="Directory for cache files (default: <project_root>/<reponame>_cache)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_resolve = sub.add_parser("resolve", help="Fetch merged PRs via GraphQL and build (issue_id, pr_id) pairs + GHSA IDs")
    p_resolve.add_argument("--refresh", action="store_true", help="Force re-fetch from API even if cache exists")

    p_extract = sub.add_parser("extract", help="Run extract only for each pair, write extract cache. Pairs from cache or run resolve if missing.")
    p_extract.add_argument("--limit", type=int, default=None, help="Max pairs to process")

    p_build = sub.add_parser("build", help="Build dataset.jsonl strictly from extract_cache.json (no extract runs).")
    p_build.add_argument("--limit", type=int, default=None, help="Max entries to process")

    p_all = sub.add_parser("all", help="Resolve pairs, extract, then build dataset.")
    p_all.add_argument("--limit", type=int, default=None, help="Max pairs to process")

    p_apply_cursor = sub.add_parser("apply-cursor", help="Run agent_change for pairs in extract_cache; updates extract_cache.json with cursor hashes.")
    p_apply_cursor.add_argument("--limit", type=int, default=None, help="Max pairs to process")

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
            base_hash = extract_data.get("base_hash")
            merge_hash = extract_data.get("merge_hash")
            extract_entries.append({"issue_id": issue_id, "pr_id": pr_id, "base_hash": base_hash, "merge_hash": merge_hash, "branches": extract_data.get("branches")})
            print("  extract ok", flush=True)
        save_extract_cache(PROJECT_ROOT, extract_entries, cache_dir)
        print(f"Wrote {get_extract_cache_path(PROJECT_ROOT, cache_dir)}. Failed: {len(failed)}")
        if failed:
            for issue_id, pr_id, err in failed[:20]:
                print(f"  issue={issue_id} pr={pr_id}: {err}")
            if len(failed) > 20:
                print(f"  ... and {len(failed) - 20} more")
            sys.exit(1)

    elif args.cmd == "build":
        extract_cache = load_extract_cache(PROJECT_ROOT, cache_dir)
        if not extract_cache:
            print("ERROR: No extract cache found. Run 'main extract' first.", file=sys.stderr)
            sys.exit(1)
        entries = extract_cache
        if args.limit is not None:
            entries = entries[: args.limit]
        failed = []
        out_path = DATASET_DEFAULT
        with open(out_path, "w") as out:
            for i, entry in enumerate(entries):
                issue_id = entry.get("issue_id")
                pr_id = entry.get("pr_id")
                base_hash = entry.get("base_hash")
                merge_hash = entry.get("merge_hash") or entry.get("human_hash")
                if issue_id is None or pr_id is None or not base_hash or not merge_hash:
                    failed.append((issue_id, pr_id, "incomplete cache entry"))
                    continue
                print(f"[{i+1}/{len(entries)}] issue={issue_id} pr={pr_id} ...", flush=True)
                row, row_err = build_one_row(PROJECT_ROOT, issue_id, pr_id, base_hash, merge_hash)
                if row_err:
                    print(f"  build row failed: {row_err}", flush=True)
                    failed.append((issue_id, pr_id, row_err))
                    continue
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                print("  ok", flush=True)
        print(f"Wrote {out_path}. Failed: {len(failed)}")
        if failed:
            for issue_id, pr_id, err in failed[:20]:
                print(f"  issue={issue_id} pr={pr_id}: {err}")
            if len(failed) > 20:
                print(f"  ... and {len(failed) - 20} more")
        if failed:
            sys.exit(1)

    elif args.cmd == "all":
        # resolve (if needed) -> extract -> build
        pairs_path = os.path.join(get_cache_dir(PROJECT_ROOT, cache_dir), "pairs.json")
        if not os.path.isfile(pairs_path):
            print("Running resolve first...", flush=True)
            script = os.path.join(PROJECT_ROOT, "features", "resolve_pairs.py")
            cache_dir_path = get_cache_dir(PROJECT_ROOT, cache_dir)
            cmd = [sys.executable, script, "--json", "--cache", cache_dir_path]
            run_cmd(cmd)
        # extract
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
            base_hash = extract_data.get("base_hash")
            merge_hash = extract_data.get("merge_hash")
            extract_entries.append({"issue_id": issue_id, "pr_id": pr_id, "base_hash": base_hash, "merge_hash": merge_hash, "branches": extract_data.get("branches")})
            print("  extract ok", flush=True)
        save_extract_cache(PROJECT_ROOT, extract_entries, cache_dir)
        if failed:
            print(f"Extract phase: {len(failed)} failures")
            for issue_id, pr_id, err in failed[:20]:
                print(f"  issue={issue_id} pr={pr_id}: {err}")
        # build
        build_failed = []
        out_path = DATASET_DEFAULT
        with open(out_path, "w") as out:
            for i, entry in enumerate(extract_entries):
                issue_id = entry.get("issue_id")
                pr_id = entry.get("pr_id")
                base_hash = entry.get("base_hash")
                merge_hash = entry.get("merge_hash")
                if not base_hash or not merge_hash:
                    build_failed.append((issue_id, pr_id, "missing hashes"))
                    continue
                print(f"[build {i+1}/{len(extract_entries)}] issue={issue_id} pr={pr_id} ...", flush=True)
                row, row_err = build_one_row(PROJECT_ROOT, issue_id, pr_id, base_hash, merge_hash)
                if row_err:
                    print(f"  build row failed: {row_err}", flush=True)
                    build_failed.append((issue_id, pr_id, row_err))
                    continue
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                print("  ok", flush=True)
        print(f"Wrote {out_path}. Build failures: {len(build_failed)}")
        if build_failed:
            for issue_id, pr_id, err in build_failed[:20]:
                print(f"  issue={issue_id} pr={pr_id}: {err}")

    elif args.cmd == "apply-cursor":
        extract_cache = load_extract_cache(PROJECT_ROOT, cache_dir)
        if not extract_cache:
            print("ERROR: No extract cache found. Run 'main extract' first.", file=sys.stderr)
            sys.exit(1)
        work_dir = os.path.join(PROJECT_ROOT, WORK_DIR)
        has_cursor = []
        no_cursor = []
        for entry in extract_cache:
            issue_id = entry.get("issue_id")
            pr_id = entry.get("pr_id")
            base_hash = entry.get("base_hash")
            if issue_id is None or pr_id is None or not base_hash:
                continue
            h = base_hash[:8]
            branches = entry.get("branches") or {}
            if isinstance(branches, dict) and branches.get(f"{h}-cursor") and branches.get(f"{h}-cursor-creative"):
                has_cursor.append(entry)
            elif cursor_branches_exist(work_dir, h):
                has_cursor.append(entry)
            else:
                no_cursor.append(entry)

        if not no_cursor and has_cursor:
            print(f"Cursor changes already applied for all {len(has_cursor)} pair(s).")
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
        if getattr(args, "limit", None) is not None:
            to_apply = to_apply[: args.limit]

        repo_url = REPO_URL
        # Build a lookup for fast entry updates
        entry_lookup = {}
        for entry in extract_cache:
            key = (entry.get("issue_id"), entry.get("pr_id"))
            entry_lookup[key] = entry

        for i, entry in enumerate(to_apply):
            issue_id = entry.get("issue_id")
            pr_id = entry.get("pr_id")
            base_hash = entry.get("base_hash")
            h = base_hash[:8]
            print(f"[{i+1}/{len(to_apply)}] Applying cursor: issue={issue_id} pr={pr_id} ...", flush=True)
            agent_err = run_agent_change(repo_url, issue_id, pr_id, h, PROJECT_ROOT)
            if agent_err:
                print(f"  agent_change failed: {agent_err}", flush=True)
                continue
            # Resolve cursor branch SHAs
            cursor_hash = None
            cursor_creative_hash = None
            try:
                r = subprocess.run(
                    ["git", "rev-parse", f"{h}-cursor"],
                    cwd=work_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                if r.returncode == 0:
                    cursor_hash = r.stdout.strip()
            except Exception:
                pass
            try:
                r = subprocess.run(
                    ["git", "rev-parse", f"{h}-cursor-creative"],
                    cwd=work_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                if r.returncode == 0:
                    cursor_creative_hash = r.stdout.strip()
            except Exception:
                pass
            # Update the entry in the cache: add cursor branches to branches dict
            key = (issue_id, pr_id)
            if key in entry_lookup:
                ent = entry_lookup[key]
                branches = ent.get("branches")
                if isinstance(branches, dict):
                    branches[f"{h}-cursor"] = cursor_hash
                    branches[f"{h}-cursor-creative"] = cursor_creative_hash
            print(f"  ok (cursor={cursor_hash}, creative={cursor_creative_hash})", flush=True)

        save_extract_cache(PROJECT_ROOT, extract_cache, cache_dir)
        cache_path = get_extract_cache_path(PROJECT_ROOT, cache_dir)
        print(f"Updated {cache_path}.")


if __name__ == "__main__":
    main()
