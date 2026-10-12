"""Orquestração: de um GIF de entrada ao arquivo comprimido final."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from gifc import formatos
from gifc.formatos.base import EncodeError
from gifc.ladder import Toggles, aplicar_toggles
from gifc.probe import Formato, ImagemInfo, probe
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


def montar_escada(
    formato: Formato, info: ImagemInfo, toggles: Toggles, tools: Tools
) -> tuple[list[Any], int]:
    """A escada que se percorre para `formato`, e quantos dos seus primeiros
    degraus são o prefixo sem perda (`SEM_PERDA`) que a bisecção não pode
    atravessar.

    O prefixo é calculado sobre a MESMA transformação da escada inteira (os
    toggles efetivos e as duas deduplicações, a dos toggles e a dos degraus
    equivalentes nesta máquina): elas mantêm o primeiro de cada grupo e o
    prefixo é o começo da tabela, então o dele é o começo da escada.
    """
    modulo = formatos.para(formato)
    toggles = formatos.toggles_efetivos(formato, toggles, tools)
    escada = formatos.sem_equivalentes(
        formato, aplicar_toggles(modulo.TABELA, info, toggles, modulo.NEUTROS),
        tools,
    )
    prefixo = formatos.sem_equivalentes(
        formato,
        aplicar_toggles(
            modulo.TABELA[: len(modulo.SEM_PERDA)], info, toggles, modulo.NEUTROS
        ),
        tools,
    )
    return escada, len(prefixo)


def _sondar(
    index: int, params: Any, encode_fn: Callable[[int, Any], int]
) -> Attempt[Any]:
    """Uma codificação, com `RungFailed` virando um `Attempt` de tamanho
    `FALHOU`: o mesmo tratamento que `find_best` dá a cada sonda sua."""
    try:
        return Attempt(index, params, encode_fn(index, params))
    except RungFailed as exc:
        return Attempt(index, params, FALHOU, exc.mensagem)


def _sondar_prefixo(
    escada: Sequence[Any],
    n_prefixo: int,
    encode_fn: Callable[[int, Any], int],
    should_cancel: CancelFn | None,
) -> Iterator[Attempt[Any]]:
    """Sonda os degraus do prefixo sem perda, em ordem e um a um. É preguiçoso:
    quem consome decide se o próximo chega a ser codificado."""
    for i in range(n_prefixo):
        if should_cancel is not None and should_cancel():
            raise Cancelled()
        yield _sondar(i, escada[i], encode_fn)


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
    for tentativa in _sondar_prefixo(escada, n_prefixo, encode_fn, should_cancel):
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


def medir_sem_perda(
    escada: Sequence[Any],
    n_prefixo: int,
    encode_fn: Callable[[int, Any], int],
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> SearchResult[Any] | None:
    """O MENOR resultado entre os degraus sem perda de `escada`, ou `None`
    se ela não tem nenhum.

    É o regime de uma entrada que JÁ cabe no alvo: aí o pedido é "menor de
    graça", não "menor a qualquer custo", e só o que não perde nada entra em
    consideração. Todos os do prefixo são medidos (são equivalentes em
    qualidade, então vale o menor tamanho, não o primeiro) e nenhum com perda.
    `hit_target` aqui só diz que algo foi medido; quem chama o recalcula
    contra o alvo do usuário.
    """
    total = len(escada)
    tentativas: list[Attempt[Any]] = []
    for tentativa in _sondar_prefixo(escada, n_prefixo, encode_fn, should_cancel):
        tentativas.append(tentativa)
        if on_progress is not None:
            on_progress(tentativa, total)
    if not tentativas:
        return None
    # `min` fica com o primeiro dos empatados: o de menor índice.
    menor = min(tentativas, key=lambda t: t.size_bytes)
    return SearchResult(
        index=menor.index,
        params=menor.params,
        size_bytes=menor.size_bytes,
        hit_target=menor.size_bytes != FALHOU,
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
    escada, n_prefixo = montar_escada(saida, info, toggles, tools)
    # Dois regimes, separados pelo alvo do USUÁRIO contra a entrada:
    #
    # - A entrada NÃO cabe no alvo: percorre-se a escada o quanto for preciso
    #   (`buscar`). O alvo é, então, menor que a entrada.
    # - A entrada JÁ cabe: o melhor resultado que cabe no alvo é a própria
    #   entrada, e "menor" passa a significar "menor de graça". Só os degraus
    #   sem perda são considerados (`medir_sem_perda`), e se nenhum encolhe o
    #   arquivo a resposta é `already_optimized`. Sem este piso, o alvo padrão
    #   de 10 MB faria o PNG que a própria ferramenta gerou ser quantizado a
    #   128 cores só para economizar 2 KB num arquivo que já cabia 2 mil vezes.
    #   (O contrário também valia: sem limitar a busca, o degrau sem perda do
    #   WebP, maior que uma foto, "cabia" no alvo e fazia o original voltar.)
    entrada_cabe_no_alvo = info.size_bytes <= target_bytes

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

        resultado: SearchResult[Any] | None
        if entrada_cabe_no_alvo:
            resultado = medir_sem_perda(
                escada, n_prefixo, encode_fn,
                on_progress=on_progress, should_cancel=should_cancel,
            )
        else:
            resultado = buscar(
                escada, n_prefixo, target_bytes, encode_fn,
                on_progress=on_progress, should_cancel=should_cancel,
            )

        if resultado is not None and resultado.size_bytes == FALHOU:
            raise CompressError(
                f"Nenhuma configuração conseguiu comprimir {src.name}. "
                f"O arquivo pode estar corrompido."
            )

        # Arquivos já muito otimizados podem crescer ao serem reescritos, e um
        # WebP pode ficar maior que o PNG/JPEG de origem. Em ambos os casos o
        # original é copiado, no formato (e na extensão) dele. Também é o caso
        # de uma entrada que já cabe no alvo e não tem degrau sem perda
        # (`resultado is None`: GIF e JPEG) ou cujo sem perda não a encolhe.
        if resultado is None or resultado.size_bytes >= info.size_bytes:
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
                attempts=[] if resultado is None else resultado.attempts,
            )

        vencedor = _try_path(tmpdir, resultado.index, saida)
        dst = unique_output_path(src, saida)
        shutil.move(str(vencedor), str(dst))
        return CompressResult(
            src=src, dst=dst,
            original_bytes=info.size_bytes,
            final_bytes=dst.stat().st_size,
            params=resultado.params,
            hit_target=resultado.size_bytes <= target_bytes,
            already_optimized=False,
            formato_entrada=info.formato,
            formato_saida=saida,
            attempts=resultado.attempts,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
