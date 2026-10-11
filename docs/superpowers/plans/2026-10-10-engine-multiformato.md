# Engine multi-formato — Plano de Implementação (A de 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Comprimir PNG, JPEG e WebP além de GIF, reusando a busca binária sobre escada de qualidade que já existe.

**Architecture:** O `search.py` não muda — ele já é genérico, só anota `Params` por acidente de origem. O `ladder.py` passa a aplicar toggles a uma tabela fornecida por um módulo de formato, e nasce `gifc/formatos/` com um módulo por formato, cada um dono da sua tabela de degraus e do seu codificador. O `ffprobe` sai do runtime, substituído por um parser de GIF em Python puro e pelo Pillow para os formatos estáticos.

**Tech Stack:** Python 3.12, Pillow, oxipng 10.2.1, ffmpeg 9.0 LGPL, gifsicle 1.95, pytest.

**Spec:** [`docs/superpowers/specs/2026-10-10-release-executavel-design.md`](../specs/2026-10-10-release-executavel-design.md)

**Plano seguinte:** o empacotamento e a release são o plano B,
[`2026-10-10-release-executavel.md`](2026-10-10-release-executavel.md). Não
comece o B antes de o A estar verde.

## Global Constraints

- Python 3.12+. Type hints em todas as funções públicas.
- Dependências de runtime: **`tkinterdnd2` e `Pillow`** — e nada mais. (A
  restrição anterior de dependência única caiu nesta revisão de escopo.)
- Nunca modificar ou sobrescrever o arquivo de entrada, em nenhum caminho.
- Saída em `<nome>_comprimido.<ext>`; se existir, sufixar `_2`, `_3`, ...
- Temporários em `tempfile.mkdtemp()` removido em `finally`.
- `bin/` fica fora do git — nenhum binário de terceiro é commitado.
- Mensagens de interface e de erro em português.
- O pacote Python continua chamando `gifc`, apesar do repositório
  `comprime-ai`. Renomear tocaria todos os imports sem entregar nada.
- Commits em português, terminando com
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Suíte atual: 209 passando + 1 xfail deliberado. O xfail deve continuar
  XFAIL, nunca XPASS.

## Review Focus

Classes de entrada que a spec implica, que nenhuma tarefa cobriria por
inércia, e que atingem gente de verdade:

1. **GIF87a sem Graphic Control Extension e GIF de quadro único** — boa parte
   dos `.gif` por aí é estática. Sem GCE não há delay; o parser precisa
   devolver `frame_count=1` e cair no `FPS_PADRAO` em vez de estourar.
   (Tarefa 1)
2. **PNG fotográfico, onde quantizar aumenta o arquivo** — medido: +26% com
   256 cores. Não é hipótese, é o comportamento normal em gradiente suave. O
   resultado nunca pode sair maior que a entrada. (Tarefa 4)
3. **Arquivo cuja extensão mente** — foto renomeada para `.png`, GIF
   renomeado para `.jpg`. A detecção precisa ser por conteúdo, não por
   extensão, com mensagem que diga o que aconteceu. (Tarefa 2)
4. **PNG com transparência** — quantizar pode destruir o canal alfa. Um logo
   com fundo transparente que sai com fundo preto é um defeito grave e
   silencioso. (Tarefa 4)
5. **WebP e PNG animados** — a spec os recusa, mas o Pillow os abre sem
   reclamar e comprimiria só o primeiro quadro, perdendo a animação sem
   avisar. (Tarefa 2)

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `gifc/gifmeta.py` | **novo** — percorre os bytes de um GIF (puro, sem I/O) |
| `gifc/probe.py` | detecta formato e produz `ImagemInfo` |
| `gifc/ladder.py` | `Toggles`, `target_width`, e a filtragem genérica de tabelas |
| `gifc/formatos/__init__.py` | **novo** — registro formato → tabela + codificador |
| `gifc/formatos/gif.py` | **novo** — tabela de 20 degraus e pipeline ffmpeg+gifsicle |
| `gifc/formatos/png.py` | **novo** — quantize + oxipng |
| `gifc/formatos/jpeg.py` | **novo** — reempacotamento com Pillow |
| `gifc/formatos/webp.py` | **novo** — Pillow, também alvo de conversão |
| `gifc/compress.py` | despacha pelo formato detectado |
| `gifc/app.py` | mais extensões, alavancas condicionais, caixa de WebP |

