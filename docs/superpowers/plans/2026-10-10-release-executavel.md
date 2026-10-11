# Release com executável para leigos — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publicar uma release no GitHub com um executável Windows que funcione sem Python, sem ffmpeg e sem terminal.

**Architecture:** O `ffprobe` sai do runtime, substituído por um parser de GIF em Python puro — isso remove um executável estático de ~100 MB do pacote. O `find_tools` ganha precedência de busca que funciona dentro de um bundle congelado. O PyInstaller empacota app, ffmpeg e gifsicle num único artefato, e o modo (`onefile` ou `onedir`) é escolhido medindo o tempo de abertura.

**Tech Stack:** Python 3.12, PyInstaller 6.7, Pillow (só no build), ffmpeg 9.0 LGPL, gifsicle 1.95, pytest.

**Spec:** [`docs/superpowers/specs/2026-10-10-release-executavel-design.md`](../specs/2026-10-10-release-executavel-design.md)

## Global Constraints

- Python 3.12+. Type hints em todas as funções públicas.
- Única dependência de runtime: `tkinterdnd2`. Dev: `pytest` e `Pillow`.
- Nunca modificar ou sobrescrever o GIF de entrada, em nenhum caminho.
- Temporários em `tempfile.mkdtemp()` removido em `finally`.
- `bin/` fica fora do git — nenhum binário de terceiro é commitado.
- Mensagens de interface e de erro em português.
- Commits em português, terminando com
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Suíte atual: 209 passando + 1 xfail deliberado. O xfail deve continuar
  XFAIL, nunca XPASS.

## Revisão do plano contra a spec

A spec dizia "ffmpeg (build GPL)". Durante a verificação das URLs apareceu
build **LGPL** do mesmo ffmpeg 9.0, 164 MB em vez de 185 MB. Para trabalho de
paleta em GIF não usamos nada que seja GPL-only (x264, x265): `palettegen`,
`paletteuse`, `scale` e `fps` são todos do core. Então o plano usa o LGPL.
Isso alivia as obrigações do ffmpeg; o pacote segue GPL por causa do
gifsicle, então a seção 6 da spec continua valendo integralmente.

**Não assuma que o build LGPL tem os filtros.** A Tarefa 4 confere isso com
comando explícito antes de qualquer outra coisa.

## Review Focus

Classes de entrada que a spec implica, que nenhuma tarefa cobriria por
inércia, e que atingem gente de verdade. Cada uma tem teste na tarefa que
possui o código:

1. **GIF de quadro único e GIF87a sem Graphic Control Extension** — boa parte
   dos arquivos `.gif` por aí é estática. Sem GCE não há delay nenhum; o
   parser precisa devolver `frame_count=1` e cair no `FPS_PADRAO` em vez de
   estourar. (Tarefa 1)
2. **Arquivo que não é GIF com extensão `.gif`** — o erro de usuário mais
   comum: renomear uma foto. Hoje o `ffprobe` barrava; o parser novo precisa
   barrar com mensagem que diga o que aconteceu. (Tarefa 1)
3. **Caminho com acentos e espaços** — a área de trabalho do dono do projeto
   é `OneDrive\Área de Trabalho`. Funciona hoje, mas o PyInstaller muda o
   ambiente de codificação do processo. (Tarefa 6)
4. **Caminho perto do limite de 260 caracteres do Windows** — o sufixo
   `_comprimido` cresce o nome, e `unique_output_path` pode gerar um caminho
   que o sistema recusa. (Tarefa 2)
