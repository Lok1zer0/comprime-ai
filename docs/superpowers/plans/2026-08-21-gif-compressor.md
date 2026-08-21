# Compressor de GIF — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** App local com GUI drag-and-drop que comprime GIFs até um tamanho-alvo, escolhendo automaticamente a maior qualidade que cabe no limite.

**Architecture:** Cada tentativa roda `ffmpeg` (paleta otimizada + escala + fps) encadeado com `gifsicle -O3 --lossy`. Uma escada de 20 degraus ordenada por perda visual crescente é percorrida por busca binária (~5 codificações). A lógica de decisão (`ladder`, `search`) é pura e sem I/O; o I/O fica isolado em `probe`, `encode` e `tools`.

**Tech Stack:** Python 3.12, Tkinter + tkinterdnd2, ffmpeg 9.0, gifsicle 1.95, pytest.

## Global Constraints

- Python 3.12+. Type hints em todas as funções públicas.
- Única dependência de runtime: `tkinterdnd2`. Única de desenvolvimento: `pytest`.
- **Nunca modificar ou sobrescrever o arquivo GIF de entrada**, em nenhum caminho de execução.
- Saída em `<nome>_comprimido.gif`; se existir, sufixar `_2`, `_3`, ...
- Todo arquivo temporário vai para um `tempfile.mkdtemp()` removido em `finally`.
- Nenhuma escrita fora da pasta do projeto e do diretório do GIF de entrada.
- `bin/` está no `.gitignore` — o binário do gifsicle nunca é commitado.
- Mensagens de interface e de erro em português.
- Commits em português, com `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

**Referência:** [spec](../specs/2026-08-21-gif-compressor-design.md)

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `gifc/tools.py` | Localizar ffmpeg/ffprobe/gifsicle; baixar o gifsicle |
| `gifc/probe.py` | Ler metadados de um GIF via ffprobe |
| `gifc/ladder.py` | `Params`, `Toggles`, a escada de 20 degraus (puro) |
| `gifc/search.py` | Busca binária até o alvo, com encoder injetável (puro) |
| `gifc/encode.py` | Montar e executar os comandos ffmpeg + gifsicle |
| `gifc/compress.py` | Orquestrar probe → ladder → search → arquivo final |
| `gifc/app.py` | GUI Tkinter |
| `main.py` | Entrypoint |

`ladder` e `search` concentram toda a decisão e não tocam em disco — são testáveis em milissegundos. `encode` e `probe` concentram o I/O com superfície pequena.

---

### Task 1: Scaffold do projeto e detecção de ferramentas

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `pytest.ini`
- Create: `gifc/__init__.py`, `gifc/tools.py`
- Test: `tests/__init__.py`, `tests/test_tools.py`

**Interfaces:**
- Consumes: nada (primeira task)
- Produces: `Tools(ffmpeg: str, ffprobe: str, gifsicle: str | None)` (dataclass congelada), `MissingToolError`, `find_tools(bin_dir: Path) -> Tools`, `gifsicle_path(bin_dir: Path) -> Path`, `PROJECT_ROOT: Path`, `BIN_DIR: Path`

- [ ] **Step 1: Criar os arquivos de configuração**

`requirements.txt`:
```
tkinterdnd2>=0.4.0
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest>=8.0
```

`pytest.ini`:
```ini
[pytest]
testpaths = tests
python_files = test_*.py
markers =
    integration: testes que executam ffmpeg/gifsicle de verdade
```

- [ ] **Step 2: Criar os pacotes vazios**

```bash
mkdir -p gifc tests
touch gifc/__init__.py tests/__init__.py
```

- [ ] **Step 3: Escrever os testes que falham**

`tests/test_tools.py`:
```python
from pathlib import Path

import pytest

from gifc.tools import MissingToolError, Tools, find_tools, gifsicle_path


def test_gifsicle_path_fica_dentro_do_bin_dir(tmp_path):
    p = gifsicle_path(tmp_path)
    assert p.parent == tmp_path
    assert p.stem == "gifsicle"


def test_find_tools_acha_ffmpeg_no_path(tmp_path):
    tools = find_tools(tmp_path)
    assert tools.ffmpeg
    assert tools.ffprobe
    assert Path(tools.ffmpeg).name.startswith("ffmpeg")


def test_find_tools_sem_gifsicle_devolve_none(tmp_path):
    # tmp_path vazio: nenhum gifsicle instalado ali
    tools = find_tools(tmp_path)
    assert tools.gifsicle is None


def test_find_tools_acha_gifsicle_no_bin_dir(tmp_path):
    fake = gifsicle_path(tmp_path)
    fake.write_bytes(b"nao-e-um-binario-de-verdade")
    tools = find_tools(tmp_path)
    assert tools.gifsicle == str(fake)


def test_tools_e_imutavel():
    t = Tools(ffmpeg="a", ffprobe="b", gifsicle=None)
    with pytest.raises(Exception):
        t.ffmpeg = "c"  # type: ignore[misc]


def test_missing_tool_error_nomeia_a_ferramenta():
    err = MissingToolError("ffmpeg")
    assert "ffmpeg" in str(err)
```

- [ ] **Step 4: Rodar os testes e confirmar que falham**

Run: `python -m pytest tests/test_tools.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'gifc.tools'`

- [ ] **Step 5: Implementar `gifc/tools.py`**

```python
"""Localização das ferramentas externas (ffmpeg, ffprobe, gifsicle)."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = PROJECT_ROOT / "bin"


class MissingToolError(Exception):
    """Uma ferramenta externa obrigatória não foi encontrada."""

    def __init__(self, tool: str) -> None:
        self.tool = tool
        super().__init__(
            f"Ferramenta não encontrada: {tool}. "
            f"Instale-a e garanta que está no PATH."
        )


@dataclass(frozen=True)
class Tools:
    """Caminhos das ferramentas externas.

    `gifsicle` é None quando o binário não está disponível — nesse caso o
    app roda em modo degradado, sem o estágio de compressão lossy.
    """

    ffmpeg: str
    ffprobe: str
    gifsicle: str | None


def gifsicle_path(bin_dir: Path) -> Path:
    """Caminho onde o gifsicle portátil fica dentro da pasta do app."""
    name = "gifsicle.exe" if sys.platform == "win32" else "gifsicle"
    return bin_dir / name


def find_tools(bin_dir: Path = BIN_DIR) -> Tools:
    """Localiza as ferramentas. Levanta MissingToolError se faltar ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise MissingToolError("ffmpeg")

    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise MissingToolError("ffprobe")

    local = gifsicle_path(bin_dir)
    gifsicle = str(local) if local.is_file() else shutil.which("gifsicle")

    return Tools(ffmpeg=ffmpeg, ffprobe=ffprobe, gifsicle=gifsicle)
```

- [ ] **Step 6: Rodar os testes e confirmar que passam**

Run: `python -m pytest tests/test_tools.py -v`
Expected: 6 passed

- [ ] **Step 7: Commit**

```bash
git add requirements.txt requirements-dev.txt pytest.ini gifc/ tests/
git commit -m "Scaffold do projeto e detecção de ferramentas externas"
```

---

### Task 2: Download portátil do gifsicle

**Files:**
- Modify: `gifc/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `gifsicle_path`, `BIN_DIR` (Task 1)
- Produces: `GIFSICLE_URL: str`, `GIFSICLE_SHA256: str`, `DownloadError`, `download_gifsicle(bin_dir: Path, on_progress: Callable[[int, int], None] | None = None) -> Path`

**Nota de execução:** este é o único ponto do plano que baixa algo da internet. A URL e o tamanho já foram verificados: `https://eternallybored.org/misc/gifsicle/releases/gifsicle-1.95-win64.zip`, 230.072 bytes — a build oficial de Windows indicada pelo site do gifsicle (lcdf.org). **Mostre a URL ao usuário e obtenha confirmação antes do Step 5.**

- [ ] **Step 1: Escrever os testes que falham**

