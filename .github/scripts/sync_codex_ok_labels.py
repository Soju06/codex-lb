#!/usr/bin/env python3
"""Synchronize GitHub Codex review labels from current-head Codex reviews."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

CODEX_OK_LABEL = "🤖 codex: ok"
CODEX_NEEDS_WORK_LABEL = "🤖 codex: needs work"
LEGACY_CODEX_LABELS = {"🤖 codex-ok"}
CODEX_REVIEW_AUTHORS = {
    "chatgpt-codex-connector",
    "chatgpt-codex-connector[bot]",
    "openai-codex",
    "openai-codex[bot]",
}
CODEX_CLEAN_RE = re.compile(
    r"(didn['’]t find any major issues|no major issues found|no major issues)",
    re.IGNORECASE,
)
CODEX_FINDING_RE = re.compile(r"(?:\bP[0-3]\s+Badge\b|badge/P[0-3]-|(?m:(?:^|\n)\s*(?:\*\*)?(?:\[P[0-3]\]|P[0-3]\b)))")
SUCCESS_CHECK_STATES = {"SUCCESS", "NEUTRAL", "SKIPPED"}
FAIL_CHECK_STATES = {"ACTION_REQUIRED", "CANCELLED", "ERROR", "FAILURE", "STALE", "TIMED_OUT"}
PENDING_CHECK_STATES = {"EXPECTED", "IN_PROGRESS", "PENDING", "QUEUED", "REQUESTED", "WAITING"}
UNMERGEABLE_STATES = {"DIRTY", "BLOCKED"}
CODEX_LB_REQUIRED_CHECKS = frozenset(
    {
        "Frontend lint (eslint)",
        "Frontend type check (tsc)",
        "Frontend tests (vitest + coverage)",
        "Frontend build (vite)",
        "Lint (ruff)",
        "Type check (ty)",
        "Tests (pytest, unit)",
        "Tests (pytest, integration-core)",
        "Tests (pytest, integration-bridge)",
        "Tests (pytest, e2e)",
        "Tests (pytest, PostgreSQL)",
        "Migration check (alembic)",
        "Migration check (alembic, PostgreSQL)",
        "Package (build)",
        "Docker build",
        "Helm lint + template + kubeconform",
        "Helm smoke install (kind)",
        "CI Required",
    }
)
REQUIRED_CHECKS_BY_REPO = {
    "Soju06/codex-lb": CODEX_LB_REQUIRED_CHECKS,
}
PR_TIMELINE_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $before: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      headRefOid
      commits(last: 1) {
        nodes {
          commit {
            oid
          }
        }
      }
      timelineItems(
        last: 100
        before: $before
        itemTypes: [
          PULL_REQUEST_COMMIT
          ISSUE_COMMENT
          PULL_REQUEST_REVIEW
          HEAD_REF_FORCE_PUSHED_EVENT
        ]
      ) {
        pageInfo {
          hasPreviousPage
          startCursor
        }
        nodes {
          __typename
          ... on PullRequestCommit {
            commit {
              oid
            }
          }
          ... on HeadRefForcePushedEvent {
            afterCommit {
              oid
            }
          }
          ... on IssueComment {
            author {
              login
            }
            bodyText
            createdAt
            url
            reactions(first: 100) {
              nodes {
                content
                createdAt
                user {
                  login
                }
              }
            }
          }
          ... on PullRequestReview {
            databaseId
            author {
              login
            }
            bodyText
            submittedAt
            url
            commit {
              oid
            }
          }
        }
      }
    }
  }
}
"""

