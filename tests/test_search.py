import math

import pytest

from gifc.formatos.gif import Params
from gifc.search import FALHOU, Cancelled, RungFailed, find_best


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


def test_busca_binaria_aceita_o_primeiro_candidato_valido_mesmo_fora_do_otimo():
    """Em curva não-monotônica, a bisecção pode aceitar um candidato válido
    que não é o menor índice teoricamente possível — ela nunca teve a
    garantia de achar o ótimo global fora do contrato de monotonicidade.

    Este teste apenas documenta que, com o outlier no índice 4 (95 bytes,
    maior que seus vizinhos), a busca ainda assim encontra ALGUM degrau que
    cabe no alvo (aqui, índice 5). NÃO testa `menor_absoluto`: nenhuma
    sondagem nesta execução deixa de bater o alvo, então `melhor_valido`
    nunca fica None e `menor_absoluto` nunca chega a ser o valor escolhido.
    Ver `test_menor_absoluto_mantem_o_menor_visto_nao_o_ultimo_sondado` para
    um teste que de fato exercita `menor_absoluto`.
    """
    # o índice 4 sai fora da curva, maior que seus vizinhos
    tamanhos = [100, 90, 80, 70, 95, 50, 40, 30, 20, 10]
    r = find_best(escada(10), 75, encoder(tamanhos))
    assert r.size_bytes <= 75
    assert r.hit_target is True


def test_menor_absoluto_mantem_o_menor_visto_nao_o_ultimo_sondado():
    """`menor_absoluto` deve guardar o MENOR tamanho já sondado, não o
    último sondado — a diferença só aparece quando nenhuma sondagem cabe
    no alvo (então `melhor_valido` fica None e `menor_absoluto` decide o
    resultado) e o último índice sondado pela bisecção não é o de menor
    tamanho entre os sondados.

    Escada de 10 degraus, alvo impossível (1 byte): a bisecção sonda, nesta
    ordem, os índices 4, 7, 8, 9 — nenhum cabe no alvo. O menor tamanho
    entre eles está no índice 8 (30), mas o ÚLTIMO sondado é o índice 9
    (35). Se a lógica de atualização virasse "sempre substitui pelo mais
    recente" (o mutante `if True:` que o review encontrou), este teste
    devolveria índice 9 / tamanho 35 em vez de índice 8 / tamanho 30.
    """
    tamanhos = [999, 999, 999, 999, 50, 999, 999, 40, 30, 35]
    r = find_best(escada(10), 1, encoder(tamanhos))
    assert r.hit_target is False
    assert r.index == 8
    assert r.size_bytes == 30


def test_nenhum_indice_e_codificado_duas_vezes():
    """A forma da busca binária nunca revisita um índice.

    Cada sondagem invalida metade do intervalo restante, garantindo
    que o mesmo índice nunca é sondado novamente.
    """
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


def test_rung_failed_e_tratado_como_grande_demais_e_a_busca_continua():
    """Um degrau que levanta RungFailed não derruba a busca inteira.

    A escada é decrescente (dá para atingir o alvo), mas o índice sondado
    primeiro (o do meio) falha. A busca deve tratar isso como "não coube"
    e seguir para os índices mais agressivos, achando um deles.
    """
    tamanhos = [100 - i * 10 for i in range(10)]  # 100, 90, ..., 10

    def fn(index: int, params: Params) -> int:
        if index == 4:  # primeiro índice sondado por escada(10), alvo qualquer
            raise RungFailed("ffmpeg explodiu")
        return tamanhos[index]

    r = find_best(escada(10), 55, fn)

    assert r.hit_target is True
    assert r.size_bytes <= 55
    assert any(a.index == 4 and a.erro == "ffmpeg explodiu" for a in r.attempts)


def test_rung_failed_preserva_a_mensagem_no_attempt():
    def fn(index: int, params: Params) -> int:
        raise RungFailed("motivo específico do degrau")

    r = find_best(escada(10), 55, fn)

    assert r.size_bytes == FALHOU
    assert r.hit_target is False
    assert all(a.erro == "motivo específico do degrau" for a in r.attempts)
    assert all(a.size_bytes == FALHOU for a in r.attempts)