5. **GIF muito grande** — o parser lê o arquivo inteiro em memória por
   decisão de simplicidade. Precisa funcionar, não travar, num arquivo de
   centenas de MB. (Tarefa 1)

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `gifc/gifmeta.py` | **novo** — percorre os bytes de um GIF (puro, sem I/O) |
| `gifc/probe.py` | produz `GifInfo` a partir do arquivo, usando `gifmeta` |
| `gifc/tools.py` | descoberta com precedência de bundle; baixa ffmpeg e gifsicle |
| `gifc/compress.py` | ajuste da chamada de `probe` |
| `assets/icone.ico` | **novo** — ícone do executável (commitado) |
| `tools/gerar_icone.py` | **novo** — gera o `.ico` com Pillow |
| `tools/build.py` | **novo** — orquestra o empacotamento e mede |
| `gifc.spec` | **novo** — configuração do PyInstaller |
| `LICENSE`, `THIRD-PARTY-LICENSES.md` | **novos** — conformidade GPL |

O parser fica num módulo separado de propósito: percorrer bytes é puro e
testável sobre sequências construídas à mão, enquanto `probe` cuida do I/O e
da semântica de fps/duração. Misturar os dois tornaria o parser testável
apenas via arquivos.

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

O formato está descrito na seção 1 da spec, incluindo o laço de blocos, os
rótulos de extensão e a regra de pular sub-blocos. Siga-a; não reinvente a
ordem dos campos.

Regras que a spec fixa e os testes abaixo pinam: `0x21 0xF9` carrega o delay
em centésimos de segundo (uint16 little-endian); o delay pendente é somado ao
total quando um `0x2C` aparece; **sem clamp** em delays pequenos.

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_gifmeta.py`. Construa os bytes à mão — um helper que monta
cabeçalho + descritor de tela + blocos deixa os testes legíveis.

```python
def test_le_dimensoes_do_logical_screen_descriptor()
    # GIF89a 64x48 sem tabela global -> width 64, height 48

def test_conta_quadros_pelos_image_descriptors()
    # 3 blocos 0x2C -> frame_count == 3

def test_soma_delays_das_extensoes_de_controle()
    # GCEs de 10 e 15 centésimos -> total_delay_cs == 25

def test_delay_pendente_e_associado_ao_quadro_seguinte()
    # GCE(7) 0x2C GCE(0) 0x2C -> total_delay_cs == 7, frame_count == 2

def test_gif87a_sem_gce_tem_delay_zero()
    # Review Focus 1: GIF87a, 1 quadro, nenhum 0xF9
    # -> frame_count == 1 e total_delay_cs == 0, sem exceção

def test_quadro_unico_e_valido()
    # Review Focus 1: frame_count == 1

def test_pula_tabela_global_de_cores()
    # packed com bit 7 ligado e N=1 -> 6 bytes de tabela pulados corretamente

def test_pula_tabela_local_de_cores()
    # Image Descriptor com bit 7 do packed ligado

def test_pula_extensao_de_aplicacao_netscape()
    # 0x21 0xFF com sub-blocos -> não confunde o laço

def test_pula_comentario()
    # 0x21 0xFE

def test_nao_aplica_clamp_em_delay_pequeno()
    # delay de 1 centésimo permanece 1

def test_magia_invalida_levanta_erro()
    # Review Focus 2: b"\x89PNG..." -> GifMetaError

def test_arquivo_vazio_levanta_erro()

def test_truncado_no_descritor_de_tela_levanta_erro()

def test_truncado_no_meio_de_um_sub_bloco_levanta_erro()

def test_bloco_desconhecido_levanta_erro()
    # byte 0x99 onde se espera 0x21/0x2C/0x3B

def test_sem_trailer_mas_com_quadros_e_aceito()
    # arquivos reais às vezes vêm sem 0x3B; não perder os quadros já lidos

def test_arquivo_grande_e_processado()
    # Review Focus 5: ~60 MB de sub-blocos de imagem, deve concluir
    # e contar os quadros certos. Marque @pytest.mark.slow.
```

Toda mensagem de `GifMetaError` em português e dizendo onde parou.

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_gifmeta.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'gifc.gifmeta'`

- [ ] **Step 3: Implementar `gifc/gifmeta.py`**

