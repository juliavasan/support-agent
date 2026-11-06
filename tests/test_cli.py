from __future__ import annotations

import json
import subprocess  # noqa: S404
import sys
from pathlib import Path

import pytest

from conftest import FakeAnalyzer, FakeRetriever, FakeServiceNow, write_settings
from support_agent.analysis.customer_tiers import load_customer_tiers
from support_agent.cli import main
from support_agent.graph.workflow import build_graph

ROOT = Path(__file__).resolve().parents[1]


def mocked_graph(sn: FakeServiceNow | None = None):  # type: ignore[no-untyped-def]
    sn = sn or FakeServiceNow()
    return build_graph(
        client_factory=lambda: sn,  # type: ignore[arg-type,return-value]
        analyzer_factory=FakeAnalyzer,
        settings_factory=write_settings,
        tiers_factory=load_customer_tiers,
        retriever_factory=FakeRetriever,
    )


def test_cli_dry_run_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    sn = FakeServiceNow()
    code = main(["--incident", "INC0010001"], graph=mocked_graph(sn))
    out = capsys.readouterr().out
    assert code == 0
    assert "INC0010001" in out
    assert "AI Triage Assessment (Preliminary)" in out
    assert "DRY_RUN" in out
    assert sn.writes == []


def test_cli_write_flag(capsys: pytest.CaptureFixture[str]) -> None:
    sn = FakeServiceNow()
    code = main(["--incident", "INC0010001", "--write"], graph=mocked_graph(sn))
    assert code == 0
    assert "WRITTEN" in capsys.readouterr().out
    assert len(sn.writes) == 1


def test_cli_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    main(["--incident", "INC0010001", "--json"], graph=mocked_graph())
    data = json.loads(capsys.readouterr().out)
    assert data["incident"]["number"] == "INC0010001"
    assert data["write_result"]["status"] == "dry_run"


def test_cli_error_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    sn = FakeServiceNow()
    sn.fail_on.add("get_incident")
    assert main(["--incident", "INC9999999"], graph=mocked_graph(sn)) == 1
    assert "ERROR" in capsys.readouterr().out


def test_cli_requires_incident_argument() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_main_py_entrypoint_runs() -> None:
    """main.py is runnable as a script (no network: --help exits before the graph runs)."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "main.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--incident" in result.stdout
    assert "--write" in result.stdout
