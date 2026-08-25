# Compressor de GIF local — design

**Data:** 2026-08-21
**Status:** aprovado

## Objetivo

App local que reduz o peso de arquivos GIF ao máximo, perdendo o mínimo de
qualidade visual. O usuário arrasta GIFs para uma janela, informa um
tamanho-alvo, e o app busca automaticamente a melhor qualidade que cabe
nesse limite.

Roda offline. Nada é instalado no sistema.

## Escopo

**Dentro:**

- GUI drag-and-drop (Tkinter), com lote de vários arquivos
- Busca automática do melhor resultado para um tamanho-alvo
- Quatro alavancas de compressão, cada uma ligável/desligável pelo usuário
- Download portátil do `gifsicle.exe` para `bin/`

**Fora (YAGNI):**

- Preview animado lado a lado (esforço alto no Tkinter, valor baixo)
- Modo "sem perda visual" por métrica (SSIM/butteraugli)
- Conversão para MP4/WebM/WebP — a saída é sempre GIF
- Edição (corte, crop, texto)
- Empacotamento em `.exe` distribuível

## Ambiente

Verificado na máquina alvo:

| Ferramenta | Estado |
|---|---|
| ffmpeg / ffprobe | 9.0-full, no PATH |
| Python | 3.12.10 |
| gifsicle | **ausente** — será baixado para `bin/` |
| ImageMagick | ausente, não é necessário |

Dependência Python única: `tkinterdnd2` (drag-and-drop nativo do Explorer).

## Pipeline de compressão

Cada tentativa encadeia dois estágios sobre o arquivo original (nunca sobre
o resultado da tentativa anterior — evita perda acumulada):

### Estágio 1 — ffmpeg

Reconstrói o GIF com paleta otimizada, aplicando escala e framerate:

```
ffmpeg -y -i <src>
  -vf "fps=<FPS>,scale=<W>:-1:flags=lanczos,split[a][b];
       [a]palettegen=max_colors=<N>:stats_mode=diff[p];
       [b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
  <tmp>
```

Três escolhas carregam a maior parte do ganho:

- **`stats_mode=diff`** — a paleta é otimizada para as regiões que mudam
  entre frames, não para a média da imagem inteira. Em animação isso rende
  uma paleta bem mais útil pelo mesmo número de cores.
- **`dither=bayer:bayer_scale=5`** — dithering ordenado, não
  floyd-steinberg. Floyd produz ruído *diferente a cada frame*, o que
  destrói a compressão inter-frame do GIF e infla o arquivo. Bayer produz um
  padrão estático, que comprime muito melhor. Contraintuitivo, mas é o que
  economiza peso sem borrar a imagem.
- **`diff_mode=rectangle`** — só reescreve o retângulo que mudou entre
  frames.

`fps` é omitido do filtro quando o alvo é igual ao original. `scale` é
omitido quando a escala é 100%. Nunca faz upscale nem aumenta framerate.

A largura alvo é `int(largura_original × escala)`, elevada ao próximo par se
for ímpar, com mínimo de 16px. A altura é `-1` (proporcional). Isso evita
larguras degeneradas em GIFs muito pequenos e mantém o resultado previsível.

> A primeira versão desta spec dizia `round(.../2) × 2`, o que contradizia os
> próprios casos de teste: o `round` do Python arredonda para o par mais
> próximo, então 33 daria 32 e não 34. A fórmula acima é a que está no código.

### Estágio 2 — gifsicle

```
gifsicle -O3 --lossy=<L> <tmp> -o <dst>
```

`-O3` é a otimização máxima (inter-frame, transparência). `--lossy` é a
alavanca mais eficiente do conjunto: reaproveita cores entre pixels
vizinhos, tipicamente cortando 30-60% com artefato leve. Quando `L=0`, a
flag `--lossy` é omitida (fica só a otimização sem perda).

## A escada de qualidade

Testar todas as combinações de (cores × lossy × escala × fps) é lento
demais. Em vez disso, o app trabalha sobre uma **escada de 20 degraus
ordenada do sacrifício menos perceptível ao mais agressivo**:

