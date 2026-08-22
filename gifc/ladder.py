"""A escada de qualidade: da perda menos perceptível à mais agressiva.

Não faz I/O. Tudo aqui é função pura, testável sem ffmpeg.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from gifc.probe import GifInfo

LARGURA_MINIMA = 16


@dataclass(frozen=True)
class Params:
    """Um ponto no espaço de compressão.

    `fps=None` significa manter o framerate original.
    """

    colors: int
    lossy: int
    scale_pct: int
    fps: float | None


@dataclass(frozen=True)
class Toggles:
    """Quais alavancas o usuário autorizou."""

    palette: bool = True
    lossy: bool = True
    scale: bool = True
    fps: bool = True


# Cada degrau é fracamente mais agressivo que o anterior em todas as
# dimensões simultaneamente: cores nunca aumentam, lossy nunca diminui,
# escala nunca aumenta, fps nunca aumenta (None é o maior fps).
# Isto garante que o tamanho codificado é monotonicamente não-crescente
# ao longo da escada, o que torna a busca binária de lower bound válida.
BASE_LADDER: tuple[Params, ...] = (
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


def target_width(width: int, scale_pct: int) -> int:
    """Largura alvo: arredondada para par, com piso de 16px."""
    bruta = width * scale_pct / 100
    par = int(bruta) + (int(bruta) % 2)
    return max(LARGURA_MINIMA, par)


def build(info: GifInfo, toggles: Toggles) -> list[Params]:
    """Monta a escada aplicando os toggles e removendo degraus repetidos."""
    escada: list[Params] = []
    vistos: set[Params] = set()

    for base in BASE_LADDER:
        p = base
        if not toggles.palette:
            p = replace(p, colors=256)
        if not toggles.lossy:
            p = replace(p, lossy=0)
        if not toggles.scale:
            p = replace(p, scale_pct=100)
        if not toggles.fps:
            p = replace(p, fps=None)

        # Nunca aumentar o framerate: um alvo >= ao original é o original.
        if p.fps is not None and p.fps >= info.fps:
            p = replace(p, fps=None)

        if p not in vistos:
            vistos.add(p)
            escada.append(p)

    return escada
