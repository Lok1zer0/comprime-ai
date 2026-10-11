# Release com executável para leigos — design

**Data:** 2026-10-10
**Status:** aprovado

## Objetivo

Publicar o compressor como uma release no GitHub que qualquer pessoa — sem
Python, sem ffmpeg, sem terminal — consiga baixar e usar. O link precisa ser
apresentável num post de LinkedIn e o caminho do clique até o primeiro GIF
comprimido precisa ser curto o bastante para não perder ninguém no meio.

## Decisões já tomadas

| Questão | Decisão |
|---|---|
| Como entregar as ferramentas | Tudo empacotado junto; funciona offline, zero configuração |
| Encolher o pacote | Sim — substituir `ffprobe` por leitura de GIF em Python puro |
| Onde montar | Nesta máquina, validando antes de publicar |
| Licença | GPL-3.0 no projeto, com arquivo de terceiros |

## Escopo

**Dentro:**

- Parser de metadados de GIF em Python puro, aposentando o `ffprobe`
- Descoberta de ferramentas dentro do pacote congelado
- Empacotamento com PyInstaller, com ícone próprio
- `LICENSE` (GPL-3.0) e `THIRD-PARTY-LICENSES.md`
- README reescrito para duas audiências
- Release `v1.0.0` publicada com o asset e notas em português

**Fora (YAGNI):**

- Assinatura de código (certificado custa mais que o projeto vale hoje)
- Instalador MSI/NSIS — um `.exe` ou um zip resolve
- Build em GitHub Actions (decidido: build local nesta primeira versão)
- Auto-update
- Versões para macOS e Linux — o `bin/` é win64 e o público é Windows

## 1. Aposentar o ffprobe

### Por que

Os builds de ffmpeg para Windows são estáticos: cada executável carrega tudo
embutido. O build instalado nesta máquina tem **212 MB de `ffmpeg.exe` e
212 MB de `ffprobe.exe`**. O app usa o `ffprobe` para uma única coisa — ler
largura, altura, quantidade de quadros e duração — e isso está escrito em
texto claro nos primeiros bytes do arquivo.

Tirar o `ffprobe` corta um executável estático inteiro do pacote e elimina
uma chamada de subprocesso por arquivo.

### O formato

GIF é sequencial e simples de percorrer:

```
6 bytes   "GIF87a" ou "GIF89a"        -> senão, ProbeError
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
  outro byte                           -> ProbeError (arquivo malformado)
```

Pular sub-blocos é sempre o mesmo laço: lê um byte de tamanho; se for `0`,
acabou; senão pula aquele número de bytes e repete.

### Associar delay a quadro

Cada `Graphic Control Extension` descreve o quadro que vem depois dele. A
leitura guarda o delay pendente e, ao encontrar um `Image Descriptor`,
soma-o ao total e zera o pendente. Isso associa corretamente os delays aos
quadros mesmo quando há extensões intercaladas.

### Semântica preservada

`duration = total_de_delays / 100`. Quando a soma dos delays é zero — comum
em GIFs gerados por ferramentas ruins — a semântica atual é mantida sem
mudança: `fps` cai para `FPS_PADRAO` (10.0) e `duration` fica `0.0`, o "não
sei honesto" em vez de um número derivado de um palpite. Os navegadores
tratam GIFs de atraso zero como ~10fps, então a convenção que já está no
código continua certa.

**Sem clamp.** Um delay de `1` (um centésimo de segundo) dá 100fps. Isso é
estranho, mas é o que o arquivo diz e é o que o `ffprobe` também reporta.
Manter paridade mantém o teste diferencial honesto, e a escada de qualidade
lida bem com fps alto.

### Leitura em memória

O parser lê o arquivo inteiro em memória de uma vez. GIFs relevantes têm
alguns megabytes; a simplicidade vale mais que a economia aqui, e evita toda
uma classe de bug de leitura parcial.

### Mudança de interface

`probe(path, tools)` perde o parâmetro e vira **`probe(path) -> GifInfo`**.
`GifInfo` não muda. Atinge `compress.py:74` e as chamadas em
`tests/test_probe.py` e `tests/test_encode.py`.

`Tools` perde o campo `ffprobe`, e `find_tools` deixa de exigi-lo.
Verificado: nada fora de `probe.py` e de construções de teste lê
`tools.ffprobe`, então a remoção é contida.

