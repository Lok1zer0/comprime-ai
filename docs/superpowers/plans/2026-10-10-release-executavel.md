# Release com executável para leigos — Plano de Implementação (B de 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publicar uma release no GitHub com um executável Windows que comprima GIF, PNG, JPEG e WebP sem Python, sem ffmpeg e sem terminal.

**Architecture:** O `find_tools` ganha precedência de busca que funciona dentro de um bundle congelado. O PyInstaller empacota app, Pillow e os três binários num único artefato, e o modo (`onefile` ou `onedir`) é escolhido medindo o tempo de abertura, não por preferência.

**Tech Stack:** PyInstaller 6.7, Pillow, ffmpeg 9.0 LGPL, gifsicle 1.95, oxipng 10.2.1.

**Spec:** [`docs/superpowers/specs/2026-10-10-release-executavel-design.md`](../specs/2026-10-10-release-executavel-design.md)

**Pré-requisito:** o plano A,
[`2026-10-10-engine-multiformato.md`](2026-10-10-engine-multiformato.md),
precisa estar verde. Este plano empacota o que ele construiu e assume que
`gifc/formatos/` existe, que `probe` não usa mais `ffprobe`, e que o oxipng
já é baixado por `tools.py`.

## Global Constraints

- Python 3.12+. Type hints em todas as funções públicas.
- Dependências de runtime: `tkinterdnd2` e `Pillow`. Dev: `pytest`.
- Nunca modificar ou sobrescrever o arquivo de entrada.
- `bin/` fica fora do git — nenhum binário de terceiro é commitado.
- Mensagens de interface e de erro em português.
- Repositório: `comprime-ai`. Pacote Python: `gifc`.
- Commits em português, terminando com
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- O xfail deliberado da suíte deve continuar XFAIL, nunca XPASS.

## Revisão do plano contra a spec

A spec dizia "ffmpeg (build GPL)". Na verificação das URLs apareceu build
**LGPL** do mesmo ffmpeg 9.0, 164 MB em vez de 185 MB. Para paleta de GIF não
usamos nada GPL-only: `palettegen`, `paletteuse`, `scale` e `fps` são do core.
O plano usa o LGPL. O pacote segue GPL por causa do gifsicle, então a seção de
licença da spec continua valendo integralmente.

**Não assuma que o build LGPL tem os filtros.** A Tarefa 2 confere com
comando explícito antes de qualquer coisa depender disso.

## Review Focus

1. **Caminho com acentos e espaços** — a área de trabalho do dono do projeto é
   `OneDrive\Área de Trabalho`. Funciona hoje, mas o PyInstaller muda o
   ambiente de codificação do processo. (Tarefa 4)
2. **Caminho perto do limite de 260 caracteres do Windows** — o sufixo
   `_comprimido` cresce o nome e pode gerar um caminho que o sistema recusa.
   O esperado é erro claro em português, não `OSError` cru na interface.
   (Tarefa 4)
3. **Rodar o `.exe` de dentro do zip, sem extrair** — em `onedir` falha
   procurando `_internal`. É o erro que o leigo comete primeiro. (Tarefa 4,
   e a instrução em destaque na Tarefa 5)
4. **Máquina sem Visual C++ Redistributable** — binários MSVC podem exigir
   runtime ausente numa máquina limpa. (checklist manual)
5. **Pillow sem seus `.pyd` de codec** — o PyInstaller às vezes perde plugins
   de formato do Pillow; WebP é o mais provável de sumir, e falharia só em
   tempo de execução, num formato só. (Tarefa 4)

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `gifc/tools.py` | precedência de busca dentro do bundle; baixa o ffmpeg |
| `assets/icone.ico` | **novo** — ícone do executável (commitado) |
| `tools/gerar_icone.py` | **novo** — gera o `.ico` com Pillow |
| `tools/build.py` | **novo** — orquestra o empacotamento e mede |
| `comprime-ai.spec` | **novo** — configuração do PyInstaller |
| `tests/test_pacote.py` | **novo** — verifica o artefato construído |
| `LICENSE`, `THIRD-PARTY-LICENSES.md` | **novos** — conformidade |