Acrescentar a `tests/test_tools.py`:
```python
import hashlib
import io
import zipfile

from gifc.tools import (
    GIFSICLE_SHA256,
    GIFSICLE_URL,
    DownloadError,
    download_gifsicle,
    extract_gifsicle,
)


def _zip_com(nomes: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nome, dados in nomes.items():
            z.writestr(nome, dados)
    return buf.getvalue()


def test_url_e_https_e_aponta_para_gifsicle():
    assert GIFSICLE_URL.startswith("https://")
    assert "gifsicle" in GIFSICLE_URL


def test_extract_gifsicle_acha_o_exe_em_subpasta(tmp_path):
    conteudo = b"binario-falso"
    dados = _zip_com({"gifsicle-1.95-win64/gifsicle.exe": conteudo})
    destino = extract_gifsicle(dados, tmp_path)
    assert destino.is_file()
    assert destino.read_bytes() == conteudo


def test_extract_gifsicle_sem_o_exe_levanta_erro(tmp_path):
    dados = _zip_com({"leiame.txt": b"nada aqui"})
    with pytest.raises(DownloadError, match="gifsicle"):
        extract_gifsicle(dados, tmp_path)


def test_extract_gifsicle_rejeita_caminho_de_escape(tmp_path):
    # zip-slip: entrada tentando escrever fora do destino
    dados = _zip_com({"../../gifsicle.exe": b"malicioso"})
    with pytest.raises(DownloadError):
        extract_gifsicle(dados, tmp_path)


def test_sha256_registrado_tem_formato_valido():
    assert len(GIFSICLE_SHA256) == 64
    int(GIFSICLE_SHA256, 16)  # levanta ValueError se não for hex


@pytest.mark.integration
def test_download_real_bate_o_hash(tmp_path):
    destino = download_gifsicle(tmp_path)
    assert destino.is_file()
    assert destino.stat().st_size > 50_000
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_tools.py -v`
Expected: FAIL com `ImportError: cannot import name 'GIFSICLE_URL'`

- [ ] **Step 3: Implementar o download, com o hash ainda não fixado**

Acrescentar a `gifc/tools.py`:
```python
import hashlib
import io
import urllib.request
import zipfile
from collections.abc import Callable

GIFSICLE_URL = (
    "https://eternallybored.org/misc/gifsicle/releases/gifsicle-1.95-win64.zip"
)
GIFSICLE_SIZE = 230_072
# Preenchido no Step 5, a partir do download verificado.
GIFSICLE_SHA256 = ""


class DownloadError(Exception):
    """Falha ao obter ou extrair o gifsicle."""


def extract_gifsicle(zip_bytes: bytes, bin_dir: Path) -> Path:
    """Extrai o executável do gifsicle de dentro do zip baixado."""
    destino = gifsicle_path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        alvo = next(
            (n for n in z.namelist() if Path(n).name.lower() == "gifsicle.exe"),
            None,
        )
        if alvo is None:
            raise DownloadError(
                "O arquivo baixado não contém gifsicle.exe."
            )
        # Proteção contra zip-slip: o caminho normalizado deve ficar dentro
        # do diretório de destino.
        resolvido = (bin_dir / Path(alvo).name).resolve()
        if not resolvido.is_relative_to(bin_dir.resolve()):
            raise DownloadError(f"Caminho inseguro no zip: {alvo}")
        if ".." in Path(alvo).parts:
            raise DownloadError(f"Caminho inseguro no zip: {alvo}")

        destino.write_bytes(z.read(alvo))

    return destino


def download_gifsicle(
    bin_dir: Path = BIN_DIR,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Baixa o gifsicle portátil para `bin_dir` e devolve o caminho do exe."""
    try:
        with urllib.request.urlopen(GIFSICLE_URL, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            pedacos: list[bytes] = []
            baixado = 0
            while True:
                pedaco = resp.read(64 * 1024)
                if not pedaco:
                    break
                pedacos.append(pedaco)
                baixado += len(pedaco)
                if on_progress is not None:
                    on_progress(baixado, total)
            dados = b"".join(pedacos)
    except OSError as exc:
        raise DownloadError(f"Não foi possível baixar o gifsicle: {exc}") from exc

    digest = hashlib.sha256(dados).hexdigest()
    if GIFSICLE_SHA256 and digest != GIFSICLE_SHA256:
        raise DownloadError(
            "O gifsicle baixado não confere com o hash esperado.\n"
            f"esperado: {GIFSICLE_SHA256}\nobtido:   {digest}"
        )

    return extract_gifsicle(dados, bin_dir)
```

- [ ] **Step 4: Rodar os testes unitários (sem os de integração)**

Run: `python -m pytest tests/test_tools.py -v -m "not integration"`
Expected: PASS, exceto `test_sha256_registrado_tem_formato_valido`, que ainda falha porque o hash está vazio.

- [ ] **Step 5: Baixar de verdade e fixar o hash**

Confirme a URL com o usuário antes de rodar. Depois:

```bash
python -c "from pathlib import Path; from gifc.tools import download_gifsicle; print(download_gifsicle(Path('bin')))"
```

```bash
python -c "import hashlib,urllib.request; from gifc.tools import GIFSICLE_URL; print(hashlib.sha256(urllib.request.urlopen(GIFSICLE_URL,timeout=60).read()).hexdigest())"
```

Copie o hash impresso para a constante `GIFSICLE_SHA256` em `gifc/tools.py`.

- [ ] **Step 6: Verificar que o binário funciona de verdade**

```bash
./bin/gifsicle.exe --version
```
Expected: primeira linha contendo `LCDF Gifsicle 1.95`

Se a versão impressa for outra, atualize `GIFSICLE_URL` e refaça o Step 5.

- [ ] **Step 7: Rodar a suíte inteira, agora com integração**

Run: `python -m pytest tests/test_tools.py -v`
Expected: 12 passed

- [ ] **Step 8: Commit**

```bash
git add gifc/tools.py tests/test_tools.py
git commit -m "Download portátil do gifsicle com verificação de hash"
```

---

### Task 3: Leitura de metadados do GIF

**Files:**
- Create: `gifc/probe.py`
- Create: `tests/conftest.py`
- Test: `tests/test_probe.py`

**Interfaces:**
- Consumes: `Tools`, `find_tools` (Task 1)
- Produces: `GifInfo(path, width, height, fps, frame_count, duration, size_bytes)` (dataclass congelada), `ProbeError`, `probe(path: Path, tools: Tools) -> GifInfo`; fixtures `tools` e `gif_pequeno`

- [ ] **Step 1: Criar as fixtures compartilhadas**

`tests/conftest.py`:
```python
"""Fixtures compartilhadas: ferramentas e um GIF de teste real."""

import subprocess
from pathlib import Path

import pytest

from gifc.tools import BIN_DIR, MissingToolError, find_tools


@pytest.fixture(scope="session")
def tools():
    try:
        t = find_tools(BIN_DIR)
    except MissingToolError as exc:
        pytest.skip(f"ferramenta ausente: {exc.tool}")
    if t.gifsicle is None:
        pytest.skip("gifsicle não disponível em bin/")
    return t


@pytest.fixture(scope="session")
def gif_pequeno(tools, tmp_path_factory) -> Path:
    """Gera um GIF real de 64x64, 10 frames, 10fps, com ffmpeg."""
    destino = tmp_path_factory.mktemp("fixtures") / "amostra.gif"
    subprocess.run(
        [
            tools.ffmpeg, "-y", "-v", "error",
            "-f", "lavfi",
            "-i", "testsrc=size=64x64:rate=10:duration=1",
            str(destino),
        ],
        check=True,
        capture_output=True,
    )
    return destino
```

- [ ] **Step 2: Escrever os testes que falham**

`tests/test_probe.py`:
```python
from pathlib import Path

import pytest

from gifc.probe import GifInfo, ProbeError, probe


@pytest.mark.integration
def test_probe_le_dimensoes_do_gif(tools, gif_pequeno):
    info = probe(gif_pequeno, tools)
    assert info.width == 64
    assert info.height == 64


@pytest.mark.integration
def test_probe_le_fps_e_frames(tools, gif_pequeno):
    info = probe(gif_pequeno, tools)
    assert info.fps == pytest.approx(10.0, abs=0.5)
    assert info.frame_count >= 9


@pytest.mark.integration
def test_probe_le_tamanho_do_arquivo(tools, gif_pequeno):
    info = probe(gif_pequeno, tools)
    assert info.size_bytes == gif_pequeno.stat().st_size
    assert info.size_bytes > 0


@pytest.mark.integration
def test_probe_arquivo_inexistente(tools, tmp_path):
    with pytest.raises(ProbeError):
        probe(tmp_path / "nao-existe.gif", tools)


@pytest.mark.integration
def test_probe_arquivo_invalido(tools, tmp_path):
    ruim = tmp_path / "ruim.gif"
    ruim.write_bytes(b"isto nao e um gif")
    with pytest.raises(ProbeError):
        probe(ruim, tools)


def test_gif_info_e_imutavel():
    info = GifInfo(
        path=Path("x.gif"), width=1, height=1, fps=1.0,
        frame_count=1, duration=1.0, size_bytes=1,
    )
    with pytest.raises(Exception):
        info.width = 2  # type: ignore[misc]
```

