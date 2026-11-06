"""Policy for writing internal work notes.

The model proposes; this policy decides. Phase 2 Step 1 allows exactly one kind of
write (an internal work note), only in explicit write mode, only on designated test
incidents, and never while the kill switch is on.
"""

from __future__ import annotations

from dataclasses import dataclass

from support_agent.config import AgentSettings


@dataclass(frozen=True)
class WriteDecision:
    allowed: bool
    reason: str


def check_work_note_write(incident_number: str, settings: AgentSettings) -> WriteDecision:
    if settings.kill_switch:
        return WriteDecision(False, "Kill switch is on (AGENT_KILL_SWITCH): all writes are blocked")
    if not settings.write_test_incidents:
        return WriteDecision(
            False, "No designated test incidents: set WRITE_TEST_INCIDENTS to enable write mode"
        )
    if incident_number.upper() not in settings.write_test_incidents:
        return WriteDecision(
            False, f"{incident_number} is not a designated test incident (WRITE_TEST_INCIDENTS)"
        )
    return WriteDecision(True, f"{incident_number} is a designated test incident")
