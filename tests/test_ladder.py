"""Testes do que é comum a todos os formatos: `Toggles`, `target_width` e
`aplicar_toggles`, mais a invariante de monotonicidade de CADA escada.

Os testes específicos da escada de GIF (valores dos degraus, colunas) estão
em `tests/test_formato_gif.py`.
"""

import dataclasses
import itertools
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import pytest

from gifc import formatos
from gifc.formatos import gif
from gifc.ladder import Toggles, aplicar_toggles, target_width
from gifc.probe import Formato, ImagemInfo


def info(fps: float = 30.0, width: int = 800, formato: Formato = Formato.GIF) -> ImagemInfo:
    return ImagemInfo(
        path=Path("x"), formato=formato, width=width, height=600, fps=fps,
        frame_count=100, duration=100 / fps, size_bytes=1_000_000,
    )


TODOS = Toggles(palette=True, lossy=True, scale=True, fps=True)


# ---------- aplicar_toggles, sobre Params sintéticos ----------


@dataclasses.dataclass(frozen=True)
class SemFps:
    """Um formato hipotético que só tem `quality` e `scale_pct`."""

    quality: int
    scale_pct: int


@dataclasses.dataclass(frozen=True)
class Completo:
    """Um formato hipotético com as quatro colunas que os toggles conhecem."""

    colors: int
    lossy: int
    scale_pct: int
    fps: float | None


def test_aplicar_toggles_ignora_coluna_que_o_formato_nao_tem():
    tabela = [SemFps(90, 100), SemFps(80, 100), SemFps(80, 70), SemFps(60, 50)]

    # `fps`, `lossy` e `palette` desligados: nenhuma dessas colunas existe.
    escada = aplicar_toggles(tabela, info(), Toggles(False, False, True, False))

    assert escada == tabela


def test_aplicar_toggles_so_toca_as_colunas_que_o_formato_tem():
    """A coluna `scale_pct` existe e é controlada; `quality` não tem toggle
    e nunca é alterada."""
    tabela = [SemFps(90, 100), SemFps(80, 100), SemFps(80, 70), SemFps(60, 50)]

    escada = aplicar_toggles(tabela, info(), Toggles(True, True, False, True))

    assert escada == [SemFps(90, 100), SemFps(80, 100), SemFps(60, 100)]


def test_aplicar_toggles_nao_reduz_fps_em_formato_sem_a_coluna():
    """O limite 'nunca aumentar o framerate' só vale se há coluna `fps`."""
    tabela = [SemFps(90, 100), SemFps(80, 100)]
    assert aplicar_toggles(tabela, info(fps=1.0), TODOS) == tabela


def test_aplicar_toggles_deduplica_preservando_a_ordem():
    tabela = [
        Completo(256, 0, 100, None),
        Completo(256, 30, 100, None),
        Completo(128, 30, 100, None),
        Completo(128, 60, 70, None),
        Completo(64, 60, 70, None),
    ]
    # Sem lossy: degraus 0 e 1 viram o mesmo, e 2 e 3 só diferem em escala.
    escada = aplicar_toggles(tabela, info(), Toggles(True, False, True, True))

    assert escada == [
        Completo(256, 0, 100, None),
        Completo(128, 0, 100, None),
        Completo(128, 0, 70, None),
        Completo(64, 0, 70, None),
    ]


def test_dedup_fica_com_o_primeiro_de_cada_grupo_e_nao_reordena():
    a = Completo(256, 0, 100, None)
    b = Completo(128, 0, 100, None)
    c = Completo(64, 0, 100, None)
    # Repetições não consecutivas: o segundo `a` some, a ordem de b e c fica.
    assert aplicar_toggles([a, b, a, c, b], info(), TODOS) == [a, b, c]


def test_tudo_desligado_colapsa_para_um_degrau():
    tabela = [
        Completo(256, 0, 100, None),
        Completo(128, 45, 100, None),
        Completo(64, 100, 85, 20.0),
        Completo(32, 200, 50, 10.0),
    ]
    escada = aplicar_toggles(tabela, info(), Toggles(False, False, False, False))
    assert escada == [Completo(256, 0, 100, None)]


def test_aplicar_toggles_nunca_aumenta_o_framerate():
    tabela = [Completo(256, 0, 100, 30.0), Completo(256, 0, 100, 12.0)]
    # Origem a 20 fps: 30 >= 20 vira o original (None); 12 < 20 fica.
    assert aplicar_toggles(tabela, info(fps=20.0), TODOS) == [
        Completo(256, 0, 100, None),
        Completo(256, 0, 100, 12.0),
    ]


def test_aplicar_toggles_aceita_qualquer_sequencia_e_devolve_lista():
    tabela = (SemFps(90, 100),)
    assert isinstance(aplicar_toggles(tabela, info(), TODOS), list)


