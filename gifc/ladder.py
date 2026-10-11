"""O que é comum a todas as escadas de qualidade.

Cada formato (`gifc.formatos.*`) é dono da sua tabela de degraus; aqui ficam
os toggles do usuário e a lógica genérica que os aplica a qualquer tabela.

Não faz I/O. Tudo aqui é função pura, testável sem ffmpeg.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from typing import TypeVar

from gifc.probe import ImagemInfo

LARGURA_MINIMA = 16

P = TypeVar("P")


@dataclass(frozen=True)
class Toggles:
    """Quais alavancas o usuário autorizou."""

    palette: bool = True
    lossy: bool = True
    scale: bool = True
    fps: bool = True


# Para cada toggle, a coluna de `Params` que ele controla. Quanto vale a
# coluna quando a alavanca está DESLIGADA é decisão de cada formato (o seu
# `NEUTROS`): "sem paleta" é 256 cores num GIF, mas "não quantizar" num PNG.
# Um formato cujo `Params` não tenha a coluna simplesmente não é afetado.
ALAVANCAS: dict[str, str] = {
    "palette": "colors",
    "lossy": "lossy",
    "scale": "scale_pct",
    "fps": "fps",
}


def target_width(width: int, scale_pct: int) -> int:
    """Largura alvo: arredondada para par, com piso de 16px."""
    bruta = width * scale_pct / 100
    par = int(bruta) + (int(bruta) % 2)
    return max(LARGURA_MINIMA, par)


def aplicar_toggles(
    tabela: Sequence[P],
    info: ImagemInfo,
    toggles: Toggles,
    neutros: Mapping[str, object],
) -> list[P]:
    """Aplica os toggles a uma tabela de degraus e remove os repetidos.

    Funciona sobre o `Params` de qualquer formato: só toca as colunas que
    ele realmente tem (consultadas por `dataclasses.fields`). `neutros` é o
    `NEUTROS` do formato: o valor menos agressivo de cada coluna, usado
    quando o toggle dela está desligado. A ordem da tabela é preservada; de
    cada grupo de degraus iguais fica o primeiro.
    """
    escada: list[P] = []
    vistos: set[P] = set()

    for base in tabela:
        colunas = {f.name for f in fields(base)}  # type: ignore[arg-type]
        controladas = {
            toggle: coluna for toggle, coluna in ALAVANCAS.items()
            if coluna in colunas
        }
        sem_neutro = sorted(c for c in controladas.values() if c not in neutros)
        if sem_neutro:
            raise ValueError(
                f"O formato não declara o valor neutro de: {', '.join(sem_neutro)}."
            )

        p = base
        for toggle, coluna in controladas.items():
            if not getattr(toggles, toggle):
                p = replace(p, **{coluna: neutros[coluna]})  # type: ignore[type-var]

        # Nunca aumentar o framerate: um alvo >= ao original é o original.
        if "fps" in colunas:
            fps = getattr(p, "fps")
            if fps is not None and fps >= info.fps:
                p = replace(p, fps=None)  # type: ignore[type-var]

        if p not in vistos:
            vistos.add(p)
            escada.append(p)

    return escada