| # | Cores | Lossy | Escala | FPS |
|---|---|---|---|---|
| 0 | 256 | 0 | 100% | orig |
| 1 | 256 | 20 | 100% | orig |
| 2 | 256 | 30 | 100% | orig |
| 3 | 128 | 30 | 100% | orig |
| 4 | 128 | 45 | 100% | orig |
| 5 | 128 | 60 | 100% | orig |
| 6 | 64 | 60 | 100% | orig |
| 7 | 64 | 80 | 100% | orig |
| 8 | 64 | 100 | 100% | orig |
| 9 | 64 | 100 | 85% | orig |
| 10 | 64 | 120 | 85% | orig |
| 11 | 64 | 120 | 70% | orig |
| 12 | 64 | 140 | 70% | orig |
| 13 | 64 | 140 | 70% | 20 |
| 14 | 64 | 140 | 70% | 15 |
| 15 | 64 | 140 | 70% | 12 |
| 16 | 32 | 140 | 70% | 12 |
| 17 | 32 | 160 | 70% | 12 |
| 18 | 32 | 160 | 50% | 12 |
| 19 | 32 | 200 | 50% | 10 |

A ordem reflete o custo visual: paleta e lossy primeiro (quase invisíveis),
resolução depois, framerate por último — porque perda de fluidez é o que
mais salta aos olhos.

**Invariante de monotonicidade.** Cada degrau é fracamente mais agressivo que
o anterior em *todas* as quatro dimensões ao mesmo tempo: as cores nunca
sobem, o lossy nunca desce, a escala nunca sobe e o fps nunca sobe (`orig`
conta como o mais alto). Isso não é estética — é o que garante que o tamanho
codificado seja monotonicamente não-crescente, e é essa garantia que torna a
busca binária correta. Uma escada que troca uma dimensão por outra (por
exemplo, menos cores mas mais fps) pode produzir um arquivo *maior* num
degrau seguinte, e nesse caso a bisecção pode descartar uma região inteira
que continha um resultado de qualidade melhor. Um teste trava essa invariante
degrau a degrau; se você editar a tabela, ele falha antes que o defeito
chegue à busca.

### Efeito dos toggles

Cada checkbox desligada **fixa sua coluna no valor neutro** e a escada é
então deduplicada preservando a ordem (mantém a primeira ocorrência de cada
tupla distinta):

| Toggle desligado | Coluna fixada em |
|---|---|
| Reduzir paleta de cores | 256 |
| Compressão lossy | 0 |
| Reduzir resolução | 100% |
| Reduzir framerate | original |

Com as quatro desligadas, a escada colapsa para um único degrau: o degrau 0,
que ainda assim aplica `gifsicle -O3` (recompressão sem perda) e costuma
economizar algo.

Degraus cujo FPS alvo seja maior ou igual ao FPS original têm a coluna FPS
tratada como "orig" antes da deduplicação — evita degraus idênticos quando o
GIF de entrada já é de baixo framerate.

## Algoritmo de busca

Objetivo: encontrar o **menor índice** da escada cujo resultado caiba no
alvo — ou seja, a maior qualidade que atende ao limite de tamanho.

Pela invariante de monotonicidade da escada, o tamanho é não-crescente ao
longo dela, então isso é uma busca binária de lower bound: ~5 encodes em vez
de 20. A bisecção só é correta *por causa* dessa invariante — ver a seção
anterior.

```
lo, hi = 0, n-1
melhor_valido  = None    # menor TAMANHO entre os que cabem no alvo
menor_absoluto = None    # menor TAMANHO visto, cabendo ou não

enquanto lo <= hi:
    mid = (lo + hi) // 2
    tamanho = encode(mid)

    se menor_absoluto é None ou tamanho < menor_absoluto.tamanho:
        menor_absoluto = (mid, tamanho)

    se tamanho <= alvo:
        # entre os válidos, prefere o de MENOR índice (mais qualidade);
        # em empate de índice impossível, o de menor tamanho
        se melhor_valido é None ou mid < melhor_valido.indice:
            melhor_valido = (mid, tamanho)
        hi = mid - 1
    senão:
        lo = mid + 1

devolve melhor_valido se existir, senão menor_absoluto
```

Cada tentativa `i` escreve em `<tmpdir>/try_<i>.gif` e os arquivos são
mantidos até o fim da busca — assim o vencedor não precisa ser reencodado.
Ao terminar, o arquivo vencedor é movido para o destino final e o `tmpdir`
inteiro é removido.

Garantias:

- **Não desperdiça qualidade.** Se o degrau 0 já cabe no alvo, é ele que
  sai. A busca binária chega nele em ~5 tentativas.
- **Best-so-far.** A invariante garante a ordenação dos parâmetros, mas o
  codificador tem ruído próprio: um degrau pode ocasionalmente sair um pouco
  maior que o anterior. Como todo resultado válido visto é registrado, o resultado
  entregue nunca é pior que o melhor já encontrado durante a busca.
- **Alvo inatingível.** Se nenhum degrau cabe, entrega o `menor_absoluto` e
  informa o tamanho na interface, sem tratar como erro.
