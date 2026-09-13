"""Discover successful main commits without confusing a green PR with a release.

The public workflow feed needs no personal token. The updater separately checks
Git ancestry after fetching, so removed or unrelated commits cannot be installed.
Failure to contact GitHub never falls back to untested main.
"""
import json
import re
import urllib.request

REPOSITORY = "pjunak/dnd-table"
WORKFLOW = "ci.yml"
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def valid_sha(value):
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


def successful_commits(payload):
    runs = payload.get("workflow_runs", [])
    if not isinstance(runs, list):
        raise ValueError("GitHub returned an invalid workflow response")
    commits = []
    for run in sorted(runs, key=lambda item: item.get("run_number", 0), reverse=True):
        if (run.get("status") != "completed" or run.get("conclusion") != "success"
                or run.get("event") != "push" or run.get("head_branch") != "main"
                or run.get("path") != ".github/workflows/ci.yml"
                or (run.get("head_repository") or {}).get("full_name") != REPOSITORY
                or not valid_sha(run.get("head_sha"))):
            continue
        if run["head_sha"] not in commits:
            commits.append(run["head_sha"])
    return commits


def fetch_successful_commits():
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/{WORKFLOW}"
        "/runs?branch=main&event=push&status=success&per_page=100",
        headers={"Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "dnd-table-updater"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("GitHub returned an oversized workflow response")
    commits = successful_commits(json.loads(raw))
    if not commits:
        raise ValueError("No successfully tested main commit is available")
    return commits