**O `ffprobe` não desaparece do projeto — sai do runtime e do pacote.**
Continua sendo usado pelo teste diferencial da seção 8, que o localiza por
conta própria no PATH em vez de recebê-lo via `Tools`. Ou seja: quem
desenvolve ainda se beneficia de ter ffprobe instalado (senão aquele teste é
pulado), mas quem **usa** o app nunca precisa dele.

### Trava contra regressão

Um **teste diferencial** gera GIFs variados com ffmpeg (tamanhos, framerates
e contagens de quadros diferentes), lê cada um com o parser novo e com o
`ffprobe`, e exige concordância campo a campo. Se o parser errar, o teste diz
qual campo e qual arquivo. Marcado `integration`; pulado se o ffprobe não
estiver disponível.

Mais testes de unidade sobre bytes construídos à mão: GIF mínimo válido,
delays em múltiplas extensões, tabela local de cores, arquivo truncado no
meio de cada estrutura, magia inválida, bloco desconhecido.

## 2. Encontrar as ferramentas dentro do pacote

Hoje `find_tools` só consulta o PATH, o que não existe num app empacotado.
Passa a ter precedência explícita:

1. **Pasta do executável congelado** — `Path(sys.executable).parent` quando
   `sys.frozen` está marcado
2. **`sys._MEIPASS`** quando presente — é onde o PyInstaller coloca binários
   adicionados, tanto em `onedir` (`_internal/`) quanto em `onefile` (pasta
   temporária). Cobrir os dois modos sem saber qual foi usado.
3. **`bin/` do projeto** — o modo de desenvolvimento
4. **PATH** — último recurso

`PROJECT_ROOT` deriva de `__file__` hoje, o que aponta para dentro do bundle
quando congelado. Passa a respeitar `sys.frozen`.

Consequência boa para quem desenvolve: com `ffmpeg.exe` em `bin/`, não é mais
necessário ter ffmpeg no PATH para rodar o projeto.

## 3. Obtenção do ffmpeg para o build

`bin/` passa a guardar `ffmpeg.exe` além do `gifsicle.exe` — e **somente
esses dois**; `ffprobe.exe` não entra, é justamente o que estamos removendo.
Os dois seguem fora do git (`bin/` já está no `.gitignore`) e são buscados por
ferramenta, não commitados.

O download segue o mesmo padrão já usado para o gifsicle: **URL fixada numa
versão específica** (não um "latest", para que o hash seja estável), tamanho
esperado, teto de memória e SHA256 conferido. O hash é registrado no código a
partir do primeiro download verificado — não inventado.

Se o download falhar, a mensagem diz o que baixar e onde colocar, para que a
pessoa possa resolver à mão.

## 4. Empacotamento

### onedir ou onefile: decidido por medição

Com cerca de 200 MB de binários, `onefile` descompacta tudo em `%TEMP%` **a
cada abertura**, o que pode custar dezenas de segundos, sempre. `onedir` abre
instantâneo.