`parse(dados: bytes) -> GifRaw`, mais os helpers privados que você julgar
necessários para pular sub-blocos e tabelas de cores. Sem I/O, sem
`subprocess`, só `struct`/slicing e `dataclasses`.

Leia índices com verificação de limite: um `IndexError` cru vazando de um
arquivo truncado é bug, não comportamento.

- [ ] **Step 4: Rodar e confirmar que passam**

Run: `python -m pytest tests/test_gifmeta.py -v`
Expected: todos passam

- [ ] **Step 5: Commit**

```bash
git add gifc/gifmeta.py tests/test_gifmeta.py
git commit -m "Parser de metadados de GIF em Python puro"
```

---

### Task 2: `probe` sem ffprobe

**Files:**
- Modify: `gifc/probe.py` (reescrita do corpo; `GifInfo` e `FPS_PADRAO` permanecem)
- Modify: `gifc/compress.py:74`
- Modify: `tests/test_probe.py`, `tests/test_encode.py:159,175`
- Test: `tests/test_probe.py`

**Interfaces:**
- Consumes: `gifmeta.parse`, `gifmeta.GifRaw`, `gifmeta.GifMetaError` (Tarefa 1)
- Produces: `probe(path: Path) -> GifInfo` — **sem** o parâmetro `tools`.
  `GifInfo(path, width, height, fps, frame_count, duration, size_bytes)` e
  `ProbeError` ficam iguais. `_parse_fps` deixa de existir; a decisão de fps
  passa a vir dos delays.

Semântica a preservar exatamente (spec, seção 1): `duration =
total_delay_cs / 100`. Quando `total_delay_cs == 0`, então `fps =
FPS_PADRAO` **e** `duration = 0.0` — o "não sei honesto". Quando há delays,
`fps = frame_count / duration`.

`ProbeError` continua sendo o erro público: `probe` traduz `GifMetaError`
para `ProbeError` para não vazar o módulo interno aos chamadores.

- [ ] **Step 1: Escrever os testes que falham**

Reescreva `tests/test_probe.py` removendo o parâmetro `tools` das chamadas.
Os testes existentes de dimensões, contagem de quadros, tamanho do arquivo,
arquivo inexistente e imutabilidade de `GifInfo` continuam valendo com a
assinatura nova. Acrescente:

```python
def test_fps_derivado_dos_delays()
    # 10 quadros, 10 centésimos cada -> duration 1.0, fps 10.0

def test_delays_zerados_caem_no_padrao_sem_inventar_duracao()
    # total_delay_cs == 0 -> fps == FPS_PADRAO e duration == 0.0

def test_traduz_gifmetaerror_para_probeerror(monkeypatch)
    # força gifmeta.parse a levantar GifMetaError e exige ProbeError,
    # garantindo que o módulo interno não vaza

def test_arquivo_nao_gif_com_extensao_gif_levanta_probeerror(tmp_path)
    # Review Focus 2: bytes de PNG salvos como .gif

@pytest.mark.integration
def test_concorda_com_o_ffprobe(tools, tmp_path)
    # Teste diferencial. Gera com ffmpeg uma matriz de GIFs variando
    # tamanho, fps e número de quadros; para cada um compara width,
    # height, frame_count e duration contra o ffprobe, que é localizado
    # por shutil.which("ffprobe") — NÃO via Tools. Pula se ausente.
    # duration com tolerância; os inteiros devem bater exatamente.
```

E em `tests/test_compress.py`, o teste do Review Focus 4:

```python
def test_caminho_longo_proximo_do_limite_do_windows(tmp_path)
    # Review Focus 4: nome que deixa o caminho perto de 260 chars.
    # unique_output_path acrescenta "_comprimido"; o comportamento
    # esperado é um erro claro em português, não um OSError cru de
    # caminho inválido vazando para a interface.
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_probe.py -v`
Expected: FAIL — assinatura antiga ainda exige `tools`