`search.py` e `encode.py` existentes: `search.py` só troca a anotação por um
`TypeVar`; o conteúdo de `encode.py` migra para `formatos/gif.py`.

---

### Task 1: Parser de GIF em Python puro

**Files:**
- Create: `gifc/gifmeta.py`
- Test: `tests/test_gifmeta.py`

**Interfaces:**
- Consumes: nada
- Produces:
  - `GifMetaError(Exception)`
  - `@dataclass(frozen=True) GifRaw(width: int, height: int, frame_count: int, total_delay_cs: int)`
  - `parse(dados: bytes) -> GifRaw`

O formato está descrito na seção "Aposentar o ffprobe" da spec, com o laço de
blocos, os rótulos de extensão e a regra de pular sub-blocos. Siga-a.

Regras que a spec fixa: o delay vem em `0x21 0xF9` como uint16 little-endian
em centésimos de segundo; o delay pendente é somado quando um `0x2C` aparece;
**sem clamp** em delays pequenos.

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_gifmeta.py`. Construa os bytes à mão; um helper que monta
cabeçalho + descritor + blocos deixa os testes legíveis.

```python
def test_le_dimensoes_do_logical_screen_descriptor()      # 64x48 -> 64, 48
def test_conta_quadros_pelos_image_descriptors()          # 3x 0x2C -> 3
def test_soma_delays_das_extensoes_de_controle()          # 10 e 15 -> 25
def test_delay_pendente_vai_para_o_quadro_seguinte()      # GCE(7) 0x2C GCE(0) 0x2C -> 7, 2 quadros
def test_gif87a_sem_gce_tem_delay_zero()                  # Review Focus 1: 1 quadro, 0, sem exceção
def test_pula_tabela_global_de_cores()                    # bit 7 ligado, N=1 -> 6 bytes
def test_pula_tabela_local_de_cores()
def test_pula_extensao_de_aplicacao_netscape()            # 0x21 0xFF com sub-blocos
def test_pula_comentario()                                # 0x21 0xFE
def test_nao_aplica_clamp_em_delay_pequeno()              # delay 1 permanece 1
def test_magia_invalida_levanta_erro()                    # b"\x89PNG..." -> GifMetaError
def test_arquivo_vazio_levanta_erro()
def test_truncado_no_descritor_de_tela_levanta_erro()
def test_truncado_no_meio_de_um_sub_bloco_levanta_erro()
def test_bloco_desconhecido_levanta_erro()                # 0x99 onde se espera 0x21/0x2C/0x3B
def test_sem_trailer_mas_com_quadros_e_aceito()           # não perder quadros já lidos

@pytest.mark.slow
def test_arquivo_grande_e_processado()                    # ~60 MB, conclui e conta certo
```

Mensagens de `GifMetaError` em português, dizendo onde a leitura parou.

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_gifmeta.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'gifc.gifmeta'`

- [ ] **Step 3: Implementar `gifc/gifmeta.py`**

`parse(dados: bytes) -> GifRaw`, mais os helpers privados para pular
sub-blocos e tabelas de cores. Sem I/O, sem `subprocess`.

Leia índices com verificação de limite: um `IndexError` cru vazando de
arquivo truncado é bug, não comportamento.

- [ ] **Step 4: Rodar e confirmar que passam**

Run: `python -m pytest tests/test_gifmeta.py -v`

- [ ] **Step 5: Commit**

```bash
git add gifc/gifmeta.py tests/test_gifmeta.py
git commit -m "Parser de metadados de GIF em Python puro"
```

---

### Task 2: `ImagemInfo` e detecção de formato

**Files:**
- Modify: `gifc/probe.py` (reescrita), `gifc/compress.py:74`
- Modify: `tests/test_probe.py`, `tests/test_encode.py:159,175`
- Test: `tests/test_probe.py`

