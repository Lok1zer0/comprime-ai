"""Leitura de metadados de um GIF direto dos bytes, sem ffprobe.

Módulo puro: recebe bytes, devolve fatos. Sem I/O, sem subprocess.
"""

from __future__ import annotations

from dataclasses import dataclass


class GifMetaError(Exception):
    """Os bytes não formam um GIF legível; a mensagem diz onde a leitura parou."""


@dataclass(frozen=True)
class GifRaw:
    width: int
    height: int
    frame_count: int
    total_delay_cs: int
    """Soma dos delays em centésimos de segundo, exatamente como o arquivo diz."""


_TRAILER = 0x3B
_EXTENSAO = 0x21
_IMAGEM = 0x2C
_GCE = 0xF9


def _exige(dados: bytes, pos: int, n: int, onde: str) -> None:
    """Garante que existem `n` bytes a partir de `pos`; senão levanta GifMetaError."""
    if pos + n > len(dados):
        raise GifMetaError(f"GIF truncado {onde} (byte {pos} de {len(dados)})")


def _u16(dados: bytes, pos: int) -> int:
    return dados[pos] | (dados[pos + 1] << 8)


def _tamanho_tabela(packed: int) -> int:
    """Bytes de uma tabela de cores cujo campo packed tem N nos bits 0-2."""
    return 3 * (2 ** ((packed & 0x07) + 1))


def _pular_subblocos(dados: bytes, pos: int, onde: str) -> int:
    """Pula uma sequência de sub-blocos até o terminador 0; devolve a posição seguinte."""
    while True:
        _exige(dados, pos, 1, f"{onde}, no tamanho de um sub-bloco")
        tamanho = dados[pos]
        pos += 1
        if tamanho == 0:
            return pos
        _exige(dados, pos, tamanho, f"{onde}, no meio de um sub-bloco")
        pos += tamanho


def parse(dados: bytes) -> GifRaw:
    """Lê largura, altura, quantidade de quadros e soma dos delays de um GIF."""
    _exige(dados, 0, 6, "no cabeçalho")
    if dados[:6] not in (b"GIF87a", b"GIF89a"):
        raise GifMetaError(f"assinatura de GIF inválida: {bytes(dados[:6])!r}")

    _exige(dados, 6, 7, "no descritor de tela")
    largura = _u16(dados, 6)
    altura = _u16(dados, 8)
    packed = dados[10]
    pos = 13
    if packed & 0x80:
        n = _tamanho_tabela(packed)
        _exige(dados, pos, n, "na tabela global de cores")
        pos += n

    quadros = 0
    total_cs = 0
    atraso_pendente = 0

    while True:
        if pos >= len(dados):
            if quadros == 0:
                raise GifMetaError("fim do arquivo antes de qualquer quadro")
            break  # sem trailer, mas os quadros já lidos valem

        bloco = dados[pos]
        pos += 1

        if bloco == _TRAILER:
            break

        if bloco == _EXTENSAO:
            _exige(dados, pos, 1, "após o marcador de extensão")
            rotulo = dados[pos]
            pos += 1
            if rotulo == _GCE:
                _exige(dados, pos, 1, "no tamanho da extensão de controle gráfico")
                if dados[pos] != 4:
                    raise GifMetaError(
                        f"extensão de controle gráfico malformada no byte {pos - 2}: "
                        f"tamanho do bloco {dados[pos]} em vez de 4"
                    )
                _exige(dados, pos, 5, "na extensão de controle gráfico")
                atraso_pendente = _u16(dados, pos + 2)
            pos = _pular_subblocos(dados, pos, f"na extensão 0x{rotulo:02X}")

        elif bloco == _IMAGEM:
            _exige(dados, pos, 9, "no descritor de imagem")
            local = dados[pos + 8]
            pos += 9
            if local & 0x80:
                n = _tamanho_tabela(local)
                _exige(dados, pos, n, "na tabela local de cores")
                pos += n
            _exige(dados, pos, 1, "no tamanho mínimo de código LZW")
            pos += 1
            pos = _pular_subblocos(dados, pos, "nos dados de imagem")
            quadros += 1
            total_cs += atraso_pendente
            atraso_pendente = 0

        else:
            raise GifMetaError(f"bloco desconhecido 0x{bloco:02X} no byte {pos - 1}")

    if quadros == 0:
        raise GifMetaError("GIF sem nenhum quadro")
    return GifRaw(width=largura, height=altura, frame_count=quadros, total_delay_cs=total_cs)