- [ ] **Step 3: Reescrever `probe` e propagar a assinatura**

`probe(path)` lê os bytes do arquivo, chama `gifmeta.parse`, e monta
`GifInfo` com `size_bytes` do `stat()`. Remova `_parse_fps`, o `subprocess`,
o `json` e o import de `Tools`.

Atualize `gifc/compress.py:74` e as duas chamadas em `tests/test_encode.py`.

Se o Review Focus 4 revelar que `unique_output_path` deixa um `OSError` cru
escapar, trate ali em `compress.py` com mensagem em português — é o chamador
que sabe o que fazer com o arquivo.

- [ ] **Step 4: Rodar a suíte inteira**

Run: `python -m pytest -v`
Expected: tudo verde, xfail ainda XFAIL

- [ ] **Step 5: Commit**

```bash
git add gifc/probe.py gifc/compress.py tests/
git commit -m "Aposenta o ffprobe: metadados saem do parser em Python"
```

---

### Task 3: `Tools` sem ffprobe e busca dentro do pacote

**Files:**
- Modify: `gifc/tools.py`
- Modify: `tests/test_tools.py`, `tests/test_encode.py:18,137`, `tests/test_compress.py:25`
- Nota: depois da Tarefa 2, `tests/test_probe.py` não constrói mais `Tools`
  diretamente (só usa a fixture `tools` para gerar GIFs no teste
  diferencial). Confira antes de editar — pode não haver nada a mudar lá.

**Interfaces:**
- Consumes: nada das tarefas anteriores
- Produces:
  - `Tools(ffmpeg: str, gifsicle: str | None)` — campo `ffprobe` **removido**
  - `find_tools(bin_dir: Path = BIN_DIR) -> Tools` — inalterada na assinatura
  - `APP_DIR: Path` — pasta do executável quando congelado, `PROJECT_ROOT` fora disso
  - `dirs_de_busca(bin_dir: Path) -> list[Path]`

Precedência (spec, seção 2), nesta ordem: pasta do executável congelado →
`sys._MEIPASS` quando existir → `bin_dir` → PATH. Os dois primeiros só
entram quando `sys.frozen` está marcado. `_MEIPASS` cobre `onedir`
(`_internal/`) e `onefile` (pasta temporária) sem precisar saber qual foi
usado.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_tools_nao_tem_mais_ffprobe()
    # dataclasses.fields(Tools) não contém "ffprobe"

def test_dirs_de_busca_fora_do_bundle_e_so_o_bin_dir(tmp_path, monkeypatch)
    # sys.frozen ausente -> [bin_dir]

def test_dirs_de_busca_congelado_poe_a_pasta_do_exe_primeiro(tmp_path, monkeypatch)
    # sys.frozen True e sys.executable em tmp_path/app/x.exe
    # -> tmp_path/app vem antes de bin_dir

def test_dirs_de_busca_inclui_meipass(tmp_path, monkeypatch)
    # sys._MEIPASS definido -> aparece na lista, antes de bin_dir

def test_find_tools_prefere_o_bundle_ao_path(tmp_path, monkeypatch)
    # ffmpeg falso em tmp_path + shutil.which devolvendo outro caminho
    # -> o do bundle ganha

def test_find_tools_cai_no_path_quando_nao_ha_bundle(tmp_path, monkeypatch)

def test_find_tools_sem_ffmpeg_em_lugar_nenhum_levanta(tmp_path, monkeypatch)
    # which -> None e bin_dir vazio -> MissingToolError nomeando ffmpeg
```

Atualize todas as construções `Tools(...)` nos arquivos listados acima para a
forma de dois campos.

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_tools.py -v`
Expected: FAIL — `Tools` ainda tem três campos

- [ ] **Step 3: Implementar**

Remova `ffprobe` de `Tools` e a sua exigência em `find_tools`. Acrescente
`APP_DIR` e `dirs_de_busca`, e faça `find_tools` procurar cada ferramenta por
nome nas pastas da lista antes de recorrer a `shutil.which`.

