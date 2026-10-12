"""Leitura de metadados de uma imagem (GIF, PNG, JPEG ou WebP).

O formato é identificado pelo conteúdo, nunca pela extensão: usuários
renomeiam arquivos, e uma foto chamada `foto.png` tem de ser tratada pelo
que ela é. GIF passa pelo `gifmeta` (bytes puros); os demais, pelo Pillow.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from PIL import Image

from gifc import gifmeta


class ProbeError(Exception):
    """O arquivo não pôde ser lido como uma imagem suportada."""


class Formato(StrEnum):
    GIF = "gif"
    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"


@dataclass(frozen=True)
class ImagemInfo:
    path: Path
    formato: Formato
    width: int
    height: int
    fps: float
    frame_count: int
    duration: float
    size_bytes: int


FPS_PADRAO = 10.0
"""Convenção usual de navegadores para GIFs com atraso zero entre quadros.
É um valor de reserva, não um dado medido a partir do arquivo. Formatos
estáticos o usam também, junto de `frame_count = 1` e `duration = 0.0`: são
os mesmos valores neutros que um GIF de quadro único já produz."""

_TAMANHO_CABECALHO = 12  # o suficiente para "RIFF" + tamanho + "WEBP"

# Nome que o Pillow dá a cada formato; restringe o Pillow ao formato que a
# assinatura já identificou, para os dois nunca divergirem.
_NOME_PILLOW = {Formato.PNG: "PNG", Formato.JPEG: "JPEG", Formato.WEBP: "WEBP"}

_ROTULO = {Formato.PNG: "PNG", Formato.WEBP: "WebP"}


def _detectar(cabecalho: bytes) -> Formato | None:
    """Identifica o formato pela assinatura; None se não for nenhum suportado."""
    if cabecalho[:6] in (b"GIF87a", b"GIF89a"):
        return Formato.GIF
    if cabecalho[:8] == b"\x89PNG\r\n\x1a\n":
        return Formato.PNG
    if cabecalho[:3] == b"\xff\xd8\xff":
        return Formato.JPEG
    if cabecalho[:4] == b"RIFF" and cabecalho[8:12] == b"WEBP":
        return Formato.WEBP
    return None


def _info_gif(path: Path, dados: bytes) -> ImagemInfo:
    try:
        bruto = gifmeta.parse(dados)
    except gifmeta.GifMetaError as exc:
        raise ProbeError(f"{path.name} não é um GIF legível: {exc}") from exc

    duracao = bruto.total_delay_cs / 100
    # Soma de delays zero: o arquivo não informa a velocidade. fps cai para o
    # padrão e a duração fica 0.0 — "não sei" honesto, sem número inventado.
    fps = bruto.frame_count / duracao if duracao > 0 else FPS_PADRAO
    return ImagemInfo(
        path=path, formato=Formato.GIF,
        width=bruto.width, height=bruto.height,
        fps=fps, frame_count=bruto.frame_count, duration=duracao,
        size_bytes=len(dados),
    )


def _info_estatica(path: Path, formato: Formato) -> ImagemInfo:
    try:
        with Image.open(path, formats=[_NOME_PILLOW[formato]]) as img:
            largura, altura = img.size
            # JPEG fica de fora de propósito: fotos de celular são MPO (várias
            # imagens num JPEG) e o Pillow reporta n_frames > 1 nelas, sem
            # que haja animação.
            animado = formato is not Formato.JPEG and getattr(img, "n_frames", 1) > 1
        # O stat fica dentro do try junto com a leitura: se o arquivo
        # desaparecer entre abrir e medir, o erro sai como ProbeError como
        # todo o resto desta função, em vez de um OSError cru.
        tamanho = path.stat().st_size
    except Exception as exc:  # o Pillow falha de formas demais para listar
        raise ProbeError(
            f"{path.name} parece um {formato.name} mas não pôde ser lido: {exc}"
        ) from exc

    if animado:
        raise ProbeError(
            f"{path.name}: {_ROTULO[formato]} animado não é suportado "
            "(só o primeiro quadro seria comprimido e a animação se perderia)."
        )
    return ImagemInfo(
        path=path, formato=formato, width=largura, height=altura,
        fps=FPS_PADRAO, frame_count=1, duration=0.0,
        size_bytes=tamanho,
    )


def probe(path: Path) -> ImagemInfo:
    """Lê os metadados da imagem. Levanta ProbeError se não for suportada."""
    path = Path(path)
    if not path.is_file():
        raise ProbeError(f"Arquivo não encontrado: {path}")

    try:
        with path.open("rb") as f:
            cabecalho = f.read(_TAMANHO_CABECALHO)
            formato = _detectar(cabecalho)
            dados = cabecalho + f.read() if formato is Formato.GIF else b""
    except OSError as exc:
        raise ProbeError(f"Não foi possível ler {path.name}: {exc}") from exc

    if formato is None:
        raise ProbeError(
            f"{path.name}: formato não reconhecido "
            "(esperado GIF, PNG, JPEG ou WebP)."
        )
    if formato is Formato.GIF:
        return _info_gif(path, dados)
    return _info_estatica(path, formato)
