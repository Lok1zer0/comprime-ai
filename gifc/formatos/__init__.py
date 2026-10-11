"""Um módulo por formato, cada um dono da sua tabela e do seu codificador."""

from __future__ import annotations

from gifc.formatos import gif, jpeg, png, webp
from gifc.formatos.base import EncodeError, ModuloFormato
from gifc.ladder import Toggles
from gifc.probe import Formato
from gifc.tools import Tools

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
    "EncodeError", "ModuloFormato", "extensao", "formato_de_saida", "para",
    "registrados", "toggles_efetivos",
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


def registrados() -> frozenset[Formato]:
    """Os formatos que já têm módulo."""
    return frozenset(_REGISTRO)


def para(formato: Formato) -> ModuloFormato:
    """O módulo que sabe comprimir `formato`."""
    try:
        return _REGISTRO[formato]
    except KeyError:
        raise EncodeError(
            f"Ainda não há suporte para comprimir arquivos {formato.name}."
        ) from None