- [ ] **Step 3: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_probe.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'gifc.probe'`

- [ ] **Step 4: Implementar `gifc/probe.py`**

```python
"""Leitura de metadados de um GIF via ffprobe."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from gifc.tools import Tools


class ProbeError(Exception):
    """O arquivo não pôde ser lido como um GIF válido."""


@dataclass(frozen=True)
class GifInfo:
    path: Path
    width: int
    height: int
    fps: float
    frame_count: int
    duration: float
    size_bytes: int


def _parse_fps(valor: str) -> float:
    """Converte '25/1' ou '0/0' num float utilizável."""
    try:
        f = float(Fraction(valor))
    except (ValueError, ZeroDivisionError):
        return 10.0
    return f if f > 0 else 10.0


def probe(path: Path, tools: Tools) -> GifInfo:
    """Lê os metadados do GIF. Levanta ProbeError se não for um GIF válido."""
    path = Path(path)
    if not path.is_file():
        raise ProbeError(f"Arquivo não encontrado: {path}")

    cmd = [
        tools.ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-count_frames",
        "-show_entries",
        "stream=width,height,avg_frame_rate,nb_read_frames,duration",
        "-show_entries", "format=format_name,duration",
        "-print_format", "json",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except OSError as exc:
        raise ProbeError(f"Falha ao executar ffprobe: {exc}") from exc

    if proc.returncode != 0:
        raise ProbeError(f"ffprobe falhou em {path.name}: {proc.stderr.strip()}")

    try:
        dados = json.loads(proc.stdout)
        stream = dados["streams"][0]
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        raise ProbeError(f"{path.name} não contém um fluxo de vídeo.") from exc

    formato = dados.get("format", {}).get("format_name", "")
    if "gif" not in formato:
        raise ProbeError(f"{path.name} não é um GIF (formato: {formato}).")

    fps = _parse_fps(stream.get("avg_frame_rate", "0/0"))
    frame_count = int(stream.get("nb_read_frames") or 0)
    duracao = float(
        stream.get("duration") or dados.get("format", {}).get("duration") or 0.0
    )
    if duracao <= 0 and frame_count and fps:
        duracao = frame_count / fps

    return GifInfo(
        path=path,
        width=int(stream["width"]),
        height=int(stream["height"]),
        fps=fps,
        frame_count=frame_count,
        duration=duracao,
        size_bytes=path.stat().st_size,
    )
```

- [ ] **Step 5: Rodar e confirmar que passam**

Run: `python -m pytest tests/test_probe.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add gifc/probe.py tests/conftest.py tests/test_probe.py
git commit -m "Leitura de metadados do GIF via ffprobe"
```

---

### Task 4: A escada de qualidade

**Files:**
- Create: `gifc/ladder.py`
- Test: `tests/test_ladder.py`

**Interfaces:**
- Consumes: `GifInfo` (Task 3)
- Produces: `Params(colors: int, lossy: int, scale_pct: int, fps: float | None)` (congelada, hashável), `Toggles(palette, lossy, scale, fps: bool)`, `BASE_LADDER: tuple[Params, ...]`, `build(info: GifInfo, toggles: Toggles) -> list[Params]`, `target_width(width: int, scale_pct: int) -> int`

`Params.fps is None` significa "manter o framerate original". `Params` é a chave de memoização em `search`, por isso precisa ser hashável.

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_ladder.py`:
```python
from pathlib import Path

import pytest

from gifc.ladder import BASE_LADDER, Params, Toggles, build, target_width
from gifc.probe import GifInfo


def info(fps: float = 30.0, width: int = 800) -> GifInfo:
    return GifInfo(
        path=Path("x.gif"), width=width, height=600, fps=fps,
        frame_count=100, duration=100 / fps, size_bytes=1_000_000,
    )


TODOS = Toggles(palette=True, lossy=True, scale=True, fps=True)


def test_escada_base_tem_20_degraus():
    assert len(BASE_LADDER) == 20


def test_escada_base_comeca_no_degrau_mais_conservador():
    assert BASE_LADDER[0] == Params(colors=256, lossy=0, scale_pct=100, fps=None)


def test_escada_base_termina_no_mais_agressivo():
    ultimo = BASE_LADDER[-1]
    assert ultimo.colors == 32
    assert ultimo.lossy == 200
    assert ultimo.scale_pct == 50


def test_agressividade_nao_diminui_ao_longo_da_escada():
    """Nenhum degrau é melhor que o anterior em todas as dimensões."""
    for anterior, atual in zip(BASE_LADDER, BASE_LADDER[1:]):
        melhor_em_tudo = (
            atual.colors >= anterior.colors
            and atual.lossy <= anterior.lossy
            and atual.scale_pct >= anterior.scale_pct
            and (atual.fps is None or (anterior.fps is not None
                                       and atual.fps >= anterior.fps))
        )
        assert not melhor_em_tudo, f"{atual} não é mais agressivo que {anterior}"


def test_params_e_hashavel():
    assert len({Params(64, 80, 100, None), Params(64, 80, 100, None)}) == 1


def test_build_com_todos_ligados_mantem_os_20():
    assert len(build(info(), TODOS)) == 20


def test_toggle_paleta_desligado_fixa_256_cores():
    escada = build(info(), Toggles(False, True, True, True))
    assert all(p.colors == 256 for p in escada)
    assert len(escada) < 20


def test_toggle_lossy_desligado_zera_lossy():
    escada = build(info(), Toggles(True, False, True, True))
    assert all(p.lossy == 0 for p in escada)


def test_toggle_escala_desligado_fixa_100():
    escada = build(info(), Toggles(True, True, False, True))
    assert all(p.scale_pct == 100 for p in escada)


def test_toggle_fps_desligado_zera_fps():
    escada = build(info(), Toggles(True, True, True, False))
    assert all(p.fps is None for p in escada)


def test_tudo_desligado_deixa_so_o_degrau_neutro():
    escada = build(info(), Toggles(False, False, False, False))
    assert escada == [Params(colors=256, lossy=0, scale_pct=100, fps=None)]


def test_fps_alvo_acima_do_original_vira_none():
    """GIF de 10fps não deve gerar degraus pedindo 12, 15 ou 20 fps."""
    escada = build(info(fps=10.0), TODOS)
    assert all(p.fps is None for p in escada)


def test_escada_nao_tem_duplicatas():
    for toggles in [
        TODOS,
        Toggles(False, True, True, True),
        Toggles(True, False, True, True),
        Toggles(True, True, False, True),
        Toggles(True, True, True, False),
    ]:
        escada = build(info(), toggles)
        assert len(escada) == len(set(escada))


def test_dedup_preserva_a_ordem_original():
    escada = build(info(), Toggles(True, True, False, False))
    indices = [BASE_LADDER.index(
        Params(p.colors, p.lossy, 100, None)
    ) for p in escada if Params(p.colors, p.lossy, 100, None) in BASE_LADDER]
    assert indices == sorted(indices)


@pytest.mark.parametrize(
    "largura,pct,esperado",
    [
        (800, 100, 800),
        (800, 85, 680),
        (800, 70, 560),
        (800, 50, 400),
        (801, 85, 680),   # arredonda para par
        (10, 50, 16),     # respeita o mínimo
        (33, 100, 34),    # ímpar vira par
    ],
)
def test_target_width(largura, pct, esperado):
    assert target_width(largura, pct) == esperado
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_ladder.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'gifc.ladder'`

- [ ] **Step 3: Implementar `gifc/ladder.py`**

```python
"""A escada de qualidade: da perda menos perceptível à mais agressiva.

Não faz I/O. Tudo aqui é função pura, testável sem ffmpeg.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from gifc.probe import GifInfo

LARGURA_MINIMA = 16


@dataclass(frozen=True)
class Params:
    """Um ponto no espaço de compressão.

    `fps=None` significa manter o framerate original.
    """

    colors: int
    lossy: int
    scale_pct: int
    fps: float | None


@dataclass(frozen=True)
class Toggles:
    """Quais alavancas o usuário autorizou."""

    palette: bool = True
    lossy: bool = True
    scale: bool = True
    fps: bool = True