`PROJECT_ROOT` continua derivando de `__file__`; `APP_DIR` é a nova noção que
respeita `sys.frozen`. `BIN_DIR` passa a derivar de `APP_DIR`, para que o app
congelado procure ao lado de si mesmo.

- [ ] **Step 4: Rodar a suíte inteira**

Run: `python -m pytest -v`
Expected: tudo verde

- [ ] **Step 5: Commit**

```bash
git add gifc/tools.py tests/
git commit -m "Tools sem ffprobe e busca com precedência de bundle"
```

---

### Task 4: Baixar o ffmpeg para `bin/`

**Files:**
- Modify: `gifc/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `dirs_de_busca`, `DownloadError`, `BIN_DIR` (Tarefa 3)
- Produces:
  - `FFMPEG_URL: str`, `FFMPEG_ZIP_SIZE: int`, `FFMPEG_SHA256: str`
  - `ffmpeg_path(bin_dir: Path) -> Path`
  - `download_ffmpeg(bin_dir: Path, on_progress: Callable[[int, int], None] | None = None) -> Path`

Espelhe o que já existe para o gifsicle: URL fixada, tamanho esperado, teto
de memória, SHA256 conferido, extração que recusa caminhos de escape do zip.
Reaproveite os helpers do gifsicle onde fizer sentido em vez de duplicar a
lógica de extração.

**Valores verificados:**

```
FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/"
              "autobuild-2026-10-10-13-04/"
              "ffmpeg-n9.0.2-25-g67b60c310b-win64-lgpl-9.0.zip")
FFMPEG_ZIP_SIZE = 171_475_889
```

A tag é datada e imutável, então o hash é estável. `FFMPEG_SHA256` começa
vazio e é preenchido no Step 4 a partir do download real — não invente valor.

O zip traz uma pasta `ffmpeg-*/bin/` com `ffmpeg.exe`, `ffprobe.exe` e
`ffplay.exe`. **Extraia apenas `ffmpeg.exe`** — os outros dois são o que
estamos removendo do pacote.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_ffmpeg_url_e_https_e_fixada_numa_tag()
    # começa com https:// e contém "autobuild-"

def test_ffmpeg_path_fica_no_bin_dir(tmp_path)

def test_extrai_apenas_o_ffmpeg_do_zip(tmp_path)
    # zip em memória com bin/ffmpeg.exe, bin/ffprobe.exe, bin/ffplay.exe
    # -> só ffmpeg.exe aparece em bin_dir

def test_zip_sem_ffmpeg_levanta_downloaderror(tmp_path)

def test_zip_com_caminho_de_escape_e_recusado(tmp_path)

def test_ffmpeg_sha256_tem_formato_valido()
    # 64 chars hex — falha até o Step 4; é esperado

@pytest.mark.integration
def test_download_real_do_ffmpeg(tmp_path)
    # baixa de verdade e confere que o exe resultante roda
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_tools.py -v -m "not integration"`
Expected: FAIL com `ImportError: cannot import name 'FFMPEG_URL'`

- [ ] **Step 3: Implementar**

Acrescente as constantes e as duas funções a `gifc/tools.py`.

- [ ] **Step 4: Baixar de verdade e fixar o hash**

Este passo transfere 164 MB. Confirme com o dono do projeto antes de rodar.

```bash
python -c "from pathlib import Path; from gifc.tools import download_ffmpeg; print(download_ffmpeg(Path('bin')))"
```

```bash
python -c "import hashlib,urllib.request; from gifc.tools import FFMPEG_URL; print(hashlib.sha256(urllib.request.urlopen(FFMPEG_URL,timeout=300).read()).hexdigest())"
```

Copie o hash para `FFMPEG_SHA256`.

- [ ] **Step 5: Confirmar que o build LGPL serve**

