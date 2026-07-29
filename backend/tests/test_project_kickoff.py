from __future__ import annotations

import inspect

from app.services.hr_review_session_service import select_proposal
from app.services.project_provisioning import provision_team_from_plan


def test_provision_team_from_plan_accepts_send_kickoff_kwarg():
    params = inspect.signature(provision_team_from_plan).parameters
    assert "send_kickoff" in params
    assert params["send_kickoff"].default is True


def test_select_proposal_accepts_send_kickoff_kwarg():
    params = inspect.signature(select_proposal).parameters
    assert "send_kickoff" in params
    assert params["send_kickoff"].default is True
