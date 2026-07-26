from app.services.decision_record_service import extract_decision_summary, validate_decision_summary


def test_extract_decision_summary_from_json_fence():
    text = """
Review complete.

```json
{
  "summary": "Ship MVP with reduced scope",
  "actions": [{"action": "Cut feature X", "owner_role": "PM", "acceptance": "Scope doc updated"}],
  "risks": ["Vendor dependency"]
}
```
"""
    summary = extract_decision_summary(text)
    assert summary is not None
    assert summary["summary"] == "Ship MVP with reduced scope"
    assert len(summary["actions"]) == 1


def test_extract_decision_summary_nested_key():
    text = '{"decision_summary": {"summary": "Go", "actions": [], "risks": []}}'
    summary = extract_decision_summary(text)
    assert summary == {"summary": "Go", "actions": [], "risks": []}


def test_validate_decision_summary_defaults():
    normalized = validate_decision_summary({"summary": ["a", "b"]})
    assert normalized["summary"] == "a\nb"
    assert normalized["actions"] == []
    assert normalized["cancelled_tasks"] == []
