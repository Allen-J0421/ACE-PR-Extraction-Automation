## Environment and packages

- **Python:** 3.7+ (stdlib only for parsing/resolve; no pip packages)
- **Git:** on `PATH`
- **GitHub CLI (`gh`):** [install](https://cli.github.com/), authenticated (`gh auth login`)
- **Cursor Agent CLI (`agent`):** needed only when using `--cursor`; [Cursor](https://cursor.com/) v2.4.7+. Set `AGENT_PATH` if not on `PATH`.

## Concepts

- **Refs** = Issue and PR IDs derived from the repo in `params.py` (GITHUB_OWNER, GITHUB_REPO), via a single batched GraphQL query for merged PRs and their closing-issue references.
- **Pairs** = `(issue_id, pr_id)` built from merged PRs' `closingIssuesReferences` in `features/resolve_pairs.py` (no changelog parsing).
- **params.py** = config: `CREATIVE_PROMPT_SUFFIX`, `GITHUB_OWNER`, `GITHUB_REPO`.

# Single entry — main.py

Use **main.py** as the single entry point. It is the high-level controller. Run from **project root**.

1. **resolve** — calls `features/resolve_pairs.py` to fetch merged PRs via GraphQL and build (issue_id, pr_id) pairs from closing-issue references. Writes **resolve_cache.json** and **pairs.json** in the cache directory (default: **&lt;reponame&gt;_cache/**). Use **--refresh** to force re-fetch. If you run **extract** or **build** without a prior resolve, resolve runs automatically when the cache is missing.
2. **extract** — runs extract only for each pair. Pairs are loaded from the resolve cache in the cache directory; if the cache is missing, resolve is run first. Writes **extract_cache.json** in the same directory. No agent_change, no build.
3. **build** — for each pair: run extract (to set repo branches), then agent_change (if `--cursor`), then build_one_row. Uses extract cache for root_hash/h when present.
4. **all** — load pairs (from resolve cache or run resolve), then same as build. Without `--cursor`, produces a dataset with empty `cursor_diff` / `cursor_creative_diff`.
5. **apply-cursor** — use after **all** (no `--cursor`). Reads existing dataset, checks which pairs already have cursor branches; applies agent_change + build_one_row only to those that don’t, and updates the dataset rows in place (no extract).

## Pipeline flow

```
resolve   →  pairs [(issue_id, pr_id), ...]  →  <reponame>_cache/resolve_cache.json
    ↓
extract   →  (optional) run extract per pair, creates -base, -human branches  →  <reponame>_cache/extract_cache.json
    ↓
build     →  per pair: extract → [agent_change if --cursor] → build_one_row  →  dataset.jsonl
    ↓
apply-cursor  →  (if dataset exists, no --cursor was used) apply agent_change to rows that need it, update dataset
```

**Branches:** `features/extract.py` creates only `{h}-base` and `{h}-human`. `features/agent_change.py` creates `{h}-cursor` and `{h}-cursor-creative` from base, then runs the Cursor agent on each.

**Cache directory:** By default, cache files are stored in **&lt;reponame&gt;_cache/** (e.g. `flask_cache/`) in the project root: `resolve_cache.json` (refs + pairs) and `extract_cache.json` (root_hash, h per pair). Override with **--cache-dir DIR** (main.py) or **--cache DIR** (resolve_pairs.py). **extract**, **build**, and **all** load pairs from the resolve cache if it exists; if not, they run resolve first to create it, then load.

## Subcommands

| Subcommand | What it does | What it runs | Options | Output |
|------------|---------------|--------------|---------|--------|
| **resolve** | Fetch merged PRs via GraphQL, build (issue_id, pr_id) pairs | `features/resolve_pairs.py --json --cache DIR` | `--cache-dir DIR`, `--refresh` | &lt;reponame&gt;_cache/resolve_cache.json, pairs.json |
| **extract** | Run extract only for each pair, create branches and fill extract cache | Pairs from cache (run resolve if missing) → extract per pair | `--limit N`, `--cache-dir DIR` | &lt;reponame&gt;_cache/extract_cache.json |
| **build** | For each pair: extract → agent (if --cursor) → build one row | Pairs from cache (run resolve if missing) → extract → agent_change (if `--cursor`) → build_one_row | `--cursor`, `--limit N`, `--cache-dir DIR` | dataset.jsonl |
| **all** | Resolve (if needed) then build dataset | Load pairs → same as build | `--cursor`, `--limit N` | dataset.jsonl |
| **apply-cursor** | Create cursor branches and apply changes to dataset  | Read dataset → check cursor branches → agent_change + build_one_row for pairs that need it, update rows | `--limit N` | dataset.jsonl (updated) |

- Note: Use `apply-cursor` subcommand ONLY if dataset has been created and -human-base branches exists! ie. seperate cursor changes with dataset creation)

## Examples

```bash
python main.py resolve
python main.py extract --limit 5
python main.py build --cursor --limit 5
python main.py build --cursor --limit 5
python main.py all --limit 5
python main.py apply-cursor
```

**Dataset output:** Each line has `project`, `issue_text`, `issue_id`, `pr_text`, `pr_id`, `root_hash`, `pr_diff`, `cursor_diff`, `cursor_creative_diff`. Use `--cursor` to populate the cursor diffs; without it they are empty. With `--cursor`, one extract + one agent_change per pair (can take hours). Use `--limit` for testing. Failed pairs are skipped and reported.

---

# Each file’s single use

Standalone usage for each script in **features/** (run from **project root** unless noted).

## features/resolve_pairs.py

Changelog → refs and/or pairs (fetch changelog, optionally resolve via GitHub API).

```bash
python features/resolve_pairs.py [--json] [--refs-only] [--cache DIR]
```

- No flags — pairs, print one `issue_id pr_id` per line.
- `--json` — pairs as JSON array, or (with `--refs-only`) refs as JSON.
- `--refs-only` — output refs only (issue_ids, pr_ids, ghsa_ids); no pairing.
- `--cache DIR` — directory for cache files (default: cwd/&lt;reponame&gt;_cache). Resolve writes `resolve_cache.json` there; extract writes `extract_cache.json` there. main.py uses **--cache-dir DIR** for the same folder.

Save pairs: `python features/resolve_pairs.py --json > pairs.json`. Custom cache dir: `python features/resolve_pairs.py --json --cache ./my_cache`

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
