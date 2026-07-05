"""FabricCLI wrapper tests - subprocess fully mocked."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.fabric_cli import FabricCLI, FabricCLIError


def _proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


@patch("src.fabric_cli.subprocess.run")
def test_run_builds_command(mock_run):
    mock_run.return_value = _proc(stdout="ok")
    cli = FabricCLI()
    result = cli.run("ls", "ws.Workspace")
    assert mock_run.call_args[0][0] == ["fab", "ls", "ws.Workspace"]
    assert result.stdout == "ok"


@patch("src.fabric_cli.subprocess.run")
def test_run_raises_on_failure(mock_run):
    mock_run.return_value = _proc(stderr="boom", returncode=1)
    with pytest.raises(FabricCLIError, match="boom"):
        FabricCLI().run("ls", "nope.Workspace")


@patch("src.fabric_cli.subprocess.run")
def test_create_item_path_syntax(mock_run):
    mock_run.return_value = _proc()
    FabricCLI().create_item("DriftDemo", "Bronze", "Lakehouse")
    args = mock_run.call_args[0][0]
    assert args == ["fab", "create", "DriftDemo.Workspace/Bronze.Lakehouse"]


@patch("src.fabric_cli.subprocess.run")
def test_create_item_with_params(mock_run):
    mock_run.return_value = _proc()
    FabricCLI().create_item("WS", "GoldWH", "Warehouse", params="enableSchemas=true")
    args = mock_run.call_args[0][0]
    assert "-P" in args and "enableSchemas=true" in args


@patch("src.fabric_cli.subprocess.run")
def test_login_service_principal(mock_run):
    mock_run.return_value = _proc()
    FabricCLI().login_service_principal("cid", "secret", "tid")
    args = mock_run.call_args[0][0]
    assert args[:3] == ["fab", "auth", "login"]
    assert "-u" in args and "--tenant" in args


@patch("src.fabric_cli.subprocess.run")
def test_api_serializes_body(mock_run):
    mock_run.return_value = _proc(stdout='{"id": "abc"}')
    result = FabricCLI().api(
        "workspaces/123/lakehouses", method="post", body={"displayName": "Bronze"}
    )
    args = mock_run.call_args[0][0]
    assert "-X" in args and "post" in args
    body_arg = args[args.index("-i") + 1]
    assert json.loads(body_arg) == {"displayName": "Bronze"}
    assert result.json() == {"id": "abc"}


@patch("src.fabric_cli.subprocess.run")
def test_exists_true_false(mock_run):
    mock_run.return_value = _proc(stdout="* true")
    assert FabricCLI().exists("WS.Workspace/Bronze.Lakehouse") is True
    mock_run.return_value = _proc(stdout="* false", returncode=1)
    assert FabricCLI().exists("WS.Workspace/Missing.Lakehouse") is False