O plano assume que o build LGPL tem o que precisamos. Verifique agora, antes
de depender disso:

```bash
./bin/ffmpeg.exe -hide_banner -filters | grep -E "palettegen|paletteuse"
```
Expected: as duas linhas aparecem.

```bash
./bin/ffmpeg.exe -hide_banner -version | head -1
```
Expected: versão 9.0.x

Registre o tamanho de `bin/ffmpeg.exe` no relatório — é o número que define o
tamanho do pacote.

Se qualquer filtro faltar, **pare e reporte**: a alternativa é o asset GPL
(185 MB) da mesma tag, e essa é uma decisão do dono do projeto.

- [ ] **Step 6: Rodar a suíte inteira e provar que o PATH não é mais necessário**

Run: `python -m pytest -v`
Expected: tudo verde

Prova de que `bin/` basta — a razão de existir desta tarefa:

```bash
python -c "
import os, shutil
os.environ['PATH'] = ''
from gifc.tools import find_tools, BIN_DIR
t = find_tools(BIN_DIR)
print('ffmpeg:', t.ffmpeg); print('gifsicle:', t.gifsicle)
assert 'bin' in t.ffmpeg.replace(chr(92), '/')
print('OK: resolveu sem PATH')
"
```

- [ ] **Step 7: Commit**

```bash
git add gifc/tools.py tests/test_tools.py
git commit -m "Baixa o ffmpeg LGPL fixado para bin/"
```

---

### Task 5: Ícone, spec do PyInstaller e script de build

**Files:**
- Create: `tools/gerar_icone.py`, `assets/icone.ico`, `gifc.spec`, `tools/build.py`
- Modify: `requirements-dev.txt` (acrescentar `Pillow`)
- Modify: `.gitignore` (acrescentar `build/`, `dist/`)

**Interfaces:**
- Consumes: `find_tools`, `ffmpeg_path`, `gifsicle_path`, `BIN_DIR` (Tarefas 3-4)
- Produces: `dist/` com o artefato; `tools/build.py` imprime tamanho e tempo
  de abertura medidos

Pontos que a spec fixa (seção 4):

- `--noconsole`: sem janela de terminal atrás da interface.
- **`tkdnd` declarado nos `datas`.** O `tkinterdnd2` traz bibliotecas nativas
  na pasta `tkdnd` e o PyInstaller **não tem hook** para ele. Sem isso o app
  compila, abre, e arrastar arquivo não faz nada. Localize a pasta via
  `Path(tkinterdnd2.__file__).parent / "tkdnd"`.
- `ffmpeg.exe` e `gifsicle.exe` entram como `binaries`, de onde
  `dirs_de_busca` sabe encontrá-los.
- Ícone a partir de `assets/icone.ico`.

- [ ] **Step 1: Gerar o ícone**

`tools/gerar_icone.py` desenha com Pillow e grava `assets/icone.ico` com os
tamanhos 16, 32, 48, 64, 128 e 256. O desenho é livre; algo legível em 16px
que remeta a compressão ou a GIF. Commite o `.ico` para o build ser
reprodutível.

- [ ] **Step 2: Escrever `gifc.spec` e construir nos dois modos**

Monte `onedir` e `onefile` a partir do mesmo `gifc.spec` (ou dois, se ficar
mais claro). Só construir, ainda sem decidir.

- [ ] **Step 3: Medir e decidir o modo**

A spec manda decidir por medição, não por preferência. Meça a abertura **a
frio** dos dois: lançar o processo, cronometrar até a janela existir, matar.
Repita 3 vezes e use o pior caso.

Regra, já fixada na spec: `onefile` abaixo de **10 segundos** → o asset é um
`.exe` único. Acima → `onedir` em zip.

Registre no relatório: tamanho do artefato nos dois modos e os tempos
medidos. Esses números vão para as notas da release.

- [ ] **Step 4: Escrever `tools/build.py`**

