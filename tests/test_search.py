import math

import pytest

from gifc.ladder import Params
from gifc.search import Cancelled, find_best


def escada(n: int) -> list[Params]:
    return [Params(colors=256, lossy=i, scale_pct=100, fps=None) for i in range(n)]


def encoder(tamanhos: list[int], registro: list[int] | None = None):
    def fn(index: int, params: Params) -> int:
        if registro is not None:
            registro.append(index)
        return tamanhos[index]
    return fn


def test_acha_o_menor_indice_valido():
    # tamanhos 100, 90, ..., 10 — alvo 55 aceita o índice 5 (50) em diante
    tamanhos = [100 - i * 10 for i in range(10)]
    r = find_best(escada(10), 55, encoder(tamanhos))
    assert r.index == 5
    assert r.size_bytes == 50
    assert r.hit_target is True


def test_usa_busca_binaria_e_nao_forca_bruta():
    tamanhos = [100 - i for i in range(20)]
    registro: list[int] = []
    find_best(escada(20), 85, encoder(tamanhos, registro))
    assert len(registro) <= math.ceil(math.log2(21))


def test_degrau_zero_ja_valido_nao_desperdica_qualidade():
    tamanhos = [10 - i for i in range(10)]
    r = find_best(escada(10), 999, encoder(tamanhos))
    assert r.index == 0
    assert r.hit_target is True


def test_nenhum_degrau_valido_devolve_o_menor_absoluto():
    tamanhos = [100 - i * 5 for i in range(10)]  # menor é 55
    r = find_best(escada(10), 10, encoder(tamanhos))
    assert r.hit_target is False
    assert r.size_bytes == 55
    assert r.index == 9


def test_curva_nao_monotonica_nao_piora_o_resultado():
    # o índice 4 sai fora da curva, maior que seus vizinhos
    tamanhos = [100, 90, 80, 70, 95, 50, 40, 30, 20, 10]
    r = find_best(escada(10), 75, encoder(tamanhos))
    assert r.size_bytes <= 75
    assert r.hit_target is True


def test_memoiza_cada_indice_no_maximo_uma_vez():
    tamanhos = [100 - i * 10 for i in range(10)]
    registro: list[int] = []
    find_best(escada(10), 55, encoder(tamanhos, registro))
    assert len(registro) == len(set(registro))


def test_escada_de_um_degrau():
    r = find_best(escada(1), 50, encoder([40]))
    assert r.index == 0
    assert r.hit_target is True


def test_escada_vazia_levanta_erro():
    with pytest.raises(ValueError):
        find_best([], 50, encoder([]))


def test_progresso_e_chamado_para_cada_tentativa():
    tamanhos = [100 - i * 10 for i in range(10)]
    vistos: list[int] = []
    find_best(
        escada(10), 55, encoder(tamanhos),
        on_progress=lambda a, total: vistos.append(a.index),
    )
    assert vistos
    assert len(vistos) == len(set(vistos))


def test_cancelamento_interrompe_a_busca():
    tamanhos = [100 - i * 10 for i in range(10)]
    with pytest.raises(Cancelled):
        find_best(escada(10), 55, encoder(tamanhos), should_cancel=lambda: True)


def test_attempts_registra_todas_as_tentativas():
    tamanhos = [100 - i * 10 for i in range(10)]
    r = find_best(escada(10), 55, encoder(tamanhos))
    assert len(r.attempts) >= 1
    assert all(a.size_bytes == tamanhos[a.index] for a in r.attempts)
