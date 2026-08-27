from pathlib import Path

from maritime_ratify.profile import WORK_ORDER_SCOPE


ROOT = Path(__file__).parents[1]


def test_normative_use_case_matches_runtime_scope() -> None:
    requirements = (ROOT / "docs" / "REFERENCE-REQUIREMENTS.md").read_text()

    assert f"scope:       {WORK_ORDER_SCOPE}" in requirements
    assert f'"scope": "{WORK_ORDER_SCOPE}"' in requirements
    assert "scope:       work_order:create" not in requirements
    assert '"scope": "work_order:create"' not in requirements


def test_public_docs_describe_deployed_pilot_without_completion_claim() -> None:
    readme = (ROOT / "README.md").read_text()
    requirements = (ROOT / "docs" / "REFERENCE-REQUIREMENTS.md").read_text()
    normalized_readme = " ".join(readme.split())

    assert "Deployed pilot topology:" in readme
    assert "Ratify Verify is a separate managed product under development" in normalized_readme
    assert "it is not yet complete under the frozen reference criteria" in normalized_readme
    assert "Status: Frozen Phase 1 baseline; deployed pilot; completion pending" in requirements