- **Resultado maior que o original.** Se o melhor resultado ficar acima do
  tamanho do arquivo de entrada (acontece com GIFs já muito otimizados), o
  app copia o original para o destino e informa "já estava otimizado".
- **Sem codificação repetida.** A bisecção nunca revisita um índice, então
  cada degrau é codificado no máximo uma vez por arquivo.

## Estrutura de módulos

```
gifC/
  bin/
    gifsicle.exe            baixado no primeiro uso (fora do git)
  gifc/
    __init__.py
    tools.py                localiza ffmpeg/ffprobe, baixa o gifsicle
    probe.py                metadados do GIF via ffprobe
    ladder.py               a escada de 20 degraus (puro)
    search.py               busca binária até o alvo (puro)
    encode.py               executa ffmpeg + gifsicle para UM Params
    compress.py             orquestra probe -> escada -> busca -> arquivo
    app.py                  GUI Tkinter
  main.py                   entrypoint
  tests/
    conftest.py             fixtures `tools` e `gif_pequeno`
    test_tools.py  test_probe.py  test_ladder.py  test_search.py
    test_encode.py  test_compress.py  test_app.py
  requirements.txt
  docs/superpowers/
```

### Contratos

**`tools.find_tools(bin_dir=BIN_DIR) -> Tools`**
`Tools(ffmpeg, ffprobe, gifsicle)`, congelada. `gifsicle` é `None` quando o
binário não está disponível — o app segue em modo degradado, sem o estágio
lossy. Levanta `MissingToolError` (com atributo `.tool`) se faltar ffmpeg ou
ffprobe. `download_gifsicle(bin_dir, on_progress=None) -> Path` busca o
binário portátil, confere o SHA256 fixado e levanta `DownloadError` em
qualquer falha — inclusive de escrita em disco, para que o app caia em modo
degradado em vez de morrer na inicialização.

**`probe.probe(path, tools) -> GifInfo`**
`GifInfo(path, width, height, fps, frame_count, duration, size_bytes)`,
congelada. Só leitura. Levanta `ProbeError` se o arquivo não for um GIF
válido. Quando o `avg_frame_rate` do arquivo é inutilizável, `fps` cai para
`FPS_PADRAO` (10.0) e a `duration` fica `0.0` em vez de ser derivada do
palpite — um "não sei" honesto vale mais que um número inventado.

**`ladder.build(info, toggles) -> list[Params]`**
Função pura. `Params(colors, lossy, scale_pct, fps)` é congelada e hashável;
`fps=None` significa manter o framerate original. `Toggles(palette, lossy,
scale, fps)` são os quatro booleanos da interface. Devolve a escada filtrada
e deduplicada, preservando a ordem. `target_width(width, scale_pct) -> int`
dá a largura par com piso de 16px.

**`search.find_best(ladder, target_bytes, encode_fn, on_progress=None, should_cancel=None) -> SearchResult`**
`encode_fn(index, params) -> int` é injetado, então a busca é testável com
um codificador falso de curva conhecida, em milissegundos. Um degrau que
falha sinaliza `RungFailed(mensagem)`; a busca registra o motivo no `Attempt`
e o trata como grande demais, seguindo adiante em vez de derrubar o arquivo.
`SearchResult(index, params, size_bytes, hit_target, attempts)`;
`Attempt(index, params, size_bytes, erro=None)`. `on_progress(attempt,
total)` alimenta o log da UI. Levanta `Cancelled` se `should_cancel()` virar
verdadeiro entre tentativas.

**`encode.encode(src, dst, params, info, tools) -> int`**
Roda os dois estágios e devolve o tamanho de `dst`. Parte sempre do arquivo
original, nunca do resultado de uma tentativa anterior, para não acumular
perda. Usa um temporário para o intermediário e o remove em `finally`.
Levanta `EncodeError` com o stderr da ferramenta que falhou.

**`compress.compress_file(src, target_bytes, toggles, tools, on_progress=None, should_cancel=None) -> CompressResult`**
A única porta de entrada que a GUI usa. `CompressResult(src, dst,
original_bytes, final_bytes, params, hit_target, already_optimized,
attempts)` com a propriedade `reduction_pct`. Um degrau que falha é
retentado uma vez antes de ser dado como perdido; se todos falharem, levanta
`CompressError`. Sem gifsicle, desliga a coluna lossy sozinho para não
gastar codificações em degraus idênticos.

A separação importa: `ladder` e `search` concentram toda a lógica de decisão
e não têm I/O, então são testáveis de forma rápida e determinística.
`encode` e `probe` concentram o I/O e têm superfície pequena.

## Interface

Janela única, redimensionável, ~560×620.