**Interfaces:**
- Consumes: `gifmeta.parse`, `GifRaw`, `GifMetaError` (Tarefa 1)
- Produces:
  - `class Formato(StrEnum)`: `GIF`, `PNG`, `JPEG`, `WEBP`
  - `@dataclass(frozen=True) ImagemInfo(path: Path, formato: Formato, width: int, height: int, fps: float, frame_count: int, duration: float, size_bytes: int)`
  - `ProbeError`, `FPS_PADRAO` — mantidos
  - `probe(path: Path) -> ImagemInfo` — **sem** o parâmetro `tools`

`GifInfo` deixa de existir; `ImagemInfo` o substitui em todos os usos.

**Detecção por conteúdo, nunca por extensão** (Review Focus 3). GIF vai pelo
`gifmeta`; PNG, JPEG e WebP vão pelo Pillow, que também dá as dimensões.

Valores neutros para formatos estáticos, conforme a spec: `frame_count = 1`,
`duration = 0.0`, `fps = FPS_PADRAO`. São os mesmos que um GIF de quadro
único já produz, então nada no resto da engine precisa de caso especial.

- [ ] **Step 1: Escrever os testes que falham**

Reescreva `tests/test_probe.py` sem o parâmetro `tools`. Os testes atuais de
dimensões, contagem de quadros, tamanho e imutabilidade seguem valendo com a
assinatura nova. Acrescente:

```python
def test_detecta_gif_png_jpeg_webp(tmp_path)
    # um arquivo real de cada, gerado com Pillow/ffmpeg -> Formato correto

def test_deteccao_e_por_conteudo_nao_por_extensao(tmp_path)
    # Review Focus 3: PNG salvo como "foto.jpg" -> Formato.PNG, sem erro

def test_extensao_desconhecida_mas_conteudo_valido_e_aceito(tmp_path)
    # "arquivo.dat" com bytes de PNG -> Formato.PNG

def test_conteudo_nao_reconhecido_levanta_probeerror(tmp_path)
    # bytes aleatórios -> ProbeError com mensagem dizendo o que não reconheceu

def test_fps_derivado_dos_delays_do_gif(tmp_path)
    # 10 quadros, 10 centésimos -> duration 1.0, fps 10.0

def test_gif_sem_delays_cai_no_padrao_sem_inventar_duracao(tmp_path)
    # total 0 -> fps == FPS_PADRAO e duration == 0.0

def test_estatico_tem_valores_neutros_de_animacao(tmp_path)
    # PNG -> frame_count 1, duration 0.0, fps FPS_PADRAO

def test_traduz_gifmetaerror_para_probeerror(monkeypatch)
    # força gifmeta.parse a levantar e exige ProbeError: o módulo
    # interno não vaza para os chamadores

def test_gif_animado_e_aceito(tmp_path)

def test_webp_animado_e_recusado(tmp_path)
    # Review Focus 5: Pillow abre sem reclamar e comprimiria só o
    # primeiro quadro, perdendo a animação em silêncio. Deve ser
    # ProbeError dizendo que animação nesse formato não é suportada.

def test_png_animado_apng_e_recusado(tmp_path)
    # Review Focus 5: mesmo raciocínio

@pytest.mark.integration
def test_concorda_com_o_ffprobe_em_gifs(tmp_path)
    # Teste diferencial. Gera com ffmpeg uma matriz de GIFs variando
    # tamanho, fps e número de quadros; compara width, height,
    # frame_count e duration contra o ffprobe, localizado por
    # shutil.which("ffprobe") — NÃO via Tools. Pula se ausente.
    # Inteiros batem exatamente; duration com tolerância.
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_probe.py -v`
Expected: FAIL — a assinatura antiga ainda exige `tools`

- [ ] **Step 3: Reescrever `probe` e propagar**

`probe(path)` lê os bytes, identifica o formato pela assinatura, e despacha.
Remova `_parse_fps`, o `subprocess`, o `json` e o import de `Tools`.

Para detectar animação: `getattr(img, "n_frames", 1) > 1` no Pillow.

