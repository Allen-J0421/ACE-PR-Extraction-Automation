## Environment and packages

- **Python:** 3.7+ (stdlib only for parsing/resolve; no pip packages)
- **Git:** on `PATH`
- **GitHub CLI (`gh`):** [install](https://cli.github.com/), authenticated (`gh auth login`)
- **Cursor Agent CLI (`agent`):** needed only when using `--cursor`; [Cursor](https://cursor.com/) v2.4.7+. Set `AGENT_PATH` if not on `PATH`.

## Concepts

- **Refs** = A full list of issue numbers, PR numbers, and GHSA IDs from the designated repo in `params.py`. eg.[Flask stable changelog](https://flask.palletsprojects.com/en/stable/changes/).
- **Pairs** = Paired issue number and corresponding pr that resolved it `(issue_id, pr_id)` from refs, matched using `features/resolve_pairs.py` via GitHub API (e.g. “Fixes #N” in PR body, or issue timeline).
- **params.py** = config: `CREATIVE_PROMPT_SUFFIX`, `CHANGELOG_URL`, `GITHUB_OWNER`, `GITHUB_REPO`, regex patterns.

# Single entry — main.py

Use **main.py** as the single entry point. It is the high-level controller. Run from **project root**.

1. **resolve** — calls `features/resolve_pairs.py` to parse the changelog and find (issue_id, pr_id) pairs. Writes **resolve_cache.json** (refs + pairs). If you run **extract** or **build** without a prior resolve, resolve runs automatically when the cache is missing.
2. **extract** — runs extract only for each pair (from resolve cache or `--pairs`), writes **extract_cache.json** (root_hash, h per pair). No agent_change, no build.
3. **build** — for each pair: run extract (to set repo branches), then agent_change (if `--cursor`), then build_one_row. Uses extract cache for root_hash/h when present.
4. **all** — load pairs (from resolve cache or run resolve), then same as build. Without `--cursor`, produces a dataset with empty `cursor_diff` / `cursor_creative_diff`.
5. **apply-cursor** — use after **all** (no `--cursor`). Reads existing dataset, checks which pairs already have cursor branches; applies agent_change + build_one_row only to those that don’t, and updates the dataset rows in place (no extract).

## Pipeline flow

```
resolve   →  pairs [(issue_id, pr_id), ...]  →  resolve_cache.json
    ↓
extract   →  (optional) run extract per pair  →  extract_cache.json
    ↓
build     →  per pair: extract → [agent_change if --cursor] → build_one_row  →  dataset.jsonl
    ↓
apply-cursor  →  (if dataset exists, no --cursor was used) apply agent_change to rows that need it, update dataset
```

**Branches:** `features/extract.py` creates only `{h}-base` and `{h}-human`. `features/agent_change.py` creates `{h}-cursor` and `{h}-cursor-creative` from base, then runs the Cursor agent on each.

**Resolve cache:** Running **resolve** writes `resolve_cache.json` in the project root with two entries: `refs` (full list: `issue_ids`, `pr_ids`, `ghsa_ids`) and `pairs` (array of `{issue_id, pr_id}`). **build** (without `--pairs`) and **all** load pairs from this file if it exists; if not, they run resolve first to create it, then load. This avoids re-fetching the changelog and re-resolving on every run.

## Subcommands

| Subcommand | What it does | What it runs | Options | Output |
|------------|---------------|--------------|---------|--------|
| **resolve** | Parse changelog and resolve to (issue_id, pr_id) pairs | `features/resolve_pairs.py --json` | `--out pairs.json` | Pairs JSON, resolve_cache.json |
| **extract** | Run extract only for each pair, fill extract cache | Load pairs (cache or resolve) → extract per pair | `--pairs`, `--out`, `--limit N` | extract_cache.json |
| **build** | For each pair: extract → agent (if --cursor) → build one row | Load pairs → extract → agent_change (if `--cursor`) → build_one_row per pair | `--cursor`, `--pairs`, `--out dataset.jsonl`, `--limit N` | JSONL file |
| **all** | Resolve (if needed) then build dataset | Load pairs → same as build | `--cursor`, `--out dataset.jsonl`, `--limit N` | JSONL file |
| **apply-cursor** | Create cursor branches and apply changes to dataset  | Read dataset → check cursor branches → agent_change + build_one_row for pairs that need it, update rows | `--out dataset.jsonl`, `--limit N` | Updated dataset |

- Note: Use `apply-cursor` subcommand ONLY if dataset has been created and -human-base branches exists! ie. seperate cursor changes with dataset creation)

## Examples

```bash
python main.py resolve --out pairs.json
python main.py extract --limit 5
python main.py build --cursor --pairs pairs.json --out dataset.jsonl --limit 5
python main.py build --cursor --out dataset.jsonl --limit 5
python main.py all --out dataset.jsonl --limit 5
python main.py apply-cursor --out dataset.jsonl
```

**Dataset output:** Each line has `project`, `issue_text`, `issue_id`, `pr_text`, `pr_id`, `root_hash`, `pr_diff`, `cursor_diff`, `cursor_creative_diff`. Use `--cursor` to populate the cursor diffs; without it they are empty. With `--cursor`, one extract + one agent_change per pair (can take hours). Use `--limit` for testing. Failed pairs are skipped and reported.

---

# Each file’s single use

Standalone usage for each script in **features/** (run from **project root** unless noted).

## features/resolve_pairs.py

Changelog → refs and/or pairs (fetch changelog, optionally resolve via GitHub API).

```bash
python features/resolve_pairs.py [--json] [--refs-only] [--cache PATH]
```

- No flags — pairs, print one `issue_id pr_id` per line.
- `--json` — pairs as JSON array, or (with `--refs-only`) refs as JSON.
- `--refs-only` — output refs only (issue_ids, pr_ids, ghsa_ids); no pairing.
- `--cache PATH` — write a single JSON file with `refs` and `pairs` (default path: resolve_cache.json in current directory). build/all load from this cache when no `--pairs` file is given.

Save pairs: `python features/resolve_pairs.py --json > pairs.json`. With cache: `python features/resolve_pairs.py --json --cache resolve_cache.json`

## features/extract.py

Creates **two** branches only: `{h}-base` and `{h}-human`. Does not run the Cursor agent. Cursor branches are created in agent_change.py.

```bash
python features/extract.py <repo_url> <issue_number> <pr_number> [--json] [--autoc]
```

- `--json` — machine-readable output: root_hash, h, branches (base and human only).
- `--autoc` — clone repo if missing (non-interactive).

## features/agent_change.py

Creates `{h}-cursor` and `{h}-cursor-creative` from `{h}-base`, then runs the Cursor agent on each. Call after **extract.py** (which creates base and human only).

```bash
python features/agent_change.py <repo_url> <issue_number> <pr_number> <h> [--project-root P]
```

- `repo_url` — same as extract.py (e.g. `https://github.com/owner/repo`).
- `h` — 8-char branch prefix from extract output (EXTRACT_JSON.h).
- Requires agent CLI; set `AGENT_PATH` if not on PATH.

## features/build_dataset.py

Build JSONL from **state only** (no extract/agent calls). Expects pairs with `root_hash` and `h` (e.g. from a prior run of extract). Use **main.py build** for the full flow (extract → agent → build).

```bash
python features/build_dataset.py --pairs state.json [--output dataset.jsonl] [--limit N] [--project-root P]
```

- `--pairs` — **required**. JSON array of `{issue_id, pr_id, root_hash, h}` (branches must already exist).
- `--output` — default `dataset.jsonl`.
- `--limit` — process only first N pairs.
