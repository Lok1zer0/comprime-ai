"""Busca binária pelo melhor degrau da escada que cabe no alvo.

Não faz I/O: o encoder é injetado.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from gifc.ladder import Params

EncodeFn = Callable[[int, Params], int]
ProgressFn = Callable[["Attempt", int], None]
CancelFn = Callable[[], bool]


class Cancelled(Exception):
    """A busca foi cancelada pelo usuário."""


@dataclass(frozen=True)
class Attempt:
    index: int
    params: Params
    size_bytes: int


@dataclass
class SearchResult:
    index: int
    params: Params
    size_bytes: int
    hit_target: bool
    attempts: list[Attempt] = field(default_factory=list)


def find_best(
    ladder: Sequence[Params],
    target_bytes: int,
    encode_fn: EncodeFn,
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> SearchResult:
    """Acha o degrau de MAIOR qualidade cujo resultado cabe no alvo.

    Como o tamanho decresce ao longo da escada, isto é uma busca de lower
    bound: ~log2(n) codificações em vez de n.

    Se nenhum degrau couber, devolve o de menor tamanho encontrado, com
    `hit_target=False`.
    """
    if not ladder:
        raise ValueError("A escada de qualidade está vazia.")

    total = len(ladder)
    cache: dict[int, int] = {}
    attempts: list[Attempt] = []

    melhor_valido: Attempt | None = None
    menor_absoluto: Attempt | None = None

    lo, hi = 0, total - 1
    while lo <= hi:
        if should_cancel is not None and should_cancel():
            raise Cancelled()

        mid = (lo + hi) // 2
        params = ladder[mid]

        if mid in cache:
            tamanho = cache[mid]
        else:
            tamanho = encode_fn(mid, params)
            cache[mid] = tamanho
            tentativa = Attempt(index=mid, params=params, size_bytes=tamanho)
            attempts.append(tentativa)
            if on_progress is not None:
                on_progress(tentativa, total)

        atual = Attempt(index=mid, params=params, size_bytes=tamanho)

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
