# gif-compressor

Compressor de GIF local. Você arrasta os arquivos, define um tamanho-alvo, e
ele busca automaticamente a melhor qualidade que cabe nesse limite.

Roda offline, em Python, com uma GUI drag-and-drop.

## Como funciona

Cada tentativa encadeia dois estágios:

```
ffmpeg  →  palettegen/paletteuse (paleta otimizada, escala, fps)
gifsicle → -O3 --lossy
```

Em vez de testar todas as combinações de parâmetros, o app percorre uma
**escada de qualidade** de 20 degraus — ordenada do sacrifício menos
perceptível ao mais agressivo — com busca binária. São ~5 codificações em vez
de 20, e o resultado é sempre a maior qualidade que atende ao alvo.

Quatro alavancas, cada uma ligável ou desligável na interface: paleta de
cores, compressão lossy, resolução e framerate.

## Estado

Em desenvolvimento. O design completo está em
[`docs/superpowers/specs/`](docs/superpowers/specs/2026-08-21-gif-compressor-design.md).

## Requisitos

- Python 3.12+
- ffmpeg / ffprobe no PATH
- gifsicle (baixado para `bin/` no primeiro uso)