# Ordenada por custo visual crescente: paleta e lossy primeiro (quase
# invisíveis), resolução depois, framerate por último — perda de fluidez é
# o que mais salta aos olhos.
BASE_LADDER: tuple[Params, ...] = (
    Params(256, 0, 100, None),      # 0
    Params(256, 20, 100, None),     # 1
    Params(256, 30, 100, None),     # 2
    Params(128, 30, 100, None),     # 3
    Params(128, 45, 100, None),     # 4
    Params(128, 60, 100, None),     # 5
    Params(64, 60, 100, None),      # 6
    Params(64, 80, 100, None),      # 7
    Params(64, 100, 100, None),     # 8
    Params(64, 80, 85, None),       # 9
    Params(64, 100, 85, None),      # 10
    Params(64, 80, 70, None),       # 11
    Params(64, 100, 70, None),      # 12
    Params(64, 80, 70, 20.0),       # 13
    Params(64, 100, 70, 15.0),      # 14
    Params(64, 100, 70, 12.0),      # 15
    Params(32, 100, 70, 15.0),      # 16
    Params(32, 140, 70, 12.0),      # 17
    Params(32, 140, 50, 12.0),      # 18
    Params(32, 200, 50, 10.0),      # 19
)


def target_width(width: int, scale_pct: int) -> int:
    """Largura alvo: arredondada para par, com piso de 16px."""
    bruta = width * scale_pct / 100
    par = round(bruta / 2) * 2
    return max(LARGURA_MINIMA, par)


def build(info: GifInfo, toggles: Toggles) -> list[Params]:
    """Monta a escada aplicando os toggles e removendo degraus repetidos."""
    escada: list[Params] = []
    vistos: set[Params] = set()

    for base in BASE_LADDER:
        p = base
        if not toggles.palette:
            p = replace(p, colors=256)
        if not toggles.lossy:
            p = replace(p, lossy=0)
        if not toggles.scale:
            p = replace(p, scale_pct=100)
        if not toggles.fps:
            p = replace(p, fps=None)

        # Nunca aumentar o framerate: um alvo >= ao original é o original.
        if p.fps is not None and p.fps >= info.fps:
            p = replace(p, fps=None)

        if p not in vistos:
            vistos.add(p)
            escada.append(p)

    return escada
```

- [ ] **Step 4: Rodar e confirmar que passam**

Run: `python -m pytest tests/test_ladder.py -v`
Expected: 21 passed

- [ ] **Step 5: Commit**

```bash
git add gifc/ladder.py tests/test_ladder.py
git commit -m "Escada de qualidade de 20 degraus com filtragem por toggles"
```

---

### Task 5: Busca binária até o alvo

**Files:**
- Create: `gifc/search.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `Params` (Task 4)
- Produces: `Attempt(index, params, size_bytes)`, `SearchResult(index, params, size_bytes, hit_target, attempts)`, `Cancelled`, `find_best(ladder, target_bytes, encode_fn, on_progress=None, should_cancel=None) -> SearchResult`

`encode_fn` tem assinatura `(index: int, params: Params) -> int` e devolve o tamanho em bytes. É injetado para que a busca seja testável sem ffmpeg.

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_search.py`:
```python
import math

import pytest

from gifc.ladder import Params
from gifc.search import Cancelled, find_best


def escada(n: int) -> list[Params]:
    return [Params(colors=256, lossy=i, scale_pct=100, fps=None) for i in range(n)]


def encoder(tamanhos: list[int], registro: list[int] | None = None):
    def fn(index: int, params: Params) -> int:
        if registro is not None:
            registro.append(index)
        return tamanhos[index]
    return fn


def test_acha_o_menor_indice_valido():
    # tamanhos 100, 90, ..., 10 — alvo 55 aceita o índice 5 (50) em diante
    tamanhos = [100 - i * 10 for i in range(10)]
    r = find_best(escada(10), 55, encoder(tamanhos))
    assert r.index == 5
    assert r.size_bytes == 50
    assert r.hit_target is True


def test_usa_busca_binaria_e_nao_forca_bruta():
    tamanhos = [100 - i for i in range(20)]
    registro: list[int] = []
    find_best(escada(20), 85, encoder(tamanhos, registro))
    assert len(registro) <= math.ceil(math.log2(21))


def test_degrau_zero_ja_valido_nao_desperdica_qualidade():
    tamanhos = [10 - i for i in range(10)]
    r = find_best(escada(10), 999, encoder(tamanhos))
    assert r.index == 0
    assert r.hit_target is True


def test_nenhum_degrau_valido_devolve_o_menor_absoluto():
    tamanhos = [100 - i * 5 for i in range(10)]  # menor é 55
    r = find_best(escada(10), 10, encoder(tamanhos))
    assert r.hit_target is False
    assert r.size_bytes == 55
    assert r.index == 9


def test_curva_nao_monotonica_nao_piora_o_resultado():
    # o índice 4 sai fora da curva, maior que seus vizinhos
    tamanhos = [100, 90, 80, 70, 95, 50, 40, 30, 20, 10]
    r = find_best(escada(10), 75, encoder(tamanhos))
    assert r.size_bytes <= 75
    assert r.hit_target is True


def test_memoiza_cada_indice_no_maximo_uma_vez():
    tamanhos = [100 - i * 10 for i in range(10)]
    registro: list[int] = []
    find_best(escada(10), 55, encoder(tamanhos, registro))
    assert len(registro) == len(set(registro))


def test_escada_de_um_degrau():
    r = find_best(escada(1), 50, encoder([40]))
    assert r.index == 0
    assert r.hit_target is True


def test_escada_vazia_levanta_erro():
    with pytest.raises(ValueError):
        find_best([], 50, encoder([]))


def test_progresso_e_chamado_para_cada_tentativa():
    tamanhos = [100 - i * 10 for i in range(10)]
    vistos: list[int] = []
    find_best(
        escada(10), 55, encoder(tamanhos),
        on_progress=lambda a, total: vistos.append(a.index),
    )
    assert vistos
    assert len(vistos) == len(set(vistos))


def test_cancelamento_interrompe_a_busca():
    tamanhos = [100 - i * 10 for i in range(10)]
    with pytest.raises(Cancelled):
        find_best(escada(10), 55, encoder(tamanhos), should_cancel=lambda: True)