---

### Task 1: `Tools` sem ffprobe e busca dentro do pacote

**Files:**
- Modify: `gifc/tools.py`
- Modify: `tests/test_tools.py` e as construções de `Tools` nos testes

**Interfaces:**
- Consumes: nada
- Produces:
  - `Tools(ffmpeg: str, gifsicle: str | None, oxipng: str | None)` — campo
    `ffprobe` **removido**; `oxipng` veio do plano A
  - `find_tools(bin_dir: Path = BIN_DIR) -> Tools` — assinatura inalterada
  - `APP_DIR: Path` — pasta do executável quando congelado, `PROJECT_ROOT` fora
  - `dirs_de_busca(bin_dir: Path) -> list[Path]`

Precedência (spec): pasta do executável congelado → `sys._MEIPASS` quando
existir → `bin_dir` → PATH. Os dois primeiros só entram com `sys.frozen`
marcado. `_MEIPASS` cobre `onedir` (`_internal/`) e `onefile` (pasta
temporária) sem precisar saber qual foi usado.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_tools_nao_tem_mais_ffprobe()
    # dataclasses.fields(Tools) não contém "ffprobe"

def test_dirs_de_busca_fora_do_bundle_e_so_o_bin_dir(tmp_path, monkeypatch)
def test_dirs_de_busca_congelado_poe_a_pasta_do_exe_primeiro(tmp_path, monkeypatch)
def test_dirs_de_busca_inclui_meipass(tmp_path, monkeypatch)
def test_find_tools_prefere_o_bundle_ao_path(tmp_path, monkeypatch)
def test_find_tools_cai_no_path_quando_nao_ha_bundle(tmp_path, monkeypatch)
def test_find_tools_sem_ffmpeg_em_lugar_nenhum_levanta(tmp_path, monkeypatch)
    # MissingToolError nomeando ffmpeg
```

Atualize as construções `Tools(...)` nos testes para a forma de três campos.
Depois do plano A, `tests/test_probe.py` pode não construir `Tools` — confira
antes de editar.

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_tools.py -v`

- [ ] **Step 3: Implementar**

Remova `ffprobe` de `Tools` e sua exigência em `find_tools`. Acrescente
`APP_DIR` e `dirs_de_busca`, e faça `find_tools` procurar cada ferramenta por
nome nas pastas da lista antes de recorrer a `shutil.which`.

`BIN_DIR` passa a derivar de `APP_DIR`, para o app congelado procurar ao lado
de si mesmo.

- [ ] **Step 4: Rodar a suíte inteira**

- [ ] **Step 5: Commit**

```bash
git add gifc/tools.py tests/
git commit -m "Tools sem ffprobe e busca com precedência de bundle"
```

---

### Task 2: Baixar o ffmpeg para `bin/`

**Files:**
- Modify: `gifc/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `dirs_de_busca`, `DownloadError`, `BIN_DIR` (Tarefa 1)
- Produces: `FFMPEG_URL`, `FFMPEG_ZIP_SIZE`, `FFMPEG_SHA256`,
  `ffmpeg_path(bin_dir)`, `download_ffmpeg(bin_dir, on_progress=None)`

**Valores verificados:**

```
FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/"
              "autobuild-2026-10-10-13-04/"
              "ffmpeg-n9.0.2-25-g67b60c310b-win64-lgpl-9.0.zip")