def test_attempt_bem_sucedido_nao_carrega_erro():
    tamanhos = [100 - i * 10 for i in range(10)]
    r = find_best(escada(10), 55, encoder(tamanhos))
    assert all(a.erro is None for a in r.attempts)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "find_best só é correta em curvas de tamanho monótonas, e isto não vem "
        "de graça com a escada: as de PNG (degraus 0 e 1) e de WebP (degrau 0) "
        "NÃO são monótonas, porque o degrau sem perda pode ser dezenas de vezes "
        "MENOR que os com perda logo abaixo (test_formato_webp afirma essa "
        "desigualdade; ver o teste que a liga à busca, abaixo). A orquestração "
        "(compress.buscar) por isso codifica o prefixo SEM_PERDA de cada formato "
        "fora da bisecção e só bisecta a cauda. find_best em si continua com a "
        "pré-condição de monotonia: este xfail documenta o contrato, não um "
        "defeito a corrigir aqui."
    ),
)
def test_regressao_curva_nao_monotonica_encontra_menor_valido():
    """Demonstra que a bisecção falha em curva não-monotônica.

    Com tamanhos [100, 90, 80, 70, 95, 50, 40, 30, 20, 10] e alvo 75,
    o verdadeiro menor índice válido é 3 (tamanho 70).
    Mas a bisecção pode falhar porque uma sonda descarta incorretamente
    todos os índices menores quando encontra o outlier no índice 4.

    Este teste documenta que find_best depende da monotonicidade da escada.
    """
    tamanhos = [100, 90, 80, 70, 95, 50, 40, 30, 20, 10]
    r = find_best(escada(10), 75, encoder(tamanhos))
    assert r.index == 3  # O verdadeiro melhor, não um fallback


# --- o elo entre a desigualdade medida do WebP e a busca -------------------


@pytest.mark.integration
def test_webp_sem_perda_menor_que_o_lossy_e_o_que_quebra_find_best(tmp_path):
    """Liga os dois fatos que antes viviam em testes sem relação.

    1. `test_sem_perda_encolhe_arte_chapada_que_o_lossy_incharia`
       (test_formato_webp) afirma que, em arte chapada, o WebP sem perda é
       MENOS DA METADE do com perda (q90). Esta é a desigualdade que torna a
       curva de tamanho da escada não-monótona logo no degrau 0.
    2. `find_best` só é correta em curvas monótonas. Sobre a curva REAL
       medida abaixo, com um alvo que o degrau 0 cumpre, ela devolve outro
       degrau — o defeito que o `SEM_PERDA` existe para evitar.

    Se a desigualdade do item 1 deixar de valer o item 2 deixa de ser
    demonstrável e este teste avisa, em vez de a defesa apodrecer sem que
    ninguém note. E se alguém tirar o degrau 0 do `SEM_PERDA`, `buscar`
    deixa de achá-lo e a última parte falha.
    """
    from gifc.compress import buscar
    from gifc.formatos import webp
    from gifc.probe import probe
    from gifc.tools import Tools
    from tests.test_formato_webp import _captura_de_tela

    src = _captura_de_tela(tmp_path / "captura.png")
    info = probe(src)
    ferramentas = Tools("ffmpeg", "ffprobe", None, oxipng=None)
    tamanhos = [
        webp.codificar(src, tmp_path / f"t{i}.webp", p, info, ferramentas)
        for i, p in enumerate(webp.TABELA)
    ]

    # (1) a desigualdade medida: o sem perda é menos da metade do q90
    assert webp.TABELA[0].quality is None and webp.TABELA[1].quality == 90
    assert tamanhos[0] < tamanhos[1] / 2

    escada = list(webp.TABELA)

    def medido(index: int, params) -> int:
        return tamanhos[index]

    # (2) algum alvo que o degrau 0 cumpre faz a bisecção cega perdê-lo
    perdidos = [
        alvo for alvo in sorted(set(tamanhos))
        if alvo >= tamanhos[0]
        and find_best(escada, alvo, medido).index != 0
    ]
    assert perdidos, (
        "find_best achou o degrau 0 em todos os alvos: a curva medida deixou de "
        "ser não-monótona e a defesa do SEM_PERDA já não é demonstrada aqui"
    )

    # (3) a orquestração acha o degrau 0 em TODOS esses alvos, e também onde
    # a bisecção cega acertava
    n_prefixo = len(webp.SEM_PERDA)
    assert n_prefixo == 1
    for alvo in (a for a in sorted(set(tamanhos)) if a >= tamanhos[0]):
        assert buscar(escada, n_prefixo, alvo, medido).index == 0, alvo
    assert find_best(escada, perdidos[0], medido).index != 0
    assert buscar(escada, n_prefixo, perdidos[0], medido).index == 0
