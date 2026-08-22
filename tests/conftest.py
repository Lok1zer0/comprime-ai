"""Fixtures compartilhadas: ferramentas e um GIF de teste real."""

import subprocess
from pathlib import Path

import pytest

from gifc.tools import BIN_DIR, MissingToolError, find_tools


@pytest.fixture(scope="session")
def tools():
    try:
        t = find_tools(BIN_DIR)
    except MissingToolError as exc:
        pytest.skip(f"ferramenta ausente: {exc.tool}")
    if t.gifsicle is None:
        pytest.skip("gifsicle não disponível em bin/")
    return t


@pytest.fixture(scope="session")
def gif_pequeno(tools, tmp_path_factory) -> Path:
    """Gera um GIF real de 64x64, 10 frames, 10fps, com ffmpeg."""
    destino = tmp_path_factory.mktemp("fixtures") / "amostra.gif"
    subprocess.run(
        [
            tools.ffmpeg, "-y", "-v", "error",
            "-f", "lavfi",
            "-i", "testsrc=size=64x64:rate=10:duration=1",
            str(destino),
        ],
        check=True,
        capture_output=True,
    )
    return destino