Orquestra de ponta a ponta: garantir `bin/ffmpeg.exe` e `bin/gifsicle.exe`
(baixando se faltar), gerar o ícone se faltar, rodar o PyInstaller no modo
escolhido, e imprimir tamanho final e tempo de abertura.

Falhar alto e claro: se um binário não estiver lá e o download não for
possível, diga o que baixar e onde pôr.

- [ ] **Step 5: Commit**

```bash
git add tools/ assets/ gifc.spec requirements-dev.txt .gitignore
git commit -m "Empacotamento com PyInstaller, ícone e script de build"
```

---

### Task 6: Verificar o pacote

**Files:**
- Create: `tests/test_pacote.py`
- Test: `tests/test_pacote.py`

**Interfaces:**
- Consumes: o artefato em `dist/` (Tarefa 5), `compress_file` e `Toggles`
- Produces: nada que outra tarefa use

Estes testes são marcados `integration` e pulam com mensagem clara quando
`dist/` não existe, para que a suíte siga verde numa máquina que não
construiu o pacote.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_binarios_estao_no_pacote()
    # ffmpeg.exe e gifsicle.exe presentes no artefato

def test_tkdnd_esta_no_pacote()
    # a pasta tkdnd foi empacotada — a falha silenciosa mais provável
    # de todo este trabalho

def test_comprime_de_verdade_usando_so_os_binarios_do_pacote(tmp_path)
    # Monta Tools apontando para os binários DENTRO de dist/, com
    # PATH vazio no ambiente, e roda compress_file num GIF real.
    # Prova que o empacotamento funciona — não que a máquina de build
    # tinha ffmpeg instalado.

def test_comprime_em_caminho_com_acentos_e_espacos(tmp_path)
    # Review Focus 3: pasta "Área de Trabalho ção" com espaços.
    # O ambiente de codificação do PyInstaller difere do Python normal.

def test_executavel_abre_sem_crash()
    # Lança o artefato, espera alguns segundos, confirma que o processo
    # está vivo e não deixou traceback no stderr, encerra.
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_pacote.py -v`
Expected: FAIL (ou skip, se `dist/` ainda não existe — então construa antes)

- [ ] **Step 3: Corrigir o que os testes revelarem**

Esta tarefa é de verificação: o código a mudar é o que os testes acusarem.
Os três diagnósticos prováveis, do mais ao menos provável:

- **`tkdnd` ausente do artefato** → a entrada em `datas` do `gifc.spec` não
  está apontando para a pasta certa. Confira o caminho resolvido em tempo de
  build, não o literal.
- **Binário não encontrado em tempo de execução** → `dirs_de_busca` não está
  olhando onde o PyInstaller realmente pôs os `binaries`. Imprima a lista de
  pastas de dentro do app congelado para ver o que ele enxerga.
- **Falha só no caminho com acentos** → codificação do ambiente passado ao
  `subprocess`; o problema está em `encode.py`, não no empacotamento.

Se um teste revelar algo fora dessas três hipóteses, reporte antes de
corrigir — pode ser defeito de desenho, não de empacotamento.

- [ ] **Step 4: Rodar a suíte inteira**

Run: `python -m pytest -v`
Expected: tudo verde

- [ ] **Step 5: Commit**

```bash
git add tests/test_pacote.py
git commit -m "Verifica que o pacote comprime com os próprios binários"
```

---

### Task 7: Licença, terceiros e README

**Files:**
- Create: `LICENSE`, `THIRD-PARTY-LICENSES.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: as versões exatas empacotadas (Tarefas 4-5)
- Produces: nada que outra tarefa use

- [ ] **Step 1: `LICENSE`**

Texto integral da GPL-3.0, sem modificação.

- [ ] **Step 2: `THIRD-PARTY-LICENSES.md`**

Para ffmpeg e gifsicle: versão exata empacotada, licença, link do
código-fonte upstream, e a observação de que vão sem modificação. É o que a
GPL exige de quem redistribui binário.

