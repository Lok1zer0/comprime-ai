"""Orquestração: de um GIF de entrada ao arquivo comprimido final."""

from __future__ import annotations

import dataclasses
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from gifc.encode import EncodeError, encode
from gifc.ladder import Params, Toggles, build
from gifc.probe import probe
from gifc.search import Attempt, CancelFn, ProgressFn, find_best
from gifc.tools import Tools

SUFIXO = "_comprimido"

# Sentinela para um degrau que falhou: a busca o trata como "grande demais"
# e segue para degraus mais agressivos, em vez de abortar o arquivo inteiro.
FALHOU = sys.maxsize


class CompressError(Exception):
    """Nenhum degrau da escada produziu um resultado utilizável."""


@dataclass
class CompressResult:
    src: Path
    dst: Path
    original_bytes: int
    final_bytes: int
    params: Params | None
    hit_target: bool
    already_optimized: bool
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def reduction_pct(self) -> float:
        if self.original_bytes == 0:
            return 0.0
        return 100.0 * (1 - self.final_bytes / self.original_bytes)


def unique_output_path(src: Path) -> Path:
    """`gato.gif` -> `gato_comprimido.gif`, sem nunca sobrescrever."""
    base = src.with_name(f"{src.stem}{SUFIXO}{src.suffix}")
    if not base.exists():
        return base
    n = 2
    while True:
        candidato = src.with_name(f"{src.stem}{SUFIXO}_{n}{src.suffix}")
        if not candidato.exists():
            return candidato
        n += 1


def compress_file(
    src: Path,
    target_bytes: int,
    toggles: Toggles,
    tools: Tools,
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> CompressResult:
    """Comprime `src` buscando o melhor resultado que cabe em `target_bytes`."""
    src = Path(src)
    info = probe(src, tools)

    # Sem gifsicle não há estágio lossy: manter a coluna ligada só geraria
    # degraus que produzem exatamente o mesmo arquivo, desperdiçando encodes.
    if tools.gifsicle is None:
        toggles = dataclasses.replace(toggles, lossy=False)

    escada = build(info, toggles)
    dst = unique_output_path(src)

    tmpdir = Path(tempfile.mkdtemp(prefix="gifc_"))
    try:
        def encode_fn(index: int, params: Params) -> int:
            try:
                return encode(
                    src, tmpdir / f"try_{index}.gif", params, info, tools
                )
            except EncodeError:
                # Um degrau ruim não derruba o arquivo: é pulado.
                return FALHOU

        resultado = find_best(
            escada, target_bytes, encode_fn,
            on_progress=on_progress, should_cancel=should_cancel,
        )

        if resultado.size_bytes == FALHOU:
            raise CompressError(
                f"Nenhuma configuração conseguiu comprimir {src.name}. "
                f"O arquivo pode estar corrompido."
            )

        vencedor = tmpdir / f"try_{resultado.index}.gif"

        # GIFs já muito otimizados podem crescer ao serem reescritos.
        if resultado.size_bytes >= info.size_bytes:
            shutil.copy2(src, dst)
            return CompressResult(
                src=src, dst=dst,
                original_bytes=info.size_bytes,
                final_bytes=dst.stat().st_size,
                params=None,
                hit_target=info.size_bytes <= target_bytes,
                already_optimized=True,
                attempts=resultado.attempts,
            )

        shutil.move(str(vencedor), str(dst))
        return CompressResult(
            src=src, dst=dst,
            original_bytes=info.size_bytes,
            final_bytes=dst.stat().st_size,
            params=resultado.params,
            hit_target=resultado.hit_target,
            already_optimized=False,
            attempts=resultado.attempts,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