FFMPEG_ZIP_SIZE = 171_475_889
```

A tag é datada e imutável, então o hash é estável. `FFMPEG_SHA256` começa
vazio e é preenchido no Step 4 a partir do download real.

O zip traz uma pasta `ffmpeg-*/bin/` com `ffmpeg.exe`, `ffprobe.exe` e
`ffplay.exe`. **Extraia apenas `ffmpeg.exe`** — os outros dois são o que
estamos removendo do pacote.

Reaproveite os helpers de download e extração do gifsicle e do oxipng.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_ffmpeg_url_e_https_e_fixada_numa_tag()      # contém "autobuild-"
def test_ffmpeg_path_fica_no_bin_dir(tmp_path)
def test_extrai_apenas_o_ffmpeg_do_zip(tmp_path)
    # zip com bin/ffmpeg.exe, bin/ffprobe.exe, bin/ffplay.exe
    # -> só ffmpeg.exe aparece
def test_zip_sem_ffmpeg_levanta_downloaderror(tmp_path)
def test_zip_com_caminho_de_escape_e_recusado(tmp_path)
def test_ffmpeg_sha256_tem_formato_valido()          # falha até o Step 4

@pytest.mark.integration
def test_download_real_do_ffmpeg(tmp_path)
```

- [ ] **Step 2: Rodar e confirmar que falham**

- [ ] **Step 3: Implementar**

- [ ] **Step 4: Baixar de verdade e fixar o hash**

Este passo transfere 164 MB. **Confirme com o dono do projeto antes de
rodar.**

```bash
python -c "from pathlib import Path; from gifc.tools import download_ffmpeg; print(download_ffmpeg(Path('bin')))"
```

```bash
python -c "import hashlib,urllib.request; from gifc.tools import FFMPEG_URL; print(hashlib.sha256(urllib.request.urlopen(FFMPEG_URL,timeout=300).read()).hexdigest())"
```

- [ ] **Step 5: Confirmar que o build LGPL serve**

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
(185 MB) da mesma tag, e essa é decisão do dono do projeto.

- [ ] **Step 6: Provar que o PATH não é mais necessário**

Run: `python -m pytest -v`

