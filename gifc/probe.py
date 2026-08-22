"""Leitura de metadados de um GIF via ffprobe."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from gifc.tools import Tools


class ProbeError(Exception):
    """O arquivo não pôde ser lido como um GIF válido."""


@dataclass(frozen=True)
class GifInfo:
    path: Path
    width: int
    height: int
    fps: float
    frame_count: int
    duration: float
    size_bytes: int


def _parse_fps(valor: str) -> float:
    """Converte '25/1' ou '0/0' num float utilizável."""
    try:
        f = float(Fraction(valor))
    except (ValueError, ZeroDivisionError):
        return 10.0
    return f if f > 0 else 10.0


def probe(path: Path, tools: Tools) -> GifInfo:
    """Lê os metadados do GIF. Levanta ProbeError se não for um GIF válido."""
    path = Path(path)
    if not path.is_file():
        raise ProbeError(f"Arquivo não encontrado: {path}")

    cmd = [
        tools.ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-count_frames",
        "-show_entries",
        "stream=width,height,avg_frame_rate,nb_read_frames,duration",
        "-show_entries", "format=format_name,duration",
        "-print_format", "json",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except OSError as exc:
        raise ProbeError(f"Falha ao executar ffprobe: {exc}") from exc

    if proc.returncode != 0:
        raise ProbeError(f"ffprobe falhou em {path.name}: {proc.stderr.strip()}")

    try:
        dados = json.loads(proc.stdout)
        stream = dados["streams"][0]
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        raise ProbeError(f"{path.name} não contém um fluxo de vídeo.") from exc

    formato = dados.get("format", {}).get("format_name", "")
    if "gif" not in formato:
        raise ProbeError(f"{path.name} não é um GIF (formato: {formato}).")

    fps = _parse_fps(stream.get("avg_frame_rate", "0/0"))
    frame_count = int(stream.get("nb_read_frames") or 0)
    duracao = float(
        stream.get("duration") or dados.get("format", {}).get("duration") or 0.0
    )
    if duracao <= 0 and frame_count and fps:
        duracao = frame_count / fps

    return GifInfo(
        path=path,
        width=int(stream["width"]),
        height=int(stream["height"]),
        fps=fps,
        frame_count=frame_count,
        duration=duracao,
        size_bytes=path.stat().st_size,
    )
