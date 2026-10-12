"""Um módulo por formato, cada um dono da sua tabela e do seu codificador."""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from typing import TypeVar

from gifc.formatos import gif, jpeg, png, webp
from gifc.formatos.base import EncodeError, FormatoNaoSuportado, ModuloFormato
from gifc.ladder import Toggles
from gifc.probe import Formato
from gifc.tools import Tools

P = TypeVar("P")

_REGISTRO: dict[Formato, ModuloFormato] = {
    Formato.GIF: gif,
    Formato.PNG: png,
    Formato.JPEG: jpeg,
    Formato.WEBP: webp,
}

# Extensão do arquivo de cada formato, declarada à mão: `Formato.JPEG.value`
# é "jpeg", mas a extensão que as pessoas esperam é ".jpg". O Pillow infere o
# formato de saída pela extensão, então ela também importa para o codificador.
_EXTENSAO: dict[Formato, str] = {
    Formato.GIF: ".gif",
    Formato.PNG: ".png",
    Formato.JPEG: ".jpg",
    Formato.WEBP: ".webp",
}

__all__ = [
    "EncodeError", "FormatoNaoSuportado", "ModuloFormato", "extensao",
    "formato_de_saida", "para", "registrados", "sem_equivalentes",
    "toggles_efetivos",
]


def extensao(formato: Formato) -> str:
    """A extensão (com ponto) dos arquivos de `formato`."""
    return _EXTENSAO[formato]


def formato_de_saida(entrada: Formato, converter_webp: bool) -> Formato:
    """O formato do arquivo que a compressão de `entrada` produz.

    Com a conversão ligada, PNG e JPEG viram WebP. GIF NUNCA converte:
    converter animação é outro problema e está fora de escopo. WebP segue
    WebP.
    """
    if converter_webp and entrada in (Formato.PNG, Formato.JPEG):
        return Formato.WEBP
    return entrada


def toggles_efetivos(formato: Formato, toggles: Toggles, tools: Tools) -> Toggles:
    """Os toggles que de fato valem para `formato` com estas ferramentas.

    Delega ao gancho opcional `toggles_efetivos` do módulo do formato; sem
    ele, os toggles passam inalterados.
    """
    gancho = getattr(para(formato), "toggles_efetivos", None)
    return toggles if gancho is None else gancho(toggles, tools)


def sem_equivalentes(formato: Formato, escada: Sequence[P], tools: Tools) -> list[P]:
    """`escada` sem os degraus que, com estas ferramentas, gerariam o mesmo
    arquivo de um anterior (fica o primeiro de cada grupo).

    Delega ao gancho opcional `chave_de_equivalencia` do módulo; sem ele a
    escada passa inalterada. Complementa `toggles_efetivos`: aquele cobre o
    que um toggle do usuário desliga, este o que só a falta de uma ferramenta
    torna inerte (uma coluna sem toggle, como o nível do oxipng).
    """
    chave = getattr(para(formato), "chave_de_equivalencia", None)
    if chave is None:
        return list(escada)
    vistas: set[Hashable] = set()
    saida: list[P] = []
    for params in escada:
        k = chave(params, tools)
        if k not in vistas:
            vistas.add(k)
            saida.append(params)
    return saida


def registrados() -> frozenset[Formato]:
    """Os formatos que já têm módulo."""
    return frozenset(_REGISTRO)


def para(formato: Formato) -> ModuloFormato:
    """O módulo que sabe comprimir `formato`."""
    try:
        return _REGISTRO[formato]
    except KeyError:
        raise FormatoNaoSuportado(
            f"Ainda não há suporte para comprimir arquivos {formato.name}."
        ) from None