- [ ] **Step 3: Reescrever o `README.md`**

Duas portas, nesta ordem (spec, seção 7):

**Topo — "só quero usar":** o aviso do SmartScreen **antes** do link, com o
caminho de cliques ("Mais informações" → "Executar assim mesmo"); o link do
download com o tamanho declarado; três passos. Nada de Python, nada de
terminal.

**Abaixo — "quero o código":** o conteúdo técnico atual, mais o `bin/` como
fonte das ferramentas (ffmpeg no PATH deixou de ser necessário) e `pytest -m
"not integration"` para a suíte rápida.

Deixe o espaço do GIF de demonstração marcado com um comentário HTML dizendo
onde ele entra — o dono do projeto vai gravá-lo.

- [ ] **Step 4: Commit**

```bash
git add LICENSE THIRD-PARTY-LICENSES.md README.md
git commit -m "GPL-3.0, licenças de terceiros e README para quem só quer usar"
```

---

### Task 8: Publicar a release

**Files:** nenhum — é operação sobre o repositório remoto

**Interfaces:**
- Consumes: o artefato (Tarefa 5), os números medidos (Tarefa 5, Step 3)

**Esta tarefa publica conteúdo visível a qualquer pessoa e é difícil de
desfazer. Apresente as notas ao dono do projeto e espere aprovação explícita
antes do Step 3.**

- [ ] **Step 1: Confirmar que a árvore está limpa e a suíte verde**

```bash
git status --short && python -m pytest -q
```

- [ ] **Step 2: Escrever as notas e mostrá-las**

Em português, para quem não é da área: o que o programa faz, o que esperar de
resultado (com o número real medido no GIF de teste), o aviso do SmartScreen
com o caminho de cliques, o tamanho do download declarado antes do link, e a
nota de que o pacote é GPL por incluir ffmpeg e gifsicle.

- [ ] **Step 3: Criar a tag e a release**

O artefato depende da decisão medida no Step 3 da Tarefa 5: um `.exe` único
quando o `onefile` abriu em menos de 10 segundos, ou um `.zip` da pasta
`onedir` caso contrário. Use o nome real, não um genérico — quem baixa lê o
nome do arquivo:

```bash
git tag -a v1.0.0 -m "Primeira release pública"
git push origin main --tags
gh release create v1.0.0 "dist/CompressorDeGIF-1.0.0-windows.<exe|zip>"   --title "Compressor de GIF 1.0.0" --notes-file <caminho das notas>
```

- [ ] **Step 4: Conferir a release publicada**

```bash
gh release view v1.0.0
```

Baixe o asset pelo link público e confirme que abre — a release é o produto,
não o `dist/` local.

---

## Verificação final

- [ ] `python -m pytest -v` verde, xfail deliberado ainda XFAIL
- [ ] `python -m pytest -m "not integration" -v` passa sem ffmpeg no PATH
- [ ] O app empacotado comprime um GIF real com PATH vazio
- [ ] Tamanho do pacote e tempo de abertura medidos e registrados na release
- [ ] `LICENSE` e `THIRD-PARTY-LICENSES.md` publicados
- [ ] README começa pelo caminho do leigo, SmartScreen antes do link
- [ ] Nenhum binário de terceiro commitado (`git ls-files | grep -E "\.exe$"` vazio)

## Checklist manual do dono do projeto

O que não dá para verificar sem ver a tela. Dois minutos:

1. A janela abre com layout correto e o ícone aparece
2. **Arrastar um GIF do Explorer funciona** — o teste do `tkdnd`, o ponto
   mais frágil de todo o trabalho
3. O log preenche, a barra anda, o resultado aparece
4. "Abrir pasta" leva ao lugar certo
5. O GIF comprimido abre e está visualmente aceitável
6. Rodar numa máquina sem Python, se houver uma à mão
