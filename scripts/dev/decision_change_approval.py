#!/usr/bin/env python3
"""Check that a PR whose Train decisions changed cites the Code Owner's OK (#798, ADR-024).

The PR body must hold one line

    Decision changes approved: https://github.com/<repo>/pull/<pr>#issuecomment-<id>

and that comment must be on this PR and written by the Code Owner. The body
and the comment are read from the GitHub REST API with ``GH_TOKEN``, so a
re-run checks the current body.

Usage:
    GH_TOKEN=... python scripts/dev/decision_change_approval.py <repo> <pr> <code-owner>

Exit codes:
    0 - the body cites a comment of the Code Owner on this PR
    1 - no citation, or the cited comment is not the Code Owner's on this PR
    2 - usage error
"""

import json
import os
import re
import sys
import urllib.request
from typing import Any, Final

_API: Final = "https://api.github.com"
_TIMEOUT_S: Final = 20


def cited_comment_id(body: str, repo: str, pr: int) -> int | None:
    """Return the comment id the body cites for this PR, or ``None``."""
    pattern = re.compile(
        rf"^Decision changes approved: https://github\.com/{re.escape(repo)}/pull/{pr}"
        r"#issuecomment-(\d+)\s*$",
        re.MULTILINE,
    )
    found = pattern.search(body)
    return int(found.group(1)) if found else None


def is_owner_comment(comment: dict[str, Any], *, repo: str, pr: int, owner: str) -> bool:
    """Return whether ``comment`` is the Code Owner's, on this PR."""
    user = comment.get("user") or {}
    on_pr = str(comment.get("issue_url", "")).endswith(f"/repos/{repo}/issues/{pr}")
    return on_pr and user.get("login") == owner


def _get(path: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{_API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
        loaded = json.load(response)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: expected an object")
    return loaded


def main(argv: list[str] | None = None) -> int:
    """Check the citation; print why it fails."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3 or not args[1].isdigit():
        print(__doc__, file=sys.stderr)
        return 2
    repo, pr, owner = args[0], int(args[1]), args[2]
    token = os.environ.get("GH_TOKEN", "")
    body = str(_get(f"/repos/{repo}/pulls/{pr}", token).get("body") or "")
    comment_id = cited_comment_id(body, repo, pr)
    if comment_id is None:
        print(
            "No 'Decision changes approved: <link to the Code Owner's comment on this PR>' "
            "line in the PR body."
        )
        return 1
    comment = _get(f"/repos/{repo}/issues/comments/{comment_id}", token)
    if not is_owner_comment(comment, repo=repo, pr=pr, owner=owner):
        print(f"Comment {comment_id} is not {owner}'s on PR #{pr}.")
        return 1
    print(f"Decision changes approved by {owner} in comment {comment_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
