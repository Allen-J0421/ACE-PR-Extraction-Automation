## Environment and packages

- **Python:** 3.7+ (stdlib only for parsing/resolve; no pip packages)
- **Git:** on `PATH`
- **GitHub CLI (`gh`):** [install](https://cli.github.com/), authenticated (`gh auth login`)
- **Cursor Agent CLI (`agent`):** needed only for `apply-cursor`; [Cursor](https://cursor.com/) v2.4.7+. Set `AGENT_PATH` if not on `PATH`.

## Concepts

- **Refs** = Issue and PR IDs derived from the repo in `params.py` (GITHUB_OWNER, GITHUB_REPO), via a single batched GraphQL query for merged PRs and their closing-issue references.
- **Pairs** = `(issue_id, pr_id)` built from merged PRs' `closingIssuesReferences` in `features/resolve_pairs.py` (no changelog parsing).
- **params.py** = config: `CREATIVE_PROMPT_SUFFIX`, `GITHUB_OWNER`, `GITHUB_REPO`, `REPO_URL`.

# Single entry — main.py

Use **main.py** as the single entry point. It is the high-level controller. Run from **project root**.

1. **resolve** — calls `features/resolve_pairs.py` to fetch merged PRs via GraphQL and build (issue_id, pr_id) pairs from closing-issue references. Writes **resolve_cache.json** and **pairs.json** in the cache directory (default: **\<reponame\>_cache/**). Use **--refresh** to force re-fetch.
2. **extract** — runs extract for each pair. Pairs are loaded from the resolve cache; if missing, resolve runs first. Writes **extract_cache.json** with `base_hash`, `merge_hash`, and `branches` per entry. No agent_change, no build.
3. **apply-cursor** — reads **extract_cache.json**. For each entry whose `branches` does not yet have cursor refs, runs `agent_change` to create cursor branches, then adds `{h}-cursor` and `{h}-cursor-creative` to the entry's `branches` dict. Does NOT touch `dataset.jsonl`.
4. **build** — strictly reads **extract_cache.json** and calls `build_one_row` per entry to produce **dataset.jsonl**. No extraction, no agent_change. If extract cache is missing, errors out.
5. **all** — resolve (if needed) → extract → build. No cursor step; run `apply-cursor` separately if needed.

## Pipeline flow

```
resolve   →  pairs.json  →  resolve_cache.json
    ↓
extract   →  run extract per pair, create {h}-base / {h}-human branches  →  extract_cache.json
    ↓                                                                           ↓
apply-cursor  →  run agent_change, create {h}-cursor / {h}-cursor-creative  →  extract_cache.json (branches updated with cursor refs)
    ↓                                                                           ↓
build     →  read extract_cache.json  →  build_one_row per entry  →  dataset.jsonl
```

**Branches:** `features/extract.py` creates only `{h}-base` and `{h}-human`. `features/agent_change.py` creates `{h}-cursor` and `{h}-cursor-creative` from base, then runs the Cursor agent on each.

**Cache directory:** By default, cache files are stored in **\<reponame\>_cache/** (e.g. `flask_cache/`) in the project root: `resolve_cache.json`, `pairs.json`, and `extract_cache.json`. Override with **--cache-dir DIR** (main.py) or **--cache DIR** (resolve_pairs.py).

**Extract cache format:**

```json
{"issue_id": 348, "pr_id": 2686, "base_hash": "16d83d6b...", "merge_hash": "abba4b2a...", "branches": {"16d83d6b-base": "16d83d6b...", "16d83d6b-human": "abba4b2a..."}}
```

After `apply-cursor`:

```json
{"issue_id": 348, "pr_id": 2686, "base_hash": "16d83d6b...", "merge_hash": "abba4b2a...", "branches": {"16d83d6b-base": "16d83d6b...", "16d83d6b-human": "abba4b2a...", "16d83d6b-cursor": "abc123...", "16d83d6b-cursor-creative": "def456..."}}
```

## Subcommands

| Subcommand | What it does | Options | Output |
|------------|--------------|---------|--------|
| **resolve** | Fetch merged PRs via GraphQL, build (issue_id, pr_id) pairs | `--cache-dir DIR`, `--refresh` | resolve_cache.json, pairs.json |
| **extract** | Run extract per pair, create branches, write extract cache | `--limit N`, `--cache-dir DIR` | extract_cache.json |
| **apply-cursor** | Run agent_change for pairs missing cursor hashes, update extract cache | `--limit N`, `--cache-dir DIR` | extract_cache.json (updated) |
| **build** | Build dataset.jsonl strictly from extract_cache.json | `--limit N`, `--cache-dir DIR` | dataset.jsonl |
| **all** | Resolve + extract + build (no cursor) | `--limit N`, `--cache-dir DIR` | extract_cache.json, dataset.jsonl |

## Examples

```bash
python main.py resolve                   # fetch pairs via GraphQL
python main.py extract --limit 5         # extract first 5 pairs
python main.py apply-cursor --limit 5    # apply cursor agent to first 5 pairs
python main.py build                     # build dataset from extract cache
python main.py all --limit 5             # resolve + extract + build (first 5)
```

**Dataset output:** Each line in `dataset.jsonl` has `project`, `issue_text`, `issue_id`, `pr_text`, `pr_id`, `root_hash`, `base_hash`, `merge_hash`, `pr_diff`, `cursor_diff`, `cursor_creative_diff`. The cursor diffs are populated only if `apply-cursor` was run before `build`. Use `--limit` for testing. Failed pairs are skipped and reported.

---

# Each file's single use

Standalone usage for each script in **features/** (run from **project root** unless noted).

## features/resolve_pairs.py

Fetch merged PRs via GraphQL, build (issue_id, pr_id) pairs.

```bash
python features/resolve_pairs.py [--json] [--refs-only] [--cache DIR]
```

- No flags — pairs, print one `issue_id pr_id` per line.
- `--json` — pairs as JSON array, or (with `--refs-only`) refs as JSON.
- `--refs-only` — output refs only (issue_ids, pr_ids); no pairing.
- `--cache DIR` — directory for cache files (default: cwd/\<reponame\>_cache). Resolve writes `resolve_cache.json` there.

## features/extract.py

Creates **two** branches only: `{h}-base` and `{h}-human`. Does not run the Cursor agent. Cursor branches are created in agent_change.py.

```bash
python features/extract.py <repo_url> <issue_number> <pr_number> [--json] [--autoc]
```

- `--json` — machine-readable output: `base_hash`, `merge_hash`, `branches`.
- `--autoc` — clone repo if missing (non-interactive).

## features/agent_change.py

Creates `{h}-cursor` and `{h}-cursor-creative` from `{h}-base`, then runs the Cursor agent on each. Call after **extract.py** (which creates base and human only).

```bash
python features/agent_change.py <repo_url> <issue_number> <pr_number> <h> [--project-root P]
```

- `repo_url` — same as extract.py (e.g. `https://github.com/owner/repo`).
- `h` — 8-char branch prefix from extract output (`base_hash[:8]`).
- Requires agent CLI; set `AGENT_PATH` if not on PATH.

## features/build_dataset.py

Build JSONL from cached state only (no extract/agent calls). Expects entries with `base_hash` and `merge_hash` (e.g. from `extract_cache.json`). Use **main.py build** for the standard flow.

```bash
python features/build_dataset.py --pairs state.json [--output dataset.jsonl] [--limit N] [--project-root P]
```

- `--pairs` — **required**. JSON array of `{issue_id, pr_id, base_hash, merge_hash}` (branches must already exist).
- `--output` — default `dataset.jsonl`.
- `--limit` — process only first N pairs.