Atualize `gifc/compress.py:74` e as duas chamadas em `tests/test_encode.py`.
Renomeie `GifInfo` → `ImagemInfo` em todos os usos.

- [ ] **Step 4: Rodar a suíte inteira**

Run: `python -m pytest -v`
Expected: tudo verde, xfail ainda XFAIL

- [ ] **Step 5: Commit**

```bash
git add gifc/probe.py gifc/compress.py tests/
git commit -m "ImagemInfo com detecção de formato por conteúdo"
```

---

### Task 3: `ladder.py` genérico e GIF movido para `formatos/`

**Files:**
- Create: `gifc/formatos/__init__.py`, `gifc/formatos/gif.py`
- Modify: `gifc/ladder.py`, `gifc/search.py` (só a anotação), `gifc/compress.py`
- Delete: `gifc/encode.py` (conteúdo migra para `formatos/gif.py`)
- Modify: `tests/test_ladder.py`, `tests/test_encode.py` → `tests/test_formato_gif.py`
- Test: `tests/test_ladder.py`, `tests/test_formato_gif.py`

**Interfaces:**
- Consumes: `ImagemInfo`, `Formato` (Tarefa 2)
- Produces:
  - `ladder.Toggles(palette, lossy, scale, fps: bool)` — mantido
  - `ladder.target_width(width, scale_pct) -> int` — mantido
  - `ladder.aplicar_toggles(tabela: Sequence[P], info: ImagemInfo, toggles: Toggles) -> list[P]` — a filtragem genérica, antes embutida em `build`
  - `formatos.para(formato: Formato) -> ModuloFormato` — registro
  - Cada módulo de formato expõe: `TABELA: tuple[Params, ...]`,
    `Params` (congelada e hashável), `codificar(src, dst, params, info, tools) -> int`
  - `formatos.gif.Params(colors, lossy, scale_pct, fps)` e os 20 degraus atuais

`search.find_best` troca a anotação `Params` por um `TypeVar` — nenhuma
mudança de comportamento, e é a prova de que o desenho original já era
genérico.

`aplicar_toggles` precisa funcionar sobre qualquer `Params`: use
`dataclasses.replace` só para os campos que o `Params` daquele formato
realmente tem, consultando `dataclasses.fields`. Um formato sem a coluna
`fps` não deve quebrar quando o toggle de framerate está desligado.

- [ ] **Step 1: Escrever os testes que falham**

Mova os testes de escada de GIF para `tests/test_formato_gif.py`, com os
valores atuais. Em `tests/test_ladder.py` deixe só o que é genérico, e
acrescente:

```python
def test_aplicar_toggles_ignora_coluna_que_o_formato_nao_tem()
    # Params com apenas (quality, scale_pct) e Toggles(fps=False)
    # -> não levanta, devolve a tabela filtrada

def test_aplicar_toggles_deduplica_preservando_a_ordem()

def test_tudo_desligado_colapsa_para_um_degrau()
```

E o teste que a spec chama de central — a propriedade da qual a bisecção
depende, agora **parametrizada por formato**:

```python
@pytest.mark.parametrize("formato", list(Formato))
def test_invariante_de_monotonicidade_por_formato(formato)
    """Cada degrau é fracamente mais agressivo que o anterior em TODAS
    as dimensões daquele formato. Sem isso o tamanho codificado não é
    monotônico e a bisecção pode descartar uma região que continha um
    resultado de qualidade melhor."""
    # Para cada par consecutivo da TABELA do formato, para cada campo
    # do Params: compara segundo o sentido daquele campo. Campos onde
    # "mais alto é mais agressivo" (lossy, oxipng) e campos onde
    # "mais baixo é mais agressivo" (colors, quality, scale_pct, fps)
    # têm sentidos opostos — declare o sentido por campo no módulo do
    # formato e leia-o aqui, em vez de embutir uma lista no teste.
```

