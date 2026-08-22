"""Localização das ferramentas externas (ffmpeg, ffprobe, gifsicle)."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = PROJECT_ROOT / "bin"


class MissingToolError(Exception):
    """Uma ferramenta externa obrigatória não foi encontrada."""

    def __init__(self, tool: str) -> None:
        self.tool = tool
        super().__init__(
            f"Ferramenta não encontrada: {tool}. "
            f"Instale-a e garanta que está no PATH."
        )


@dataclass(frozen=True)
class Tools:
    """Caminhos das ferramentas externas.

    `gifsicle` é None quando o binário não está disponível — nesse caso o
    app roda em modo degradado, sem o estágio de compressão lossy.
    """

    ffmpeg: str
    ffprobe: str
    gifsicle: str | None


def gifsicle_path(bin_dir: Path) -> Path:
    """Caminho onde o gifsicle portátil fica dentro da pasta do app."""
    name = "gifsicle.exe" if sys.platform == "win32" else "gifsicle"
    return bin_dir / name


def find_tools(bin_dir: Path = BIN_DIR) -> Tools:
    """Localiza as ferramentas. Levanta MissingToolError se faltar ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise MissingToolError("ffmpeg")

    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise MissingToolError("ffprobe")

    local = gifsicle_path(bin_dir)
    gifsicle = str(local) if local.is_file() else shutil.which("gifsicle")

    return Tools(ffmpeg=ffmpeg, ffprobe=ffprobe, gifsicle=gifsicle)
