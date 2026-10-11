"""Busca binária pelo melhor degrau da escada que cabe no alvo.

Não faz I/O: o encoder é injetado.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Generic, TypeVar

# Tipo dos parâmetros de um degrau. A busca não olha dentro deles: só os
# repassa ao codificador, então vale para o `Params` de qualquer formato.
P = TypeVar("P")

EncodeFn = Callable[[int, P], int]
ProgressFn = Callable[["Attempt[P]", int], None]
CancelFn = Callable[[], bool]

# Sentinela de tamanho para um degrau que falhou: a busca o trata como
# "grande demais" e segue para degraus mais agressivos, em vez de abortar
# o arquivo inteiro. O motivo real da falha vai em `Attempt.erro`.
FALHOU = sys.maxsize


class Cancelled(Exception):
    """A busca foi cancelada pelo usuário."""


class RungFailed(Exception):
    """O codificador falhou neste degrau. A busca o trata como grande demais."""

    def __init__(self, mensagem: str) -> None:
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass(frozen=True)
class Attempt(Generic[P]):
    index: int
    params: P
    size_bytes: int
    erro: str | None = None


@dataclass
class SearchResult(Generic[P]):
    index: int
    params: P
    size_bytes: int
    hit_target: bool
    attempts: list[Attempt[P]] = field(default_factory=list)


def find_best(
    ladder: Sequence[P],
    target_bytes: int,
    encode_fn: EncodeFn[P],
    on_progress: ProgressFn[P] | None = None,
    should_cancel: CancelFn | None = None,
) -> SearchResult[P]:
    """Acha o degrau de MAIOR qualidade cujo resultado cabe no alvo.

    A escada garante que cada degrau é fracamente mais agressivo que o
    anterior em todas as dimensões, o que torna o tamanho codificado
    monotonicamente não-crescente. Isto permite usar uma busca de lower
    bound: ~log2(n) codificações em vez de n.

    O registro de `menor_absoluto` continua necessário para proteger contra
    variações do encoder que possam produzir um arquivo localmente maior.

    Se nenhum degrau couber, devolve o de menor tamanho encontrado, com
    `hit_target=False`.
    """
    if not ladder:
        raise ValueError("A escada de qualidade está vazia.")

    total = len(ladder)
    attempts: list[Attempt[P]] = []

    melhor_valido: Attempt[P] | None = None
    menor_absoluto: Attempt[P] | None = None

    lo, hi = 0, total - 1
    while lo <= hi:
        if should_cancel is not None and should_cancel():
            raise Cancelled()

        mid = (lo + hi) // 2
        params = ladder[mid]

        try:
            tamanho = encode_fn(mid, params)
            erro = None
        except RungFailed as exc:
            # O degrau não produziu resultado: tratado como "grande demais"
            # para que a busca continue, mas o motivo é preservado no
            # Attempt em vez de virar um tamanho fictício sem explicação.
            tamanho = FALHOU
            erro = exc.mensagem

        tentativa = Attempt(index=mid, params=params, size_bytes=tamanho, erro=erro)
        attempts.append(tentativa)
        if on_progress is not None:
            on_progress(tentativa, total)

        atual = tentativa

        if menor_absoluto is None or tamanho < menor_absoluto.size_bytes:
            menor_absoluto = atual

        if tamanho <= target_bytes:
            # Candidato. Entre os válidos preferimos o de menor índice,
            # que é o de maior qualidade.
            if melhor_valido is None or mid < melhor_valido.index:
                melhor_valido = atual
            hi = mid - 1
        else:
            lo = mid + 1

    escolhido = melhor_valido or menor_absoluto
    assert escolhido is not None  # a escada não é vazia, logo houve tentativa

    return SearchResult(
        index=escolhido.index,
        params=escolhido.params,
        size_bytes=escolhido.size_bytes,
        hit_target=melhor_valido is not None,
        attempts=attempts,
    )