Declarar o sentido no módulo do formato é o que faz este teste continuar
correto quando a Tarefa 4 acrescentar PNG: um formato novo que esqueça de
declarar o sentido de um campo deve **falhar** este teste, não ser ignorado.

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_ladder.py tests/test_formato_gif.py -v`

- [ ] **Step 3: Implementar a extração**

Crie `gifc/formatos/` com o registro e `gif.py`. Mova a tabela de 20 degraus,
o `Params` de GIF e todo o conteúdo de `encode.py` para `formatos/gif.py`.
Extraia `aplicar_toggles` de `build`. Troque a anotação em `search.py`.

Cada módulo de formato declara o sentido de agressividade dos seus campos —
por exemplo um mapa `SENTIDOS: dict[str, str]` com `"menor"` ou `"maior"` por
campo — para que o teste de invariante seja genérico e não precise saber de
cada formato.

**Esta tarefa não muda comportamento nenhum.** A suíte inteira deve passar
com os mesmos resultados de antes. Se algum teste de GIF mudar de resultado,
a extração introduziu bug — pare e reporte.

- [ ] **Step 4: Rodar a suíte inteira**

Run: `python -m pytest -v`
Expected: tudo verde, xfail ainda XFAIL, nenhum teste de GIF alterado

- [ ] **Step 5: Commit**

```bash
git add gifc/ tests/
git commit -m "Extrai o formato GIF para formatos/ e generaliza a escada"
```

---

### Task 4: Formato PNG — quantize + oxipng

**Files:**
- Create: `gifc/formatos/png.py`
- Modify: `gifc/tools.py` (baixar e localizar o oxipng)
- Test: `tests/test_formato_png.py`, `tests/test_tools.py`

**Interfaces:**
- Consumes: `ladder.aplicar_toggles`, `target_width`, `ImagemInfo`, `Tools`
- Produces:
  - `tools.OXIPNG_URL`, `OXIPNG_ZIP_SIZE`, `OXIPNG_SHA256`, `oxipng_path(bin_dir)`, `download_oxipng(bin_dir, on_progress=None)`
  - `Tools` ganha o campo `oxipng: str | None`
  - `formatos.png.Params(colors: int | None, oxipng: int, scale_pct: int)`
  - `formatos.png.TABELA` — os 10 degraus da spec
  - `formatos.png.codificar(src, dst, params, info, tools) -> int`

**Valores verificados:**

```
OXIPNG_URL = ("https://github.com/oxipng/oxipng/releases/download/v10.2.1/"
              "oxipng-10.2.1-x86_64-pc-windows-msvc.zip")
```

Tag versionada, então o hash é estável. `OXIPNG_SHA256` começa vazio e é
preenchido no Step 4 a partir do download real — não invente valor. Licença
MIT; registre isso para o `THIRD-PARTY-LICENSES.md` do plano B.

A tabela está na spec, seção "Escadas". `colors = None` significa não
quantizar e é o extremo **menos** agressivo da coluna — degraus 0 e 1 são sem
perda.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_tabela_tem_dez_degraus()
def test_degraus_zero_e_um_nao_quantizam()                # colors is None
def test_sentidos_declarados_cobrem_todos_os_campos()     # exigido pela Tarefa 3

@pytest.mark.integration
def test_png_de_interface_comprime_acima_de_cinquenta_por_cento(tmp_path)
    # Gera um PNG de áreas planas + texto (o caso medido na spec),
    # roda o degrau 2 (256 cores) e exige redução > 50%.

@pytest.mark.integration
def test_png_fotografico_nunca_sai_maior_que_a_entrada(tmp_path)
    # Review Focus 2: gradiente suave, onde a spec mediu +26% com 256
    # cores. Roda compress_file com alvo folgado e exige
    # final_bytes <= original_bytes. É o caminho que já_otimizado e
    # menor_absoluto existem para cobrir.

@pytest.mark.integration
def test_transparencia_e_preservada(tmp_path)
    # Review Focus 4: PNG RGBA com fundo transparente. Depois de
    # quantizar, o pixel do canto precisa continuar com alfa 0.
    # Um logo que volta com fundo preto é defeito grave e silencioso.

@pytest.mark.integration
def test_degrau_sem_perda_nao_altera_os_pixels(tmp_path)
    # Degrau 0: só oxipng. Os pixels decodificados devem ser
    # idênticos aos da entrada — é o que "sem perda" significa.

@pytest.mark.integration
def test_resultado_abre_como_png_valido(tmp_path)
```