```bash
python -c "
import os
os.environ['PATH'] = ''
from gifc.tools import find_tools, BIN_DIR
t = find_tools(BIN_DIR)
print('ffmpeg:', t.ffmpeg); print('gifsicle:', t.gifsicle); print('oxipng:', t.oxipng)
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

### Task 3: Ícone, spec do PyInstaller e script de build

**Files:**
- Create: `tools/gerar_icone.py`, `assets/icone.ico`, `comprime-ai.spec`, `tools/build.py`
- Modify: `requirements-dev.txt`, `.gitignore` (`build/`, `dist/`)

**Interfaces:**
- Consumes: `find_tools`, `ffmpeg_path`, `gifsicle_path`, `oxipng_path`, `BIN_DIR`
- Produces: `dist/` com o artefato; `tools/build.py` imprime tamanho e tempo
  de abertura medidos

Pontos que a spec fixa:

- `--noconsole`: sem janela de terminal atrás da interface
- **`tkdnd` declarado nos `datas`.** O `tkinterdnd2` traz bibliotecas nativas
  na pasta `tkdnd` e o PyInstaller **não tem hook** para ele. Sem isso o app
  compila, abre, e arrastar arquivo não faz nada. Localize via
  `Path(tkinterdnd2.__file__).parent / "tkdnd"`.
- Os **três** binários (`ffmpeg.exe`, `gifsicle.exe`, `oxipng.exe`) como
  `binaries`
- Pillow com seus plugins de codec. O PyInstaller às vezes perde os `.pyd` de
  formato; a Tarefa 4 tem teste para isso, mas declare `hiddenimports` do
  Pillow se o teste acusar falta.
- Ícone a partir de `assets/icone.ico`

- [ ] **Step 1: Gerar o ícone**

`tools/gerar_icone.py` desenha com Pillow e grava `assets/icone.ico` nos
tamanhos 16, 32, 48, 64, 128 e 256. O desenho é livre; algo legível em 16px.
Commite o `.ico` para o build ser reprodutível.

- [ ] **Step 2: Escrever `comprime-ai.spec` e construir nos dois modos**

Só construir, ainda sem decidir.

- [ ] **Step 3: Medir e decidir o modo**

A spec manda decidir por medição. Meça a abertura **a frio** dos dois modos:
lançar o processo, cronometrar até a janela existir, matar. Repita 3 vezes e
use o pior caso.

Regra fixada na spec: `onefile` abaixo de **10 segundos** → o asset é um
`.exe` único. Acima → `onedir` em zip.

Registre no relatório o tamanho nos dois modos e os tempos. Esses números vão
para as notas da release.

- [ ] **Step 4: Escrever `tools/build.py`**

Garantir os três binários em `bin/` (baixando se faltar), gerar o ícone se
faltar, rodar o PyInstaller no modo escolhido, e imprimir tamanho final e
tempo de abertura.

Falhar alto e claro: se um binário não estiver lá e o download não for
possível, diga o que baixar e onde pôr.

- [ ] **Step 5: Commit**

```bash
git add tools/ assets/ comprime-ai.spec requirements-dev.txt .gitignore
git commit -m "Empacotamento com PyInstaller, ícone e script de build"
```

---

### Task 4: Verificar o pacote

**Files:**
- Create: `tests/test_pacote.py`

**Interfaces:**
- Consumes: o artefato em `dist/` (Tarefa 3), `compress_file`, `Toggles`, `Formato`
- Produces: nada que outra tarefa use

Marcados `integration`, pulam com mensagem clara quando `dist/` não existe,
para a suíte seguir verde numa máquina que não construiu o pacote.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_os_tres_binarios_estao_no_pacote()
    # ffmpeg.exe, gifsicle.exe, oxipng.exe

def test_tkdnd_esta_no_pacote()
    # a falha silenciosa mais provável de todo este trabalho

def test_pillow_tem_os_codecs_no_pacote()
    # Review Focus 5: roda o Python do pacote (ou importa de dentro
    # dele) e confirma que JPEG, PNG e WebP estão disponíveis.
    # WebP é o que mais some e falharia só em runtime, num formato só.

@pytest.mark.parametrize("formato", list(Formato))
def test_comprime_cada_formato_usando_so_os_binarios_do_pacote(formato, tmp_path)
    # Tools apontando para os binários DENTRO de dist/, PATH vazio no
    # ambiente, compress_file num arquivo real de cada formato.
    # Prova que o empacotamento funciona — não que a máquina de build
    # tinha as ferramentas instaladas.

def test_comprime_em_caminho_com_acentos_e_espacos(tmp_path)
    # Review Focus 1: pasta "Área de Trabalho ção"

def test_caminho_longo_proximo_do_limite_do_windows(tmp_path)
    # Review Focus 2: erro claro em português, não OSError cru

def test_executavel_abre_sem_crash()
    # lança, espera, confirma processo vivo e stderr sem traceback, encerra
```

