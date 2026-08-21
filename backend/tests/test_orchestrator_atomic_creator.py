"""T2.6 + T-006: AtomicCreator idempotency."""
import uuid
from app.services.orchestrator.atomic_creator import AtomicCreator


def test_deterministic_uuid_is_stable():
    """Same (draft_id, suffix) -> same UUID across calls."""
    ac = AtomicCreator()
    draft_id = uuid.uuid4()
    u1 = ac.deterministic_uuid(draft_id, "group")
    u2 = ac.deterministic_uuid(draft_id, "group")
    assert u1 == u2, "deterministic_uuid must be stable (idempotency)"
    # Different suffix -> different UUID
    u3 = ac.deterministic_uuid(draft_id, "chief_agent")
    assert u1 != u3
    # Different draft_id -> different UUID
    u4 = ac.deterministic_uuid(uuid.uuid4(), "group")
    assert u1 != u4


def test_replay_result_returns_prior_ids():
    """When given an already-consumed draft row, replay returns prior IDs."""
    ac = AtomicCreator()
    draft_id = uuid.uuid4()
    prior_group = uuid.uuid4()
    prior_chief_run = uuid.uuid4()
    prior_card = uuid.uuid4()

    class FakeRow:
        error_detail = {
            "group_id": str(prior_group),
            "chief_run_id": str(prior_chief_run),
            "task_card_ids": [str(prior_card)],
        }

    result = ac._replay_result(draft_id, FakeRow())
    assert result["group_id"] == prior_group
    assert result["chief_run_id"] == prior_chief_run
    assert result["task_card_ids"] == [prior_card]


def test_atomic_creator_error_has_code():
    from app.services.orchestrator.atomic_creator import AtomicCreatorError
    e = AtomicCreatorError("draft_not_found", "test")
    assert e.code == "draft_not_found"
    assert "test" in str(e)