E em `tests/test_tools.py`, espelhando o que já existe para o gifsicle:

```python
def test_oxipng_url_e_https_e_versionada()
def test_oxipng_path_fica_no_bin_dir(tmp_path)
def test_extrai_o_oxipng_do_zip(tmp_path)
def test_zip_sem_oxipng_levanta_downloaderror(tmp_path)
def test_oxipng_sha256_tem_formato_valido()               # falha até o Step 4
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_formato_png.py -v`

- [ ] **Step 3: Implementar**

`codificar` faz: abrir com Pillow → escalar se `scale_pct != 100` → quantizar
se `colors is not None` → salvar PNG → rodar `oxipng -o <nível>` sobre o
resultado.

Para preservar transparência na quantização, `Image.quantize` precisa do
método e do modo certos — um RGBA quantizado sem cuidado perde o alfa. O
teste do Review Focus 4 é o que valida a escolha.

Reaproveite os helpers de download e extração que já existem para o gifsicle
em vez de duplicar a lógica.

- [ ] **Step 4: Baixar o oxipng e fixar o hash**

```bash
python -c "from pathlib import Path; from gifc.tools import download_oxipng; print(download_oxipng(Path('bin')))"
```

```bash
./bin/oxipng.exe --version
```

Copie o SHA256 para `OXIPNG_SHA256`.

- [ ] **Step 5: Rodar a suíte inteira**

Run: `python -m pytest -v`

- [ ] **Step 6: Commit**

```bash
git add gifc/ tests/
git commit -m "Formato PNG: quantização do Pillow mais oxipng"
```

---

### Task 5: Formato JPEG

**Files:**
- Create: `gifc/formatos/jpeg.py`
- Test: `tests/test_formato_jpeg.py`

**Interfaces:**
- Consumes: `ladder.aplicar_toggles`, `target_width`, `ImagemInfo`
- Produces:
  - `formatos.jpeg.Params(quality: int, subsampling: int, scale_pct: int)`
  - `formatos.jpeg.TABELA` — os 12 degraus da spec
  - `formatos.jpeg.codificar(src, dst, params, info, tools) -> int`

`subsampling` usa a convenção do Pillow: `0` é 4:4:4 e `2` é 4:2:0. Então
nessa coluna **o maior é o mais agressivo** — declare o sentido
corretamente, é o tipo de inversão que passa batido.

`progressive=True` em todos os degraus: economiza alguns por cento sem custo
de qualidade, então é padrão, não alavanca.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_tabela_tem_doze_degraus()
def test_comeca_em_qualidade_noventa_e_dois()
def test_sentido_de_subsampling_e_maior_mais_agressivo()
    # a inversão: 0 = 4:4:4 (menos agressivo), 2 = 4:2:0 (mais)
def test_sentidos_declarados_cobrem_todos_os_campos()

@pytest.mark.integration
def test_jpeg_comprime_de_verdade(tmp_path)
    # foto sintética salva em qualidade 95; degrau 4 (q75) deve
    # reduzir substancialmente

@pytest.mark.integration
def test_progressive_esta_ligado_na_saida(tmp_path)
    # relê com Pillow e confirma o marcador progressivo

@pytest.mark.integration
def test_resultado_abre_como_jpeg_valido(tmp_path)

