"""Contrato comum a todos os módulos de formato.

Fica fora de `__init__` para que cada módulo de formato possa importá-lo
sem ciclo com o registro.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from gifc.probe import ImagemInfo
from gifc.tools import Tools


class EncodeError(Exception):
    """Um dos estágios de codificação falhou."""


class ModuloFormato(Protocol):
    """O que todo módulo em `gifc.formatos` expõe.

    - `Params`: dataclass congelada (hashável) com uma coluna por alavanca.
    - `TABELA`: a escada do formato, do sacrifício menos ao mais perceptível.
    - `SENTIDOS`: para cada campo de `Params`, `"maior"` se um valor mais
      ALTO é mais agressivo (ex.: `lossy`) ou `"menor"` se um valor mais
      BAIXO é (ex.: `colors`, `scale_pct`, `fps`). `None` conta como o
      maior valor possível ("sem limite"/original). O teste de invariante
      de monotonicidade lê este mapa e exige que ele cubra TODOS os campos.
    - `codificar`: parte sempre de `src` e escreve `dst`; devolve o tamanho.
    """

    Params: type
    TABELA: tuple[Any, ...]
    SENTIDOS: dict[str, str]

    def codificar(
        self, src: Path, dst: Path, params: Any, info: ImagemInfo, tools: Tools
    ) -> int: ...
