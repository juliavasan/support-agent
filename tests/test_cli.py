from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from conftest import INCIDENT_RECORD
from support_agent.cli import main
from support_agent.graph.workflow import build_graph
from support_agent.servicenow import Incident, IncidentNotFoundError

ROOT = Path(__file__).resolve().parents[1]


def mocked_graph(incident: Incident | None = None, error: Exception | None = None):  # type: ignore[no-untyped-def]
    client = MagicMock()
    if error:
        client.get_incident.side_effect = error
    else:
        client.get_incident.return_value = incident
    return build_graph(client_factory=lambda: client)


def test_cli_prints_incident(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--incident", "INC0010001"], graph=mocked_graph(Incident.from_api(INCIDENT_RECORD)))
    out = capsys.readouterr().out
    assert code == 0
    assert "INC0010001" in out
    assert "Amira Haddad" in out


def test_cli_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    main(["--incident", "INC0010001", "--json"], graph=mocked_graph(Incident.from_api(INCIDENT_RECORD)))
    data = json.loads(capsys.readouterr().out)
    assert data["incident"]["number"] == "INC0010001"


def test_cli_error_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--incident", "INC9999999"], graph=mocked_graph(error=IncidentNotFoundError("not found")))
    assert code == 1
    assert "ERROR" in capsys.readouterr().out


def test_cli_requires_incident_argument() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_main_py_entrypoint_runs() -> None:
    """main.py is runnable as a script (no network: --help exits before the graph runs)."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "--incident" in result.stdout
