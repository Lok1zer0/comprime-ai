"""Um módulo por formato, cada um dono da sua tabela e do seu codificador."""

from __future__ import annotations

from gifc.formatos import gif
from gifc.formatos.base import EncodeError, ModuloFormato
from gifc.probe import Formato

_REGISTRO: dict[Formato, ModuloFormato] = {
    Formato.GIF: gif,
}

__all__ = ["EncodeError", "ModuloFormato", "para", "registrados"]


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
