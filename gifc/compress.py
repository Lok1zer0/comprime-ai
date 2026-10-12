"""Orquestração: de um GIF de entrada ao arquivo comprimido final."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from gifc import formatos
from gifc.formatos.base import EncodeError
from gifc.ladder import Toggles, aplicar_toggles
from gifc.probe import Formato, probe
from gifc.search import (
    FALHOU,
    Attempt,
    Cancelled,
    CancelFn,
    ProgressFn,
    RungFailed,
    SearchResult,
    find_best,
)
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


def _sondar(
    index: int, params: Any, encode_fn: Callable[[int, Any], int]
) -> Attempt[Any]:
    """Uma codificação, com `RungFailed` virando um `Attempt` de tamanho
    `FALHOU`: o mesmo tratamento que `find_best` dá a cada sonda sua."""
    try:
        return Attempt(index, params, encode_fn(index, params))
    except RungFailed as exc:
        return Attempt(index, params, FALHOU, exc.mensagem)


def buscar(
    escada: Sequence[Any],
    n_prefixo: int,
    alvo: int,
    encode_fn: Callable[[int, Any], int],
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> SearchResult[Any]:
    """O degrau de MAIOR qualidade de `escada` que cabe em `alvo`.

    `find_best` bisecta, e a bisecção só é correta numa curva de tamanho
    monótona. Os `n_prefixo` primeiros degraus (os sem perda, ver
    `SEM_PERDA` em `gifc.formatos.base`) quebram essa monotonia, então são
    codificados um a um, em ordem, FORA da bisecção; só a cauda é bisectada.
    Vence o menor índice entre todos os que cabem, do prefixo e da cauda.

    Um degrau do prefixo que cabe encerra a busca: ele tem índice menor que
    qualquer coisa da cauda, e é o que o critério escolheria de qualquer
    forma. O pior caso (nada cabe) custa o prefixo inteiro mais a bisecção.

    Índices e total em `Attempt`/`on_progress` são os da escada inteira.
    Se nada cabe, devolve o menor tamanho medido, com `hit_target=False`.
    """
    if not escada:
        raise ValueError("A escada de qualidade está vazia.")
    total = len(escada)
    tentativas: list[Attempt[Any]] = []

    def registrar(tentativa: Attempt[Any]) -> None:
        tentativas.append(tentativa)
        if on_progress is not None:
            on_progress(tentativa, total)

    cabe_no_prefixo = False
    for i in range(n_prefixo):
        if should_cancel is not None and should_cancel():
            raise Cancelled()
        tentativa = _sondar(i, escada[i], encode_fn)
        registrar(tentativa)
        if tentativa.size_bytes <= alvo:
            cabe_no_prefixo = True
            break

    if not cabe_no_prefixo and n_prefixo < total:
        # `find_best` enxerga só a cauda, com índices locais; deslocar de
        # volta para os da escada inteira, aqui e no `encode_fn`.
        find_best(
            escada[n_prefixo:], alvo,
            lambda i, params: encode_fn(i + n_prefixo, params),
            on_progress=lambda a, _total: registrar(
                replace(a, index=a.index + n_prefixo)
            ),
            should_cancel=should_cancel,
        )

    cabem = [t for t in tentativas if t.size_bytes <= alvo]
    if cabem:
        escolhido = min(cabem, key=lambda t: t.index)
    else:
        # `min` fica com o primeiro dos empatados: o primeiro medido.
        escolhido = min(tentativas, key=lambda t: t.size_bytes)
    return SearchResult(
        index=escolhido.index,
        params=escolhido.params,
        size_bytes=escolhido.size_bytes,
        hit_target=bool(cabem),
        attempts=tentativas,
    )


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
    # O prefixo sem perda, depois dos toggles: dedupe mantém o primeiro de
    # cada grupo e o prefixo é o começo da tabela, então o dele é o começo da
    # escada. Aplicar os toggles só a ele dá o tamanho certo.
    n_prefixo = len(
        aplicar_toggles(
            modulo.TABELA[: len(modulo.SEM_PERDA)], info, toggles, modulo.NEUTROS
        )
    )
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

        resultado = buscar(
            escada, n_prefixo, alvo_da_busca, encode_fn,
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
