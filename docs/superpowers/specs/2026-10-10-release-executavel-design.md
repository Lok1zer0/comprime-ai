# Release v1 — compressor multi-formato com executável para leigos

**Data:** 2026-10-10
**Status:** aprovado

> **Revisão de escopo.** A primeira versão desta spec cobria apenas o
> empacotamento do compressor de GIF. O dono do projeto decidiu incluir
> compressão de imagens comuns na mesma v1. Isso atravessa quase todos os
> módulos e torna o Pillow uma dependência de runtime, quebrando a restrição
> anterior de "só `tkinterdnd2`". O repositório foi renomeado de
> `gif-compressor` para **`comprime-ai`** antes de qualquer link ser
> divulgado.

## Objetivo

Publicar uma release no GitHub que qualquer pessoa — sem Python, sem ffmpeg,
sem terminal — consiga baixar e usar para reduzir o peso de **GIFs, PNGs,
JPEGs e WebPs**, perdendo o mínimo de qualidade possível. O link precisa ser
apresentável num post de LinkedIn e o caminho do clique até o primeiro
arquivo comprimido precisa ser curto o bastante para não perder ninguém.

## Decisões tomadas

| Questão | Decisão |
|---|---|
| Formatos na v1 | GIF, PNG, JPEG e WebP — tudo junto |
| Conversão de formato | Mantém o original por padrão; WebP como caixa opcional |
| Como entregar as ferramentas | Tudo empacotado junto; funciona offline |
| `ffprobe` | Removido, substituído por leitura de GIF em Python puro |
| Onde montar | Nesta máquina, validando antes de publicar |
| Licença | GPL-3.0 no projeto, com arquivo de terceiros |
| Nome | `comprime-ai` |

## Escopo

**Dentro:**

- Compressão de GIF (pipeline atual, inalterado)
- Compressão de PNG, JPEG e WebP — WebP também é aceito como **entrada**
- Conversão opcional para WebP a partir de PNG e JPEG
- Parser de metadados de GIF em Python puro, aposentando o `ffprobe`
- Descoberta de ferramentas dentro do pacote congelado
- Empacotamento com PyInstaller, com ícone próprio
- `LICENSE` (GPL-3.0) e `THIRD-PARTY-LICENSES.md`
- README para duas audiências e release `v1.0.0`

**Fora (YAGNI):**

- AVIF — o Pillow desta máquina não traz o módulo
- WebP e PNG animados — tratados como estáticos seria errado, e tratá-los
  direito é outro problema; são recusados com mensagem clara