@pytest.mark.parametrize(
    "largura,pct,esperado",
    [
        (800, 100, 800),
        (800, 85, 680),
        (800, 70, 560),
        (800, 50, 400),
        (801, 85, 680),   # arredonda para par
        (10, 50, 16),     # respeita o mínimo
        (33, 100, 34),    # ímpar vira par
    ],
)
def test_target_width(largura, pct, esperado):
    assert target_width(largura, pct) == esperado


# ---------- invariante de monotonicidade, por formato ----------


def _valor(v: Any) -> float:
    """`None` é o maior valor possível (fps original, sem limite)."""
    return float("inf") if v is None else v


def verificar_monotonicidade(modulo: Any, rotulo: str) -> None:
    """Cada degrau de `modulo.TABELA` é fracamente mais agressivo que o
    anterior em TODOS os campos do `Params` do formato.

    Os sentidos vêm de `modulo.SENTIDOS`. A declaração tem de cobrir
    exatamente os campos do `Params`: um campo sem sentido declarado FALHA
    aqui em vez de ser ignorado — senão uma coluna nova de um formato novo
    passaria despercebida e a próxima escada não-monotônica entraria.
    """
    campos = {f.name for f in dataclasses.fields(modulo.Params)}
    declarados = set(modulo.SENTIDOS)

    faltando = campos - declarados
    assert not faltando, (
        f"[{rotulo}] campo(s) sem sentido declarado em SENTIDOS: "
        f"{sorted(faltando)}"
    )
    sobrando = declarados - campos
    assert not sobrando, (
        f"[{rotulo}] SENTIDOS declara campo(s) que o Params não tem: "
        f"{sorted(sobrando)}"
    )
    invalidos = {c: s for c, s in modulo.SENTIDOS.items() if s not in ("maior", "menor")}
    assert not invalidos, (
        f"[{rotulo}] sentido inválido (use 'maior' ou 'menor'): {invalidos}"
    )

    _assert_agressividade_nao_diminui(modulo.TABELA, modulo.SENTIDOS, rotulo)


def _assert_agressividade_nao_diminui(
    escada: Sequence[Any], sentidos: dict[str, str], rotulo: str
) -> None:
    """A comparação par a par, sobre uma escada qualquer (a própria TABELA
    ou a saída de `aplicar_toggles`). Todo campo é comparado: um campo sem
    sentido levanta KeyError em vez de ser pulado.

    É o que torna o tamanho codificado monotonicamente não-crescente e,
    portanto, a busca binária de lower bound correta.
    """
    for i, (anterior, atual) in enumerate(zip(escada, escada[1:])):
        for campo in (f.name for f in dataclasses.fields(anterior)):
            sentido = sentidos[campo]
            a, b = _valor(getattr(anterior, campo)), _valor(getattr(atual, campo))
            if sentido == "maior":
                assert b >= a, (
                    f"[{rotulo}] Rung {i} -> {i+1}: {campo} diminuiu "
                    f"({getattr(anterior, campo)} -> {getattr(atual, campo)}), "
                    f"mas mais alto é mais agressivo"
                )
            else:
                assert b <= a, (
                    f"[{rotulo}] Rung {i} -> {i+1}: {campo} aumentou "
                    f"({getattr(anterior, campo)} -> {getattr(atual, campo)}), "
                    f"mas mais baixo é mais agressivo"
                )


# Formatos que existem em `Formato` mas ainda não têm módulo. Cada tarefa que
# registra um formato DEVE tirá-lo daqui — `test_pendentes_nao_inclui_...`
# falha se alguém esquecer, para a invariante nunca ser pulada em silêncio.
PENDENTES = {Formato.PNG, Formato.JPEG, Formato.WEBP}


@pytest.mark.parametrize("formato", list(Formato))
def test_invariante_de_monotonicidade_por_formato(formato):
    """Cada degrau é fracamente mais agressivo que o anterior em TODAS as
    dimensões daquele formato. Sem isso o tamanho codificado não é
    monotônico e a bisecção pode descartar uma região que continha um
    resultado de qualidade melhor."""
    if formato in PENDENTES:
        pytest.skip(f"{formato.name} ainda não tem módulo em gifc.formatos")
    verificar_monotonicidade(formatos.para(formato), formato.name)


def test_pendentes_nao_inclui_formato_registrado():
    assert not (PENDENTES & formatos.registrados()), (
        "Formato registrado ainda listado em PENDENTES: o teste de "
        "invariante estaria sendo pulado para ele."
    )


def test_todo_formato_ou_esta_registrado_ou_esta_pendente():
    assert set(Formato) == formatos.registrados() | PENDENTES


@pytest.mark.parametrize("formato", sorted(formatos.registrados()))
def test_params_e_hashavel_e_congelado(formato):
    modulo = formatos.para(formato)
    p = modulo.TABELA[0]
    assert len({p, dataclasses.replace(p)}) == 1
    campo = next(iter(modulo.SENTIDOS))
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(p, campo, getattr(p, campo))


