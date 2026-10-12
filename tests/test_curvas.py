"""A curva de tamanho REAL de cada escada, medida com o codificador de verdade.

`SENTIDOS` é declarado à mão e `test_ladder` só confere que a TABELA é
coerente com ele; nada olhava o que o codificador de fato produz. Foi assim
que as escadas de PNG e WebP, cujo sem perda é MENOR que os degraus com perda
logo abaixo, passaram por uma bisecção que supõe tamanho monótono.

O que `compress.buscar` supõe, e estes testes medem: ao longo da CAUDA da
escada (tudo depois do prefixo `SEM_PERDA`) o tamanho codificado não cresce.
O prefixo é isento, por construção.

Cada teste mede a escada EFETIVA (toggles ligados, como no uso padrão) sobre
imagens de natureza diferente: arte chapada, degradê, foto.

A asserção é estrita. Onde a curva medida tem uma inversão na cauda o caso
está marcado `xfail` com os números medidos no motivo, em vez de afrouxar a
comparação: o que fazer com elas é uma decisão a tomar, não um detalhe.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from PIL import Image

from gifc import formatos
from gifc.compress import montar_escada
from gifc.ladder import Toggles
from gifc.probe import Formato, probe
from gifc.tools import Tools
from tests.imagens import (
    captura_de_tela,
    degrade,
    degrade_horizontal,
    foto,
    radial,
    textura,
)

pytestmark = pytest.mark.integration

TODOS = Toggles(True, True, True, True)
SEM_FERRAMENTAS = Tools("ffmpeg", "ffprobe", None, oxipng=None)

IMAGENS = {
    "captura": captura_de_tela,   # arte chapada
    "degrade": degrade,           # liso, sem arestas
    "degrade_h": degrade_horizontal,
    "radial": radial,             # curvas de nível em todas as direções
    "foto": foto,                 # ruído de baixa frequência
    "textura": textura,           # foto com bordas nítidas
}


def medir_escada(
    formato: Formato, origem: Path, pasta: Path, ferramentas: Tools
) -> tuple[list[int], int]:
    """Codifica a escada efetiva de `formato` (toggles ligados) sobre `origem`.

    Devolve os tamanhos, na ordem da escada, e quantos degraus iniciais são o
    prefixo sem perda (o que `compress_file` isentaria da bisecção).
    """
    modulo = formatos.para(formato)
    info = probe(origem)
    escada, n_prefixo = montar_escada(formato, info, TODOS, ferramentas)
    ext = formatos.extensao(formato)
    tamanhos = [
        modulo.codificar(origem, pasta / f"degrau_{i}{ext}", params, info, ferramentas)
        for i, params in enumerate(escada)
    ]
    return tamanhos, n_prefixo


def inversoes_na_cauda(
    tamanhos: Sequence[int], n_prefixo: int
) -> list[tuple[int, int, int]]:
    """Os pares consecutivos da CAUDA em que o tamanho CRESCE, como
    `(índice do degrau menos agressivo, seu tamanho, tamanho do seguinte)`.
    O prefixo não participa, nem a passagem dele para a cauda."""
    return [
        (i, tamanhos[i], tamanhos[i + 1])
        for i in range(n_prefixo, len(tamanhos) - 1)
        if tamanhos[i + 1] > tamanhos[i]
    ]


def assert_cauda_nao_cresce(tamanhos: Sequence[int], n_prefixo: int, rotulo: str) -> None:
    inversoes = inversoes_na_cauda(tamanhos, n_prefixo)
    assert not inversoes, (
        f"[{rotulo}] o tamanho CRESCE na cauda da escada (prefixo sem perda = "
        f"{n_prefixo} degrau(s)); a bisecção supõe o contrário. "
        f"Curva medida: {list(tamanhos)}. "
        f"Inversões (degrau, tamanho, tamanho do seguinte): {inversoes}"
    )


def _salvar(imagem: Image.Image, formato: Formato, destino: Path) -> Path:
    if formato is Formato.PNG:
        imagem.save(destino, "PNG", optimize=True)
    elif formato is Formato.JPEG:
        imagem.save(destino, "JPEG", quality=95)
    else:
        imagem.save(destino, "WEBP", quality=95)
    return destino


# Inversões medidas na CAUDA (fora do prefixo), com a chave
# `(formato da escada, formato da fonte, imagem)`. Esta máquina: Pillow 10.3,
# o libwebp do Pillow, oxipng 10.2.1, ffmpeg e gifsicle de bin/ ou do PATH.
# Os degraus são os índices da escada EFETIVA (a do GIF tem 18, não 20, por
# causa da deduplicação). Todos os outros casos medidos NÃO têm inversão,
# inclusive TODOS os de JPEG.
#
# Os dois piores: no PNG, a passagem de 256 para 128 cores num degradê radial
# (+38,6%) e de 32 cores a 100% para 32 a 85% de escala numa captura (+35,3%).
# `strict=False`: os tamanhos dependem das versões do Pillow/libwebp/oxipng, e
# um caso que deixe de inverter noutra máquina não deve reprovar a suíte.
INVERSOES_MEDIDAS: dict[tuple[Formato, Formato, str], str] = {
    (Formato.PNG, Formato.PNG, "captura"): (
        "degrau 5->6: 4812 -> 6512 B (+35.3%). Curva: [6214, 5708, 5708, 5120, 5047, 4812, 6512, 4316, 2857, 1744]"
    ),
    (Formato.PNG, Formato.PNG, "degrade_h"): (
        "degrau 2->3: 2064 -> 2105 B (+2.0%); degrau 4->5: 2093 -> 2172 B (+3.8%). Curva: [2671, 2064, 2064, 2105, 2093, 2172, 1668, 1308, 1292, 725]"
    ),
    (Formato.PNG, Formato.PNG, "radial"): (
        "degrau 2->3: 16674 -> 23109 B (+38.6%); degrau 3->4: 23109 -> 23391 B (+1.2%). Curva: [17614, 16674, 16674, 23109, 23391, 15175, 14653, 8980, 7690, 5930]"
    ),
    (Formato.WEBP, Formato.WEBP, "captura"): (
        "degrau 5->6: 32318 -> 32592 B (+0.8%). Curva: [72488, 53072, 45808, 41898, 33120, 32318, 32592, 28086, 26788, 8002, 3560]"
    ),
    (Formato.WEBP, Formato.WEBP, "degrade"): (
        "degrau 4->5: 2606 -> 2742 B (+5.2%); degrau 5->6: 2742 -> 2930 B (+6.9%). Curva: [964, 3004, 2972, 2674, 2606, 2742, 2930, 1832, 1828, 1270, 708]"
    ),
    (Formato.WEBP, Formato.WEBP, "degrade_h"): (
        "degrau 3->4: 2394 -> 2418 B (+1.0%); degrau 4->5: 2418 -> 2484 B (+2.7%); degrau 5->6: 2484 -> 2488 B (+0.2%). Curva: [1308, 3384, 2876, 2394, 2418, 2484, 2488, 1762, 1752, 1222, 678]"
    ),
    (Formato.WEBP, Formato.WEBP, "radial"): (
        "degrau 5->6: 4452 -> 4614 B (+3.6%). Curva: [55220, 14050, 8230, 5482, 4670, 4452, 4614, 3136, 2790, 1866, 1142]"
    ),
    (Formato.WEBP, Formato.PNG, "captura"): (
        "degrau 4->5: 36026 -> 36352 B (+0.9%). Curva: [2262, 50698, 47070, 36932, 36026, 36352, 34840, 28260, 27068, 8352, 3606]"
    ),
    (Formato.WEBP, Formato.PNG, "degrade"): (
        "degrau 5->6: 2732 -> 2774 B (+1.5%). Curva: [754, 4576, 3856, 2804, 2760, 2732, 2774, 1826, 1816, 1270, 692]"
    ),
    (Formato.WEBP, Formato.PNG, "radial"): (
        "degrau 5->6: 4120 -> 4190 B (+1.7%). Curva: [22628, 9728, 6474, 4768, 4180, 4120, 4190, 3012, 2698, 1822, 1106]"
    ),
    (Formato.WEBP, Formato.JPEG, "degrade"): (
        "degrau 5->6: 2608 -> 2734 B (+4.8%). Curva: [806, 4492, 4044, 2742, 2696, 2608, 2734, 1860, 1822, 1270, 692]"
    ),
    (Formato.WEBP, Formato.JPEG, "radial"): (
        "degrau 5->6: 4166 -> 4242 B (+1.8%). Curva: [107780, 10168, 6542, 4708, 4258, 4166, 4242, 3032, 2706, 1804, 1118]"
    ),
    (Formato.GIF, Formato.GIF, "smptebars"): (
        "degrau 8->9: 5793 -> 6099 B (+5.3%); degrau 14->15: 2629 -> 2637 B (+0.3%). Curva: [5793, 5793, 5793, 5793, 5793, 5793, 5793, 5793, 5793, 6099, 5877, 3943, 3778, 3778, 2629, 2637, 1705, 1698]"
    ),
}


def _caso(formato: Formato, fonte: Formato, imagem: str):
    motivo = INVERSOES_MEDIDAS.get((formato, fonte, imagem))
    marcas = [pytest.mark.xfail(reason=motivo, strict=False)] if motivo else []
    return pytest.param(
        formato, fonte, imagem, marks=marcas,
        id=f"{formato.name}<-{fonte.name}-{imagem}",
    )


# A escada de um formato é percorrida a partir de fontes de formatos
# diferentes: o WebP é a saída da conversão de PNG e JPEG, além de ser entrada.
CASOS_DE_IMAGEM = [
    (Formato.PNG, Formato.PNG),
    (Formato.JPEG, Formato.JPEG),
    (Formato.WEBP, Formato.WEBP),
    (Formato.WEBP, Formato.PNG),
    (Formato.WEBP, Formato.JPEG),
]


@pytest.mark.parametrize(
    "formato, fonte, imagem",
    [_caso(f, o, i) for f, o in CASOS_DE_IMAGEM for i in IMAGENS],
)
def test_cauda_da_escada_de_imagem_nao_cresce(formato, fonte, imagem, tmp_path, tools):
    if formato is Formato.PNG and tools.oxipng is None:
        pytest.skip("oxipng não disponível em bin/")
    ferramentas = tools if formato is Formato.PNG else SEM_FERRAMENTAS
    origem = _salvar(
        IMAGENS[imagem](), fonte, tmp_path / f"entrada{formatos.extensao(fonte)}"
    )

    tamanhos, n_prefixo = medir_escada(formato, origem, tmp_path, ferramentas)

    assert_cauda_nao_cresce(tamanhos, n_prefixo, f"{formato.name}<-{fonte.name}, {imagem}")


# --- GIF: o ffmpeg gera as fontes ------------------------------------------

FONTES_GIF = {
    "testsrc2": "testsrc2=size=320x240:rate=15:duration=2",
    "smptebars": "smptebars=size=320x240:rate=15:duration=2",
}


@pytest.fixture(scope="module")
def gifs(tools, tmp_path_factory) -> dict[str, Path]:
    pasta = tmp_path_factory.mktemp("gifs")
    saida = {}
    for nome, fonte in FONTES_GIF.items():
        destino = pasta / f"{nome}.gif"
        subprocess.run(
            [tools.ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", fonte,
             "-t", "2", str(destino)],
            check=True, capture_output=True,
        )
        saida[nome] = destino
    return saida


@pytest.mark.parametrize(
    "fonte",
    [
        pytest.param(
            nome,
            marks=[pytest.mark.xfail(reason=INVERSOES_MEDIDAS[(Formato.GIF, Formato.GIF, nome)], strict=False)]
            if (Formato.GIF, Formato.GIF, nome) in INVERSOES_MEDIDAS else [],
        )
        for nome in FONTES_GIF
    ],
)
def test_cauda_da_escada_de_gif_nao_cresce(fonte, gifs, tools, tmp_path):
    tamanhos, n_prefixo = medir_escada(Formato.GIF, gifs[fonte], tmp_path, tools)

    assert n_prefixo == 0     # o GIF não tem prefixo: a curva inteira vale
    assert_cauda_nao_cresce(tamanhos, n_prefixo, f"GIF, {fonte}")


# --- a própria medição tem de ser capaz de reprovar -------------------------


def test_a_asserção_reprova_uma_inversao_na_cauda():
    with pytest.raises(AssertionError, match="CRESCE"):
        assert_cauda_nao_cresce([100, 90, 95, 80], 0, "sintético")


def test_a_asserção_isenta_o_prefixo_mas_so_ele():
    # prefixo de 1: subir do degrau 0 para o 1 é o caso do WebP em arte chapada
    assert_cauda_nao_cresce([100, 9000, 8000, 7000], 1, "sintético")
    # a inversão no INTERIOR da cauda continua reprovando
    with pytest.raises(AssertionError, match="CRESCE"):
        assert_cauda_nao_cresce([100, 9000, 8000, 8500], 1, "sintético")
    # e o prefixo declarado curto demais reprova
    with pytest.raises(AssertionError, match="CRESCE"):
        assert_cauda_nao_cresce([100, 9000, 8000, 7000], 0, "sintético")


def test_sem_declarar_o_prefixo_a_curva_real_do_webp_reprova(tmp_path):
    """O teste que teria pegado o defeito: a mesma curva real do WebP em arte
    chapada, lida como se `SEM_PERDA` fosse `()`, reprova. Com o prefixo
    declarado pelo módulo ela passa (no degrau 0, ao menos)."""
    origem = _salvar(captura_de_tela(), Formato.PNG, tmp_path / "entrada.png")
    tamanhos, n_prefixo = medir_escada(Formato.WEBP, origem, tmp_path, SEM_FERRAMENTAS)

    assert n_prefixo == len(formatos.para(Formato.WEBP).SEM_PERDA) == 1
    assert tamanhos[0] < tamanhos[1]       # o sem perda é menor que o q90
    with pytest.raises(AssertionError, match="CRESCE"):
        assert_cauda_nao_cresce(tamanhos, 0, "WEBP sem prefixo")
    assert not inversoes_na_cauda(tamanhos[:2], n_prefixo)
