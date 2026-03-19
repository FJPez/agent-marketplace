from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_render_api_reference_pdf_generates_pdf(tmp_path: Path) -> None:
    source = tmp_path / "api-reference.md"
    output = tmp_path / "api-reference.pdf"
    source.write_text(
        "\n".join(
            [
                "# API Reference",
                "",
                "This document describes a small API.",
                "",
                "## Endpoint",
                "",
                "- `GET /health`",
                "- `POST /items`",
                "",
                "```json",
                "{",
                '  "status": "ok"',
                "}",
                "```",
            ]
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/render_api_reference_pdf.py",
            str(source),
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output.exists()
    assert output.read_bytes().startswith(b"%PDF")
    assert output.stat().st_size > 1_000