@pytest.mark.parametrize("formato", sorted(formatos.registrados()))
def test_tabela_so_tem_instancias_do_params_do_formato(formato):
    modulo = formatos.para(formato)
    assert modulo.TABELA, "tabela vazia"
    assert all(type(p) is modulo.Params for p in modulo.TABELA)


@pytest.mark.parametrize(
    "toggles",
    [
        Toggles(p, l, s, f)
        for p, l, s, f in itertools.product([True, False], repeat=4)
    ],
    ids=lambda t: f"palette={t.palette},lossy={t.lossy},scale={t.scale},fps={t.fps}",
)
@pytest.mark.parametrize("fps_origem", [10, 12, 15, 20, 30])
@pytest.mark.parametrize("formato", sorted(formatos.registrados()))
def test_aplicar_toggles_preserva_monotonicidade_para_qualquer_combinacao(
    formato, toggles, fps_origem
):
    """A busca binária em `find_best` depende da SAÍDA de `aplicar_toggles`
    ser monotonicamente não-crescente, não só da tabela crua. Cada uma das
    16 combinações de toggles (2**4) precisa preservar o invariante, para
    alguns fps de origem representativos (incluindo valores baixos que
    colapsam mais degraus de fps em None)."""
    modulo = formatos.para(formato)
    escada = aplicar_toggles(
        modulo.TABELA, info(fps=float(fps_origem), formato=formato), toggles
    )
    _assert_agressividade_nao_diminui(
        escada, modulo.SENTIDOS, f"{formato.name}, {toggles}, fps={fps_origem}"
    )


# ---------- a própria verificação tem de ser capaz de falhar ----------
#
# Sem estes testes, `verificar_monotonicidade` poderia estar pulando campos
# ou aceitando tudo e a invariante acima passaria para sempre sem provar nada.


def _falso(**mudancas: Any) -> SimpleNamespace:
    """Um 'módulo de formato' sintético, partindo do GIF real."""
    base = dict(Params=gif.Params, TABELA=gif.TABELA, SENTIDOS=dict(gif.SENTIDOS))
    base.update(mudancas)
    return SimpleNamespace(**base)


def test_o_verificador_aceita_o_gif_real():
    verificar_monotonicidade(_falso(), "controle")


@pytest.mark.parametrize("campo", list(gif.SENTIDOS))
def test_formato_que_esquece_de_declarar_um_campo_falha(campo):
    sentidos = {c: s for c, s in gif.SENTIDOS.items() if c != campo}
    with pytest.raises(AssertionError, match=campo):
        verificar_monotonicidade(_falso(SENTIDOS=sentidos), "teste")


def test_sentido_declarado_para_campo_inexistente_falha():
    sentidos = {**gif.SENTIDOS, "oxipng": "maior"}
    with pytest.raises(AssertionError, match="oxipng"):
        verificar_monotonicidade(_falso(SENTIDOS=sentidos), "teste")


def test_sentido_com_valor_invalido_falha():
    sentidos = {**gif.SENTIDOS, "lossy": "up"}
    with pytest.raises(AssertionError, match="inválido"):
        verificar_monotonicidade(_falso(SENTIDOS=sentidos), "teste")


@pytest.mark.parametrize("campo", list(gif.SENTIDOS))
def test_sentido_declarado_ao_contrario_falha_na_tabela_real(campo):
    """O sentido é de fato lido: inverter qualquer um quebra a tabela do GIF."""
    invertido = "menor" if gif.SENTIDOS[campo] == "maior" else "maior"
    sentidos = {**gif.SENTIDOS, campo: invertido}
    with pytest.raises(AssertionError, match=campo):
        verificar_monotonicidade(_falso(SENTIDOS=sentidos), "teste")


@pytest.mark.parametrize(
    "campo,anterior,atual",
    [
        ("colors", dict(colors=64), dict(colors=128)),          # menor: subiu
        ("lossy", dict(lossy=100), dict(lossy=80)),             # maior: caiu
        ("scale_pct", dict(scale_pct=70), dict(scale_pct=85)),  # menor: subiu
        ("fps", dict(fps=12.0), dict(fps=15.0)),                # menor: subiu
        ("fps", dict(fps=12.0), dict(fps=None)),                # None é o maior
    ],
)
def test_tabela_que_regride_em_um_campo_falha(campo, anterior, atual):
    base = dict(colors=64, lossy=100, scale_pct=70, fps=12.0)
    tabela = (gif.Params(**{**base, **anterior}), gif.Params(**{**base, **atual}))
    with pytest.raises(AssertionError, match=campo):
        verificar_monotonicidade(_falso(TABELA=tabela), "teste")


def test_de_none_para_numero_e_o_caminho_normal_do_fps():
    base = dict(colors=64, lossy=100, scale_pct=70)
    tabela = (gif.Params(**base, fps=None), gif.Params(**base, fps=12.0))
    verificar_monotonicidade(_falso(TABELA=tabela), "teste")
