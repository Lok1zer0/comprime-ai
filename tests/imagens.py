"""Imagens sintéticas compartilhadas pelos testes que medem codificadores de
verdade. Determinísticas: nenhuma depende de sorteio sem semente."""

from __future__ import annotations

import random

from PIL import Image, ImageDraw


def textura(tamanho: tuple[int, int] = (640, 480)) -> Image.Image:
    """Ruído de baixa frequência sobre um degradê, com bordas nítidas:
    compressível, mas não trivial, como uma foto."""
    rng = random.Random(7)
    largura, altura = tamanho
    pequeno = Image.frombytes(
        "RGB", (80, 60), bytes(rng.randrange(256) for _ in range(80 * 60 * 3))
    )
    img = pequeno.resize(tamanho, Image.Resampling.BICUBIC)
    grad = Image.linear_gradient("L").resize(tamanho)
    img = Image.blend(img, Image.merge("RGB", (grad, grad, grad)), 0.5)
    d = ImageDraw.Draw(img)
    d.rectangle((20, 20, 100, 80), outline=(255, 255, 255), width=3)
    d.ellipse(
        (largura // 3, altura // 3, largura // 3 + 80, altura // 3 + 80),
        fill=(220, 40, 40),
    )
    return img


def captura_de_tela(largura: int = 1280, altura: int = 720) -> Image.Image:
    """Arte chapada como uma captura de tela: fundo liso, texto sem
    degradê e, embaixo, uma hachura de duas cores. É o tipo de imagem em que
    o WebP sem perda é DEZENAS de vezes menor que o q90."""
    img = Image.new("RGB", (largura, altura), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, largura, altura // 16), fill=(32, 33, 36))
    d.rectangle((0, altura // 16, largura // 6, altura), fill=(243, 244, 246))
    for i in range(altura // 30):
        d.text((10, altura // 12 + i * 20), f"Item de menu numero {i}", fill=(60, 60, 60))
    for y in range(altura * 2 // 3, altura - 4, 2):
        d.line((largura // 5, y, largura - 20, y), fill=(0, 0, 0))
    for x in range(largura // 2, largura - 20, 8):
        d.line((x, altura * 2 // 3, x, altura - 4), fill=(200, 30, 30))
    return img


def degrade(largura: int = 1280, altura: int = 800) -> Image.Image:
    """Um degradê linear de cinza: liso, sem nenhuma aresta."""
    return Image.linear_gradient("L").resize((largura, altura)).convert("RGB")


def degrade_horizontal(largura: int = 1280, altura: int = 800) -> Image.Image:
    """O mesmo degradê, na horizontal."""
    vertical = Image.linear_gradient("L").resize((largura, altura))
    return vertical.rotate(90, expand=True).resize((largura, altura)).convert("RGB")


def radial(largura: int = 1280, altura: int = 800) -> Image.Image:
    """Um degradê radial de cinza: curvas de nível em todas as direções."""
    return Image.radial_gradient("L").resize((largura, altura)).convert("RGB")


def foto(largura: int = 640, altura: int = 480) -> Image.Image:
    """Ruído de baixa frequência: o pior caso para quem quantiza, e o
    melhor para o JPEG e o WebP com perda."""
    rng = random.Random(1)
    n = (largura // 8) * (altura // 8) * 3
    pequeno = Image.frombytes(
        "RGB", (largura // 8, altura // 8), bytes(rng.randrange(256) for _ in range(n))
    )
    return pequeno.resize((largura, altura), Image.Resampling.BICUBIC)
