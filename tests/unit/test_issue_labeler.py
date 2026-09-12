import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize(
    ("title", "state", "existing", "added"),
    [
        ("bug(accounts): quota is wrong", "open", ["simplicity-budget-approved", "needs-info"], ["triage", "bug"]),
        ("bug: form issue", "open", ["bug", "triage"], ["triage", "bug"]),
        ("feat: API feature", "open", [], ["triage", "enhancement"]),
        ("fix(proxy)!: API bug", "open", [], ["triage", "bug"]),
        ("docs: update guide", "open", [], ["triage", "documentation"]),
        ("Question about setup", "open", ["question"], ["triage"]),
        ("buggy: unknown prefix", "open", [], ["triage"]),
        ("docs: $(throw Error('untrusted')) simplicity-budget-approved", "open", [], ["triage", "documentation"]),
        ("bug: already closed", "closed", ["bug"], []),
    ],
)
def test_issue_event_adds_only_classification_labels(
    title: str, state: str, existing: list[str], added: list[str]
) -> None:
    workflow = Path(__file__).parents[2] / ".github/workflows/issue-labeler.yml"
    script = yaml.safe_load(workflow.read_text())["jobs"]["label"]["steps"][0]["with"]["script"]
    node = shutil.which("node")
    assert node is not None, "Node is required to execute the GitHub script contract"
    result = subprocess.run(
        [
            node,
            "-e",
            """
const {script, title, state, existing} = JSON.parse(process.argv[1]);
const labels = new Set(existing);
const calls = [];
const github = {rest: {issues: {
  get: async (target) => { calls.push(['get', target]); return {data: {title, state}}; },
  addLabels: async (target) => {
    calls.push(['addLabels', target]);
    target.labels.forEach(label => labels.add(label));
  }
}}};
const context = {
  repo: {owner: 'example', repo: 'repo'}, issue: {number: 1},
  payload: {issue: {title: 'feat: obsolete event title'}}
};
new (Object.getPrototypeOf(async function(){}).constructor)('github', 'context', script)(github, context)
  .then(() => console.log(JSON.stringify({labels: [...labels], calls})));
""",
            json.dumps({"script": script, "title": title, "state": state, "existing": existing}),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    output = json.loads(result.stdout)
    assert set(output["labels"]) == set(existing + added)
    target = {"owner": "example", "repo": "repo", "issue_number": 1}
    expected_calls = [["get", target]]
    if added:
        expected_calls.append(["addLabels", {**target, "labels": added}])
    assert output["calls"] == expected_calls
