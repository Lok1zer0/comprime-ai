"""O formato PNG: quantização de paleta do Pillow e depois o oxipng.

Espelha a forma do GIF: um estágio com perda que reduz a paleta, seguido de
um otimizador sem perda. Os degraus 0 e 1 não quantizam nada — são só
recompressão — porque, em imagens muito suaves e previsíveis, a tabela de
256 cores custa mais do que a quantização economiza e o arquivo sai MAIOR
que o original.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from gifc.formatos.base import EncodeError, executar
from gifc.ladder import target_width
from gifc.probe import ImagemInfo
from gifc.tools import Tools


@dataclass(frozen=True)
class Params:
    """Um ponto no espaço de compressão do PNG.

    `colors=None` significa não quantizar (o extremo MENOS agressivo da
    coluna). `oxipng` é o nível `-o` do otimizador sem perda.
    """

    colors: int | None
    oxipng: int
    scale_pct: int


# Cada degrau é fracamente mais agressivo que o anterior em todas as
# dimensões: cores nunca aumentam (None é o maior valor), o nível do oxipng
# nunca diminui, a escala nunca aumenta.
TABELA: tuple[Params, ...] = (
    Params(None, 2, 100),     # 0  sem perda
    Params(None, 4, 100),     # 1  sem perda
    Params(256, 4, 100),      # 2
    Params(128, 4, 100),      # 3
    Params(64, 4, 100),       # 4
    Params(32, 4, 100),       # 5
    Params(32, 4, 85),        # 6
    Params(16, 4, 85),        # 7
    Params(16, 4, 70),        # 8
    Params(16, 4, 50),        # 9
)

# Em que sentido cada campo fica MAIS agressivo (None = maior possível).
SENTIDOS: dict[str, str] = {
    "colors": "menor",
    "oxipng": "maior",
    "scale_pct": "menor",
}

# Os degraus 0 e 1 são sem perda e NÃO entram na bisecção: numa imagem suave
# a recompressão pode ser menor que a versão quantizada, de modo que o tamanho
# sobe antes de descer e a bisecção descartaria o degrau 0 sem medi-lo.
SEM_PERDA: tuple[int, ...] = (0, 1)

# O valor MENOS agressivo de cada coluna controlada por toggle. As cores são
# `None` (não quantizar), NÃO 256: desligar a paleta tem de deixar o arquivo
# como está, e quantizar uma imagem muito suave e previsível a 256 cores a
# faz crescer.
NEUTROS: dict[str, object] = {
    "colors": None,
    "scale_pct": 100,
}


def build_oxipng_cmd(tools: Tools, alvo: Path, params: Params) -> list[str]:
    """O oxipng reescreve `alvo` no lugar — nunca o arquivo original — e só
    o troca se o resultado ficar menor."""
    if tools.oxipng is None:
        raise EncodeError("oxipng não está disponível.")
    return [tools.oxipng, "-o", str(params.oxipng), "--quiet", str(alvo)]


def _tem_canal_alfa(img: Image.Image) -> bool:
    """Alfa de canal, ou transparência de paleta/cor-chave (chunk tRNS)."""
    return "A" in img.getbands() or "transparency" in img.info


def _para_8_bits(img: Image.Image) -> Image.Image:
    """Cinza de 16 bits vira 8; o `convert` do Pillow não cobre esses modos."""
    if img.mode.startswith("I;16") or img.mode in ("I", "F"):
        return img.point(lambda v: v * (1 / 256)).convert("L")  # type: ignore[no-any-return]
    return img


def _para_rgb_ou_rgba(img: Image.Image) -> Image.Image:
    """RGBA se há transparência de verdade; senão RGB.

    Sai sempre de P, L e 1: o Pillow IGNORA o filtro de `resize` nesses modos
    e usa vizinho mais próximo, o que serrilha toda imagem em paleta (capturas
    já otimizadas, a saída desta própria ferramenta). Um RGBA que só carrega
    um canal alfa 100% opaco (muitas ferramentas gravam assim) vira RGB, para
    quantizar com o método melhor.
    """
    if _tem_canal_alfa(img):
        rgba = img.convert("RGBA")
        if rgba.getchannel("A").getextrema()[0] < 255:
            return rgba
        return rgba.convert("RGB")
    return img.convert("RGB")


def _quantizar(img: Image.Image, cores: int) -> Image.Image:
    """Reduz a paleta a `cores`, preservando o alfa quando há.

    `img` vem de `_para_rgb_ou_rgba`. Com alfa (RGBA), `FASTOCTREE`: é o
    único método embutido que quantiza o canal alfa junto (a paleta sai em
    modo RGBA e o PNG ganha o tRNS). Quantizar depois de `convert("RGB")`, ou
    usar um método que não conhece o alfa, devolve o fundo transparente como
    preto opaco — um defeito silencioso, que passa por qualquer checagem de
    tamanho. Sem alfa (RGB), corte mediano, que dá paleta melhor.
    """
    metodo = (
        Image.Quantize.FASTOCTREE if img.mode == "RGBA"
        else Image.Quantize.MEDIANCUT
    )
    return img.quantize(colors=cores, method=metodo)


def _escalar(img: Image.Image, info: ImagemInfo, scale_pct: int) -> Image.Image:
    """Reduz pela mesma regra de largura do resto do projeto; nunca amplia."""
    if scale_pct == 100:
        return img
    largura = target_width(info.width, scale_pct)
    if largura >= img.width:
        return img
    altura = max(1, round(img.height * largura / img.width))
    return img.resize((largura, altura), Image.Resampling.LANCZOS)


def codificar(
    src: Path, dst: Path, params: Params, info: ImagemInfo, tools: Tools
) -> int:
    """Codifica `src` em `dst` com `params`. Devolve o tamanho de `dst`.

    Sempre parte do arquivo original, nunca do resultado de uma tentativa
    anterior, e nunca escreve em `src`.

    Sem perda (`colors=None` e escala 100%) o arquivo é copiado como está
    antes do oxipng, em vez de decodificado e salvo de novo: assim nenhum
    modo, profundidade de bits ou chunk passa por conversão do Pillow.
    Sem oxipng instalado esse estágio é pulado — o modo degradado, como o GIF
    sem gifsicle: os degraus com perda saem do Pillow (com `optimize`) e os
    sem perda ficam iguais à entrada.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if params.colors is None and params.scale_pct == 100:
            shutil.copyfile(src, dst)
        else:
            with Image.open(src) as aberta:
                # `resize` e `quantize` não carregam todo o `info`; o perfil
                # ICC sai de `aberta` e vai explícito ao `save`, senão uma
                # imagem de gamut largo mudaria de cor em silêncio.
                perfil = aberta.info.get("icc_profile")
                img = _para_rgb_ou_rgba(_para_8_bits(aberta))
                img = _escalar(img, info, params.scale_pct)
                if params.colors is not None:
                    img = _quantizar(img, params.colors)
                img.save(dst, format="PNG", optimize=True, icc_profile=perfil)

        if tools.oxipng is not None:
            executar(build_oxipng_cmd(tools, dst, params), "oxipng")

        if not dst.is_file():
            raise EncodeError("A codificação não produziu arquivo de saída.")
        return dst.stat().st_size
    except EncodeError:
        dst.unlink(missing_ok=True)
        raise
    except (OSError, ValueError) as exc:
        dst.unlink(missing_ok=True)
        raise EncodeError(f"Falha ao gerar o PNG: {exc}") from exc