E o Review Focus 3, que é instrução, não código — verifique à mão uma vez e
registre no relatório: em `onedir`, rodar o `.exe` de dentro do zip falha.
Confirme a mensagem que aparece, porque ela precisa estar no README.

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_pacote.py -v`
Expected: FAIL (ou skip, se `dist/` ainda não existe — então construa antes)

- [ ] **Step 3: Corrigir o que os testes revelarem**

Tarefa de verificação: o código a mudar é o que os testes acusarem. Os
diagnósticos prováveis, do mais ao menos:

- **`tkdnd` ausente** → a entrada em `datas` não aponta para a pasta certa.
  Confira o caminho resolvido em tempo de build, não o literal.
- **Codec do Pillow ausente** → falta `hiddenimports` do plugin
  correspondente no `.spec`.
- **Binário não encontrado em runtime** → `dirs_de_busca` não olha onde o
  PyInstaller realmente pôs os `binaries`. Imprima a lista de dentro do app
  congelado para ver o que ele enxerga.
- **Falha só no caminho com acentos** → codificação do ambiente passado ao
  `subprocess`; o problema está no codificador, não no empacotamento.

Se um teste revelar algo fora dessas quatro hipóteses, reporte antes de
corrigir — pode ser defeito de desenho.

- [ ] **Step 4: Rodar a suíte inteira**

- [ ] **Step 5: Commit**

```bash
git add tests/test_pacote.py
git commit -m "Verifica que o pacote comprime os quatro formatos sozinho"
```

---

### Task 5: Licença, terceiros e README

**Files:**
- Create: `LICENSE`, `THIRD-PARTY-LICENSES.md`
- Modify: `README.md`

- [ ] **Step 1: `LICENSE`**

Texto integral da GPL-3.0, sem modificação.

- [ ] **Step 2: `THIRD-PARTY-LICENSES.md`**

Para as quatro dependências — ffmpeg (LGPL), gifsicle (GPL-2+), oxipng (MIT)
e Pillow (HPND): versão exata empacotada, licença, link do código-fonte
upstream, e a observação de que vão sem modificação.

- [ ] **Step 3: Reescrever o `README.md`**

Duas portas, nesta ordem:

**Topo — "só quero usar":** o aviso do SmartScreen **antes** do link, com o
caminho de cliques ("Mais informações" → "Executar assim mesmo"); se o modo
for `onedir`, a instrução de **extrair a pasta antes de abrir** com a mensagem
de erro real que aparece se não extrair (medida na Tarefa 4); o link com o
tamanho declarado; a lista dos quatro formatos; três passos. Nada de Python,
nada de terminal.

**Abaixo — "quero o código":** instalação, testes, o pipeline de cada formato,
as escadas, e o `bin/` como fonte das ferramentas.

Deixe o espaço do GIF de demonstração marcado com um comentário HTML dizendo
onde ele entra.

- [ ] **Step 4: Commit**

```bash
git add LICENSE THIRD-PARTY-LICENSES.md README.md
git commit -m "GPL-3.0, licenças de terceiros e README para quem só quer usar"
```

---

### Task 6: Publicar a release

**Files:** nenhum — é operação sobre o repositório remoto

**Esta tarefa publica conteúdo visível a qualquer pessoa e é difícil de
desfazer. Apresente as notas ao dono do projeto e espere aprovação explícita
antes do Step 3.**

- [ ] **Step 1: Confirmar árvore limpa e suíte verde**

```bash
git status --short && python -m pytest -q
```

- [ ] **Step 2: Escrever as notas e mostrá-las**

Em português, para quem não é da área: o que o programa faz e para quais
formatos, o que esperar de resultado com os **números medidos de verdade**
(GIF, PNG de interface, JPEG), o aviso do SmartScreen com o caminho de
cliques, o tamanho do download antes do link, e a nota de que o pacote é GPL.

- [ ] **Step 3: Criar a tag e a release**

O artefato depende da decisão medida na Tarefa 3: um `.exe` único se o
`onefile` abriu em menos de 10 segundos, ou um `.zip` da pasta `onedir` caso
contrário. Use o nome real — quem baixa lê o nome do arquivo.

```bash
git tag -a v1.0.0 -m "Primeira release pública"
git push origin main --tags
gh release create v1.0.0 "dist/ComprimeAi-1.0.0-windows.<exe|zip>" \
  --title "Comprime Aí 1.0.0" --notes-file <caminho das notas>
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
- [ ] O app empacotado comprime os quatro formatos com PATH vazio
- [ ] Tamanho do pacote e tempo de abertura medidos e registrados na release
- [ ] `LICENSE` e `THIRD-PARTY-LICENSES.md` publicados
- [ ] README começa pelo caminho do leigo, SmartScreen antes do link
- [ ] `git ls-files | grep -E "\.exe$"` vazio

## Checklist manual do dono do projeto

1. A janela abre com layout correto e o ícone aparece
2. **Arrastar arquivos do Explorer funciona** — o teste do `tkdnd`, o ponto
   mais frágil de todo o trabalho
3. Soltar um de cada formato e conferir os resultados
4. As alavancas que não se aplicam ao formato soltado ficam desabilitadas
5. A caixa de WebP produz `.webp` quando ligada
6. "Abrir pasta" leva ao lugar certo
7. Rodar numa máquina sem Python **e sem Visual C++ Redistributable**, se
   houver uma à mão (Review Focus 4)
