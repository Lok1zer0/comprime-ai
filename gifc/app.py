"""GUI Tkinter: arraste imagens, defina um alvo, receba as comprimidas.

Aceita GIF, PNG, JPEG e WebP; PNG e JPEG podem ser convertidos para WebP.

Disciplina de threading: a compressão roda numa thread separada
(`_trabalhar`). Essa thread NUNCA toca em um widget — ela só faz
`self.fila.put(...)` (queue.Queue é thread-safe) e lê `self.cancelar.is_set`
(threading.Event, também thread-safe). Toda leitura da fila e toda
atualização de widget acontece em `_drenar_fila`, agendada via `after()` na
thread principal.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from dataclasses import fields
from pathlib import Path
from tkinter import BooleanVar, StringVar, messagebox, ttk

from tkinterdnd2 import DND_FILES, TkinterDnD

from gifc import formatos
from gifc.compress import CompressResult, compress_file
from gifc.ladder import ALAVANCAS, Toggles
from gifc.probe import Formato
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

# Extensão aceita -> formato. É a única fonte da lista de extensões: o filtro,
# o seletor de arquivos e as regras de alavanca saem todos daqui. O formato
# real do arquivo é decidido pelo conteúdo, em `probe`; a extensão serve só
# para filtrar a entrada e decidir quais alavancas ficam habilitadas.
_FORMATO_DA_EXTENSAO: dict[str, Formato] = {
    ".gif": Formato.GIF,
    ".png": Formato.PNG,
    ".jpg": Formato.JPEG,
    ".jpeg": Formato.JPEG,
    ".webp": Formato.WEBP,
}
EXTENSOES_ACEITAS = frozenset(_FORMATO_DA_EXTENSAO)

_ROTULO_FORMATO: dict[Formato, str] = {
    Formato.GIF: "GIF",
    Formato.PNG: "PNG",
    Formato.JPEG: "JPEG",
    Formato.WEBP: "WebP",
}

# Formatos que a caixa "Converter para WebP" afeta. O GIF nunca converte.
_CONVERSIVEIS = frozenset({Formato.PNG, Formato.JPEG})

PRESETS = [("Discord 10", 10.0), ("WhatsApp 16", 16.0), ("5", 5.0), ("2", 2.0)]

# A busca é binária sobre a escada do formato; a maior é a do GIF, com 20
# degraus (gifc/formatos/gif.py), então o pior caso é floor(log2(20)) + 1 = 5
# tentativas por arquivo. Usado só para
# estimar o progresso do lote de forma honesta — não afeta a busca em si.
TENTATIVAS_ESPERADAS_POR_ARQUIVO = 5

# Quanto tempo _ao_fechar espera o worker terminar sozinho (e rodar o seu
# `finally` de limpeza do tmpdir) antes de destruir a janela de qualquer
# jeito. É um best-effort limitado, não uma garantia: o worker pode estar
# preso dentro de uma chamada longa de ffmpeg/gifsicle, e a janela não pode
# ficar parecendo travada por causa disso.
FECHAR_JOIN_TIMEOUT_SEGUNDOS = 2.0


def _mb(n: int) -> str:
    """Formata um tamanho em bytes como string de MB com duas casas."""
    return f"{n / MB:.2f} MB"


def _parse_alvo_mb(texto: str) -> int:
    """Converte o texto do campo de alvo (em MB) para um inteiro de bytes.

    Aceita vírgula ou ponto como separador decimal e ignora espaços nas
    pontas. Levanta `ValueError` para texto vazio, não numérico, zero ou
    negativo.
    """
    texto = texto.strip().replace(",", ".")
    if not texto:
        raise ValueError("Informe um número de MB maior que zero.")
    valor = float(texto)
    if valor <= 0:
        raise ValueError("O alvo deve ser maior que zero.")
    return int(valor * MB)


def _filtrar_caminhos(caminhos: Iterable[Path]) -> tuple[list[Path], int]:
    """Separa os caminhos que o app sabe comprimir dos que ele ignora.

    Devolve `(aceitos, quantidade_ignorada)`. Um caminho é aceito se for um
    arquivo existente com extensão em `EXTENSOES_ACEITAS` (sem distinguir
    maiúsculas). Pastas, arquivos inexistentes e extensões desconhecidas
    contam como ignorados. Mantém a ordem recebida.
    """
    aceitos: list[Path] = []
    total = 0
    for p in caminhos:
        total += 1
        if p.suffix.lower() in EXTENSOES_ACEITAS and p.is_file():
            aceitos.append(p)
    return aceitos, total - len(aceitos)


def _formatos_dos_arquivos(caminhos: Iterable[Path]) -> set[Formato]:
    """Os formatos dos caminhos, pela extensão (já filtrados)."""
    return {_FORMATO_DA_EXTENSAO[p.suffix.lower()] for p in caminhos}


def _conversao_possivel(entradas: Iterable[Formato]) -> bool:
    """A caixa "Converter para WebP" só faz algo se houver PNG ou JPEG.

    Sem nenhum arquivo solto ainda não há o que restringir: a caixa fica
    disponível.
    """
    entradas = set(entradas)
    return not entradas or bool(entradas & _CONVERSIVEIS)


def _alavancas_aplicaveis(
    entradas: Iterable[Formato], converter_webp: bool
) -> frozenset[str]:
    """Quais alavancas (chaves de `ladder.ALAVANCAS`) mudam algo.

    Uma alavanca vale se a coluna que ela controla existe no `Params` da
    escada que o arquivo vai percorrer, e essa escada é a do formato de
    SAÍDA (com a conversão, um PNG percorre a de WebP). Sem arquivos, todas.
    """
    # Dedupe que preserva a ordem recebida: iterar um `set` de enum dependeria
    # do PYTHONHASHSEED, e um bug de união ("=" no lugar de "|=") só apareceria
    # em parte das execuções.
    entradas = list(dict.fromkeys(entradas))
    if not entradas:
        return frozenset(ALAVANCAS)
    aplicaveis: set[str] = set()
    for entrada in entradas:
        saida = formatos.formato_de_saida(entrada, converter_webp)
        colunas = {c.name for c in fields(formatos.para(saida).Params)}
        aplicaveis |= {t for t, coluna in ALAVANCAS.items() if coluna in colunas}
    return frozenset(aplicaveis)


def _subamostragem(valor: object) -> str:
    nomes = {-1: "croma da fonte", 0: "4:4:4", 1: "4:2:2", 2: "4:2:0"}
    return nomes.get(valor, f"subamostragem {valor}")  # type: ignore[call-overload]


# Como mostrar cada coluna conhecida de `Params`. Uma coluna que não está
# aqui é mostrada como `nome valor`: um formato novo nunca derruba o log.
_FORMATADORES_DE_COLUNA: dict[str, Callable[[object], str]] = {
    "colors": lambda v: "sem quantizar" if v is None else f"{v} cores",
    "lossy": lambda v: f"lossy {v}",
    "oxipng": lambda v: f"oxipng {v}",
    # `None` é o degrau sem perda do WebP, mas o log de tentativas não sabe de
    # onde o arquivo veio: para um JPEG de entrada essa frase seria mentira.
    # A afirmação fica só em `_texto_resultado`, onde há o formato de entrada.
    "quality": lambda v: "qualidade máxima" if v is None else f"qualidade {v}",
    "subsampling": _subamostragem,
    "scale_pct": lambda v: f"{v}%",
    "fps": lambda v: "orig" if v is None else f"{v:g}fps",
}


def _descrever_params(params: object) -> str:
    """Uma linha com todas as colunas de um `Params` de qualquer formato.

    Percorre `dataclasses.fields`, sem supor quais colunas existem.
    """
    partes = []
    for campo in fields(params):  # type: ignore[arg-type]
        valor = getattr(params, campo.name)
        formatador = _FORMATADORES_DE_COLUNA.get(campo.name)
        partes.append(
            formatador(valor) if formatador is not None else f"{campo.name} {valor}"
        )
    return " · ".join(partes)


def _sem_perda(r: CompressResult) -> bool:
    """O resultado pode ser descrito como sem perda?

    A regra é por formato de ENTRADA, e só vale onde é verdade. PNG nos
    degraus sem quantizar e sem escalar é só recompressão. WebP no degrau
    sem perda também. JPEG NUNCA: a entrada já é lossy, então reempacotar
    degrada de novo mesmo no degrau mais conservador (spec, "JPEG é perda
    sobre perda"), inclusive quando a saída é o degrau sem perda do WebP.
    GIF também fica de fora: nenhum degrau dele é garantidamente exato.
    """
    if r.already_optimized or r.params is None:
        return False
    if r.formato_entrada not in (Formato.PNG, Formato.WEBP):
        return False
    # Limite conhecido, não verificado de ponta a ponta: um PNG cinza de 16 bits
    # passa por `_para_8_bits` em qualquer degrau do WebP (o do PNG copia o
    # arquivo nos degraus 0-1), então para essa entrada a frase pode ser falsa.
    if r.formato_saida is Formato.PNG:
        return r.params.colors is None and r.params.scale_pct == 100
    if r.formato_saida is Formato.WEBP:
        return r.params.quality is None and r.params.scale_pct == 100
    return False


def _texto_resultado(r: CompressResult, converteu_webp: bool = False) -> str:
    """A linha de resultado de um arquivo.

    O que se PROMETE vem só do que `r` diz que foi escrito (`formato_saida`,
    `dst`): nunca se anuncia um `.webp` que não existe. `converteu_webp` (se o
    lote pediu a conversão) só serve para EXPLICAR por que o original foi
    mantido, e só quando `r` confirma que foi isso que aconteceu: entrada PNG
    ou JPEG, cópia mantida, saída que não é WebP. Com a conversão ligada o
    arquivo nunca percorreu a escada do próprio formato, então "já estava
    otimizado" seria uma afirmação sobre ela.
    """
    if r.already_optimized:
        rotulo = _ROTULO_FORMATO[r.formato_saida]
        if (
            converteu_webp
            and r.formato_entrada in _CONVERSIVEIS
            and r.formato_saida is not Formato.WEBP
        ):
            return (
                f"{r.src.name}: nenhum degrau de WebP ficou menor que o original; "
                f"cópia mantida ({rotulo}), a escada de {rotulo} não foi tentada "
                f"→ {r.dst.name}"
            )
        return (
            f"{r.src.name}: já estava otimizado, cópia mantida "
            f"({rotulo}) → {r.dst.name}"
        )
    texto = (
        f"{r.src.name}: {_mb(r.original_bytes)} → {_mb(r.final_bytes)} "
        f"(−{r.reduction_pct:.0f}%)"
    )
    if r.formato_saida is not r.formato_entrada:
        texto += (
            f" · {_ROTULO_FORMATO[r.formato_entrada]} → "
            f"{_ROTULO_FORMATO[r.formato_saida]}"
        )
    if _sem_perda(r):
        texto += " · sem perda"
    return f"{texto} → {r.dst.name}"


class App(TkinterDnD.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Comprime Aí")
        self.geometry("580x720")
        self.minsize(480, 600)

        self.tools: Tools | None = None
        self.erro_ferramentas: str | None = None
        self.fila: queue.Queue = queue.Queue()
        self.cancelar = threading.Event()
        self.trabalhando = False
        self.ultima_pasta: Path | None = None
        self._after_id: str | None = None
        self._thread: threading.Thread | None = None
        # Formatos do último conjunto solto; vazio antes de qualquer soltura.
        self._entradas: set[Formato] = set()
        # Se o lote atual pediu a conversão. Gravado em `_processar` (thread
        # principal) e lido em `_log_resultado` (também): o worker não o toca.
        self._lote_converteu = False

        # Progresso do lote atual (ver _iniciar_lote / _atualizar_progresso).
        self.total_arquivos = 0
        self.arquivos_concluidos = 0
        self.tentativas_arquivo_atual = 0

        self.alvo_mb = StringVar(value="10")
        self.tg_palette = BooleanVar(value=True)
        self.tg_lossy = BooleanVar(value=True)
        self.tg_scale = BooleanVar(value=True)
        self.tg_fps = BooleanVar(value=True)
        self.tg_webp = BooleanVar(value=False)

        self._montar()
        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self._checar_ferramentas()
        self._after_id = self.after(100, self._drenar_fila)

    # ---------- construção da interface ----------

    def _montar(self) -> None:
        raiz = ttk.Frame(self, padding=12)
        raiz.pack(fill="both", expand=True)

        self.drop = ttk.Label(
            raiz,
            text="Arraste imagens aqui (GIF, PNG, JPEG ou WebP)",
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
        # Uma caixa por alavanca de `ladder.ALAVANCAS`, guardada pela mesma
        # chave: é por ela que `_ajustar_alavancas` habilita e desabilita.
        self._caixas_alavanca: dict[str, ttk.Checkbutton] = {}
        for chave, texto, var in [
            ("palette", "Reduzir paleta de cores", self.tg_palette),
            ("lossy", "Compressão lossy da paleta (só GIF)", self.tg_lossy),
            ("scale", "Reduzir resolução", self.tg_scale),
            ("fps", "Reduzir framerate (só GIF)", self.tg_fps),
        ]:
            caixa = ttk.Checkbutton(alav, text=texto, variable=var)
            caixa.pack(anchor="w")
            self._caixas_alavanca[chave] = caixa
        self.cb_palette = self._caixas_alavanca["palette"]
        self.cb_lossy = self._caixas_alavanca["lossy"]
        self.cb_scale = self._caixas_alavanca["scale"]
        self.cb_fps = self._caixas_alavanca["fps"]

        saida = ttk.LabelFrame(raiz, text="Formato de saída", padding=8)
        saida.pack(fill="x", pady=(0, 12))
        self.cb_webp = ttk.Checkbutton(
            saida,
            text="Converter para WebP (PNG e JPEG)",
            variable=self.tg_webp,
            command=self._ao_mudar_conversao,
        )
        self.cb_webp.pack(anchor="w")

        # O rodapé é empacotado ANTES do log, grudado embaixo: com a janela
        # menor que o pedido, é o log que encolhe, não os botões que somem.
        rodape = ttk.Frame(raiz)
        rodape.pack(side="bottom", fill="x", pady=(8, 0))
        self.btn_cancelar = ttk.Button(
            rodape, text="Cancelar", command=self._cancelar, state="disabled"
        )
        self.btn_cancelar.pack(side="left")
        self.btn_pasta = ttk.Button(
            rodape, text="Abrir pasta", command=self._abrir_pasta, state="disabled"
        )
        self.btn_pasta.pack(side="right")

        self.barra = ttk.Progressbar(raiz, mode="determinate")
        self.barra.pack(fill="x")

        self.status = ttk.Label(raiz, text="Pronto.", anchor="w")
        self.status.pack(fill="x", pady=(6, 4))

        self.log = ttk.Treeview(
            raiz, columns=("msg",), show="tree", height=12, selectmode="none"
        )
        self.log.pack(fill="both", expand=True)


    # ---------- ferramentas ----------

    def _checar_ferramentas(self) -> None:
        try:
            self.tools = find_tools(BIN_DIR)
        except MissingToolError as exc:
            self.erro_ferramentas = str(exc)
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
            title="Escolha as imagens",
            filetypes=[
                ("Imagens", tuple(f"*{e}" for e in sorted(EXTENSOES_ACEITAS))),
                ("Todos os arquivos", "*.*"),
            ],
        )
        self._processar([Path(n) for n in nomes])

    def _processar(self, caminhos: list[Path]) -> None:
        if self.trabalhando:
            messagebox.showinfo("Aguarde", "Já existe uma compressão em andamento.")
            return
        if self.tools is None:
            messagebox.showerror(
                "Sem ferramentas",
                self.erro_ferramentas or "ffmpeg não foi encontrado.",
            )
            return

        arquivos, ignorados = _filtrar_caminhos(caminhos)
        if ignorados:
            self._log(
                f"{ignorados} arquivo(s) ignorado(s): não são GIF, PNG, JPEG ou WebP."
            )
        if not arquivos:
            messagebox.showwarning("Nada a fazer", "Nenhuma imagem válida.")
            return

        self._ajustar_alavancas(_formatos_dos_arquivos(arquivos))

        try:
            alvo = _parse_alvo_mb(self.alvo_mb.get())
        except ValueError:
            messagebox.showerror(
                "Alvo inválido", "Informe um número de MB maior que zero."
            )
            return

        toggles = Toggles(
            palette=self.tg_palette.get(),
            lossy=self.tg_lossy.get(),
            scale=self.tg_scale.get(),
            fps=self.tg_fps.get(),
        )

        # Lido AQUI, na thread principal: o worker recebe um bool, nunca a
        # variável Tk. A caixa pode estar marcada e desabilitada (só GIF);
        # o que vale é o que ela pode fazer.
        converter_webp = self.tg_webp.get() and _conversao_possivel(self._entradas)
        self._lote_converteu = converter_webp

        self.trabalhando = True
        self.cancelar.clear()
        self.btn_cancelar.config(state="normal")
        self.log.delete(*self.log.get_children())
        self._iniciar_lote(len(arquivos))

        self._thread = threading.Thread(
            target=self._trabalhar,
            args=(arquivos, alvo, toggles, converter_webp),
            daemon=True,
        )
        self._thread.start()

    # ---------- alavancas que valem para o que foi solto ----------

    def _ao_mudar_conversao(self) -> None:
        """A caixa de WebP muda a escada do PNG/JPEG: reavalia as alavancas."""
        self._ajustar_alavancas(self._entradas)

    def _ajustar_alavancas(self, entradas: Iterable[Formato]) -> None:
        """Desabilita o que não se aplica ao que foi solto.

        Uma alavanca que não faria nada fica desabilitada em vez de aceitar o
        clique e ignorá-lo em silêncio. Sem arquivos, tudo fica habilitado.
        """
        self._entradas = set(entradas)
        converter = self.tg_webp.get() and _conversao_possivel(self._entradas)
        aplicaveis = _alavancas_aplicaveis(self._entradas, converter)
        for chave, caixa in self._caixas_alavanca.items():
            caixa.state(["!disabled" if chave in aplicaveis else "disabled"])
        self.cb_webp.state(
            ["!disabled" if _conversao_possivel(self._entradas) else "disabled"]
        )

    # ---------- thread de trabalho ----------
    #
    # Tudo neste método (e em tudo que ele chama) roda fora da thread
    # principal. Não é permitido tocar em nenhum widget aqui — só em
    # `self.fila` (queue.Queue, thread-safe) e `self.cancelar`
    # (threading.Event, thread-safe). `self.tools` é lido, nunca escrito,
    # aqui.

    def _trabalhar(
        self,
        arquivos: list[Path],
        alvo: int,
        toggles: Toggles,
        converter_webp: bool,
    ) -> None:
        assert self.tools is not None
        for i, arquivo in enumerate(arquivos, 1):
            if self.cancelar.is_set():
                break
            self.fila.put(("status", f"[{i}/{len(arquivos)}] {arquivo.name}"))
            try:
                r = compress_file(
                    arquivo, alvo, toggles, self.tools,
                    converter_webp=converter_webp,
                    on_progress=lambda a, t: self.fila.put(("tentativa", (a, t))),
                    should_cancel=self.cancelar.is_set,
                )
                self.fila.put(("resultado", r))
            except Cancelled:
                self.fila.put(("log", "Cancelado."))
                break
            except Exception as exc:  # falha em um arquivo não derruba o lote
                self.fila.put(("erro_arquivo", f"{arquivo.name}: {exc}"))
        self.fila.put(("fim", None))

    # ---------- drenagem da fila na thread da UI ----------

    def _drenar_fila(self) -> None:
        """Drena a fila do worker e reagenda a si mesma.

        O reagendamento fica num `finally` de propósito: se um handler
        levantar, o Tk engole o traceback silenciosamente, e sem o `finally`
        o laço morreria para sempre — a janela ficaria congelada no meio de
        um lote, sem log, sem barra e sem forma de recuperar. Cada mensagem
        também é tratada isoladamente, para que uma carga inesperada não
        derrube o laço inteiro.
        """
        try:
            while True:
                try:
                    tipo, carga = self.fila.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._tratar_mensagem(tipo, carga)
                except Exception as exc:  # nunca derruba o laço de drenagem
                    self._log(f"Erro interno ao processar mensagem: {exc}")
        finally:
            self._after_id = self.after(100, self._drenar_fila)

    def _tratar_mensagem(self, tipo: str, carga: object) -> None:
        if tipo == "status":
            self.status.config(text=carga)
        elif tipo == "log":
            self._log(str(carga))
        elif tipo == "erro_arquivo":
            self.arquivos_concluidos += 1
            self.tentativas_arquivo_atual = 0
            self._atualizar_progresso()
            self._log(f"ERRO em {carga}")
        elif tipo == "tentativa":
            self._log_tentativa(*carga)  # type: ignore[misc]
        elif tipo == "resultado":
            self._log_resultado(carga)  # type: ignore[arg-type]
        elif tipo == "fim":
            self._terminar()

    def _log(self, texto: str) -> None:
        item = self.log.insert("", "end", text=texto)
        self.log.see(item)

    def _log_tentativa(self, a: Attempt, total: int) -> None:
        if a.erro is not None:
            mensagem = a.erro if len(a.erro) <= 200 else a.erro[:200] + "..."
            self._log(f"  degrau {a.index}/{total - 1} falhou: {mensagem}")
        else:
            self._log(
                f"  degrau {a.index}/{total - 1} · {_descrever_params(a.params)} "
                f"→ {_mb(a.size_bytes)}"
            )
        self.tentativas_arquivo_atual += 1
        self._atualizar_progresso()

    # ---------- progresso do lote ----------

    def _iniciar_lote(self, total_arquivos: int) -> None:
        """Zera o progresso no começo de cada lote."""
        self.total_arquivos = total_arquivos
        self.arquivos_concluidos = 0
        self.tentativas_arquivo_atual = 0
        self.barra.config(value=0)

    def _atualizar_progresso(self) -> None:
        """Progresso do lote inteiro, não de um arquivo isolado.

        Conta em unidades de tentativa: cada arquivo custa cerca de
        TENTATIVAS_ESPERADAS_POR_ARQUIVO codificações, então a barra anda de
        forma suave mesmo quando o usuário soltou um único GIF — em vez de
        ficar parada em zero por vinte segundos e pular direto para 100.
        """
        if self.total_arquivos <= 0:
            return
        esperado = self.total_arquivos * TENTATIVAS_ESPERADAS_POR_ARQUIVO
        feito = (
            self.arquivos_concluidos * TENTATIVAS_ESPERADAS_POR_ARQUIVO
            + self.tentativas_arquivo_atual
        )
        self.barra.config(value=min(100.0, 100.0 * feito / esperado))

    def _log_resultado(self, r: CompressResult) -> None:
        self.ultima_pasta = r.dst.parent
        self.arquivos_concluidos += 1
        self.tentativas_arquivo_atual = 0
        self._atualizar_progresso()
        self._log(_texto_resultado(r, self._lote_converteu))
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

    def _ao_fechar(self) -> None:
        """Fecha a janela sem deixar callback pendente nem worker órfão.

        Sinaliza o cancelamento para que um worker em andamento pare no
        próximo checkpoint entre tentativas, e cancela o `after` pendente
        para que a drenagem não tente mexer em widget já destruído.

        Se havia uma compressão em andamento, o worker ainda precisa
        terminar para rodar o `finally` de `compress_file` que remove o
        tmpdir — sem isso, um `gifc_*` cheio de `try_N.gif` fica para trás
        em %TEMP% e o processo de ffmpeg/gifsicle continua rodando órfão.
        Por isso esperamos o worker com um `join` limitado
        (`FECHAR_JOIN_TIMEOUT_SEGUNDOS`) antes de destruir. É um
        best-effort: se o worker estiver preso no meio de uma chamada
        longa de subprocesso, o join estoura o tempo e destruímos mesmo
        assim — a janela não pode parecer travada esperando por isso.
        """
        self.cancelar.set()
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass  # já disparou ou a janela já está indo embora
            self._after_id = None

        if self.trabalhando and self._thread is not None and self._thread.is_alive():
            try:
                self.status.config(text="Cancelando e limpando arquivos temporários...")
                self.update_idletasks()
            except Exception:
                pass  # a janela pode já estar em processo de destruição
            self._thread.join(timeout=FECHAR_JOIN_TIMEOUT_SEGUNDOS)

        self.destroy()

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
