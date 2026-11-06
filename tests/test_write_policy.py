from __future__ import annotations

from support_agent.config import AgentSettings
from support_agent.guardrails.write_policy import check_work_note_write


def test_designated_test_incident_is_allowed() -> None:
    settings = AgentSettings(write_test_incidents=frozenset({"INC0010001"}))
    assert check_work_note_write("inc0010001", settings).allowed


def test_no_designated_incidents_blocks_all_writes() -> None:
    decision = check_work_note_write("INC0010001", AgentSettings())
    assert not decision.allowed
    assert "WRITE_TEST_INCIDENTS" in decision.reason


def test_other_incidents_are_blocked() -> None:
    settings = AgentSettings(write_test_incidents=frozenset({"INC0010001"}))
    assert not check_work_note_write("INC0010002", settings).allowed


def test_kill_switch_overrides_the_allow_list() -> None:
    settings = AgentSettings(write_test_incidents=frozenset({"INC0010001"}), kill_switch=True)
    decision = check_work_note_write("INC0010001", settings)
    assert not decision.allowed
    assert "Kill switch" in decision.reason
