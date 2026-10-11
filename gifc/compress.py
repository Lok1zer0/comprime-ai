"""Orquestração: de um GIF de entrada ao arquivo comprimido final."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gifc import formatos
from gifc.formatos.base import EncodeError
from gifc.ladder import Toggles, aplicar_toggles
from gifc.probe import Formato, probe
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
    # `formato_saida` é o formato do arquivo que FOI escrito, não o pedido:
    # se a conversão não rendeu e o original foi copiado, é o da entrada.
    formato_entrada: Formato
    formato_saida: Formato
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def reduction_pct(self) -> float:
        if self.original_bytes == 0:
            return 0.0
        return 100.0 * (1 - self.final_bytes / self.original_bytes)


def unique_output_path(src: Path, formato: Formato | None = None) -> Path:
    """`gato.gif` -> `gato_comprimido.gif`, sem nunca sobrescrever.

    Com `formato`, a extensão é a desse formato (a do mapa em
    `gifc.formatos`, nunca `formato.value`); sem ele, a de `src`.
    """
    ext = src.suffix if formato is None else formatos.extensao(formato)
    base = src.with_name(f"{src.stem}{SUFIXO}{ext}")
    if not base.exists():
        return base
    n = 2
    while True:
        candidato = src.with_name(f"{src.stem}{SUFIXO}_{n}{ext}")
        if not candidato.exists():
            return candidato
        n += 1


def _try_path(tmpdir: Path, index: int, formato: Formato) -> Path:
    """Caminho do arquivo intermediário de um degrau dentro do tmpdir.

    Único lugar que sabe a convenção `try_{index}{extensão}` — usado tanto ao
    codificar quanto ao localizar o vencedor depois da busca. A extensão
    segue o formato de SAÍDA da escada em uso (é o WebP numa conversão)
    porque o Pillow escolhe o formato de saída por ela.
    """
    return tmpdir / f"try_{index}{formatos.extensao(formato)}"


def compress_file(
    src: Path,
    target_bytes: int,
    toggles: Toggles,
    tools: Tools,
    converter_webp: bool = False,
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> CompressResult:
    """Comprime `src` buscando o melhor resultado que cabe em `target_bytes`.

    Com `converter_webp`, um PNG ou JPEG percorre a escada de WebP. Se nenhum
    degrau ficar menor que a entrada o original é copiado, e então
    `formato_saida` (e a extensão de `dst`) é o da entrada: o resultado diz o
    que foi escrito, não o que foi pedido.
    """
    src = Path(src)
    info = probe(src)

    saida = formatos.formato_de_saida(info.formato, converter_webp)
    modulo = formatos.para(saida)
    toggles = formatos.toggles_efetivos(saida, toggles, tools)
    escada = aplicar_toggles(modulo.TABELA, info, toggles, modulo.NEUTROS)
    # Um alvo >= à entrada não é restrição: pede-se "menor", não "qualquer
    # coisa abaixo de 10 MB". Sem este limite um degrau de alta qualidade
    # MAIOR que a entrada (o sem perda do WebP numa foto) caberia no alvo,
    # venceria por ser o de maior qualidade, e `already_optimized` devolveria
    # o original apesar de um degrau menor já ter sido medido. O limite é só
    # da busca: `hit_target` continua sendo contra o alvo do usuário.
    alvo_da_busca = max(0, min(target_bytes, info.size_bytes - 1))

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
                        src, _try_path(tmpdir, index, saida),
                        params, info, tools,
                    )
                except EncodeError as exc:
                    ultimo_erro = exc
            assert ultimo_erro is not None  # o loop rodou 2x e sempre falhou
            raise RungFailed(str(ultimo_erro))

        resultado = find_best(
            escada, alvo_da_busca, encode_fn,
            on_progress=on_progress, should_cancel=should_cancel,
        )

        if resultado.size_bytes == FALHOU:
            raise CompressError(
                f"Nenhuma configuração conseguiu comprimir {src.name}. "
                f"O arquivo pode estar corrompido."
            )

        vencedor = _try_path(tmpdir, resultado.index, saida)

        # Arquivos já muito otimizados podem crescer ao serem reescritos, e um
        # WebP pode ficar maior que o PNG/JPEG de origem. Em ambos os casos o
        # original é copiado, no formato (e na extensão) dele.
        if resultado.size_bytes >= info.size_bytes:
            dst = unique_output_path(src, info.formato)
            shutil.copy2(src, dst)
            return CompressResult(
                src=src, dst=dst,
                original_bytes=info.size_bytes,
                final_bytes=dst.stat().st_size,
                params=None,
                hit_target=info.size_bytes <= target_bytes,
                already_optimized=True,
                formato_entrada=info.formato,
                formato_saida=info.formato,
                attempts=resultado.attempts,
            )

        dst = unique_output_path(src, saida)
        shutil.move(str(vencedor), str(dst))
        return CompressResult(
            src=src, dst=dst,
            original_bytes=info.size_bytes,
            final_bytes=dst.stat().st_size,
            params=resultado.params,
            # Aqui o resultado é menor que a entrada, logo `alvo_da_busca` e o
            # alvo do usuário só divergem a favor dele: acertar um implica o
            # outro. Mesmo assim mede-se contra o alvo do usuário.
            hit_target=resultado.size_bytes <= target_bytes,
            already_optimized=False,
            formato_entrada=info.formato,
            formato_saida=saida,
            attempts=resultado.attempts,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
