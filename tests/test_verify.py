"""Verify CLI: argparse + happy-path parsing exercised via mocked HTTP."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from pursue_mcp import verify


def test_unknown_source_exits_nonzero(capsys) -> None:
    rc = verify.main(["aaro", "not_a_category"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "unknown category" in captured.err


@respx.mock
async def test_verify_aaro_happy_path(tmp_path: Path, monkeypatch, capsys) -> None:
    fixture = (
        Path(__file__).parent / "fixtures" / "aaro_resolution_reports.html"
    ).read_text(encoding="utf-8")
    respx.get("https://www.aaro.mil/UAP-Cases/UAP-Case-Resolution-Reports/").mock(
        return_value=httpx.Response(200, text=fixture)
    )
    monkeypatch.setattr(verify, "FIXTURE_DIR", tmp_path)

    rc = await verify.verify_aaro("resolution_reports")
    assert rc == 0
    out = capsys.readouterr().out
    assert "parsed: 3 records" in out
    assert "GIMBAL" in out
