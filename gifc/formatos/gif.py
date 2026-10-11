"""O formato GIF: tabela de degraus e os dois estágios, ffmpeg e gifsicle."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from gifc.formatos.base import EncodeError, executar
from gifc.ladder import Toggles, target_width
from gifc.probe import ImagemInfo
from gifc.tools import Tools


@dataclass(frozen=True)
class Params:
    """Um ponto no espaço de compressão do GIF.

    `fps=None` significa manter o framerate original.
    """

    colors: int
    lossy: int
    scale_pct: int
    fps: float | None


# Cada degrau é fracamente mais agressivo que o anterior em todas as
# dimensões simultaneamente: cores nunca aumentam, lossy nunca diminui,
# escala nunca aumenta, fps nunca aumenta (None é o maior fps).
# Isto garante que o tamanho codificado é monotonicamente não-crescente
# ao longo da escada, o que torna a busca binária de lower bound válida.
TABELA: tuple[Params, ...] = (
    Params(256, 0, 100, None),      # 0
    Params(256, 20, 100, None),     # 1
    Params(256, 30, 100, None),     # 2
    Params(128, 30, 100, None),     # 3
    Params(128, 45, 100, None),     # 4
    Params(128, 60, 100, None),     # 5
    Params(64, 60, 100, None),      # 6
    Params(64, 80, 100, None),      # 7
    Params(64, 100, 100, None),     # 8
    Params(64, 100, 85, None),      # 9
    Params(64, 120, 85, None),      # 10
    Params(64, 120, 70, None),      # 11
    Params(64, 140, 70, None),      # 12
    Params(64, 140, 70, 20.0),      # 13
    Params(64, 140, 70, 15.0),      # 14
    Params(64, 140, 70, 12.0),      # 15
    Params(32, 140, 70, 12.0),      # 16
    Params(32, 160, 70, 12.0),      # 17
    Params(32, 160, 50, 12.0),      # 18
    Params(32, 200, 50, 10.0),      # 19
)

# Em que sentido cada campo fica MAIS agressivo (None = maior possível).
SENTIDOS: dict[str, str] = {
    "colors": "menor",
    "lossy": "maior",
    "scale_pct": "menor",
    "fps": "menor",
}

# O valor MENOS agressivo de cada coluna: o que ela assume quando o usuário
# desliga a alavanca correspondente.
NEUTROS: dict[str, object] = {
    "colors": 256,
    "lossy": 0,
    "scale_pct": 100,
    "fps": None,
}


def toggles_efetivos(toggles: Toggles, tools: Tools) -> Toggles:
    """Sem gifsicle não há estágio lossy: manter a coluna ligada só geraria
    degraus que produzem exatamente o mesmo arquivo, desperdiçando encodes."""
    if tools.gifsicle is None:
        return replace(toggles, lossy=False)
    return toggles


def build_filter(info: ImagemInfo, params: Params) -> str:
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
    tools: Tools, src: Path, dst: Path, info: ImagemInfo, params: Params
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


def codificar(
    src: Path, dst: Path, params: Params, info: ImagemInfo, tools: Tools
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
        executar(build_ffmpeg_cmd(tools, src, intermediario, info, params), "ffmpeg")

        if tools.gifsicle is None:
            # Modo degradado: sem o estágio lossy, o resultado do ffmpeg
            # já é o final.
            intermediario.replace(dst)
        else:
            executar(build_gifsicle_cmd(tools, intermediario, dst, params), "gifsicle")

        if not dst.is_file():
            raise EncodeError("A codificação não produziu arquivo de saída.")
        return dst.stat().st_size
    finally:
        intermediario.unlink(missing_ok=True)
