"""O formato WebP: codificação com perda pelo Pillow.

Serve a dois caminhos: um WebP de entrada, e um PNG ou JPEG convertido quando
o usuário liga a conversão. Em ambos o WebP é recodificado com perda, mesmo
no degrau 0 (qualidade 90).

`method=6` vale para todos os degraus: é o ajuste mais lento e o que melhor
comprime, ou seja, um botão de tempo e não de qualidade. Não é alavanca.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

from gifc.formatos.base import EncodeError
from gifc.ladder import target_width
from gifc.probe import ImagemInfo
from gifc.tools import Tools

METODO = 6


@dataclass(frozen=True)
class Params:
    """Um ponto no espaço de compressão do WebP."""

    quality: int
    scale_pct: int


# Cada degrau é fracamente mais agressivo que o anterior: qualidade e escala
# nunca aumentam.
TABELA: tuple[Params, ...] = (
    Params(90, 100),    # 0
    Params(85, 100),    # 1
    Params(80, 100),    # 2
    Params(75, 100),    # 3
    Params(70, 100),    # 4
    Params(65, 100),    # 5
    Params(60, 85),     # 6
    Params(55, 85),     # 7
    Params(50, 70),     # 8
    Params(45, 50),     # 9
)

# Nos dois campos o valor MENOR é o mais agressivo.
SENTIDOS: dict[str, str] = {
    "quality": "menor",
    "scale_pct": "menor",
}

# O valor MENOS agressivo de cada coluna controlada por toggle. A qualidade
# não tem toggle (o WebP só tem o da escala).
NEUTROS: dict[str, object] = {
    "scale_pct": 100,
}


def _para_8_bits(img: Image.Image) -> Image.Image:
    """Cinza de 16 bits (e inteiros/flutuantes) vira 8; o `convert` do Pillow
    não cobre esses modos."""
    if img.mode.startswith("I;16") or img.mode in ("I", "F"):
        return img.point(lambda v: v * (1 / 256)).convert("L")  # type: ignore[no-any-return]
    return img


def _tem_alfa(img: Image.Image) -> bool:
    """Alfa de canal, ou transparência de paleta/cor-chave."""
    return "A" in img.getbands() or "transparency" in img.info


def _para_srgb(
    img: Image.Image, perfil: bytes | None
) -> tuple[Image.Image, bytes | None]:
    """Resolve perfis que não são RGB. O WebP só grava RGB, e um perfil de
    cinza ou de CMYK colado em dados RGB faria o visualizador interpretar as
    cores errado.

    Cinza e CMYK com perfil são convertidos para sRGB por ele; o alfa de um
    LA segue ao lado. Se a conversão não for possível, o perfil é descartado:
    sem perfil o visualizador assume sRGB, que é a leitura honesta de dados
    RGB convertidos. Perfil RGB passa intacto.
    """
    if perfil is None:
        return img, None
    if perfil[16:20] == b"RGB ":
        return img, perfil
    if img.mode in ("L", "LA", "CMYK"):
        try:
            cor = img.getchannel("L") if img.mode == "LA" else img
            rgb = ImageCms.profileToProfile(
                cor,
                ImageCms.ImageCmsProfile(BytesIO(perfil)),
                ImageCms.createProfile("sRGB"),
                outputMode="RGB",
            )
            if img.mode == "LA":
                rgb.putalpha(img.getchannel("A"))
            return rgb, None
        except (ImageCms.PyCMSError, OSError, ValueError):
            pass
    return img, None


def _para_webp(img: Image.Image) -> Image.Image:
    """Leva `img` a RGB ou RGBA, os modos que o WebP grava.

    - Sai sempre de P, L e 1 ANTES de escalar: o Pillow ignora o filtro de
      `resize` nesses modos e usa vizinho mais próximo, o que serrilha toda
      imagem em paleta.
    - Transparência (canal, paleta ou cor-chave) vira RGBA e o alfa é
      mantido: `convert("RGB")` direto descartaria o canal e mostraria o que
      estiver "por baixo" dos pixels transparentes, em geral preto.
    - Um RGBA com alfa 100% opaco fica RGBA aqui, e não custa nada: o
      libwebp descarta sozinho um alfa todo opaco (medido: bytes idênticos aos
      do mesmo RGB).
    """
    img = _para_8_bits(img)
    return img.convert("RGBA" if _tem_alfa(img) else "RGB")


def _escalar(img: Image.Image, scale_pct: int) -> Image.Image:
    """Reduz pela mesma regra de largura do resto do projeto; nunca amplia.

    Parte da largura da imagem JÁ endireitada (`img.width`), não de
    `ImagemInfo.width`, que é a largura ARMAZENADA. Em RGBA o Pillow escala
    com o alfa pré-multiplicado, então a cor escondida sob um fundo
    transparente não vaza para as bordas.
    """
    if scale_pct == 100:
        return img
    largura = target_width(img.width, scale_pct)
    if largura >= img.width:
        return img
    altura = max(1, round(img.height * largura / img.width))
    return img.resize((largura, altura), Image.Resampling.LANCZOS)


def codificar(
    src: Path, dst: Path, params: Params, info: ImagemInfo, tools: Tools
) -> int:
    """Codifica `src` (PNG, JPEG ou WebP) em `dst` com `params`. Devolve o
    tamanho de `dst`.

    Sempre parte do arquivo original, nunca do resultado de uma tentativa
    anterior, e nunca escreve em `src`. Não usa ferramentas externas; `info`
    e `tools` existem pelo contrato comum dos formatos.

    Orientação EXIF: os pixels são girados de verdade (`exif_transpose`)
    ANTES de escalar, e a tag sai do arquivo. O resto do EXIF e o perfil ICC
    RGB são mantidos: sem o perfil uma imagem de gamut largo mudaria de cor
    em silêncio. A transparência sobrevive à conversão (ver `_para_webp`).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(src) as aberta:
            perfil = aberta.info.get("icc_profile")
            # Devolve uma cópia já girada e com a tag removida do EXIF.
            img = ImageOps.exif_transpose(aberta)
            exif = img.getexif()
            img, perfil = _para_srgb(_para_8_bits(img), perfil)
            img = _para_webp(img)
            img = _escalar(img, params.scale_pct)
            # `resize` e as conversões não carregam o `info`: EXIF e perfil
            # vão explícitos ao `save`.
            img.save(
                dst,
                format="WEBP",
                quality=params.quality,
                method=METODO,
                exif=exif.tobytes() if len(exif) else b"",
                icc_profile=perfil,
            )
        if not dst.is_file():
            raise EncodeError("A codificação não produziu arquivo de saída.")
        return dst.stat().st_size
    except EncodeError:
        dst.unlink(missing_ok=True)
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        dst.unlink(missing_ok=True)
        raise EncodeError(f"Falha ao gerar o WebP: {exc}") from exc