def test_attempts_registra_todas_as_tentativas():
    tamanhos = [100 - i * 10 for i in range(10)]
    r = find_best(escada(10), 55, encoder(tamanhos))
    assert len(r.attempts) >= 1
    assert all(a.size_bytes == tamanhos[a.index] for a in r.attempts)
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_search.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'gifc.search'`

- [ ] **Step 3: Implementar `gifc/search.py`**

```python
"""Busca binária pelo melhor degrau da escada que cabe no alvo.

Não faz I/O: o encoder é injetado.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from gifc.ladder import Params

EncodeFn = Callable[[int, Params], int]
ProgressFn = Callable[["Attempt", int], None]
CancelFn = Callable[[], bool]


class Cancelled(Exception):
    """A busca foi cancelada pelo usuário."""


@dataclass(frozen=True)
class Attempt:
    index: int
    params: Params
    size_bytes: int


@dataclass
class SearchResult:
    index: int
    params: Params
    size_bytes: int
    hit_target: bool
    attempts: list[Attempt] = field(default_factory=list)


def find_best(
    ladder: Sequence[Params],
    target_bytes: int,
    encode_fn: EncodeFn,
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> SearchResult:
    """Acha o degrau de MAIOR qualidade cujo resultado cabe no alvo.

    Como o tamanho decresce ao longo da escada, isto é uma busca de lower
    bound: ~log2(n) codificações em vez de n.

    Se nenhum degrau couber, devolve o de menor tamanho encontrado, com
    `hit_target=False`.
    """
    if not ladder:
        raise ValueError("A escada de qualidade está vazia.")

    total = len(ladder)
    cache: dict[int, int] = {}
    attempts: list[Attempt] = []

    melhor_valido: Attempt | None = None
    menor_absoluto: Attempt | None = None

    lo, hi = 0, total - 1
    while lo <= hi:
        if should_cancel is not None and should_cancel():
            raise Cancelled()

        mid = (lo + hi) // 2
        params = ladder[mid]

        if mid in cache:
            tamanho = cache[mid]
        else:
            tamanho = encode_fn(mid, params)
            cache[mid] = tamanho
            tentativa = Attempt(index=mid, params=params, size_bytes=tamanho)
            attempts.append(tentativa)
            if on_progress is not None:
                on_progress(tentativa, total)

        atual = Attempt(index=mid, params=params, size_bytes=tamanho)

        if menor_absoluto is None or tamanho < menor_absoluto.size_bytes:
            menor_absoluto = atual

        if tamanho <= target_bytes:
            # Candidato. Entre os válidos preferimos o de menor índice,
            # que é o de maior qualidade.
            if melhor_valido is None or mid < melhor_valido.index:
                melhor_valido = atual
            hi = mid - 1
        else:
            lo = mid + 1

    escolhido = melhor_valido or menor_absoluto
    assert escolhido is not None  # a escada não é vazia, logo houve tentativa

    return SearchResult(
        index=escolhido.index,
        params=escolhido.params,
        size_bytes=escolhido.size_bytes,
        hit_target=melhor_valido is not None,
        attempts=attempts,
    )
```

- [ ] **Step 4: Rodar e confirmar que passam**

Run: `python -m pytest tests/test_search.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add gifc/search.py tests/test_search.py
git commit -m "Busca binária pelo melhor degrau que cabe no alvo"
```

---

### Task 6: Codificação ffmpeg + gifsicle

**Files:**
- Create: `gifc/encode.py`
- Test: `tests/test_encode.py`

**Interfaces:**
- Consumes: `Tools` (Task 1), `GifInfo` (Task 3), `Params`, `target_width` (Task 4)
- Produces: `EncodeError`, `build_filter(info, params) -> str`, `build_ffmpeg_cmd(tools, src, dst, info, params) -> list[str]`, `build_gifsicle_cmd(tools, src, dst, params) -> list[str]`, `encode(src, dst, params, info, tools) -> int`

Os construtores de comando são separados da execução justamente para poderem ser testados sem rodar nada.

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_encode.py`:
```python
from pathlib import Path

import pytest

from gifc.encode import (
    EncodeError,
    build_ffmpeg_cmd,
    build_filter,
    build_gifsicle_cmd,
    encode,
)
from gifc.ladder import Params
from gifc.probe import GifInfo
from gifc.tools import Tools

FERRAMENTAS = Tools(ffmpeg="ffmpeg", ffprobe="ffprobe", gifsicle="gifsicle")


def info(fps: float = 30.0, width: int = 800) -> GifInfo:
    return GifInfo(
        path=Path("x.gif"), width=width, height=600, fps=fps,
        frame_count=100, duration=100 / fps, size_bytes=1_000_000,
    )


def test_filtro_usa_bayer_e_nao_floyd():
    """Bayer comprime muito melhor: o padrão é estático entre frames."""
    f = build_filter(info(), Params(256, 0, 100, None))
    assert "dither=bayer" in f
    assert "floyd" not in f


def test_filtro_usa_stats_mode_diff():
    f = build_filter(info(), Params(256, 0, 100, None))
    assert "stats_mode=diff" in f


def test_filtro_respeita_o_numero_de_cores():
    f = build_filter(info(), Params(64, 0, 100, None))
    assert "max_colors=64" in f


def test_filtro_omite_escala_quando_100():
    f = build_filter(info(), Params(256, 0, 100, None))
    assert "scale=" not in f


def test_filtro_inclui_escala_quando_reduzida():
    f = build_filter(info(width=800), Params(256, 0, 70, None))
    assert "scale=560:-1" in f
    assert "lanczos" in f


def test_filtro_omite_fps_quando_none():
    f = build_filter(info(), Params(256, 0, 100, None))
    assert "fps=" not in f


def test_filtro_inclui_fps_quando_reduzido():
    f = build_filter(info(fps=30.0), Params(256, 0, 100, 15.0))
    assert "fps=15" in f


def test_ffmpeg_cmd_tem_overwrite_e_saida():
    cmd = build_ffmpeg_cmd(
        FERRAMENTAS, Path("a.gif"), Path("b.gif"), info(),
        Params(256, 0, 100, None),
    )
    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd
    assert cmd[-1] == "b.gif"


def test_gifsicle_cmd_usa_O3():
    cmd = build_gifsicle_cmd(
        FERRAMENTAS, Path("a.gif"), Path("b.gif"), Params(256, 0, 100, None)
    )
    assert "-O3" in cmd


def test_gifsicle_cmd_omite_lossy_quando_zero():
    cmd = build_gifsicle_cmd(
        FERRAMENTAS, Path("a.gif"), Path("b.gif"), Params(256, 0, 100, None)
    )
    assert not any(a.startswith("--lossy") for a in cmd)


def test_gifsicle_cmd_inclui_lossy_quando_positivo():
    cmd = build_gifsicle_cmd(
        FERRAMENTAS, Path("a.gif"), Path("b.gif"), Params(256, 80, 100, None)
    )
    assert "--lossy=80" in cmd


@pytest.mark.integration
def test_encode_produz_um_gif_valido(tools, gif_pequeno, tmp_path):
    from gifc.probe import probe

    origem = probe(gif_pequeno, tools)
    destino = tmp_path / "saida.gif"
    tamanho = encode(
        gif_pequeno, destino, Params(64, 80, 100, None), origem, tools
    )
    assert destino.is_file()
    assert tamanho == destino.stat().st_size
    assert tamanho > 0
    assert destino.read_bytes()[:3] == b"GIF"


@pytest.mark.integration
def test_encode_nao_altera_o_arquivo_de_origem(tools, gif_pequeno, tmp_path):
    from gifc.probe import probe

    antes = gif_pequeno.read_bytes()
    origem = probe(gif_pequeno, tools)
    encode(
        gif_pequeno, tmp_path / "saida.gif",
        Params(32, 100, 70, None), origem, tools,
    )
    assert gif_pequeno.read_bytes() == antes


@pytest.mark.integration
def test_encode_com_origem_invalida_levanta_erro(tools, tmp_path):
    ruim = tmp_path / "ruim.gif"
    ruim.write_bytes(b"nao e um gif")
    with pytest.raises(EncodeError):
        encode(
            ruim, tmp_path / "saida.gif", Params(64, 0, 100, None),
            info(), tools,
        )
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_encode.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'gifc.encode'`

- [ ] **Step 3: Implementar `gifc/encode.py`**

```python
"""Execução dos dois estágios de compressão: ffmpeg e gifsicle."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from gifc.ladder import Params, target_width
from gifc.probe import GifInfo
from gifc.tools import Tools

TIMEOUT_SEGUNDOS = 600


class EncodeError(Exception):
    """Um dos estágios de codificação falhou."""


def build_filter(info: GifInfo, params: Params) -> str:
    """Monta a cadeia de filtros do ffmpeg.

    Duas escolhas carregam a maior parte do ganho de compressão:

    - `stats_mode=diff` otimiza a paleta para o que muda entre frames.
    - `dither=bayer` em vez de floyd-steinberg. Floyd gera ruído diferente
      a cada frame, o que destrói a compressão inter-frame do GIF e infla o
      arquivo. Bayer gera um padrão estático, que comprime muito melhor.
    """
    pre: list[str] = []
    if params.fps is not None and params.fps < info.fps:
        pre.append(f"fps={params.fps:g}")
    if params.scale_pct != 100:
        largura = target_width(info.width, params.scale_pct)
        pre.append(f"scale={largura}:-1:flags=lanczos")

    prefixo = ",".join(pre)
    if prefixo:
        prefixo += ","

    return (
        f"{prefixo}split[a][b];"
        f"[a]palettegen=max_colors={params.colors}:stats_mode=diff[p];"
        f"[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )


def build_ffmpeg_cmd(
    tools: Tools, src: Path, dst: Path, info: GifInfo, params: Params
) -> list[str]:
    return [
        tools.ffmpeg, "-y", "-v", "error",
        "-i", str(src),
        "-filter_complex", build_filter(info, params),
        "-loop", "0",
        str(dst),
    ]


def build_gifsicle_cmd(
    tools: Tools, src: Path, dst: Path, params: Params
) -> list[str]:
    if tools.gifsicle is None:
        raise EncodeError("gifsicle não está disponível.")
    cmd = [tools.gifsicle, "-O3"]
    if params.lossy > 0:
        cmd.append(f"--lossy={params.lossy}")
    cmd += [str(src), "-o", str(dst)]
    return cmd


def _run(cmd: list[str], etapa: str) -> None:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_SEGUNDOS
        )
    except subprocess.TimeoutExpired as exc:
        raise EncodeError(f"{etapa} demorou demais e foi interrompido.") from exc
    except OSError as exc:
        raise EncodeError(f"Falha ao executar {etapa}: {exc}") from exc

    if proc.returncode != 0:
        raise EncodeError(f"{etapa} falhou: {proc.stderr.strip()[:500]}")


