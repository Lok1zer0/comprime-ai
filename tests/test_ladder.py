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
from gifc.ladder import ALAVANCAS, Toggles, aplicar_toggles, target_width
from gifc.probe import Formato, ImagemInfo


def info(fps: float = 30.0, width: int = 800, formato: Formato = Formato.GIF) -> ImagemInfo:
    return ImagemInfo(
        path=Path("x"), formato=formato, width=width, height=600, fps=fps,
        frame_count=100, duration=100 / fps, size_bytes=1_000_000,
    )


TODOS = Toggles(palette=True, lossy=True, scale=True, fps=True)

# Valores neutros dos formatos sintéticos abaixo (o que cada formato real
# declara em seu NEUTROS).
NEUTROS_SEM_FPS = {"scale_pct": 100}
NEUTROS_COMPLETO = {"colors": 256, "lossy": 0, "scale_pct": 100, "fps": None}


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
    escada = aplicar_toggles(tabela, info(), Toggles(False, False, True, False), NEUTROS_SEM_FPS)

    assert escada == tabela


def test_aplicar_toggles_so_toca_as_colunas_que_o_formato_tem():
    """A coluna `scale_pct` existe e é controlada; `quality` não tem toggle
    e nunca é alterada."""
    tabela = [SemFps(90, 100), SemFps(80, 100), SemFps(80, 70), SemFps(60, 50)]

    escada = aplicar_toggles(tabela, info(), Toggles(True, True, False, True), NEUTROS_SEM_FPS)

    assert escada == [SemFps(90, 100), SemFps(80, 100), SemFps(60, 100)]


def test_aplicar_toggles_nao_reduz_fps_em_formato_sem_a_coluna():
    """O limite 'nunca aumentar o framerate' só vale se há coluna `fps`."""
    tabela = [SemFps(90, 100), SemFps(80, 100)]
    assert aplicar_toggles(tabela, info(fps=1.0), TODOS, NEUTROS_SEM_FPS) == tabela


def test_aplicar_toggles_deduplica_preservando_a_ordem():
    tabela = [
        Completo(256, 0, 100, None),
        Completo(256, 30, 100, None),
        Completo(128, 30, 100, None),
        Completo(128, 60, 70, None),
        Completo(64, 60, 70, None),
    ]
    # Sem lossy: degraus 0 e 1 viram o mesmo, e 2 e 3 só diferem em escala.
    escada = aplicar_toggles(tabela, info(), Toggles(True, False, True, True), NEUTROS_COMPLETO)

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
    assert aplicar_toggles([a, b, a, c, b], info(), TODOS, NEUTROS_COMPLETO) == [a, b, c]


def test_tudo_desligado_colapsa_para_um_degrau():
    tabela = [
        Completo(256, 0, 100, None),
        Completo(128, 45, 100, None),
        Completo(64, 100, 85, 20.0),
        Completo(32, 200, 50, 10.0),
    ]
    escada = aplicar_toggles(tabela, info(), Toggles(False, False, False, False), NEUTROS_COMPLETO)
    assert escada == [Completo(256, 0, 100, None)]


def test_aplicar_toggles_nunca_aumenta_o_framerate():
    tabela = [Completo(256, 0, 100, 30.0), Completo(256, 0, 100, 12.0)]
    # Origem a 20 fps: 30 >= 20 vira o original (None); 12 < 20 fica.
    assert aplicar_toggles(tabela, info(fps=20.0), TODOS, NEUTROS_COMPLETO) == [
        Completo(256, 0, 100, None),
        Completo(256, 0, 100, 12.0),
    ]


def test_aplicar_toggles_aceita_qualquer_sequencia_e_devolve_lista():
    tabela = (SemFps(90, 100),)
    assert isinstance(aplicar_toggles(tabela, info(), TODOS, NEUTROS_SEM_FPS), list)


def test_o_valor_neutro_vem_do_formato_e_nao_do_aplicar_toggles():
    """O mesmo toggle desligado produz valores diferentes conforme o
    NEUTROS do formato: 256 cores num, `None` ("não quantizar") noutro."""
    tabela = [Completo(64, 0, 100, None)]
    desligado = Toggles(False, True, True, True)

    como_gif = aplicar_toggles(tabela, info(), desligado, NEUTROS_COMPLETO)
    como_png = aplicar_toggles(
        tabela, info(), desligado, {**NEUTROS_COMPLETO, "colors": None}
    )

    assert como_gif == [Completo(256, 0, 100, None)]
    assert como_png == [Completo(None, 0, 100, None)]