@pytest.mark.integration
def test_dimensoes_preservadas_quando_escala_e_cem(tmp_path)
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_formato_jpeg.py -v`

- [ ] **Step 3: Implementar**

`codificar` abre com Pillow, converte para RGB quando necessário (um JPEG não
tem canal alfa; um PNG convertido precisa achatar), escala, e salva com
`quality`, `subsampling`, `progressive=True` e `optimize=True`.

- [ ] **Step 4: Rodar a suíte inteira**

Run: `python -m pytest -v`
Expected: tudo verde, xfail ainda XFAIL

- [ ] **Step 5: Commit**

```bash
git add gifc/formatos/jpeg.py tests/test_formato_jpeg.py
git commit -m "Formato JPEG: reempacotamento com Pillow"
```

---

### Task 6: Formato WebP e conversão opcional

**Files:**
- Create: `gifc/formatos/webp.py`
- Modify: `gifc/formatos/__init__.py` (resolver o formato de saída)
- Test: `tests/test_formato_webp.py`

**Interfaces:**
- Consumes: `ladder.aplicar_toggles`, `target_width`, `ImagemInfo`, `Formato`
- Produces:
  - `formatos.webp.Params(quality: int, scale_pct: int)`
  - `formatos.webp.TABELA` — os 10 degraus da spec
  - `formatos.webp.codificar(src, dst, params, info, tools) -> int`
  - `formatos.formato_de_saida(entrada: Formato, converter_webp: bool) -> Formato`

`method=6` fixo: é o ajuste mais lento e que comprime melhor, e é botão de
tempo, não de qualidade. Não entra na escada.

`formato_de_saida` resolve a conversão: PNG e JPEG viram `WEBP` quando
`converter_webp` está ligado; GIF **nunca** converte (animação é outro
problema e está fora de escopo); WebP de entrada segue WebP.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_tabela_tem_dez_degraus()
def test_sentidos_declarados_cobrem_todos_os_campos()

def test_formato_de_saida_sem_conversao_mantem_a_entrada()
    # os quatro formatos, converter_webp=False -> saída == entrada

def test_formato_de_saida_converte_png_e_jpeg()
    # converter_webp=True -> WEBP

def test_formato_de_saida_nunca_converte_gif()
    # converter_webp=True e entrada GIF -> GIF

def test_formato_de_saida_de_webp_e_webp()

@pytest.mark.integration
def test_webp_comprime_de_verdade(tmp_path)

@pytest.mark.integration
def test_png_transparente_convertido_mantem_alfa(tmp_path)
    # WebP suporta alfa; converter não pode perdê-lo

@pytest.mark.integration
def test_resultado_abre_como_webp_valido(tmp_path)
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_formato_webp.py -v`

- [ ] **Step 3: Implementar**

- [ ] **Step 4: Rodar a suíte inteira**

Run: `python -m pytest -v`
Expected: tudo verde, xfail ainda XFAIL

- [ ] **Step 5: Commit**

```bash
git add gifc/formatos/ tests/test_formato_webp.py
git commit -m "Formato WebP e resolução do formato de saída"
```

---

### Task 7: `compress.py` despacha por formato

**Files:**
- Modify: `gifc/compress.py`
- Test: `tests/test_compress.py`

**Interfaces:**
- Consumes: tudo das Tarefas 2-6
- Produces:
  - `compress_file(src, target_bytes, toggles, tools, converter_webp: bool = False, on_progress=None, should_cancel=None) -> CompressResult`
  - `CompressResult` ganha `formato_entrada: Formato` e `formato_saida: Formato`

A estrutura atual permanece: `probe` → tabela → `aplicar_toggles` →
`find_best` → mover o vencedor. O que muda é de onde vêm a tabela e o
codificador, e que a extensão da saída segue `formato_de_saida`.

Tudo que o plano anterior conquistou aqui continua valendo e **não deve
regredir**: o sentinela de degrau que falha com retentativa, `menor_absoluto`,
`already_optimized`, a limpeza do tmpdir em `finally`, e os testes de mutação
que travam `hit_target` e o empate exato.

- [ ] **Step 1: Escrever os testes que falham**

Os testes atuais de `test_compress.py` seguem valendo. Acrescente:

```python
@pytest.mark.parametrize("formato", list(Formato))
@pytest.mark.integration
def test_comprime_cada_formato(formato, tmp_path)
    # um arquivo real de cada; resultado existe, abre, e não é maior

@pytest.mark.integration
def test_extensao_da_saida_segue_o_formato_de_saida(tmp_path)
    # PNG com converter_webp=True -> arquivo .webp

@pytest.mark.integration
def test_converter_webp_nao_afeta_gif(tmp_path)
    # GIF com converter_webp=True -> saída .gif

def test_resultado_carrega_os_dois_formatos(tmp_path, monkeypatch)
    # formato_entrada e formato_saida preenchidos
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_compress.py -v`

