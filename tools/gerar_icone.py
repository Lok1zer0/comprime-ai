"""Gera `assets/icone.ico` a partir de `assets/cai-logo.png`.

O logo é largo (o conteúdo visível ocupa 94% da largura e 56% da altura de um
PNG quadrado) e traz uma sombra de alfa muito baixo que vai até as bordas. Usar
o arquivo cru como ícone desperdiçaria quase metade do quadrado: o desenho sairia
a 56% da altura, fraco justamente em 16 e 32px, que são os tamanhos que aparecem
na barra de tarefas e na barra de título.

Por isso o recorte usa um LIMIAR de alfa em vez de `getbbox()`: a sombra tem
alfa baixo mas diferente de zero, então `getbbox()` devolve quase a imagem
inteira e não serve para enquadrar.

O `.ico` não é commitado: `assets/cai-logo.png` é a única fonte da verdade, e
binário commitado dessincroniza do desenho. Quem chama este módulo é o
empacotamento (`comprime-ai.spec`, via `tools/build.py`) e, no modo de
desenvolvimento, `tema.icone_da_janela` na primeira janela.

Rodar à mão: python tools/gerar_icone.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

RAIZ = Path(__file__).resolve().parent.parent
ORIGEM = RAIZ / "assets" / "cai-logo.png"
DESTINO = RAIZ / "assets" / "icone.ico"

# Abaixo disto é sombra, não desenho. Medido: com 0 o recorte pega 99%x96%
# da imagem; com 16 pega os 94%x56% que de fato se enxergam.
LIMIAR_DE_ALFA = 16

# Margem como fração do lado, para o desenho não encostar na borda do ícone.
MARGEM = 0.04

# O Windows escolhe o quadro mais próximo do tamanho que precisa em cada
# escala de DPI. Sem o 16 ele reduz o 32 e borra; e sem os intermediários 20,
# 40 e 96 ele estica o vizinho nas escalas de 125%, 150% e 175%, que são as
# que a maioria dos monitores usa hoje.
TAMANHOS = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def recorta_conteudo(img: Image.Image, limiar: int = LIMIAR_DE_ALFA) -> Image.Image:
    """Corta a moldura transparente, ignorando a sombra de alfa baixo."""
    alfa = img.getchannel("A")
    caixa = alfa.point(lambda v: 255 if v > limiar else 0).getbbox()
    return img.crop(caixa) if caixa else img


def em_quadrado(img: Image.Image, margem: float = MARGEM) -> Image.Image:
    """Centraliza num quadrado transparente, preservando a proporção."""
    lado = round(max(img.width, img.height) * (1 + 2 * margem))
    quadro = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    quadro.paste(img, ((lado - img.width) // 2, (lado - img.height) // 2))
    return quadro


def gerar(origem: Path = ORIGEM, destino: Path = DESTINO) -> Path:
    """Escreve o `.ico` multi-resolução e devolve o caminho."""
    if not origem.is_file():
        raise FileNotFoundError(f"Logo não encontrado: {origem}")
    with Image.open(origem) as bruto:
        quadro = em_quadrado(recorta_conteudo(bruto.convert("RGBA")))
    destino.parent.mkdir(parents=True, exist_ok=True)
    # O Pillow gera cada tamanho a partir do quadro, com LANCZOS.
    quadro.save(destino, format="ICO", sizes=[(n, n) for n in TAMANHOS])
    return destino


if __name__ == "__main__":
    caminho = gerar()
    print(f"{caminho}  ({caminho.stat().st_size} bytes, {len(TAMANHOS)} tamanhos)")