PR_REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          isResolved
          isOutdated
          comments(first: 20) {
            nodes {
              author {
                login
              }
              body
              url
              commit {
                oid
              }
              originalCommit {
                oid
              }
            }
          }
        }
      }
    }
  }
}
"""


class GhError(RuntimeError):
    """A GitHub CLI call failed."""


_RATE_LIMIT_MARKER = "API rate limit exceeded"
_fallback_token_active = False


def _activate_fallback_token() -> bool:
    """Switch gh calls to GH_FALLBACK_TOKEN once after a rate-limit failure.

    The primary token is typically a user PAT whose quota is shared with
    other consumers; github.token carries a separate per-repository quota,
    so a single runtime switch keeps the sync alive through PAT exhaustion.
    """
    global _fallback_token_active
    if _fallback_token_active:
        return False
    fallback = os.environ.get("GH_FALLBACK_TOKEN", "").strip()
    if not fallback or fallback == os.environ.get("GH_TOKEN", ""):
        return False
    os.environ["GH_TOKEN"] = fallback
    _fallback_token_active = True
    return True


@dataclass(frozen=True)
class SyncDecision:
    repo: str
    number: int
    head_sha: str
    has_ok_label: bool
    wants_ok_label: bool
    ok_action: str
    has_needs_work_label: bool
    wants_needs_work_label: bool
    needs_work_action: str
    legacy_labels: frozenset[str]
    reason: str
    review_url: str | None
    review_state: str
    checks_state: str
    merge_state: str
    trigger_codex_review: bool
    approve_workflow_run_ids: tuple[int, ...]


def run_gh(args: list[str], *, input_json: Any | None = None, timeout_seconds: int = 30) -> Any:
    command = ["gh", *args]
    input_text = json.dumps(input_json) if input_json is not None else None
    try:
        proc = subprocess.run(
            command,
            check=False,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise GhError(f"{' '.join(command)}: timed out after {timeout_seconds}s") from exc

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        if _RATE_LIMIT_MARKER in detail and _activate_fallback_token():
            print(
                "warning: active token rate-limited; retrying with GH_FALLBACK_TOKEN",
                file=sys.stderr,
            )
            return run_gh(args, input_json=input_json, timeout_seconds=timeout_seconds)
        raise GhError(f"{' '.join(command)}: {detail}")

    text = proc.stdout.strip()
    if not text:
        return None
    return json.loads(text)


def gh_api(path: str, *, method: str = "GET", input_json: Any | None = None) -> Any:
    if not path.startswith("/"):
        path = f"/{path}"

    args = ["api", "--method", method, path]
    if input_json is not None:
        args.append("--input")
        args.append("-")
    return run_gh(args, input_json=input_json)


def graphql(query: str, **fields: object) -> dict[str, Any]:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in fields.items():
        args.extend(["-F", f"{key}={value}"])
    payload = run_gh(args)
    if not isinstance(payload, dict):
        raise GhError("gh api graphql returned a non-object payload")
    return payload


def paged_api(path: str) -> list[dict[str, Any]]:
    page = 1
    items: list[dict[str, Any]] = []
    sep = "&" if "?" in path else "?"
    while True:
        payload = gh_api(f"{path}{sep}per_page=100&page={page}")
        if not payload:
            return items
        if isinstance(payload, dict):
            page_items = None
            for key in ("items", "check_runs", "workflow_runs"):
                if key in payload:
                    page_items = payload[key]
                    break
        else:
            page_items = payload
        if not isinstance(page_items, list):
            raise GhError(f"{path}: expected list payload, got {type(payload).__name__}")
        items.extend(item for item in page_items if isinstance(item, dict))
        if len(page_items) < 100:
            return items
        page += 1


def repo_path(repo: str) -> str:
    parts = repo.strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"repo must be owner/name, got {repo!r}")
    return f"{parts[0]}/{parts[1]}"


def list_open_pr_numbers(repo: str) -> list[int]:
    pulls = paged_api(f"/repos/{repo}/pulls?state=open")
    return [int(pr["number"]) for pr in pulls if isinstance(pr.get("number"), int)]


def issue_label_names(repo: str, number: int) -> set[str]:
    labels = paged_api(f"/repos/{repo}/issues/{number}/labels")
    return {str(label.get("name")) for label in labels if isinstance(label.get("name"), str)}


def author_login(item: dict[str, Any]) -> str:
    user = item.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    return str(login or "")


def is_clean_codex_body(body: object) -> bool:
    return isinstance(body, str) and CODEX_CLEAN_RE.search(body) is not None


def is_needs_work_codex_body(body: object) -> bool:
    return isinstance(body, str) and CODEX_FINDING_RE.search(body) is not None


def body_mentions_head(body: object, head_sha: str) -> bool:
    if not isinstance(body, str):
        return False
    return head_sha in body or head_sha[:12] in body or head_sha[:8] in body


def review_node_commit_oid(node: dict[str, Any]) -> str | None:
    commit = node.get("commit")
    oid = commit.get("oid") if isinstance(commit, dict) else None
    return oid if isinstance(oid, str) else None


def review_node_database_id(node: dict[str, Any]) -> int | None:
    value = node.get("databaseId")
    return value if isinstance(value, int) else None


def node_body(node: dict[str, Any]) -> str:
    body = node.get("bodyText")
    if not isinstance(body, str):
        body = node.get("body")
    return body if isinstance(body, str) else ""


def node_url(node: dict[str, Any]) -> str | None:
    url = node.get("url") or node.get("html_url")
    return str(url) if isinstance(url, str) else None


def node_author_login(node: dict[str, Any]) -> str:
    author = node.get("author")
    login = author.get("login") if isinstance(author, dict) else None
    if isinstance(login, str):
        return login
    return author_login(node)


def is_timeline_codex_author(node: dict[str, Any], allowed: set[str]) -> bool:
    return node_author_login(node) in allowed


def is_codex_review_request_comment(node: dict[str, Any]) -> bool:
    if node.get("__typename") != "IssueComment":
        return False
    return node_body(node).strip().casefold() == "@codex review"


def reaction_user_login(node: dict[str, Any]) -> str:
    user = node.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    return str(login or "")


def codex_request_reaction_state(node: dict[str, Any], *, allowed_authors: set[str]) -> str:
    if not is_codex_review_request_comment(node):
        return "none"

    reactions = node.get("reactions")
    reaction_nodes = reactions.get("nodes") if isinstance(reactions, dict) else []
    if not isinstance(reaction_nodes, list):
        return "none"

    state = "none"
    for reaction in reaction_nodes:
        if not isinstance(reaction, dict):
            continue
        if reaction_user_login(reaction) not in allowed_authors:
            continue
        content = str(reaction.get("content") or "").upper()
        if content in {"THUMBS_UP", "+1"}:
            state = "clean"
        elif content == "EYES" and state == "none":
            state = "pending"
    return state


def timeline_head_oid(node: dict[str, Any]) -> str | None:
    if node.get("__typename") == "PullRequestCommit":
        commit = node.get("commit")
        oid = commit.get("oid") if isinstance(commit, dict) else None
        return oid if isinstance(oid, str) else None
    if node.get("__typename") == "HeadRefForcePushedEvent":
        commit = node.get("afterCommit")
        oid = commit.get("oid") if isinstance(commit, dict) else None
        return oid if isinstance(oid, str) else None
    return None


def timeline_node_timestamp(node: dict[str, Any]) -> str | None:
    for key in ("createdAt", "submittedAt", "committedDate"):
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def unresolved_review_comment_urls(repo: str, number: int) -> set[str]:
    owner, name = repo.split("/", 1)
    after: str | None = None
    urls: set[str] = set()

    while True:
        fields: dict[str, object] = {"owner": owner, "name": name, "number": number}
        if after is not None:
            fields["after"] = after
        payload = graphql(PR_REVIEW_THREADS_QUERY, **fields)
        pr = payload.get("data", {}).get("repository", {}).get("pullRequest")
        if not isinstance(pr, dict):
            raise GhError(f"{repo}#{number}: GraphQL did not return a pull request")

        threads = pr.get("reviewThreads")
        if not isinstance(threads, dict):
            raise GhError(f"{repo}#{number}: GraphQL did not return review threads")
        nodes = threads.get("nodes", [])
        if not isinstance(nodes, list):
            raise GhError(f"{repo}#{number}: GraphQL did not return review thread nodes")

        for thread in nodes:
            if not isinstance(thread, dict):
                continue
            if thread.get("isResolved") or thread.get("isOutdated"):
                continue
            comments = thread.get("comments")
            comment_nodes = comments.get("nodes") if isinstance(comments, dict) else []
            if not isinstance(comment_nodes, list):
                continue
            for comment in comment_nodes:
                if not isinstance(comment, dict):
                    continue
                url = comment.get("url")
                if isinstance(url, str):
                    urls.add(url)

        page_info = threads.get("pageInfo")
        if not isinstance(page_info, dict) or not page_info.get("hasNextPage"):
            break
        end_cursor = page_info.get("endCursor")
        if not isinstance(end_cursor, str) or not end_cursor:
            break
        after = end_cursor

    return urls


def pull_review_comment_nodes(repo: str, number: int, *, head_sha: str) -> list[dict[str, Any]]:
    comments = paged_api(f"/repos/{repo}/pulls/{number}/comments")
    unresolved_urls = unresolved_review_comment_urls(repo, number)
    nodes: list[dict[str, Any]] = []
    for comment in comments:
        body = comment.get("body")
        if not isinstance(body, str):
            continue
        commit_id = comment.get("commit_id")
        original_commit_id = comment.get("original_commit_id")
        review_id = comment.get("pull_request_review_id")
        commit_matches_head = commit_id == head_sha
        original_matches_head = original_commit_id == head_sha
        body_mentions_current_head = body_mentions_head(body, head_sha)
        if not commit_matches_head and not original_matches_head and not body_mentions_current_head:
            continue
        effective_commit_id = original_commit_id if original_matches_head else commit_id
        effective_review_id = review_id if original_matches_head else None
        html_url = comment.get("html_url") or comment.get("url")
        if is_needs_work_codex_body(body) and html_url not in unresolved_urls:
            continue
        user = comment.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        nodes.append(
            {
                "__typename": "PullRequestReviewComment",
                "author": {"login": login} if isinstance(login, str) else None,
                "bodyText": body,
                "createdAt": comment.get("created_at"),
                "url": html_url,
                "commit": {"oid": effective_commit_id} if isinstance(effective_commit_id, str) else None,
                "pullRequestReviewDatabaseId": effective_review_id if isinstance(effective_review_id, int) else None,
            }
        )
    return nodes


def merge_review_comment_nodes(
    timeline_nodes: list[dict[str, Any]],
    comment_nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not comment_nodes:
        return timeline_nodes

    comments_by_review_id: dict[int, list[dict[str, Any]]] = {}
    unplaced: list[dict[str, Any]] = []
    for node in comment_nodes:
        review_id = node.get("pullRequestReviewDatabaseId")
        if isinstance(review_id, int):
            comments_by_review_id.setdefault(review_id, []).append(node)
        else:
            unplaced.append(node)

    merged: list[dict[str, Any]] = []
    placed_ids: set[int] = set()
    for node in timeline_nodes:
        merged.append(node)
        if node.get("__typename") != "PullRequestReview":
            continue
        review_id = review_node_database_id(node)
        if review_id is None:
            continue
        for comment in comments_by_review_id.get(review_id, []):
            merged.append(comment)
            placed_ids.add(id(comment))

    for node in comment_nodes:
        if id(node) not in placed_ids and node not in unplaced:
            unplaced.append(node)
    for node in unplaced:
        timestamp = timeline_node_timestamp(node)
        if timestamp is None:
            merged.append(node)
            continue
        insert_at = len(merged)
        for index, candidate in enumerate(merged):
            candidate_timestamp = timeline_node_timestamp(candidate)
            if candidate_timestamp is not None and candidate_timestamp > timestamp:
                insert_at = index
                break
        merged.insert(insert_at, node)
    return merged


def find_current_head_codex_review_state(
    timeline_nodes: list[dict[str, Any]],
    *,
    head_sha: str,
    allowed_authors: set[str],
) -> tuple[str, dict[str, Any] | None]:
    head_index = None
    for index, node in enumerate(timeline_nodes):
        if timeline_head_oid(node) == head_sha:
            head_index = index

    if head_index is None:
        return "none", None

    latest_state = "none"
    latest_node: dict[str, Any] | None = None
    for node in timeline_nodes[head_index + 1 :]:
        reaction_state = codex_request_reaction_state(node, allowed_authors=allowed_authors)
        if reaction_state == "clean":
            latest_state = "clean"
            latest_node = node
            continue
        if reaction_state == "pending" and latest_state == "none":
            latest_state = "pending"
            latest_node = node
            continue

        if not is_timeline_codex_author(node, allowed_authors):
            continue

        if node.get("__typename") == "PullRequestReview":
            body = node_body(node)
            commit_oid = review_node_commit_oid(node)
            if commit_oid != head_sha and not body_mentions_head(body, head_sha):
                continue
            if is_needs_work_codex_body(body):
                latest_state = "needs_work"
                latest_node = node
                continue
            latest_state = "clean"
            latest_node = node
            continue

        if node.get("__typename") == "PullRequestReviewComment":
            body = node_body(node)
            commit_oid = review_node_commit_oid(node)
            if commit_oid != head_sha and not body_mentions_head(body, head_sha):
                continue
            if is_needs_work_codex_body(body):
                latest_state = "needs_work"
                latest_node = node
            continue

        if node.get("__typename") == "IssueComment" and is_clean_codex_body(node_body(node)):
            latest_state = "clean"
            latest_node = node

    return latest_state, latest_node


def find_current_head_clean_review(
    timeline_nodes: list[dict[str, Any]],
    *,
    head_sha: str,
    allowed_authors: set[str],
) -> dict[str, Any] | None:
    state, node = find_current_head_codex_review_state(
        timeline_nodes,
        head_sha=head_sha,
        allowed_authors=allowed_authors,
    )
    return node if state == "clean" else None


def has_codex_news_after_current_head(
    timeline_nodes: list[dict[str, Any]],
    *,
    head_sha: str,
    allowed_authors: set[str],
) -> bool:
    head_index = None
    for index, node in enumerate(timeline_nodes):
        if timeline_head_oid(node) == head_sha:
            head_index = index

    if head_index is None:
        return False

    for node in timeline_nodes[head_index + 1 :]:
        if is_codex_review_request_comment(node):
            return True
        if not is_timeline_codex_author(node, allowed_authors):
            continue
        if node.get("__typename") == "PullRequestReview":
            body = node_body(node)
            commit_oid = review_node_commit_oid(node)
            if commit_oid != head_sha and not body_mentions_head(body, head_sha):
                continue
            return True
        if node.get("__typename") == "PullRequestReviewComment":
            body = node_body(node)
            commit_oid = review_node_commit_oid(node)
            if commit_oid != head_sha and not body_mentions_head(body, head_sha):
                continue
            return True
        if node.get("__typename") == "IssueComment":
            return True

    return False


def pr_timeline_evidence(repo: str, number: int) -> tuple[str, list[dict[str, Any]]]:
    owner, name = repo.split("/", 1)
    before: str | None = None
    head_sha: str | None = None
    timeline_nodes: list[dict[str, Any]] = []

    while True:
        fields: dict[str, object] = {"owner": owner, "name": name, "number": number}
        if before is not None:
            fields["before"] = before
        payload = graphql(PR_TIMELINE_QUERY, **fields)
        pr = payload.get("data", {}).get("repository", {}).get("pullRequest")
        if not isinstance(pr, dict):
            raise GhError(f"{repo}#{number}: GraphQL did not return a pull request")

        page_head_sha = pr.get("headRefOid")
        commit_nodes = pr.get("commits", {}).get("nodes", [])
        last_commit = commit_nodes[-1].get("commit", {}) if commit_nodes else {}
        commit_sha = last_commit.get("oid")
        if not isinstance(page_head_sha, str) or not page_head_sha:
            raise GhError(f"{repo}#{number}: GraphQL did not return headRefOid")
        if commit_sha != page_head_sha:
            raise GhError(f"{repo}#{number}: headRefOid {page_head_sha} disagrees with commits.last {commit_sha}")
        if head_sha is None:
            head_sha = page_head_sha
        elif head_sha != page_head_sha:
            raise GhError(f"{repo}#{number}: headRefOid changed while paging timeline")

        timeline = pr.get("timelineItems")
        if not isinstance(timeline, dict):
            raise GhError(f"{repo}#{number}: GraphQL did not return timeline items")
        nodes = timeline.get("nodes", [])
        if not isinstance(nodes, list):
            raise GhError(f"{repo}#{number}: GraphQL did not return timeline nodes")
        page_nodes = [node for node in nodes if isinstance(node, dict)]
        timeline_nodes = page_nodes + timeline_nodes
        if any(timeline_head_oid(node) == head_sha for node in page_nodes):
            break

        page_info = timeline.get("pageInfo")
        if not isinstance(page_info, dict) or not page_info.get("hasPreviousPage"):
            break
        start_cursor = page_info.get("startCursor")
        if not isinstance(start_cursor, str) or not start_cursor:
            break
        before = start_cursor

    if head_sha is None:
        raise GhError(f"{repo}#{number}: GraphQL did not return headRefOid")
    return head_sha, merge_review_comment_nodes(
        timeline_nodes,
        pull_review_comment_nodes(repo, number, head_sha=head_sha),
    )


def classify_check_state(
    check_runs: list[dict[str, Any]],
    combined_status: dict[str, Any],
    *,
    required_check_names: frozenset[str] = frozenset(),
) -> str:
    states: list[str] = []
    seen_check_names: set[str] = set()
    named_check_runs: dict[str, dict[str, Any]] = {}
    unnamed_check_runs: list[dict[str, Any]] = []

    authoritative_ci_workflow = authoritative_ci_workflow_id(check_runs)
    authoritative_ci_run = authoritative_ci_workflow_run_id(
        check_runs,
        workflow_id=authoritative_ci_workflow,
    )
    if authoritative_ci_run is not None and authoritative_ci_workflow is not None:
        check_runs = [
            item
            for item in check_runs
            if github_actions_workflow_id(item) != authoritative_ci_workflow
            or github_actions_workflow_run_id(item) in {None, authoritative_ci_run}
        ]

    for item in check_runs:
        name = item.get("name")
        if isinstance(name, str):
            previous = named_check_runs.get(name)
            if previous is None or check_run_recency_key(item) >= check_run_recency_key(previous):
                named_check_runs[name] = item
        else:
            unnamed_check_runs.append(item)

    for item in [*named_check_runs.values(), *unnamed_check_runs]:
        name = item.get("name")
        if isinstance(name, str):
            seen_check_names.add(name)
        conclusion = str(item.get("conclusion") or "").upper()
        status = str(item.get("status") or "").upper()
        states.append(conclusion or status or "UNKNOWN")

    for item in combined_status.get("statuses", []) if isinstance(combined_status, dict) else []:
        if isinstance(item, dict):
            states.append(str(item.get("state") or "").upper())

    if not states:
        return "none"
    if any(state in FAIL_CHECK_STATES for state in states):
        return "failure"
    if any(state in PENDING_CHECK_STATES for state in states):
        return "pending"
    if required_check_names and not required_check_names <= seen_check_names:
        return "pending"
    if all(state in SUCCESS_CHECK_STATES for state in states):
        return "success"
    return "unknown"


def check_run_recency_key(item: dict[str, Any]) -> tuple[str, str]:
    return (
        str(
            item.get("started_at")
            or item.get("startedAt")
            or item.get("created_at")
            or item.get("createdAt")
            or item.get("completed_at")
            or item.get("completedAt")
            or ""
        ),
        str(item.get("completed_at") or item.get("completedAt") or ""),
    )


def github_actions_workflow_run_id(item: dict[str, Any]) -> str | None:
    details_url = item.get("details_url") or item.get("detailsUrl")
    if not isinstance(details_url, str):
        return None
    match = re.search(r"/actions/runs/(\d+)(?:/|$)", details_url)
    return match.group(1) if match is not None else None


def authoritative_ci_workflow_run_id(
    check_runs: list[dict[str, Any]],
    *,
    workflow_id: str | None,
) -> str | None:
    if workflow_id is None:
        return None
    workflow_runs = [
        item
        for item in check_runs
        if github_actions_workflow_id(item) == workflow_id and github_actions_workflow_run_id(item) is not None
    ]
    if not workflow_runs:
        return None
    latest_run = max(workflow_runs, key=github_actions_workflow_run_recency_key)
    return github_actions_workflow_run_id(latest_run)


def github_actions_workflow_run_recency_key(item: dict[str, Any]) -> tuple[str, tuple[str, str], int]:
    run_id = github_actions_workflow_run_id(item)
    return (
        str(item.get("_github_actions_run_started_at") or item.get("_github_actions_run_created_at") or ""),
        check_run_recency_key(item),
        int(run_id) if isinstance(run_id, str) and run_id.isdigit() else 0,
    )


def github_actions_workflow_id(item: dict[str, Any]) -> str | None:
    workflow_id = item.get("_github_actions_workflow_id")
    return str(workflow_id) if isinstance(workflow_id, (int, str)) else None


def authoritative_ci_workflow_id(check_runs: list[dict[str, Any]]) -> str | None:
    required_runs = [item for item in check_runs if item.get("name") == "CI Required"]
    if not required_runs:
        return None
    latest_required = max(required_runs, key=check_run_recency_key)
    return github_actions_workflow_id(latest_required)


def annotate_github_actions_workflow_ids(repo: str, check_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    workflow_metadata_by_run: dict[str, tuple[str, str | None]] = {}
    for run_id in {github_actions_workflow_run_id(item) for item in check_runs} - {None}:
        assert run_id is not None
        try:
            workflow_run = gh_api(f"/repos/{repo}/actions/runs/{run_id}")
        except GhError:
            continue
        workflow_id = workflow_run.get("workflow_id") if isinstance(workflow_run, dict) else None
        if isinstance(workflow_id, (int, str)):
            # GitHub preserves ``created_at`` when an existing run id is rerun,
            # while ``run_started_at`` advances to the current attempt.
            run_started_at = workflow_run.get("run_started_at") or workflow_run.get("created_at")
            workflow_metadata_by_run[run_id] = (
                str(workflow_id),
                str(run_started_at) if isinstance(run_started_at, str) else None,
            )

    annotated: list[dict[str, Any]] = []
    for item in check_runs:
        run_id = github_actions_workflow_run_id(item)
        metadata = workflow_metadata_by_run.get(run_id) if run_id is not None else None
        if metadata is None:
            annotated.append(item)
            continue
        workflow_id, run_started_at = metadata
        annotated_item = {**item, "_github_actions_workflow_id": workflow_id}
        if run_started_at is not None:
            annotated_item["_github_actions_run_started_at"] = run_started_at
        annotated.append(annotated_item)
    return annotated


def commit_checks_state(repo: str, head_sha: str) -> str:
    check_runs = paged_api(f"/repos/{repo}/commits/{head_sha}/check-runs")
    check_runs = annotate_github_actions_workflow_ids(repo, check_runs)
    combined_status = gh_api(f"/repos/{repo}/commits/{head_sha}/status")
    return classify_check_state(
        check_runs,
        combined_status if isinstance(combined_status, dict) else {},
        required_check_names=REQUIRED_CHECKS_BY_REPO.get(repo, frozenset()),
    )


def pr_merge_state(repo: str, number: int) -> str:
    payload = run_gh(
        ["pr", "view", str(number), "--repo", repo, "--json", "mergeStateStatus,mergeable"],
        timeout_seconds=30,
    )
    if not isinstance(payload, dict):
        raise GhError(f"{repo}#{number}: expected pull request object")
    mergeable = str(payload.get("mergeable") or "").upper()
    merge_state = str(payload.get("mergeStateStatus") or "").upper()
    if mergeable == "CONFLICTING":
        return "CONFLICTING"
    return merge_state or "UNKNOWN"


def workflow_runs_requiring_approval(repo: str, head_sha: str) -> tuple[int, ...]:
    runs = paged_api(f"/repos/{repo}/actions/runs?event=pull_request&head_sha={head_sha}")
    run_ids: list[int] = []
    for run in runs:
        status = str(run.get("status") or "").lower()
        conclusion = str(run.get("conclusion") or "").lower()
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        if status == "action_required" or conclusion == "action_required":
            run_ids.append(run_id)
    return tuple(run_ids)


def unresolved_codex_finding_thread_urls(
    repo: str,
    number: int,
    *,
    head_sha: str,
    allowed_authors: set[str],
) -> tuple[str, ...]:
    owner, name = repo.split("/", 1)
    after: str | None = None
    urls: list[str] = []

    while True:
        fields: dict[str, object] = {"owner": owner, "name": name, "number": number}
        if after is not None:
            fields["after"] = after
        payload = graphql(PR_REVIEW_THREADS_QUERY, **fields)
        pr = payload.get("data", {}).get("repository", {}).get("pullRequest")
        if not isinstance(pr, dict):
            raise GhError(f"{repo}#{number}: GraphQL did not return a pull request")

        threads = pr.get("reviewThreads")
        if not isinstance(threads, dict):
            raise GhError(f"{repo}#{number}: GraphQL did not return review threads")
        nodes = threads.get("nodes", [])
        if not isinstance(nodes, list):
            raise GhError(f"{repo}#{number}: GraphQL did not return review thread nodes")

        for thread in nodes:
            if not isinstance(thread, dict):
                continue
            if thread.get("isResolved") or thread.get("isOutdated"):
                continue
            comments = thread.get("comments")
            comment_nodes = comments.get("nodes") if isinstance(comments, dict) else []
            if not isinstance(comment_nodes, list):
                continue
            for comment in comment_nodes:
                if not isinstance(comment, dict):
                    continue
                author = comment.get("author")
                login = author.get("login") if isinstance(author, dict) else None
                if login not in allowed_authors:
                    continue
                if not is_needs_work_codex_body(comment.get("body")):
                    continue
                body = comment.get("body")
                commit = comment.get("commit")
                commit_oid = commit.get("oid") if isinstance(commit, dict) else None
                original_commit = comment.get("originalCommit")
                original_oid = original_commit.get("oid") if isinstance(original_commit, dict) else None
                body_mentions_current_head = body_mentions_head(body, head_sha)
                if body_mentions_current_head:
                    pass
                elif commit_oid == head_sha:
                    pass
                elif original_oid == head_sha:
                    pass
                else:
                    continue
                url = comment.get("url")
                urls.append(str(url) if isinstance(url, str) else "unresolved Codex review thread")

        page_info = threads.get("pageInfo")
        if not isinstance(page_info, dict) or not page_info.get("hasNextPage"):
            break
        end_cursor = page_info.get("endCursor")
        if not isinstance(end_cursor, str) or not end_cursor:
            break
        after = end_cursor

    return tuple(urls)


def is_github_app_write_denial(exc: BaseException) -> bool:
    """Return True when GitHub rejected a write from the current token."""

    text = str(exc)
    return "Resource not accessible by integration" in text and "HTTP 403" in text


def write_warning(action: str, exc: BaseException) -> str:
    return f"{action}: skipped because the GitHub token cannot write this resource ({exc})"


def is_missing_issue_label(exc: BaseException) -> bool:
    """Return True when GitHub reports that an issue label is already absent."""

    text = str(exc)
    return "HTTP 404" in text and "Label does not exist" in text


def gh_api_write(
    path: str,
    *,
    method: str = "GET",
    input_json: Any | None = None,
    tolerate_permission_errors: bool,
    tolerate_missing: bool = False,
    action: str,
) -> str | None:
    try:
        gh_api(path, method=method, input_json=input_json)
    except GhError as exc:
        if tolerate_missing and is_missing_issue_label(exc):
            return None
        if tolerate_permission_errors and is_github_app_write_denial(exc):
            return write_warning(action, exc)
        raise
    return None


def run_gh_write(
    args: list[str],
    *,
    timeout_seconds: int,
    tolerate_permission_errors: bool,
    action: str,
) -> str | None:
    try:
        run_gh(args, timeout_seconds=timeout_seconds)
    except GhError as exc:
        if tolerate_permission_errors and is_github_app_write_denial(exc):
            return write_warning(action, exc)
        raise
    return None


def ensure_label(
    repo: str,
    label: str,
    *,
    color: str,
    description: str,
    apply: bool,
    tolerate_permission_errors: bool = False,
) -> tuple[str, ...]:
    if not apply:
        return ()
    try:
        gh_api(f"/repos/{repo}/labels/{quote(label, safe='')}")
        return ()
    except GhError as exc:
        if "HTTP 404" not in str(exc):
            raise

    try:
        warning = gh_api_write(
            f"/repos/{repo}/labels",
            method="POST",
            input_json={
                "name": label,
                "color": color,
                "description": description,
            },
            tolerate_permission_errors=tolerate_permission_errors,
            action=f"create label {repo}:{label}",
        )
        return (warning,) if warning else ()
    except GhError as exc:
        if "already_exists" not in str(exc) and "already exists" not in str(exc).lower():
            raise
    return ()


def decide_pr(
    repo: str,
    number: int,
    *,
    allowed_authors: set[str],
    ignore_checks: bool,
) -> SyncDecision:
    head_sha, timeline_nodes = pr_timeline_evidence(repo, number)
    labels = issue_label_names(repo, number)
    checks_state = commit_checks_state(repo, head_sha)
    merge_state = pr_merge_state(repo, number)
    review_state, review_node = find_current_head_codex_review_state(
        timeline_nodes,
        head_sha=head_sha,
        allowed_authors=allowed_authors,
    )
    unresolved_finding_urls = unresolved_codex_finding_thread_urls(
        repo,
        number,
        head_sha=head_sha,
        allowed_authors=allowed_authors,
    )
    has_codex_news = has_codex_news_after_current_head(
        timeline_nodes,
        head_sha=head_sha,
        allowed_authors=allowed_authors,
    )
    has_ok_label = CODEX_OK_LABEL in labels
    has_needs_work_label = CODEX_NEEDS_WORK_LABEL in labels
    legacy_labels = frozenset(label for label in labels if label in LEGACY_CODEX_LABELS)

    reason_parts: list[str] = []
    wants_ok_label = review_state == "clean"
    wants_needs_work_label = review_state == "needs_work"
    if review_state == "none":
        reason_parts.append("no provable clean Codex review for current head")
    elif review_state == "pending":
        reason_parts.append("Codex review request is acknowledged but still pending")
    elif review_state == "needs_work":
        reason_parts.append("Codex raised current-head review issues")
    else:
        reason_parts.append("clean Codex review matches current head")

    if unresolved_finding_urls:
        wants_ok_label = False
        wants_needs_work_label = True
        reason_parts.append(f"unresolved Codex review threads: {len(unresolved_finding_urls)}")

    if not ignore_checks and checks_state != "success":
        wants_ok_label = False
        reason_parts.append(f"checks are {checks_state}")
    if not ignore_checks and merge_state in UNMERGEABLE_STATES | {"CONFLICTING"}:
        wants_ok_label = False
        reason_parts.append(f"merge state is {merge_state.lower()}")
    if not ignore_checks and merge_state == "UNKNOWN" and not has_ok_label:
        wants_ok_label = False
        reason_parts.append("merge state is still unknown")

    trigger_codex_review = (
        review_state == "none"
        and checks_state == "success"
        and merge_state not in UNMERGEABLE_STATES | {"CONFLICTING"}
        and merge_state != "UNKNOWN"
        and not has_codex_news
    )
    if trigger_codex_review:
        reason_parts.append("current-head CI is green and no Codex news exists after head")

    approve_workflow_run_ids: tuple[int, ...] = ()
    if (
        review_state == "clean"
        and not unresolved_finding_urls
        and merge_state not in UNMERGEABLE_STATES | {"CONFLICTING"}
        and merge_state != "UNKNOWN"
    ):
        approve_workflow_run_ids = workflow_runs_requiring_approval(repo, head_sha)
        if approve_workflow_run_ids:
            reason_parts.append(
                "workflow runs need approval: " + ",".join(str(run_id) for run_id in approve_workflow_run_ids)
            )

    if wants_ok_label and not has_ok_label:
        ok_action = "add"
    elif not wants_ok_label and has_ok_label:
        ok_action = "remove"
    else:
        ok_action = "keep"
    if wants_needs_work_label and not has_needs_work_label:
        needs_work_action = "add"
    elif not wants_needs_work_label and has_needs_work_label:
        needs_work_action = "remove"
    else:
        needs_work_action = "keep"

    review_url = unresolved_finding_urls[0] if unresolved_finding_urls else None
    if review_url is None and isinstance(review_node, dict):
        review_url = node_url(review_node)

    return SyncDecision(
        repo=repo,
        number=number,
        head_sha=head_sha,
        has_ok_label=has_ok_label,
        wants_ok_label=wants_ok_label,
        ok_action=ok_action,
        has_needs_work_label=has_needs_work_label,
        wants_needs_work_label=wants_needs_work_label,
        needs_work_action=needs_work_action,
        legacy_labels=legacy_labels,
        reason="; ".join(reason_parts),
        review_url=review_url,
        review_state=review_state,
        checks_state=checks_state,
        merge_state=merge_state,
        trigger_codex_review=trigger_codex_review,
        approve_workflow_run_ids=approve_workflow_run_ids,
    )


def apply_decision(decision: SyncDecision, *, tolerate_permission_errors: bool = False) -> tuple[str, ...]:
    warnings: list[str] = []

    def record(warning: str | None) -> None:
        if warning:
            warnings.append(warning)

    if decision.ok_action == "add":
        record(
            gh_api_write(
                f"/repos/{decision.repo}/issues/{decision.number}/labels",
                method="POST",
                input_json={"labels": [CODEX_OK_LABEL]},
                tolerate_permission_errors=tolerate_permission_errors,
                action=f"add {CODEX_OK_LABEL} to {decision.repo}#{decision.number}",
            )
        )
    elif decision.ok_action == "remove":
        record(
            gh_api_write(
                f"/repos/{decision.repo}/issues/{decision.number}/labels/{quote(CODEX_OK_LABEL, safe='')}",
                method="DELETE",
                tolerate_permission_errors=tolerate_permission_errors,
                tolerate_missing=True,
                action=f"remove {CODEX_OK_LABEL} from {decision.repo}#{decision.number}",
            )
        )
    if decision.needs_work_action == "add":
        record(
            gh_api_write(
                f"/repos/{decision.repo}/issues/{decision.number}/labels",
                method="POST",
                input_json={"labels": [CODEX_NEEDS_WORK_LABEL]},
                tolerate_permission_errors=tolerate_permission_errors,
                action=f"add {CODEX_NEEDS_WORK_LABEL} to {decision.repo}#{decision.number}",
            )
        )
    elif decision.needs_work_action == "remove":
        record(
            gh_api_write(
                f"/repos/{decision.repo}/issues/{decision.number}/labels/{quote(CODEX_NEEDS_WORK_LABEL, safe='')}",
                method="DELETE",
                tolerate_permission_errors=tolerate_permission_errors,
                tolerate_missing=True,
                action=f"remove {CODEX_NEEDS_WORK_LABEL} from {decision.repo}#{decision.number}",
            )
        )
    for label in decision.legacy_labels:
        record(
            gh_api_write(
                f"/repos/{decision.repo}/issues/{decision.number}/labels/{quote(label, safe='')}",
                method="DELETE",
                tolerate_permission_errors=tolerate_permission_errors,
                tolerate_missing=True,
                action=f"remove legacy {label} from {decision.repo}#{decision.number}",
            )
        )
    return tuple(warnings)


def trigger_codex_review(
    decision: SyncDecision,
    *,
    body: str,
    tolerate_permission_errors: bool = False,
) -> tuple[str, ...]:
    warning = run_gh_write(
        [
            "api",
            "--method",
            "POST",
            f"/repos/{decision.repo}/issues/{decision.number}/comments",
            "-f",
            f"body={body}",
        ],
        timeout_seconds=30,
        tolerate_permission_errors=tolerate_permission_errors,
        action=f"request Codex review on {decision.repo}#{decision.number}",
    )
    return (warning,) if warning else ()


def approve_workflow_runs(
    decision: SyncDecision,
    *,
    tolerate_permission_errors: bool = False,
) -> tuple[str, ...]:
    warnings: list[str] = []
    for run_id in decision.approve_workflow_run_ids:
        warning = gh_api_write(
            f"/repos/{decision.repo}/actions/runs/{run_id}/approve",
            method="POST",
            tolerate_permission_errors=tolerate_permission_errors,
            action=f"approve workflow run {run_id} for {decision.repo}#{decision.number}",
        )
        if warning:
            warnings.append(warning)
    return tuple(warnings)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Idempotently sync GitHub Codex review labels based on current-head Codex review state.")
    )
    parser.add_argument("--repo", action="append", required=True, help="GitHub repo as owner/name. May repeat.")
    parser.add_argument("--pr", action="append", type=int, help="PR number to sync. May repeat.")
    parser.add_argument("--all-open", action="store_true", help="Sync all open PRs in each --repo.")
    parser.add_argument("--apply", action="store_true", help="Actually write labels. Default is dry-run.")
    parser.add_argument(
        "--no-trigger-missing-codex",
        action="store_true",
        help="Do not post @codex review when current-head CI is green and Codex has no current-head news.",
    )
    parser.add_argument(
        "--no-approve-workflow-runs",
        action="store_true",
        help=(
            "Do not approve action_required fork workflow runs after a current-head "
            "clean Codex review on a mergeable PR."
        ),
    )
    parser.add_argument(
        "--codex-review-command",
        default="@codex review",
        help="Issue comment body used to request a missing Codex review.",
    )
    parser.add_argument(
        "--ignore-checks",
        action="store_true",
        help="Ignore current-head CI state when deciding the ok label. Normally do not use this.",
    )
    parser.add_argument(
        "--tolerate-write-permission-errors",
        action="store_true",
        help=(
            "Log and continue when GitHub returns Resource not accessible by integration "
            "for label/comment/approval writes. Read/classification errors still fail."
        ),
    )
    parser.add_argument(
        "--tolerate-read-errors",
        action="store_true",
        help=(
            "Log and continue when a selected PR cannot be classified because of a GitHub "
            "read/API error. Intended for broad --all-open best-effort maintenance runs."
        ),
    )
    parser.add_argument(
        "--reviewer-login",
        action="append",
        default=[],
        help="Allowed Codex reviewer login. May repeat; defaults include chatgpt-codex-connector[bot].",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repos = [repo_path(repo) for repo in args.repo]
    allowed_authors = CODEX_REVIEW_AUTHORS | set(args.reviewer_login)
    had_error = False

    for repo in repos:
        setup_warnings: list[str] = []
        setup_warnings.extend(
            ensure_label(
                repo,
                CODEX_OK_LABEL,
                color="0e8a16",
                description="Current PR head has green CI and a clean Codex review",
                apply=args.apply,
                tolerate_permission_errors=args.tolerate_write_permission_errors,
            )
        )
        setup_warnings.extend(
            ensure_label(
                repo,
                CODEX_NEEDS_WORK_LABEL,
                color="d93f0b",
                description="Codex raised issues on the current PR head that still need work",
                apply=args.apply,
                tolerate_permission_errors=args.tolerate_write_permission_errors,
            )
        )
        for warning in setup_warnings:
            print(f"warning: {warning}", file=sys.stderr, flush=True)
        numbers = list_open_pr_numbers(repo) if args.all_open else list(args.pr or [])
        if not numbers:
            print(f"{repo}: no PRs selected; pass --pr or --all-open", file=sys.stderr)
            had_error = True
            continue

        classified_count = 0
        for number in sorted(set(numbers)):
            try:
                decision = decide_pr(
                    repo,
                    number,
                    allowed_authors=allowed_authors,
                    ignore_checks=args.ignore_checks,
                )
            except GhError as exc:
                if not args.tolerate_read_errors:
                    had_error = True
                print(f"{repo}#{number}: {exc}", file=sys.stderr, flush=True)
                continue
            except Exception as exc:  # noqa: BLE001
                had_error = True
                print(f"{repo}#{number}: {exc}", file=sys.stderr, flush=True)
                continue

            classified_count += 1
            try:
                write_warnings: tuple[str, ...] = ()
                if args.apply:
                    accumulated_warnings: list[str] = []
                    accumulated_warnings.extend(
                        apply_decision(
                            decision,
                            tolerate_permission_errors=args.tolerate_write_permission_errors,
                        )
                    )
                    if decision.approve_workflow_run_ids and not args.no_approve_workflow_runs:
                        accumulated_warnings.extend(
                            approve_workflow_runs(
                                decision,
                                tolerate_permission_errors=args.tolerate_write_permission_errors,
                            )
                        )
                    if decision.trigger_codex_review and not args.no_trigger_missing_codex:
                        accumulated_warnings.extend(
                            trigger_codex_review(
                                decision,
                                body=args.codex_review_command,
                                tolerate_permission_errors=args.tolerate_write_permission_errors,
                            )
                        )
                    write_warnings = tuple(accumulated_warnings)
                mode = "apply" if args.apply else "dry-run"
                print(
                    f"{mode} {repo}#{number}: "
                    f"head={decision.head_sha[:12]} checks={decision.checks_state} "
                    f"merge={decision.merge_state} review={decision.review_state} "
                    f"ok={decision.has_ok_label}->{decision.wants_ok_label}/{decision.ok_action} "
                    f"needs_work={decision.has_needs_work_label}->{decision.wants_needs_work_label}/"
                    f"{decision.needs_work_action} "
                    f"legacy={','.join(sorted(decision.legacy_labels)) or '-'} "
                    f"approve_runs={','.join(str(run_id) for run_id in decision.approve_workflow_run_ids) or '-'} "
                    f"trigger_codex={decision.trigger_codex_review and not args.no_trigger_missing_codex} "
                    f"reason={decision.reason}",
                    flush=True,
                )
                if decision.review_url:
                    print(f"  review_url={decision.review_url}", flush=True)
                for warning in write_warnings:
                    print(f"  write_warning={warning}", flush=True)
            except Exception as exc:  # noqa: BLE001
                had_error = True
                print(f"{repo}#{number}: {exc}", file=sys.stderr, flush=True)

        if args.tolerate_read_errors and classified_count == 0:
            had_error = True
            print(
                f"{repo}: all selected PRs failed classification; refusing a false-green tolerant run",
                file=sys.stderr,
                flush=True,
            )

    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
