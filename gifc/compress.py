"""Orquestração: de um GIF de entrada ao arquivo comprimido final."""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gifc import formatos
from gifc.formatos.base import EncodeError
from gifc.ladder import Toggles, aplicar_toggles
from gifc.probe import probe
from gifc.search import FALHOU, Attempt, CancelFn, ProgressFn, RungFailed, find_best
from gifc.tools import Tools

SUFIXO = "_comprimido"


class CompressError(Exception):
    """Nenhum degrau da escada produziu um resultado utilizável."""


@dataclass
class CompressResult:
    src: Path
    dst: Path
    original_bytes: int
    final_bytes: int
    params: Any | None  # o `Params` do formato do arquivo; None se já otimizado
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


def _try_path(tmpdir: Path, index: int) -> Path:
    """Caminho do arquivo intermediário de um degrau dentro do tmpdir.

    Único lugar que sabe a convenção `try_{index}.gif` — usado tanto ao
    codificar quanto ao localizar o vencedor depois da busca.
    """
    return tmpdir / f"try_{index}.gif"


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
    info = probe(src)

    # Sem gifsicle não há estágio lossy: manter a coluna ligada só geraria
    # degraus que produzem exatamente o mesmo arquivo, desperdiçando encodes.
    if tools.gifsicle is None:
        toggles = dataclasses.replace(toggles, lossy=False)

    modulo = formatos.para(info.formato)
    escada = aplicar_toggles(modulo.TABELA, info, toggles)
    dst = unique_output_path(src)

    tmpdir = Path(tempfile.mkdtemp(prefix="gifc_"))
    try:
        def encode_fn(index: int, params: Any) -> int:
            # Uma falha transitória do ffmpeg/gifsicle (arquivo temporário
            # ainda travado por antivírus, hiccup de I/O) não pode custar a
            # metade de alta qualidade da escada inteira. Tenta de novo uma
            # vez antes de desistir do degrau; só na segunda falha ele é
            # reportado como RungFailed para a busca tratar como "grande
            # demais".
            ultimo_erro: EncodeError | None = None
            for _tentativa in range(2):
                try:
                    return modulo.codificar(
                        src, _try_path(tmpdir, index), params, info, tools
                    )
                except EncodeError as exc:
                    ultimo_erro = exc
            assert ultimo_erro is not None  # o loop rodou 2x e sempre falhou
            raise RungFailed(str(ultimo_erro))

        resultado = find_best(
            escada, target_bytes, encode_fn,
            on_progress=on_progress, should_cancel=should_cancel,
        )

        if resultado.size_bytes == FALHOU:
            raise CompressError(
                f"Nenhuma configuração conseguiu comprimir {src.name}. "
                f"O arquivo pode estar corrompido."
            )

        vencedor = _try_path(tmpdir, resultado.index)

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