A contrapartida é real: o leigo precisa extrair a pasta antes de clicar, e
abrir o `.exe` de dentro do zip falha de um jeito confuso ("não encontrou
_internal").

**Decisão com número na mão.** O build mede o tempo de abertura a frio nos
dois modos. Se o `onefile` abrir em **menos de 10 segundos**, o asset da
release é um `.exe` único — baixa, clica, pronto, sem extração e sem chance
de erro. Acima disso, vai `onedir` em zip, com a instrução de extrair em
primeiro lugar no release. O número medido fica registrado na release.

### Configuração

- `--noconsole`: sem janela de terminal preta atrás da interface
- `tkdnd` declarado explicitamente nos `datas`. O `tkinterdnd2` traz
  bibliotecas nativas numa pasta `tkdnd` e **o PyInstaller não tem hook para
  ele** — sem isso o app compila, abre, e arrastar arquivo simplesmente não
  faz nada. É a falha mais provável de todo este trabalho e tem teste próprio
  no checklist manual.
- Ícone gerado com Pillow para `assets/icone.ico`, commitado para que o build
  seja reprodutível. Pillow entra em `requirements-dev.txt`, não no runtime —
  a restrição de ter `tkinterdnd2` como única dependência de execução
  continua valendo.
- `gifc.spec` versionado, e um script de build que orquestra: garantir
  binários em `bin/`, gerar ícone se faltar, rodar PyInstaller, verificar o
  resultado, empacotar.

## 5. SmartScreen

Executável não assinado dispara **"O Windows protegeu o seu computador"**.
Para um leigo isso é o bloqueio principal — é onde a maior parte das pessoas
desiste, e não há como evitar sem certificado de assinatura.

O tratamento é tratar como parte do fluxo, não como nota de rodapé: o caminho
de cliques ("Mais informações" → "Executar assim mesmo") aparece **antes** do
link de download, tanto no README quanto nas notas da release. A reputação do
binário melhora com o volume de downloads, então o aviso tende a sumir com o
tempo, mas não na estreia.

## 6. Licença e conformidade

`ffmpeg` (build GPL) e `gifsicle` são GPL. Redistribuí-los dentro do pacote
obriga a incluir as licenças e dar acesso ao código-fonte.

- `LICENSE` — GPL-3.0
- `THIRD-PARTY-LICENSES.md` — textos de licença do ffmpeg e do gifsicle, as
  versões exatas empacotadas e os links de código-fonte upstream. Como as
  duas ferramentas vão sem modificação, apontar para o upstream é a prática
  aceita.
- README e notas da release declaram que o pacote distribuído é GPL

## 7. README e release

O README hoje é escrito para quem vai rodar `pip install`. Passa a ter duas
portas:

**Topo — "só quero usar":** o aviso do SmartScreen, o link do download, e
três passos. Nada de Python, nada de terminal.

**Abaixo — "quero o código":** o que já existe hoje (instalação, testes, como
o pipeline funciona, a escada de qualidade).

A release `v1.0.0` sai via `gh release create`, com o asset e notas em
português escritas para quem não é da área: o que o programa faz, o que
esperar de resultado, o aviso do SmartScreen, e o tamanho do download
declarado antes do clique.

### O que falta e não posso produzir

Um **GIF do app em ação** no topo do README seria o próprio produto se
demonstrando — o melhor ativo possível para um post de LinkedIn. Depende de
uma gravação de tela, que exige ver a tela. Fica como pedido ao dono do
projeto; comprimir essa gravação com a própria ferramenta é um detalhe
simpático a mais.

## 8. Verificação

### Automática

- Suíte atual verde (209 testes + 1 xfail deliberado), com as mudanças de
  assinatura propagadas
- Testes de unidade do parser sobre bytes construídos à mão
- Teste diferencial parser × ffprobe sobre GIFs gerados
- Testes de precedência do `find_tools`, com `sys.frozen`, `sys.executable` e
  `_MEIPASS` simulados
- **Compressão real usando apenas os binários do pacote**, com o PATH
  deliberadamente sem ffmpeg — prova que o empacotamento funciona de fato, e
  não que a máquina de build tinha as ferramentas instaladas
- Lançar o executável e confirmar que o processo sobrevive sem crash

### Manual, pelo dono do projeto

O que não dá para verificar sem ver a tela. Checklist curto, dois minutos:

1. A janela abre com layout correto e o ícone aparece
2. **Arrastar um GIF do Explorer funciona** — o teste do `tkdnd`, o ponto
   mais frágil
3. O log preenche, a barra anda, o resultado aparece
4. "Abrir pasta" leva ao lugar certo
5. O GIF comprimido abre e está visualmente aceitável
6. Rodar numa máquina sem Python, se houver uma à mão

## Critérios de sucesso

1. `python -m pytest` verde, com o xfail deliberado ainda xfail
2. O app empacotado comprime um GIF real sem ffmpeg no PATH
3. O executável abre sem crash
4. Release publicada com asset, notas, `LICENSE` e `THIRD-PARTY-LICENSES.md`
5. README começa pelo caminho do leigo, com o aviso do SmartScreen antes do
   link
6. O tamanho final do pacote e o tempo de abertura ficam registrados na
   release — números medidos, não estimados

## Riscos

| Risco | Tratamento |
|---|---|
| `tkdnd` não empacotado: arrastar não funciona | Declarado nos `datas`; item 2 do checklist manual |
| SmartScreen afasta usuários | Instruções antes do link; sem solução completa sem certificado |
| Pacote grande demais para convencer o download | Tamanho declarado antes do clique; remoção do ffprobe reduz |
| Parser de GIF discorda do ffprobe | Teste diferencial sobre arquivos gerados |
| Antivírus marca o binário do PyInstaller | Conhecido e sem solução limpa; mencionado nas notas |
| URL do ffmpeg quebra no futuro | Versão fixada e hash registrado; erro diz o que baixar à mão |