- [ ] **Step 3: Implementar o despacho**

- [ ] **Step 4: Rodar a suíte inteira**

Run: `python -m pytest -v`
Expected: tudo verde; nenhum teste do plano anterior alterado

- [ ] **Step 5: Commit**

```bash
git add gifc/compress.py tests/test_compress.py
git commit -m "compress_file despacha por formato e resolve a saída"
```

---

### Task 8: `app.py` multi-formato

**Files:**
- Modify: `gifc/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `compress_file`, `CompressResult`, `Formato`, `Toggles`
- Produces: nada que outra tarefa use

Mudanças na interface:

- Aceita `.gif`, `.png`, `.jpg`, `.jpeg`, `.webp` no arraste e no seletor
- Caixa **"Converter para WebP"**, desligada por padrão, repassada a
  `compress_file`
- As alavancas que não se aplicam ao conjunto soltado ficam **desabilitadas**,
  não aceitas e ignoradas em silêncio. "Reduzir framerate" e "compressão
  lossy" só valem para GIF.
- O texto do resultado nomeia os formatos quando houve conversão

A disciplina de thread permanece: só a thread principal toca widget, o worker
só põe na fila. Nada nesta tarefa muda isso, e o teste de resiliência do laço
de drenagem deve continuar passando.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_extensoes_aceitas_incluem_os_quatro_formatos()
    # a função pura que filtra caminhos, testável sem Tk

def test_extensao_nao_suportada_e_recusada()
    # .txt, .bmp, .tiff

def test_alavancas_de_gif_desabilitadas_quando_nao_ha_gif(app)
    # só PNGs soltos -> toggles de framerate e lossy desabilitados

def test_alavancas_de_gif_habilitadas_quando_ha_gif(app)

def test_caixa_webp_desabilitada_quando_so_ha_gif(app)
    # GIF não converte; a caixa não deve aceitar clique inútil

def test_caixa_webp_habilitada_quando_ha_png_ou_jpeg(app)

def test_resultado_de_jpeg_nunca_promete_ausencia_de_perda()
    # A spec é explícita: reempacotar JPEG degrada mesmo no degrau mais
    # conservador, e dizer o contrário seria mentira. O texto do
    # resultado para JPEG não pode conter "sem perda".
    # Para PNG nos degraus 0-1 a afirmação É verdadeira e permitida —
    # então a regra é por formato, não global.

def test_resultado_de_png_sem_perda_pode_dizer_sem_perda()
    # o outro lado da regra acima, para que ninguém a implemente
    # removendo a frase de todos os formatos
```

Os testes existentes de `_parse_alvo_mb`, `_mb`, resiliência da drenagem,
progresso e fechamento continuam valendo sem mudança.

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_app.py -v`

- [ ] **Step 3: Implementar**

Extraia a filtragem de caminhos para uma função pura, como já foi feito com
`_parse_alvo_mb` — é o que permite testá-la sem instanciar Tk.

- [ ] **Step 4: Rodar a suíte inteira**

Run: `python -m pytest -v`
Expected: tudo verde, xfail ainda XFAIL

- [ ] **Step 5: Commit**

```bash
git add gifc/app.py tests/test_app.py
git commit -m "Interface aceita os quatro formatos e a conversão para WebP"
```

---

## Verificação final do plano A

- [ ] `python -m pytest -v` verde, xfail deliberado ainda XFAIL
- [ ] `python -m pytest -m "not integration" -v` passa
- [ ] Os quatro formatos comprimem de verdade, a partir do código-fonte
- [ ] PNG de interface reduz acima de 50%; PNG fotográfico nunca sai maior
- [ ] Transparência preservada em PNG e em WebP
- [ ] Nenhum resultado do plano anterior regrediu
- [ ] `git ls-files | grep -E "\.exe$"` vazio

Com o A verde, siga para o plano B:
[`2026-10-10-release-executavel.md`](2026-10-10-release-executavel.md).