- Assinatura de código — certificado custa mais que o projeto vale hoje
- Instalador MSI/NSIS
- Build em GitHub Actions
- macOS e Linux — os binários são win64 e o público é Windows
- Edição de imagem (corte, rotação, marca d'água)

## Arquitetura multi-formato

A engine existente foi construída em torno de uma ideia que **não é
específica de GIF**: percorrer uma escada de qualidade ordenada do sacrifício
menos ao mais perceptível, e buscar nela por bisecção o degrau de maior
qualidade cujo resultado caiba num alvo de tamanho. Essa ideia vale para
qualquer formato com um botão de qualidade.

O que isso implica para cada módulo:

| Módulo | Mudança |
|---|---|
| `search.py` | **Nenhuma.** Já é genérico: só precisa de uma sequência de parâmetros e de uma função que codifique e devolva bytes. A anotação `Params` é acidente de origem e vira um `TypeVar`. |
| `ladder.py` | Deixa de ter uma escada e passa a ter uma por formato |
| `probe.py` | `GifInfo` vira `ImagemInfo`, com um campo `formato` |
| `encode.py` | Despacha para o codificador do formato |
| `compress.py` | Escolhe escada e codificador pelo formato detectado |
| `app.py` | Aceita mais extensões, desabilita alavancas que não se aplicam, ganha a caixa de WebP |

Nasce um pacote `gifc/formatos/`, um módulo por formato, cada um dono da sua
tabela de degraus e do seu codificador. É o que mantém os módulos focados em
vez de inflar `ladder.py` e `encode.py` com condicionais de formato.

O que **sobra** em `ladder.py` é o que é comum a todos os formatos: o tipo
`Toggles`, a função `target_width`, e a lógica genérica que aplica os toggles
a uma tabela e deduplica preservando a ordem. Cada módulo de formato fornece
a tabela; `ladder.py` continua sendo quem a filtra.

**O pacote Python continua chamando `gifc`**, mesmo com o repositório
renomeado para `comprime-ai`. Renomear o pacote tocaria todos os imports e
todos os arquivos de teste sem entregar nada ao usuário — é churn. A
divergência entre nome de repositório e nome de pacote é comum e inofensiva.

**A invariante de monotonicidade passa a valer por escada.** Cada formato tem
a sua, e cada uma precisa satisfazer a mesma propriedade: cada degrau é
fracamente mais agressivo que o anterior em todas as suas dimensões. Sem
isso a bisecção fica incorreta — exatamente o defeito que já corrigimos uma
vez na escada de GIF. O teste de propriedade existente passa a ser
parametrizado por formato.

## Pipelines por formato

| Formato | Pipeline |
|---|---|
| **GIF** | `ffmpeg` (palettegen/paletteuse) → `gifsicle -O3 --lossy` |
| **PNG** | Pillow `quantize()` → `oxipng` |
| **JPEG** | Pillow, reempacotamento com qualidade e subamostragem; EXIF e ICC preservados, CMYK segue CMYK |
| **WebP** | Pillow, `quality` com `method=6` |

O PNG espelha a forma do GIF de propósito: um passo com perda que reduz
paleta, seguido de um otimizador sem perda. `oxipng` substitui o `pngquant`,
que seria a escolha natural para a quantização, porque **`pngquant.org` não
resolve** — sem URL verificável não entra em script de build. O
`Image.quantize()` do Pillow faz o mesmo trabalho com algoritmo um pouco
inferior, e dispensa um binário.

### O que medimos sobre PNG

Medição real, PNG de 1280×800, `optimize=True` em todos os casos:

| Entrada | 256 cores | 64 cores | 32 cores |
|---|---|---|---|
| Interface / captura de tela | **−59%** | −66% | −69% |
| Gradiente **linear** | **+26%** | −40% | −57% |
| Gradiente **radial** | **−60%** | — | — |

> **Correção.** A primeira versão desta spec chamava a linha do meio de "foto /
> gradiente suave" e concluía que "PNG fotográfico fica maior". Isso foi uma
> generalização indevida de um único fixture. Medindo de novo: o crescimento de
> +26% acontece com gradiente **linear**, que os filtros de linha do PNG
> predizem quase perfeitamente — a paleta mais os índices custam mais do que o
> filtro já economizava. Um gradiente radial da mesma dimensão **encolhe 60%**,
> e uma fotografia real, sendo ruidosa, se parece muito mais com o caso radial.
> Então: para fotos de verdade a quantização normalmente reduz.

Duas consequências de desenho:

**PNG de interface é o caso que importa e comprime muito bem** — 59% já na
redução mais conservadora.

**Um degrau pode produzir arquivo maior que a entrada.** Em imagens muito
suaves e previsíveis, o custo da paleta e dos índices supera o que os filtros
de linha já economizavam. Não é o caso comum — é o caso que existe — e é
por isso que o degrau 0 da escada de PNG **não quantiza nada**: é só
`oxipng`, sem perda, garantindo um piso. Para PNG fotográfico pesado, a
conversão para WebP tende a render mais que a quantização.

A engine já trata isso corretamente: `menor_absoluto` garante que um degrau
que aumentou o arquivo não piore o resultado, e `already_optimized` devolve o
original quando nada o superou. Foi construído para GIF e é exatamente o
comportamento certo aqui.

### JPEG é perda sobre perda

Diferente de GIF e PNG, um JPEG de entrada já é lossy. Reempacotar degrada de
novo, inclusive no degrau mais conservador. A escada começa em qualidade 92 e
a interface **nunca promete ausência de perda** para JPEG — dizer o contrário
seria mentira.

## Escadas

Cada escada satisfaz a invariante: nenhuma dimensão fica menos agressiva ao
descer um degrau. `None` conta como o extremo menos agressivo.

### PNG — `quantize` + `oxipng`

| # | Cores | oxipng | Escala |
|---|---|---|---|
| 0 | nenhuma | 2 | 100% |
| 1 | nenhuma | 4 | 100% |
| 2 | 256 | 4 | 100% |
| 3 | 128 | 4 | 100% |
| 4 | 64 | 4 | 100% |
| 5 | 32 | 4 | 100% |
| 6 | 32 | 4 | 85% |
| 7 | 16 | 4 | 85% |
| 8 | 16 | 4 | 70% |
| 9 | 16 | 4 | 50% |

Degraus 0 e 1 são **sem perda**: só recompressão. `cores = None` significa
"não quantizar" e é o extremo menos agressivo da coluna.

### JPEG — Pillow

| # | Qualidade | Subamostragem | Escala |
|---|---|---|---|
| 0 | 92 | igual à fonte | 100% |
| 1 | 88 | igual à fonte | 100% |
| 2 | 85 | 4:2:0 | 100% |
| 3 | 80 | 4:2:0 | 100% |
| 4 | 75 | 4:2:0 | 100% |
| 5 | 70 | 4:2:0 | 100% |
| 6 | 65 | 4:2:0 | 100% |
| 7 | 60 | 4:2:0 | 85% |
| 8 | 55 | 4:2:0 | 85% |
| 9 | 50 | 4:2:0 | 70% |
| 10 | 45 | 4:2:0 | 70% |
| 11 | 40 | 4:2:0 | 50% |

`progressive=True` em todos os degraus: economiza alguns por cento sem custo
de qualidade, então não é uma alavanca, é um padrão.

**Os degraus 0 e 1 mantêm a subamostragem da fonte**, em vez de forçar 4:4:4
como dizia a primeira versão desta spec. Medido: num JPEG de câmera (q95,
4:2:0), o degrau 0 forçando 4:4:4 saía a **96,7% do original** — quase nada,
porque 4:4:4 devolve resolução de cor que a fonte nunca teve. Mantendo a
subamostragem da fonte, o degrau 0 fica em ~74%, e uma fonte 4:4:4 preserva
4:4:4 até o degrau 2.

> **Cuidado com o `-1`.** No `Params` o valor `-1` é *nosso* e significa
> "igual à fonte"; ele é resolvido lendo a fonte aberta com
> `JpegImagePlugin.get_sampling`. **Nunca repasse `-1` ao Pillow**, onde ele
> é o padrão do libjpeg e vale 4:2:0 — verificado byte a byte. E `"keep"` não
> serve: o Pillow o recusa quando a imagem a gravar é uma cópia, o que é
> sempre o caso depois de `exif_transpose` ou `resize`. Passar `-1` adiante
> degradaria em silêncio toda fonte 4:4:4 justamente no degrau mais
> conservador.

### WebP — Pillow

| # | Qualidade | Escala |
|---|---|---|
| 0 | 90 | 100% |
| 1 | 85 | 100% |
| 2 | 80 | 100% |
| 3 | 75 | 100% |
| 4 | 70 | 100% |
| 5 | 65 | 100% |
| 6 | 60 | 85% |
| 7 | 55 | 85% |
| 8 | 50 | 70% |
| 9 | 45 | 50% |

`method=6` fixo em todos: é o ajuste mais lento e que comprime melhor, e é um
botão de tempo, não de qualidade. Não entra na escada.

### GIF

Inalterada. A tabela de 20 degraus permanece como está, com a invariante já
travada por teste.

## Conversão para WebP

Desligada por padrão. Quando ligada, um PNG ou JPEG de entrada usa a escada
de WebP e a saída sai `.webp`. GIF animado não participa: a conversão de
animação é outro problema e está fora de escopo.

A caixa fica desabilitada quando os arquivos soltos não admitem conversão,
em vez de aceitar o clique e ignorá-lo silenciosamente.

## Aposentar o ffprobe

### Por que

Os builds de ffmpeg para Windows são estáticos: cada executável carrega tudo
embutido. O build nesta máquina tem **212 MB de `ffmpeg.exe` e 212 MB de
`ffprobe.exe`**. O app usa o `ffprobe` para uma única coisa — largura,
altura, quantidade de quadros e duração — e isso está escrito em texto claro
nos primeiros bytes do arquivo.

### O formato

GIF é sequencial e simples de percorrer:

```
6 bytes   "GIF87a" ou "GIF89a"        -> senão, erro
7 bytes   Logical Screen Descriptor
            width  : uint16 little-endian
            height : uint16 little-endian
            packed : bit 7 = há tabela global de cores
                     bits 0-2 = N, tamanho da tabela
            + 2 bytes ignorados (cor de fundo, aspecto)
se houver tabela global: pular 3 × 2^(N+1) bytes

laço de blocos:
  0x3B  Trailer                        -> fim
  0x21  Extensão, seguido do rótulo:
          0xF9  Graphic Control Extension
                  1 byte  tamanho do bloco (4)
                  1 byte  packed
                  2 bytes delay, uint16 LE, em centésimos de segundo
                  1 byte  índice transparente
                  1 byte  terminador 0x00
          outros (0xFF app, 0xFE comentário, 0x01 texto)
                  pular sub-blocos
  0x2C  Image Descriptor               -> isto é um quadro
          9 bytes: left, top, width, height (uint16 LE), packed
          se packed bit 7: tabela local, pular 3 × 2^(N+1) bytes
          1 byte  LZW minimum code size
          pular sub-blocos de dados da imagem
  outro byte                           -> erro (arquivo malformado)
```

Pular sub-blocos é sempre o mesmo laço: lê um byte de tamanho; se for `0`,
acabou; senão pula aquele número de bytes e repete.

Cada `Graphic Control Extension` descreve o quadro seguinte. A leitura guarda
o delay pendente e o soma ao total quando um `Image Descriptor` aparece.

### Semântica preservada

`duration = total_de_delays / 100`. Quando a soma é zero — comum em GIFs de
ferramentas ruins — a semântica atual é mantida: `fps` cai para `FPS_PADRAO`
(10.0) e `duration` fica `0.0`, o "não sei honesto" em vez de um número
derivado de um palpite.

**Sem clamp.** Um delay de `1` dá 100fps. É estranho, mas é o que o arquivo
diz e o que o `ffprobe` também reporta. Manter paridade mantém o teste
diferencial honesto.

### Metadados dos outros formatos

PNG, JPEG e WebP têm dimensões lidas pelo Pillow, que já é dependência. Não
há parser à mão para eles — seria trabalho sem retorno, já que o Pillow está
no pacote de qualquer forma.

`ImagemInfo` substitui `GifInfo`:

```
ImagemInfo(path, formato, width, height, fps, frame_count, duration, size_bytes)
```

`formato` é um enum (`GIF`, `PNG`, `JPEG`, `WEBP`). Para formatos estáticos,
`frame_count` é `1`, `duration` é `0.0` e `fps` é `FPS_PADRAO` — os mesmos
valores neutros que um GIF de quadro único já produz hoje, então nada no
restante da engine precisa de um caso especial.

**O `ffprobe` não desaparece do projeto — sai do runtime e do pacote.**
Continua sendo usado pelo teste diferencial, que o localiza por conta própria
no PATH. Quem desenvolve se beneficia de tê-lo; quem **usa** nunca precisa.

## Ferramentas e dependências

| Ferramenta | Papel | Tamanho | Licença |
|---|---|---|---|
| `ffmpeg` (LGPL 9.0) | paleta de GIF, escala, framerate | ~100 MB | LGPL |
| `gifsicle` 1.95 | otimização final de GIF | 290 KB | GPL-2+ |
| `oxipng` 10.2.1 | recompressão sem perda de PNG | 470 KB | MIT |
| Pillow | PNG, JPEG, WebP e dimensões | ~3 MB | HPND |

**Pillow passa a ser dependência de runtime**, ao lado do `tkinterdnd2`. A
restrição anterior de uma única dependência cai; a nova é: apenas
`tkinterdnd2` e `Pillow`, nada mais.

### Obtenção

`bin/` guarda `ffmpeg.exe`, `gifsicle.exe` e `oxipng.exe` — e **somente
esses**; `ffprobe.exe` não entra, é o que estamos removendo. Todos ficam fora
do git e são buscados por ferramenta, não commitados.

O download de cada um segue o padrão já usado para o gifsicle: URL fixada
numa versão específica (nunca um "latest", para o hash ser estável), tamanho
esperado, teto de memória e SHA256 conferido, registrado a partir do primeiro
download verificado.

## Descoberta de ferramentas no pacote

`find_tools` hoje só consulta o PATH, que não existe num app empacotado.
Passa a ter precedência explícita:

1. Pasta do executável congelado, quando `sys.frozen` está marcado
2. `sys._MEIPASS` quando presente — cobre `onedir` (`_internal/`) e
   `onefile` (pasta temporária) sem saber qual foi usado
3. `bin/` do projeto — o modo de desenvolvimento
4. PATH — último recurso

Consequência boa: com os binários em `bin/`, não é mais necessário ter ffmpeg
no PATH para desenvolver.

## Empacotamento

### onedir ou onefile: decidido por medição

Com cerca de 100 MB de binários, `onefile` descompacta tudo em `%TEMP%` **a
cada abertura**, o que pode custar dezenas de segundos, sempre. `onedir` abre
instantâneo, mas exige que o leigo extraia a pasta antes de clicar — e abrir
o `.exe` de dentro do zip falha de um jeito confuso.

**Decisão com número na mão.** O build mede a abertura a frio nos dois modos.
`onefile` abaixo de **10 segundos** → o asset é um `.exe` único. Acima →
`onedir` em zip, com a instrução de extrair em primeiro lugar. O número
medido vai para as notas da release.

### Configuração

- `--noconsole`: sem janela de terminal atrás da interface
- **`tkdnd` declarado nos `datas`.** O `tkinterdnd2` traz bibliotecas nativas
  numa pasta `tkdnd` e o PyInstaller **não tem hook** para ele. Sem isso o
  app compila, abre, e arrastar arquivo não faz nada. É a falha mais provável
  de todo este trabalho e tem item próprio no checklist manual.
- Os três binários como `binaries`, onde a precedência de busca os encontra
- Ícone a partir de `assets/icone.ico`, gerado com Pillow e commitado

## SmartScreen

Executável não assinado dispara **"O Windows protegeu o seu computador"**.
Para um leigo é o bloqueio principal, e não há como evitar sem certificado.

O tratamento é tratá-lo como parte do fluxo: o caminho de cliques ("Mais
informações" → "Executar assim mesmo") aparece **antes** do link de download,
no README e nas notas. A reputação melhora com volume de downloads, mas não
na estreia.

## Licença e conformidade

`ffmpeg` é LGPL e `gifsicle` é GPL-2+, então o pacote distribuído é GPL.
`oxipng` é MIT e o Pillow é HPND, ambos compatíveis.

- `LICENSE` — GPL-3.0
- `THIRD-PARTY-LICENSES.md` — textos de licença, versões exatas empacotadas e
  links de código-fonte upstream das quatro dependências. Todas vão sem
  modificação, então apontar para o upstream é a prática aceita.

## README e release

Duas portas:

**Topo — "só quero usar":** o aviso do SmartScreen, o link do download com o
tamanho declarado, três passos, e a lista de formatos aceitos. Nada de
Python, nada de terminal.

**Abaixo — "quero o código":** instalação, testes, como o pipeline funciona
por formato, e as escadas.

Release `v1.0.0` via `gh release create`, com notas em português para quem não
é da área: o que o programa faz, o que esperar de resultado com números
medidos de verdade, o aviso do SmartScreen, o tamanho antes do clique, e a
nota de que o pacote é GPL.

### O que falta e não posso produzir

Um **GIF do app em ação** no topo do README seria o produto se demonstrando.
Depende de gravação de tela, que exige ver a tela. Fica como pedido ao dono do
projeto.

## Testes

### Automáticos

- Suíte atual verde, com as mudanças de assinatura propagadas
- Unidade do parser de GIF sobre bytes construídos à mão
- Diferencial parser × `ffprobe` sobre GIFs gerados
- **Invariante de monotonicidade por formato**, parametrizada sobre as quatro
  escadas — a propriedade da qual a bisecção depende
- Compressão real de cada formato, conferindo que o resultado abre e é válido
- PNG fotográfico: confirma que o resultado nunca é maior que a entrada,
  exercitando o caminho que a medição mostrou ser real
- JPEG: confirma que o reempacotamento não é anunciado como sem perda
- Precedência do `find_tools` com `sys.frozen`, `sys.executable` e `_MEIPASS`
  simulados
- **Compressão usando apenas os binários do pacote**, com o PATH
  deliberadamente vazio — prova que o empacotamento funciona, e não que a
  máquina de build tinha as ferramentas
- Lançar o executável e confirmar que o processo sobrevive

### Manual, pelo dono do projeto

1. A janela abre com layout correto e o ícone aparece
2. **Arrastar arquivos do Explorer funciona** — o teste do `tkdnd`, o ponto
   mais frágil
3. Soltar um de cada formato e conferir os resultados
4. As alavancas que não se aplicam ao formato soltado ficam desabilitadas
5. A caixa de WebP produz `.webp` quando ligada
6. "Abrir pasta" leva ao lugar certo
7. Rodar numa máquina sem Python, se houver uma à mão

## Critérios de sucesso

1. `python -m pytest` verde, com o xfail deliberado ainda xfail
2. O app empacotado comprime os quatro formatos sem ffmpeg no PATH
3. O executável abre sem crash
4. PNG de interface comprime acima de 50%; nenhum PNG sai maior que a entrada
5. Release publicada com asset, notas, `LICENSE` e `THIRD-PARTY-LICENSES.md`
6. README começa pelo caminho do leigo, SmartScreen antes do link
7. Tamanho do pacote e tempo de abertura registrados na release — medidos,
   não estimados

## Riscos

| Risco | Tratamento |
|---|---|
| `tkdnd` não empacotado: arrastar não funciona | Declarado nos `datas`; item 2 do checklist manual |
| Uma escada nova viola a monotonicidade e quebra a bisecção | Teste de propriedade parametrizado por formato |
| SmartScreen afasta usuários | Instruções antes do link; sem solução sem certificado |
| Parser de GIF discorda do `ffprobe` | Teste diferencial sobre arquivos gerados |
| `quantize` do Pillow é inferior ao `pngquant` | Aceito: `pngquant.org` não resolve. Degraus 0-1 sem perda garantem que PNG nunca piore |
| Reempacotar JPEG degrada sem o usuário perceber | Escada começa em 92; interface não promete ausência de perda |
| Antivírus marca o binário do PyInstaller | Conhecido, sem solução limpa; mencionado nas notas |
| URLs das ferramentas quebram no futuro | Versões fixadas e hashes registrados; erro diz o que baixar à mão |
