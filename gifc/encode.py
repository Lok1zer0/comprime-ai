"""Execução dos dois estágios de compressão: ffmpeg e gifsicle."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from gifc.ladder import Params, target_width
from gifc.probe import GifInfo
from gifc.tools import Tools

TIMEOUT_SEGUNDOS = 600


class EncodeError(Exception):
    """Um dos estágios de codificação falhou."""


def build_filter(info: GifInfo, params: Params) -> str:
    """Monta a cadeia de filtros do ffmpeg.

    Duas escolhas carregam a maior parte do ganho de compressão:

    - `stats_mode=diff` otimiza a paleta para o que muda entre frames.
    - `dither=bayer` em vez de floyd-steinberg. Floyd gera ruído diferente
      a cada frame, o que destrói a compressão inter-frame do GIF e infla o
      arquivo. Bayer gera um padrão estático, que comprime muito melhor.
    - `bayer_scale=5` alarga o padrão de dithering em relação ao padrão do
      ffmpeg (2), o que comprime melhor ainda — é uma escolha deliberada,
      não decoração incidental.
    """
    pre: list[str] = []
    if params.fps is not None and params.fps < info.fps:
        pre.append(f"fps={params.fps:g}")
    if params.scale_pct != 100:
        largura = target_width(info.width, params.scale_pct)
        pre.append(f"scale={largura}:-1:flags=lanczos")

    prefixo = ",".join(pre)
    if prefixo:
        prefixo += ","

    return (
        f"{prefixo}split[a][b];"
        f"[a]palettegen=max_colors={params.colors}:stats_mode=diff[p];"
        f"[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )


def build_ffmpeg_cmd(
    tools: Tools, src: Path, dst: Path, info: GifInfo, params: Params
) -> list[str]:
    return [
        tools.ffmpeg, "-y", "-v", "error",
        "-i", str(src),
        "-filter_complex", build_filter(info, params),
        "-loop", "0",
        str(dst),
    ]


def build_gifsicle_cmd(
    tools: Tools, src: Path, dst: Path, params: Params
) -> list[str]:
    if tools.gifsicle is None:
        raise EncodeError("gifsicle não está disponível.")
    cmd = [tools.gifsicle, "-O3"]
    if params.lossy > 0:
        cmd.append(f"--lossy={params.lossy}")
    cmd += [str(src), "-o", str(dst)]
    return cmd


def _run(cmd: list[str], etapa: str) -> None:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_SEGUNDOS
        )
    except subprocess.TimeoutExpired as exc:
        raise EncodeError(f"{etapa} demorou demais e foi interrompido.") from exc
    except OSError as exc:
        raise EncodeError(f"Falha ao executar {etapa}: {exc}") from exc

    if proc.returncode != 0:
        raise EncodeError(f"{etapa} falhou: {proc.stderr.strip()[:500]}")


def encode(
    src: Path, dst: Path, params: Params, info: GifInfo, tools: Tools
) -> int:
    """Codifica `src` em `dst` com `params`. Devolve o tamanho de `dst`.

    Sempre parte do arquivo original — nunca do resultado de uma tentativa
    anterior — para não acumular perda.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, intermediario_str = tempfile.mkstemp(suffix=".gif", dir=str(dst.parent))
    os.close(fd)
    intermediario = Path(intermediario_str)

    try:
        _run(build_ffmpeg_cmd(tools, src, intermediario, info, params), "ffmpeg")

        if tools.gifsicle is None:
            # Modo degradado: sem o estágio lossy, o resultado do ffmpeg
            # já é o final.
            intermediario.replace(dst)
        else:
            _run(build_gifsicle_cmd(tools, intermediario, dst, params), "gifsicle")

        if not dst.is_file():
            raise EncodeError("A codificação não produziu arquivo de saída.")
        return dst.stat().st_size
    finally:
        intermediario.unlink(missing_ok=True)