def test_aplicar_toggles_recusa_coluna_controlada_sem_neutro():
    """Mesmo com o toggle LIGADO: a falha tem de aparecer sempre, não só no
    dia em que alguém desliga a alavanca."""
    tabela = [Completo(64, 0, 100, None)]
    sem_colors = {k: v for k, v in NEUTROS_COMPLETO.items() if k != "colors"}
    with pytest.raises(ValueError, match="colors"):
        aplicar_toggles(tabela, info(), TODOS, sem_colors)


def test_aplicar_toggles_nao_exige_neutro_de_coluna_sem_toggle():
    """`quality` não é controlada por nenhum toggle: dispensa NEUTROS."""
    tabela = [SemFps(90, 100)]
    assert aplicar_toggles(tabela, info(), TODOS, NEUTROS_SEM_FPS) == tabela


@pytest.mark.parametrize(
    "largura,pct,esperado",
    [
        (800, 100, 800),
        (800, 85, 680),
        (800, 70, 560),
        (800, 50, 400),
        (801, 85, 680),   # 680,85 trunca para 680, que já é par
        (10, 50, 16),     # respeita o mínimo
        (33, 100, 34),    # ímpar sobe para o par seguinte
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


def verificar_neutros(modulo: Any, rotulo: str) -> None:
    """`NEUTROS` cobre exatamente as colunas controladas por toggle e cada
    valor é o extremo MENOS agressivo da coluna.

    - Coluna do `Params` ligada a um toggle (`ladder.ALAVANCAS`) sem entrada
      em `NEUTROS`: falha (senão desligar a alavanca não teria o que fazer).
    - Entrada de `NEUTROS` para coluna que o `Params` não tem: falha.
    - Neutro que não é o extremo não-agressivo de toda a coluna: falha.
      É o erro que isto evita: um PNG com `colors=256` como neutro
      QUANTIZARIA ao desligar a paleta (o PNG deve não quantizar), e aí
      desligar a alavanca pioraria o arquivo.
    """
    campos = {f.name for f in dataclasses.fields(modulo.Params)}
    controladas = campos & set(ALAVANCAS.values())

    faltando = controladas - set(modulo.NEUTROS)
    assert not faltando, (
        f"[{rotulo}] coluna(s) controlada(s) por toggle sem valor em NEUTROS: "
        f"{sorted(faltando)}"
    )
    sobrando = set(modulo.NEUTROS) - campos
    assert not sobrando, (
        f"[{rotulo}] NEUTROS declara coluna(s) que o Params não tem: "
        f"{sorted(sobrando)}"
    )

    for coluna, neutro in modulo.NEUTROS.items():
        sentido = modulo.SENTIDOS[coluna]
        for i, degrau in enumerate(modulo.TABELA):
            v = getattr(degrau, coluna)
            if sentido == "menor":
                ok = _valor(neutro) >= _valor(v)
            else:
                ok = _valor(neutro) <= _valor(v)
            assert ok, (
                f"[{rotulo}] NEUTROS[{coluna!r}]={neutro!r} não é o extremo "
                f"menos agressivo: o degrau {i} tem {v!r}"
            )


# Formatos que existem em `Formato` mas ainda não têm módulo. Cada tarefa que
# registra um formato DEVE tirá-lo daqui — `test_pendentes_nao_inclui_...`
# falha se alguém esquecer, para a invariante nunca ser pulada em silêncio.
PENDENTES: set[Formato] = set()


@pytest.mark.parametrize("formato", list(Formato))
def test_invariante_de_monotonicidade_por_formato(formato):
    """Cada degrau é fracamente mais agressivo que o anterior em TODAS as
    dimensões daquele formato. Sem isso o tamanho codificado não é
    monotônico e a bisecção pode descartar uma região que continha um
    resultado de qualidade melhor."""
    if formato in PENDENTES:
        pytest.skip(f"{formato.name} ainda não tem módulo em gifc.formatos")
    verificar_monotonicidade(formatos.para(formato), formato.name)


@pytest.mark.parametrize("formato", list(Formato))
def test_neutros_por_formato(formato):
    if formato in PENDENTES:
        pytest.skip(f"{formato.name} ainda não tem módulo em gifc.formatos")
    verificar_neutros(formatos.para(formato), formato.name)


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
        modulo.TABELA, info(fps=float(fps_origem), formato=formato), toggles,
        modulo.NEUTROS,
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
    base = dict(
        Params=gif.Params, TABELA=gif.TABELA,
        SENTIDOS=dict(gif.SENTIDOS), NEUTROS=dict(gif.NEUTROS),
    )
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


def test_o_verificador_de_neutros_aceita_o_gif_real():
    verificar_neutros(_falso(), "controle")


@pytest.mark.parametrize("campo", list(gif.NEUTROS))
def test_formato_sem_neutro_para_coluna_controlada_falha(campo):
    neutros = {c: v for c, v in gif.NEUTROS.items() if c != campo}
    with pytest.raises(AssertionError, match=campo):
        verificar_neutros(_falso(NEUTROS=neutros), "teste")


def test_neutro_para_coluna_inexistente_falha():
    neutros = {**gif.NEUTROS, "oxipng": 0}
    with pytest.raises(AssertionError, match="oxipng"):
        verificar_neutros(_falso(NEUTROS=neutros), "teste")


@pytest.mark.parametrize(
    "campo,errado",
    [("colors", 64), ("lossy", 100), ("scale_pct", 85), ("fps", 12.0)],
)
def test_neutro_que_nao_e_o_extremo_menos_agressivo_falha(campo, errado):
    neutros = {**gif.NEUTROS, campo: errado}
    with pytest.raises(AssertionError, match=campo):
        verificar_neutros(_falso(NEUTROS=neutros), "teste")


def test_de_none_para_numero_e_o_caminho_normal_do_fps():
    base = dict(colors=64, lossy=100, scale_pct=70)
    tabela = (gif.Params(**base, fps=None), gif.Params(**base, fps=12.0))
    verificar_monotonicidade(_falso(TABELA=tabela), "teste")


# ---------- SEM_PERDA: o prefixo que a bisecção não pode atravessar ----------
#
# A bisecção só é correta numa curva de tamanho monótona, e um degrau sem
# perda a quebra (em arte chapada ele é MUITO menor que os com perda logo
# abaixo). Cada formato declara esse prefixo em `SEM_PERDA`; a verificação
# abaixo é o equivalente, para ele, do que `verificar_neutros` é para o
# `NEUTROS`: um formato que esquece o campo, ou o declara incoerente, falha.


def verificar_sem_perda(modulo: Any, rotulo: str) -> None:
    """`SEM_PERDA` existe, é um prefixo da TABELA e deixa uma cauda.

    - Ausente: falha. Sem o campo o `compress_file` bisectaria a escada
      inteira e o defeito voltaria em silêncio para o próximo formato.
    - Índice fora da TABELA: falha.
    - Que não seja exatamente `(0, 1, ..., n-1)`: falha. O `compress_file`
      só sabe tratar um PREFIXO.
    - Sem nenhum degrau sobrando para a cauda: falha.
    - Um degrau declarado que um toggle altera: falha. Um degrau sem perda
      já está no valor neutro de toda coluna; se um toggle o muda, a escada
      efetiva deixa de ter o prefixo onde `compress_file` o procura.
    """
    assert hasattr(modulo, "SEM_PERDA"), (
        f"[{rotulo}] o formato não declara SEM_PERDA (use () se não houver "
        f"degrau sem perda)"
    )
    sem_perda = modulo.SEM_PERDA
    assert isinstance(sem_perda, tuple) and all(
        type(i) is int for i in sem_perda
    ), f"[{rotulo}] SEM_PERDA deve ser uma tupla de índices inteiros: {sem_perda!r}"

    n = len(modulo.TABELA)
    fora = [i for i in sem_perda if not 0 <= i < n]
    assert not fora, (
        f"[{rotulo}] SEM_PERDA tem índice(s) fora da TABELA (0..{n - 1}): {fora}"
    )
    assert sem_perda == tuple(range(len(sem_perda))), (
        f"[{rotulo}] SEM_PERDA deve ser um prefixo (0, 1, ...), não {sem_perda}"
    )
    assert len(sem_perda) < n, (
        f"[{rotulo}] SEM_PERDA cobre a escada inteira: não sobra cauda para bisectar"
    )

    todos_desligados = Toggles(False, False, False, False)
    prefixo = list(modulo.TABELA[: len(sem_perda)])
    efetivo = aplicar_toggles(
        prefixo, info(fps=1_000.0), todos_desligados, modulo.NEUTROS
    )
    assert efetivo == prefixo, (
        f"[{rotulo}] um toggle altera um degrau declarado em SEM_PERDA: "
        f"{prefixo} -> {efetivo}"
    )


@pytest.mark.parametrize("formato", sorted(formatos.registrados()))
def test_sem_perda_por_formato(formato):
    verificar_sem_perda(formatos.para(formato), formato.name)


@pytest.mark.parametrize(
    "formato, esperado",
    [
        (Formato.GIF, ()),
        (Formato.JPEG, ()),
        (Formato.PNG, (0, 1)),
        (Formato.WEBP, (0,)),
    ],
)
def test_sem_perda_de_cada_formato_e_o_documentado(formato, esperado):
    assert formatos.para(formato).SEM_PERDA == esperado


def test_o_verificador_de_sem_perda_aceita_o_gif_real():
    verificar_sem_perda(_falso(SEM_PERDA=()), "controle")


def test_formato_que_omite_sem_perda_falha():
    with pytest.raises(AssertionError, match="não declara SEM_PERDA"):
        verificar_sem_perda(_falso(), "teste")   # `_falso` não traz o campo


@pytest.mark.parametrize("formato", sorted(formatos.registrados()))
def test_modulo_real_sem_o_campo_falha(formato):
    """O mesmo, sobre cada módulo real com o campo removido."""
    real = formatos.para(formato)
    sem_o_campo = SimpleNamespace(
        **{k: getattr(real, k) for k in ("Params", "TABELA", "SENTIDOS", "NEUTROS")}
    )
    with pytest.raises(AssertionError, match="não declara SEM_PERDA"):
        verificar_sem_perda(sem_o_campo, formato.name)


@pytest.mark.parametrize(
    "sem_perda",
    [(len(gif.TABELA),), (0, 99), (-1,), (0, len(gif.TABELA))],
)
def test_indice_fora_da_tabela_falha(sem_perda):
    with pytest.raises(AssertionError, match="fora da TABELA"):
        verificar_sem_perda(_falso(SEM_PERDA=sem_perda), "teste")


@pytest.mark.parametrize("sem_perda", [(1,), (0, 2), (1, 0), (0, 0)])
def test_sem_perda_que_nao_e_prefixo_falha(sem_perda):
    with pytest.raises(AssertionError, match="prefixo"):
        verificar_sem_perda(_falso(SEM_PERDA=sem_perda), "teste")


def test_sem_perda_que_cobre_a_escada_inteira_falha():
    todos = tuple(range(len(gif.TABELA)))
    with pytest.raises(AssertionError, match="cauda"):
        verificar_sem_perda(_falso(SEM_PERDA=todos), "teste")


@pytest.mark.parametrize("sem_perda", [[0], {0}, 0, "0"])
def test_sem_perda_que_nao_e_tupla_de_inteiros_falha(sem_perda):
    with pytest.raises(AssertionError, match="tupla"):
        verificar_sem_perda(_falso(SEM_PERDA=sem_perda), "teste")


def test_degrau_sem_perda_que_um_toggle_altera_falha():
    """O degrau 1 do GIF tem lossy=20: um toggle o leva a 0. Declará-lo
    sem perda é incoerente, e a verificação enxerga."""
    with pytest.raises(AssertionError, match="toggle altera"):
        verificar_sem_perda(_falso(SEM_PERDA=(0, 1)), "teste")


# ---------- ALAVANCAS liga toggle a coluna PELO NOME ----------
#
# `ladder.ALAVANCAS` associa o toggle "palette" à coluna `colors`, "lossy" à
# `lossy` e assim por diante, só pelo nome. Um formato novo com uma coluna
# chamada `colors` que não é uma paleta (ou `lossy` que não é o lossy do
# gifsicle) ficaria ligado à caixa errada em silêncio. O teste abaixo trava o
# que cada formato tem ligado: um formato novo, ou uma coluna nova num
# existente, FALHA aqui até alguém declarar de propósito que a ligação é
# verdadeira.

COLUNAS_LIGADAS_A_TOGGLES: dict[Formato, set[str]] = {
    Formato.GIF: {"colors", "lossy", "scale_pct", "fps"},
    Formato.PNG: {"colors", "scale_pct"},
    Formato.JPEG: {"scale_pct"},
    Formato.WEBP: {"scale_pct"},
}


def test_todo_formato_registrado_declara_as_colunas_que_os_toggles_controlam():
    assert set(COLUNAS_LIGADAS_A_TOGGLES) == formatos.registrados()


@pytest.mark.parametrize("formato", sorted(formatos.registrados()))
def test_colunas_ligadas_a_toggles_sao_as_declaradas(formato):
    campos = {f.name for f in dataclasses.fields(formatos.para(formato).Params)}
    ligadas = campos & set(ALAVANCAS.values())
    assert ligadas == COLUNAS_LIGADAS_A_TOGGLES[formato], (
        f"[{formato.name}] colunas ligadas a toggles pelo NOME: {sorted(ligadas)}. "
        f"Confirme que cada uma tem mesmo o significado do toggle "
        f"({ {t: c for t, c in ALAVANCAS.items() if c in ligadas} }) e atualize "
        f"COLUNAS_LIGADAS_A_TOGGLES."
    )
