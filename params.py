# Prompt suffixes and reusable text for PR extraction

CREATIVE_PROMPT_SUFFIX = (
    "\n\n---\n"
    "Be creative in your solution. Consider innovative, elegant, "
    "and efficient approaches that go beyond the obvious fix."
)

# --- resolve_pairs.py: changelog and GitHub repo ---
CHANGELOG_URL = "https://flask.palletsprojects.com/en/stable/changes/"
GITHUB_OWNER = "pallets"
GITHUB_REPO = "flask"

# Regex patterns for changelog parsing (issue/PR/GHSA links and "Fixes #N" in PR body)
RE_ISSUE_PATTERN = r"(?:github\.com/pallets/flask/issues/|#)(\d+)"
RE_PULL_PATTERN = r"github\.com/pallets/flask/pull/(\d+)"
RE_GHSA_PATTERN = r"flask/security/advisories/(GHSA-[a-zA-Z0-9-]+)"
RE_FIXES_PATTERN = r"(?i)(?:fixes|closes|resolves)\s+#(\d+)"