- **Área de drop** grande, aceita múltiplos GIFs de uma vez. Também um botão
  "Escolher arquivos..." como alternativa. Arquivos que não sejam `.gif` são
  rejeitados no drop com aviso.
- **Alvo:** campo numérico em MB + botões de preset: `Discord 10` ·
  `WhatsApp 16` · `5` · `2`.
- **Quatro checkboxes** das alavancas, todas ligadas por padrão.
- **Progresso:** barra + log ao vivo, uma linha por tentativa
  (`tentativa 3/5 · 64 cores · lossy 80 · 100% · 30fps → 11.2 MB`).
- **Resultado por arquivo:** `entrada.gif  24.8 MB → 9.4 MB  (−62%)`, com
  aviso quando o alvo não foi atingido ou quando o arquivo já estava
  otimizado.
- **Botão "abrir pasta"** ao terminar.

Saída em `<nome>_comprimido.gif`, na mesma pasta do original. **Nunca
sobrescreve**: se o nome já existir, sufixa `_2`, `_3`, etc.

Em lote, os arquivos são processados em sequência, todos com o mesmo alvo e
os mesmos toggles.

A compressão roda em uma thread de trabalho; a UI é atualizada via
`queue.Queue` drenada por um `after()` na thread principal do Tk — nada de
tocar em widget fora da thread da UI. Botão "cancelar" que sinaliza a thread
entre tentativas.

## Tratamento de erros

| Situação | Comportamento |
|---|---|
| ffmpeg ausente | Mensagem nomeando a ferramenta, app segue aberto |
| gifsicle ausente | Diálogo oferecendo baixar para `bin/`, mostrando a URL |
| Arquivo não é GIF | Rejeitado no drop, com aviso; demais arquivos seguem |
| GIF corrompido | Falha só aquele arquivo; o lote continua |
| ffmpeg/gifsicle retorna erro | Log recebe o stderr; aquele degrau é pulado |
| Alvo inatingível | Entrega o menor possível, avisa o tamanho |
| Resultado ≥ original | Copia o original, avisa "já estava otimizado" |
| Sem espaço / permissão | Mensagem clara, temporários limpos |

Temporários vão para uma pasta dedicada em `tempfile.mkdtemp()`, removida em
`finally` mesmo em caso de erro ou cancelamento.

## Obtenção do gifsicle

`gifsicle.exe` (~200 KB, estático, portátil) é baixado para `bin/` no
primeiro uso. Antes do download, a URL exata e o tamanho são mostrados ao
usuário para confirmação. Nada é escrito fora da pasta do projeto e nada é
registrado no sistema — apagar `gifC/` remove tudo.

Se o download falhar, o app segue funcional em modo degradado: a escada
descarta a coluna lossy (equivalente ao toggle desligado) e o segundo
estágio é pulado. A interface avisa que a compressão está limitada.

## Testes

**`test_ladder.py`** — unitários puros, sem I/O:

- escada completa tem 20 degraus e é estritamente ordenada em agressividade
- cada toggle desligado fixa sua coluna no valor neutro
- deduplicação preserva a ordem e a primeira ocorrência
- quatro toggles desligados → escada de exatamente 1 degrau
- GIF de entrada com FPS baixo (ex: 10) não gera degraus de FPS redundantes

**`test_search.py`** — encoder falso com curva de tamanho conhecida:

- encontra o menor índice válido (a maior qualidade que cabe)
- usa O(log n) chamadas, não O(n)
- degrau 0 já válido → devolve o degrau 0
- nenhum degrau válido → devolve o menor absoluto com `hit_target=False`
- curva não-monotônica → o best-so-far garante que o resultado não piora
- cada índice é encodado no máximo uma vez (memoização)

**`test_encode.py`** — integração, um único teste:

Gera um GIF minúsculo com ffmpeg (`testsrc`, 10 frames, 64×64), roda
`encode` com um `Params` conhecido e verifica que o arquivo de saída existe,
é um GIF válido e tem tamanho > 0. Marcado para pular automaticamente se as
ferramentas não estiverem disponíveis.

A GUI não é testada automaticamente — é validada manualmente com um GIF
real.

## Critérios de sucesso

1. Arrastar um GIF de ~25 MB com alvo de 10 MB produz um arquivo abaixo de
   10 MB em menos de ~60 segundos, visualmente próximo do original.
2. Um GIF que já cabe no alvo sai no degrau 0, sem degradação desnecessária.
3. Desligar os toggles de resolução e framerate faz o app respeitar
   estritamente as dimensões e a fluidez originais.
4. A janela nunca congela durante a compressão.
5. Nenhum arquivo original é modificado ou sobrescrito, em nenhum caminho de
   execução.
