"""O formato JPEG: reempacotamento com o Pillow.

Um JPEG de entrada já passou por uma compressão com perda; recodificar
degrada de novo, inclusive no degrau 0 (qualidade 92). Nada neste módulo, e
nada na interface, deve sugerir o contrário.

`progressive=True` e `optimize=True` valem para todos os degraus: economizam
alguns por cento sem custo de qualidade, então são padrão, não alavancas.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, JpegImagePlugin

from gifc.formatos.base import EncodeError
from gifc.ladder import target_width
from gifc.probe import ImagemInfo
from gifc.tools import Tools


@dataclass(frozen=True)
class Params:
    """Um ponto no espaço de compressão do JPEG.

    `subsampling` segue a convenção do Pillow: 0 é 4:4:4 (sem perda de
    crominância) e 2 é 4:2:0. Nessa coluna o número MAIOR é o mais agressivo,
    o contrário de `quality` e `scale_pct`. O valor -1 é nosso, não do
    Pillow: "igual ao da fonte", o extremo menos agressivo. Ele NUNCA é
    passado ao Pillow, onde -1 significa 4:2:0.
    """

    quality: int
    subsampling: int
    scale_pct: int


# Cada degrau é fracamente mais agressivo que o anterior em todas as
# dimensões: qualidade e escala nunca aumentam, a subamostragem nunca volta
# de 4:2:0 para 4:4:4 (-1 < 0 < 2). Os degraus 0 e 1 mantêm a subamostragem
# da fonte em vez de impor 4:4:4: impor engordaria uma foto 4:2:0, e a
# spec os chama de os mais conservadores.
TABELA: tuple[Params, ...] = (
    Params(92, -1, 100),    # 0
    Params(88, -1, 100),    # 1
    Params(85, 2, 100),     # 2
    Params(80, 2, 100),     # 3
    Params(75, 2, 100),     # 4
    Params(70, 2, 100),     # 5
    Params(65, 2, 100),     # 6
    Params(60, 2, 85),      # 7
    Params(55, 2, 85),      # 8
    Params(50, 2, 70),      # 9
    Params(45, 2, 70),      # 10
    Params(40, 2, 50),      # 11
)

# Em que sentido cada campo fica MAIS agressivo. A subamostragem é a exceção
# que importa: -1 é "como a fonte", 0 é 4:4:4 e 2 é 4:2:0, então aqui o MAIOR
# é o mais agressivo.
SENTIDOS: dict[str, str] = {
    "quality": "menor",
    "subsampling": "maior",
    "scale_pct": "menor",
}

# Todo degrau do JPEG recodifica com perda. A curva de tamanho foi não-crescente
# por toda a escada em todas as imagens medidas (`tests/test_curvas.py`).
SEM_PERDA: tuple[int, ...] = ()

# O valor MENOS agressivo de cada coluna controlada por toggle. A qualidade
# e a subamostragem não têm toggle (o JPEG só tem o da escala).
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


def _para_jpeg(
    img: Image.Image, perfil: bytes | None
) -> tuple[Image.Image, bytes | None]:
    """Leva `img` a um modo que o JPEG grava (L, RGB ou CMYK), com o perfil que
    continua valendo para esse modo.

    - Tons de cinza ficam em L: converter para RGB só incharia o arquivo.
    - Alfa é achatado sobre BRANCO. Um `convert("RGB")` direto descartaria o
      canal e mostraria o que estiver "por baixo" dos pixels transparentes,
      em geral preto.
    - CMYK continua CMYK, com o perfil: o Pillow grava CMYK em JPEG, e
      converter para RGB pela fórmula dele mudaria as cores de uma imagem
      com perfil de impressão, sem aviso e sem volta.
    """
    img = _para_8_bits(img)
    if _tem_alfa(img):
        rgba = img.convert("RGBA")
        fundo = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(fundo, rgba).convert("RGB"), perfil
    if img.mode in ("L", "RGB", "CMYK"):
        return img, perfil
    return img.convert("RGB"), perfil


def _subsampling(aberta: Image.Image, params: Params) -> int:
    """A subamostragem a gravar. -1 ("como a fonte") é resolvido AQUI, lendo
    a fonte aberta, antes de qualquer cópia: o Pillow trata -1 como 4:2:0 e
    `"keep"` falha em imagens que não são o arquivo aberto (sempre o nosso
    caso: girar, converter e escalar devolvem cópias).

    Tons de cinza, CMYK e fontes que não são JPEG não têm crominância em 3
    camadas a preservar (`get_sampling` devolve -1 ou nem se aplica): 4:4:4,
    que não descarta nada.
    """
    if params.subsampling != -1:
        return params.subsampling
    if isinstance(aberta, JpegImagePlugin.JpegImageFile):
        amostragem = JpegImagePlugin.get_sampling(aberta)
        if amostragem != -1:
            return amostragem
    return 0


def _escalar(img: Image.Image, scale_pct: int) -> Image.Image:
    """Reduz pela mesma regra de largura do resto do projeto; nunca amplia.

    Parte da largura da imagem JÁ endireitada (`img.width`), não de
    `ImagemInfo.width`: esta é a largura ARMAZENADA, e numa foto de celular
    com orientação EXIF 6 ela é o lado que se vê na vertical.
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
    """Codifica `src` em `dst` com `params`. Devolve o tamanho de `dst`.

    Sempre parte do arquivo original, nunca do resultado de uma tentativa
    anterior, e nunca escreve em `src`. Não usa ferramentas externas; `info`
    e `tools` existem pelo contrato comum dos formatos.

    Orientação EXIF: os pixels são girados de verdade (`exif_transpose`)
    ANTES de escalar, e a tag sai do arquivo. Assim a escala age sobre o que
    se vê e nenhum visualizador gira a foto uma segunda vez. O resto do EXIF
    (câmera, data, GPS) é mantido, e o perfil ICC também: sem ele uma imagem
    de gamut largo mudaria de cor em silêncio.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(src) as aberta:
            perfil = aberta.info.get("icc_profile")
            subsampling = _subsampling(aberta, params)
            # Um JPEG com várias imagens (MPO) mantém só a primária, e o XMP
            # não é copiado: um mapa de ganho HDR, por exemplo, se perde.
            # Devolve uma cópia já girada e com a tag removida do EXIF.
            img = ImageOps.exif_transpose(aberta)
            exif = img.getexif()
            img, perfil = _para_jpeg(img, perfil)
            img = _escalar(img, params.scale_pct)
            # `resize` e as conversões não carregam o `info`: EXIF e perfil
            # vão explícitos ao `save`.
            img.save(
                dst,
                format="JPEG",
                quality=params.quality,
                subsampling=subsampling,
                progressive=True,
                optimize=True,
                exif=exif if len(exif) else b"",
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
        raise EncodeError(f"Falha ao gerar o JPEG: {exc}") from exc