def encode(
    src: Path, dst: Path, params: Params, info: GifInfo, tools: Tools
) -> int:
    """Codifica `src` em `dst` com `params`. Devolve o tamanho de `dst`.

    Sempre parte do arquivo original — nunca do resultado de uma tentativa
    anterior — para não acumular perda.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, intermediario_str = tempfile.mkstemp(suffix=".gif", dir=str(dst.parent))
    os.close(fd)
    intermediario = Path(intermediario_str)

    try:
        _run(build_ffmpeg_cmd(tools, src, intermediario, info, params), "ffmpeg")

        if tools.gifsicle is None:
            # Modo degradado: sem o estágio lossy, o resultado do ffmpeg
            # já é o final.
            intermediario.replace(dst)
        else:
            _run(build_gifsicle_cmd(tools, intermediario, dst, params), "gifsicle")

        if not dst.is_file():
            raise EncodeError("A codificação não produziu arquivo de saída.")
        return dst.stat().st_size
    finally:
        intermediario.unlink(missing_ok=True)
```

- [ ] **Step 4: Rodar e confirmar que passam**

Run: `python -m pytest tests/test_encode.py -v`
Expected: 14 passed

- [ ] **Step 5: Rodar a suíte inteira para garantir que nada quebrou**

Run: `python -m pytest -v`
Expected: todos passam

- [ ] **Step 6: Commit**

```bash
git add gifc/encode.py tests/test_encode.py
git commit -m "Codificação em dois estágios com ffmpeg e gifsicle"
```

---

### Task 7: Orquestração da compressão de um arquivo

**Files:**
- Create: `gifc/compress.py`
- Test: `tests/test_compress.py`

**Interfaces:**
- Consumes: tudo das Tasks 1 e 3-6
- Produces: `CompressResult(src, dst, original_bytes, final_bytes, params, hit_target, already_optimized, attempts)` com propriedade `reduction_pct: float`, `CompressError`, `FALHOU: int`, `unique_output_path(src: Path) -> Path`, `compress_file(src, target_bytes, toggles, tools, on_progress=None, should_cancel=None) -> CompressResult`

`on_progress` recebe `(Attempt, total: int)` — o mesmo tipo emitido por `search.find_best`, repassado direto.

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_compress.py`:
```python
from pathlib import Path

import pytest

from gifc.compress import CompressResult, compress_file, unique_output_path
from gifc.encode import EncodeError
from gifc.ladder import Toggles
from gifc.probe import GifInfo
from gifc.tools import Tools

TODOS = Toggles(True, True, True, True)
FAKE_TOOLS = Tools(ffmpeg="ffmpeg", ffprobe="ffprobe", gifsicle="gifsicle")


def _preparar(tmp_path, monkeypatch, gifsicle="gifsicle"):
    """Isola compress_file do disco: probe e encode viram funções falsas."""
    from gifc import compress as mod

    src = tmp_path / "a.gif"
    src.write_bytes(b"GIF89a" + b"x" * 5_000)
    info = GifInfo(
        path=src, width=100, height=100, fps=30.0,
        frame_count=30, duration=1.0, size_bytes=src.stat().st_size,
    )
    monkeypatch.setattr(mod, "probe", lambda p, t: info)
    return mod, src, info, Tools("ffmpeg", "ffprobe", gifsicle)


def test_nome_de_saida_padrao(tmp_path):
    src = tmp_path / "gato.gif"
    src.write_bytes(b"x")
    assert unique_output_path(src).name == "gato_comprimido.gif"


def test_nome_de_saida_nao_sobrescreve(tmp_path):
    src = tmp_path / "gato.gif"
    src.write_bytes(b"x")
    (tmp_path / "gato_comprimido.gif").write_bytes(b"y")
    assert unique_output_path(src).name == "gato_comprimido_2.gif"


def test_nome_de_saida_incrementa_ate_achar_livre(tmp_path):
    src = tmp_path / "gato.gif"
    src.write_bytes(b"x")
    (tmp_path / "gato_comprimido.gif").write_bytes(b"y")
    (tmp_path / "gato_comprimido_2.gif").write_bytes(b"y")
    assert unique_output_path(src).name == "gato_comprimido_3.gif"


def test_degrau_que_falha_e_pulado(tmp_path, monkeypatch):
    """Um degrau que quebra não derruba o arquivo: a busca segue adiante."""
    mod, src, info, tools = _preparar(tmp_path, monkeypatch)
    tentados: list[int] = []

    def encode_falho(s, d, params, i, t):
        idx = int(d.stem.split("_")[1])
        tentados.append(idx)
        if idx % 2 == 0:
            raise EncodeError("degrau ruim")
        d.write_bytes(b"GIF89a" + b"y" * (2_000 - idx * 50))
        return d.stat().st_size

    monkeypatch.setattr(mod, "encode", encode_falho)

    r = mod.compress_file(src, 1_800, TODOS, tools)

    assert any(i % 2 == 0 for i in tentados), "nenhum degrau falho foi exercido"
    assert r.dst.is_file()
    assert r.final_bytes <= 1_800


def test_todos_os_degraus_falhando_levanta_erro(tmp_path, monkeypatch):
    mod, src, info, tools = _preparar(tmp_path, monkeypatch)

    def sempre_falha(s, d, params, i, t):
        raise EncodeError("quebrado")

    monkeypatch.setattr(mod, "encode", sempre_falha)

    with pytest.raises(mod.CompressError):
        mod.compress_file(src, 1_000, TODOS, tools)


def test_sem_gifsicle_descarta_a_coluna_lossy(tmp_path, monkeypatch):
    """Modo degradado: manter lossy só geraria degraus idênticos."""
    mod, src, info, tools = _preparar(tmp_path, monkeypatch, gifsicle=None)
    vistos = []

    def encode_ok(s, d, params, i, t):
        vistos.append(params)
        d.write_bytes(b"GIF89a" + b"y" * 100)
        return d.stat().st_size

    monkeypatch.setattr(mod, "encode", encode_ok)
    mod.compress_file(src, 1_000_000, TODOS, tools)

    assert vistos
    assert all(p.lossy == 0 for p in vistos)


@pytest.mark.integration
def test_comprime_e_atinge_alvo_generoso(tools, gif_pequeno, tmp_path):
    copia = tmp_path / "entrada.gif"
    copia.write_bytes(gif_pequeno.read_bytes())

    r = compress_file(copia, target_bytes=10_000_000, toggles=TODOS, tools=tools)

    assert isinstance(r, CompressResult)
    assert r.hit_target is True
    assert r.dst.is_file()
    assert r.final_bytes > 0


@pytest.mark.integration
def test_alvo_generoso_usa_o_degrau_de_maior_qualidade(tools, gif_pequeno, tmp_path):
    copia = tmp_path / "entrada.gif"
    copia.write_bytes(gif_pequeno.read_bytes())

    r = compress_file(copia, target_bytes=10_000_000, toggles=TODOS, tools=tools)

    # Alvo folgado: não deve sacrificar resolução nem framerate.
    assert r.params is not None
    assert r.params.scale_pct == 100
    assert r.params.fps is None


@pytest.mark.integration
def test_nunca_modifica_o_original(tools, gif_pequeno, tmp_path):
    copia = tmp_path / "entrada.gif"
    copia.write_bytes(gif_pequeno.read_bytes())
    antes = copia.read_bytes()

    compress_file(copia, target_bytes=1_000, toggles=TODOS, tools=tools)

    assert copia.read_bytes() == antes


@pytest.mark.integration
def test_alvo_impossivel_entrega_o_menor_e_avisa(tools, gif_pequeno, tmp_path):
    copia = tmp_path / "entrada.gif"
    copia.write_bytes(gif_pequeno.read_bytes())

    r = compress_file(copia, target_bytes=1, toggles=TODOS, tools=tools)

    assert r.hit_target is False
    assert r.dst.is_file()
    assert r.final_bytes > 1


@pytest.mark.integration
def test_nao_deixa_temporarios_para_tras(tools, gif_pequeno, tmp_path):
    copia = tmp_path / "entrada.gif"
    copia.write_bytes(gif_pequeno.read_bytes())

    r = compress_file(copia, target_bytes=10_000_000, toggles=TODOS, tools=tools)

    restantes = {p.name for p in tmp_path.iterdir()}
    assert restantes == {copia.name, r.dst.name}
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `python -m pytest tests/test_compress.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'gifc.compress'`

- [ ] **Step 3: Implementar `gifc/compress.py`**

```python
"""Orquestração: de um GIF de entrada ao arquivo comprimido final."""

from __future__ import annotations

import dataclasses
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from gifc.encode import EncodeError, encode
from gifc.ladder import Params, Toggles, build
from gifc.probe import probe
from gifc.search import Attempt, CancelFn, ProgressFn, find_best
from gifc.tools import Tools

SUFIXO = "_comprimido"

# Sentinela para um degrau que falhou: a busca o trata como "grande demais"
# e segue para degraus mais agressivos, em vez de abortar o arquivo inteiro.
FALHOU = sys.maxsize


class CompressError(Exception):
    """Nenhum degrau da escada produziu um resultado utilizável."""


@dataclass
class CompressResult:
    src: Path
    dst: Path
    original_bytes: int
    final_bytes: int
    params: Params | None
    hit_target: bool
    already_optimized: bool
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def reduction_pct(self) -> float:
        if self.original_bytes == 0:
            return 0.0
        return 100.0 * (1 - self.final_bytes / self.original_bytes)


def unique_output_path(src: Path) -> Path:
    """`gato.gif` -> `gato_comprimido.gif`, sem nunca sobrescrever."""
    base = src.with_name(f"{src.stem}{SUFIXO}{src.suffix}")
    if not base.exists():
        return base
    n = 2
    while True:
        candidato = src.with_name(f"{src.stem}{SUFIXO}_{n}{src.suffix}")
        if not candidato.exists():
            return candidato
        n += 1


def compress_file(
    src: Path,
    target_bytes: int,
    toggles: Toggles,
    tools: Tools,
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> CompressResult:
    """Comprime `src` buscando o melhor resultado que cabe em `target_bytes`."""
    src = Path(src)
    info = probe(src, tools)

    # Sem gifsicle não há estágio lossy: manter a coluna ligada só geraria
    # degraus que produzem exatamente o mesmo arquivo, desperdiçando encodes.
    if tools.gifsicle is None:
        toggles = dataclasses.replace(toggles, lossy=False)

    escada = build(info, toggles)
    dst = unique_output_path(src)

    tmpdir = Path(tempfile.mkdtemp(prefix="gifc_"))
    try:
        def encode_fn(index: int, params: Params) -> int:
            try:
                return encode(
                    src, tmpdir / f"try_{index}.gif", params, info, tools
                )
            except EncodeError:
                # Um degrau ruim não derruba o arquivo: é pulado.
                return FALHOU

        resultado = find_best(
            escada, target_bytes, encode_fn,
            on_progress=on_progress, should_cancel=should_cancel,
        )

        if resultado.size_bytes == FALHOU:
            raise CompressError(
                f"Nenhuma configuração conseguiu comprimir {src.name}. "
                f"O arquivo pode estar corrompido."
            )

        vencedor = tmpdir / f"try_{resultado.index}.gif"

        # GIFs já muito otimizados podem crescer ao serem reescritos.
        if resultado.size_bytes >= info.size_bytes:
            shutil.copy2(src, dst)
            return CompressResult(
                src=src, dst=dst,
                original_bytes=info.size_bytes,
                final_bytes=dst.stat().st_size,
                params=None,
                hit_target=info.size_bytes <= target_bytes,
                already_optimized=True,
                attempts=resultado.attempts,
            )

        shutil.move(str(vencedor), str(dst))
        return CompressResult(
            src=src, dst=dst,
            original_bytes=info.size_bytes,
            final_bytes=dst.stat().st_size,
            params=resultado.params,
            hit_target=resultado.hit_target,
            already_optimized=False,
            attempts=resultado.attempts,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
```

- [ ] **Step 4: Rodar e confirmar que passam**

Run: `python -m pytest tests/test_compress.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add gifc/compress.py tests/test_compress.py
git commit -m "Orquestração da compressão de um arquivo"
```

---

### Task 8: GUI Tkinter e entrypoint

**Files:**
- Create: `gifc/app.py`, `main.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `compress_file`, `CompressResult` (Task 7), `Toggles` (Task 4), `find_tools`, `download_gifsicle`, `MissingToolError` (Tasks 1-2)
- Produces: `App` (classe Tk), `main() -> int`

A GUI não é testada automaticamente — é validada manualmente no Step 4. O trabalho pesado roda numa thread; a UI só é tocada pela thread principal, que drena uma `queue.Queue` via `after()`.

- [ ] **Step 1: Instalar a dependência de drag-and-drop**

```bash
python -m pip install -r requirements-dev.txt
```

Verifique: `python -c "import tkinterdnd2; print(tkinterdnd2.__file__)"`

- [ ] **Step 2: Implementar `gifc/app.py`**

```python
"""GUI Tkinter: arraste GIFs, defina um alvo, receba os comprimidos."""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import BooleanVar, StringVar, messagebox, ttk

from tkinterdnd2 import DND_FILES, TkinterDnD

from gifc.compress import CompressResult, compress_file
from gifc.ladder import Toggles
from gifc.search import Attempt, Cancelled
from gifc.tools import (
    BIN_DIR,
    GIFSICLE_URL,
    DownloadError,
    MissingToolError,
    Tools,
    download_gifsicle,
    find_tools,
)

MB = 1024 * 1024
PRESETS = [("Discord 10", 10.0), ("WhatsApp 16", 16.0), ("5", 5.0), ("2", 2.0)]


def _mb(n: int) -> str:
    return f"{n / MB:.2f} MB"


class App(TkinterDnD.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Compressor de GIF")
        self.geometry("580x660")
        self.minsize(480, 560)

        self.tools: Tools | None = None
        self.fila: queue.Queue = queue.Queue()
        self.cancelar = threading.Event()
        self.trabalhando = False
        self.ultima_pasta: Path | None = None

        self.alvo_mb = StringVar(value="10")
        self.tg_palette = BooleanVar(value=True)
        self.tg_lossy = BooleanVar(value=True)
        self.tg_scale = BooleanVar(value=True)
        self.tg_fps = BooleanVar(value=True)

        self._montar()
        self._checar_ferramentas()
        self.after(100, self._drenar_fila)

    # ---------- construção da interface ----------

    def _montar(self) -> None:
        raiz = ttk.Frame(self, padding=12)
        raiz.pack(fill="both", expand=True)

        self.drop = ttk.Label(
            raiz,
            text="Arraste GIFs aqui",
            anchor="center",
            relief="ridge",
            padding=30,
        )
        self.drop.pack(fill="x")
        self.drop.drop_target_register(DND_FILES)
        self.drop.dnd_bind("<<Drop>>", self._ao_soltar)

        ttk.Button(raiz, text="Escolher arquivos...", command=self._escolher).pack(
            fill="x", pady=(6, 12)
        )

        alvo = ttk.LabelFrame(raiz, text="Tamanho-alvo", padding=8)
        alvo.pack(fill="x")
        linha = ttk.Frame(alvo)
        linha.pack(fill="x")
        ttk.Entry(linha, textvariable=self.alvo_mb, width=8).pack(side="left")
        ttk.Label(linha, text="MB").pack(side="left", padx=(4, 12))
        for rotulo, valor in PRESETS:
            ttk.Button(
                linha, text=rotulo, width=11,
                command=lambda v=valor: self.alvo_mb.set(str(v)),
            ).pack(side="left", padx=2)

        alav = ttk.LabelFrame(raiz, text="Alavancas permitidas", padding=8)
        alav.pack(fill="x", pady=12)
        for texto, var in [
            ("Reduzir paleta de cores", self.tg_palette),
            ("Compressão lossy da paleta", self.tg_lossy),
            ("Reduzir resolução", self.tg_scale),
            ("Reduzir framerate", self.tg_fps),
        ]:
            ttk.Checkbutton(alav, text=texto, variable=var).pack(anchor="w")

        self.barra = ttk.Progressbar(raiz, mode="determinate")
        self.barra.pack(fill="x")

        self.status = ttk.Label(raiz, text="Pronto.", anchor="w")
        self.status.pack(fill="x", pady=(6, 4))

        self.log = ttk.Treeview(
            raiz, columns=("msg",), show="tree", height=12, selectmode="none"
        )
        self.log.pack(fill="both", expand=True)

        rodape = ttk.Frame(raiz)
        rodape.pack(fill="x", pady=(8, 0))
        self.btn_cancelar = ttk.Button(
            rodape, text="Cancelar", command=self._cancelar, state="disabled"
        )
        self.btn_cancelar.pack(side="left")
        self.btn_pasta = ttk.Button(
            rodape, text="Abrir pasta", command=self._abrir_pasta, state="disabled"
        )
        self.btn_pasta.pack(side="right")

    # ---------- ferramentas ----------

    def _checar_ferramentas(self) -> None:
        try:
            self.tools = find_tools(BIN_DIR)
        except MissingToolError as exc:
            messagebox.showerror("Ferramenta ausente", str(exc))
            self._log(f"ERRO: {exc}")
            return

        if self.tools.gifsicle is None:
            baixar = messagebox.askyesno(
                "Baixar gifsicle?",
                "O gifsicle faz a compressão lossy, a alavanca mais eficiente.\n\n"
                f"Baixar (~225 KB) de:\n{GIFSICLE_URL}\n\n"
                "Ele fica na pasta bin/ do app. Nada é instalado no sistema.\n\n"
                "Sem ele o app funciona, mas comprime bem menos.",
            )
            if baixar:
                self._baixar_gifsicle()
            else:
                self._log("Modo degradado: sem compressão lossy.")

    def _baixar_gifsicle(self) -> None:
        self.status.config(text="Baixando gifsicle...")
        self.update_idletasks()
        try:
            download_gifsicle(BIN_DIR)
            self.tools = find_tools(BIN_DIR)
            self._log("gifsicle instalado em bin/.")
            self.status.config(text="Pronto.")
        except DownloadError as exc:
            messagebox.showwarning("Download falhou", str(exc))
            self._log(f"Modo degradado: {exc}")
            self.status.config(text="Pronto (modo degradado).")

    # ---------- entrada de arquivos ----------

    def _ao_soltar(self, evento) -> None:
        caminhos = [Path(p) for p in self.splitlist(evento.data)]
        self._processar(caminhos)

    def _escolher(self) -> None:
        from tkinter import filedialog

        nomes = filedialog.askopenfilenames(
            title="Escolha os GIFs", filetypes=[("GIF", "*.gif")]
        )
        self._processar([Path(n) for n in nomes])

    def _processar(self, caminhos: list[Path]) -> None:
        if self.trabalhando:
            messagebox.showinfo("Aguarde", "Já existe uma compressão em andamento.")
            return
        if self.tools is None:
            messagebox.showerror("Sem ferramentas", "ffmpeg não foi encontrado.")
            return

        gifs = [p for p in caminhos if p.suffix.lower() == ".gif" and p.is_file()]
        ignorados = len(caminhos) - len(gifs)
        if ignorados:
            self._log(f"{ignorados} arquivo(s) ignorado(s): não são GIF.")
        if not gifs:
            messagebox.showwarning("Nada a fazer", "Nenhum GIF válido.")
            return

        try:
            alvo = int(float(self.alvo_mb.get().replace(",", ".")) * MB)
            if alvo <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Alvo inválido", "Informe um número de MB maior que zero.")
            return

        toggles = Toggles(
            palette=self.tg_palette.get(),
            lossy=self.tg_lossy.get(),
            scale=self.tg_scale.get(),
            fps=self.tg_fps.get(),
        )

        self.trabalhando = True
        self.cancelar.clear()
        self.btn_cancelar.config(state="normal")
        self.log.delete(*self.log.get_children())

        threading.Thread(
            target=self._trabalhar, args=(gifs, alvo, toggles), daemon=True
        ).start()

    # ---------- thread de trabalho ----------

    def _trabalhar(self, gifs: list[Path], alvo: int, toggles: Toggles) -> None:
        assert self.tools is not None
        for i, gif in enumerate(gifs, 1):
            if self.cancelar.is_set():
                break
            self.fila.put(("status", f"[{i}/{len(gifs)}] {gif.name}"))
            try:
                r = compress_file(
                    gif, alvo, toggles, self.tools,
                    on_progress=lambda a, t: self.fila.put(("tentativa", (a, t))),
                    should_cancel=self.cancelar.is_set,
                )
                self.fila.put(("resultado", r))
            except Cancelled:
                self.fila.put(("log", "Cancelado."))
                break
            except Exception as exc:  # falha em um arquivo não derruba o lote
                self.fila.put(("log", f"ERRO em {gif.name}: {exc}"))
        self.fila.put(("fim", None))

    # ---------- drenagem da fila na thread da UI ----------

    def _drenar_fila(self) -> None:
        try:
            while True:
                tipo, carga = self.fila.get_nowait()
                if tipo == "status":
                    self.status.config(text=carga)
                elif tipo == "log":
                    self._log(carga)
                elif tipo == "tentativa":
                    self._log_tentativa(*carga)
                elif tipo == "resultado":
                    self._log_resultado(carga)
                elif tipo == "fim":
                    self._terminar()
        except queue.Empty:
            pass
        self.after(100, self._drenar_fila)

    def _log(self, texto: str) -> None:
        item = self.log.insert("", "end", text=texto)
        self.log.see(item)

    def _log_tentativa(self, a: Attempt, total: int) -> None:
        p = a.params
        fps = "orig" if p.fps is None else f"{p.fps:g}fps"
        self._log(
            f"  degrau {a.index}/{total - 1} · {p.colors} cores · "
            f"lossy {p.lossy} · {p.scale_pct}% · {fps} → {_mb(a.size_bytes)}"
        )
        self.barra.config(value=min(100, self.barra["value"] + 20))

    def _log_resultado(self, r: CompressResult) -> None:
        self.ultima_pasta = r.dst.parent
        if r.already_optimized:
            self._log(f"{r.src.name}: já estava otimizado, cópia mantida.")
        else:
            self._log(
                f"{r.src.name}: {_mb(r.original_bytes)} → {_mb(r.final_bytes)} "
                f"(−{r.reduction_pct:.0f}%) → {r.dst.name}"
            )
        if not r.hit_target:
            self._log(
                f"  ATENÇÃO: não foi possível chegar ao alvo. "
                f"Menor tamanho possível: {_mb(r.final_bytes)}"
            )

    def _terminar(self) -> None:
        self.trabalhando = False
        self.barra.config(value=0)
        self.btn_cancelar.config(state="disabled")
        self.status.config(text="Pronto.")
        if self.ultima_pasta is not None:
            self.btn_pasta.config(state="normal")

    def _cancelar(self) -> None:
        self.cancelar.set()
        self.status.config(text="Cancelando...")

    def _abrir_pasta(self) -> None:
        if self.ultima_pasta is None:
            return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(self.ultima_pasta)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(self.ultima_pasta)])
        else:
            subprocess.Popen(["xdg-open", str(self.ultima_pasta)])
```

- [ ] **Step 3: Implementar `main.py`**

```python
"""Entrypoint do compressor de GIF."""

from __future__ import annotations

import sys


def main() -> int:
    from gifc.app import App

    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Validar manualmente**

```bash
python main.py
```

Confira, um a um:

1. A janela abre e oferece baixar o gifsicle se `bin/` estiver vazio.
2. Arrastar um GIF grande do Explorer inicia a compressão.
3. O log mostra uma linha por degrau testado, com tamanho.
4. A janela continua respondendo (arrastável, redimensionável) durante o trabalho.
5. O resultado aparece como `antes → depois (−X%)`.
6. O arquivo `<nome>_comprimido.gif` existe e abre corretamente num visualizador.
7. O GIF original continua intacto, com o mesmo tamanho de antes.
8. Desligar "Reduzir resolução" e "Reduzir framerate" e repetir: o resultado mantém as dimensões e a fluidez originais.
9. "Abrir pasta" abre o Explorer no lugar certo.
10. Arrastar um arquivo `.png` é recusado com aviso, sem travar o app.

- [ ] **Step 5: Atualizar o README com instruções de uso**

Substituir a seção `## Estado` do `README.md` por:

```markdown
## Uso

```bash
python -m pip install -r requirements.txt
python main.py
```

Arraste GIFs para a janela, defina o tamanho-alvo em MB e aguarde. Os
arquivos comprimidos aparecem ao lado dos originais, com o sufixo
`_comprimido`. Os originais nunca são modificados.

No primeiro uso o app oferece baixar o `gifsicle` (~225 KB) para `bin/`.
Nada é instalado no sistema.

## Desenvolvimento

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -v
```

Os testes marcados como `integration` executam ffmpeg e gifsicle de
verdade; rode só os unitários com `-m "not integration"`.
```

- [ ] **Step 6: Rodar a suíte completa**

Run: `python -m pytest -v`
Expected: todos passam

- [ ] **Step 7: Commit e push**

```bash
git add gifc/app.py main.py README.md
git commit -m "GUI Tkinter drag-and-drop e entrypoint"
git push
```

---

## Verificação final

- [ ] `python -m pytest -v` — suíte inteira verde
- [ ] `python -m pytest -m "not integration" -v` — unitários passam sem ffmpeg
- [ ] Um GIF real de ~25 MB com alvo de 10 MB produz saída abaixo de 10 MB, visualmente próxima do original
- [ ] Um GIF que já cabe no alvo sai no degrau 0, sem degradação desnecessária
- [ ] Nenhum arquivo original foi modificado em nenhum dos testes manuais
- [ ] `git status` limpo e `git push` concluído
