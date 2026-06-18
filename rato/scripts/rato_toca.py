"""
O Rato: biblioteca · rastros · toca

Menu interativo para os scripts operacionais em rato/scripts.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


console = Console()

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
PYTHON = sys.executable or "python3"
TEXTOS_PADRAO = "textos limpos"


RATO_ASCII = r"""
                  .*+
                 #%#+
           .+***%@#%. ##**
   .. ###*+@@@@@@@@##@@@@@=.
 +@@@@@@@@@. @@@@@%%@*@@@@*...****++++.
 #@@@@@@@@@@@@@@@@@@%@***@@@@@@@@@@@@@@@++.
  *@@@@@@@@@@@@@@@@@@%@@@@@@@@@@@@@@@@@@@@@+*.
   .**##*@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@#.
       .++=@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@#                     .  *++*+++.
           .+@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@*:              ..***@@@++++++++=
             *@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@#*           +**@@***..
             .+@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@*.        .*@@**
             .+@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@#=       +*@**
             =@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@#      *+@**.
            ==@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@#.+   :+@+*
          .+@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@#%  .+@@#
         +@@@@@@++++++*+=+=@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@*
    .==+@@@=**#.     *##@: .++@@@@@@@@@@@@@@@@@@@@@@@@@***.
   .++===+      .+*###%#=     .=+##*****=++@@@@@@##...
                                .+*****+**+++.
"""


def cabecalho(titulo: Optional[str] = None) -> None:
    console.clear()
    console.print(f"[green]{RATO_ASCII}[/green]")
    subtitulo = "biblioteca · rastros · toca"
    if titulo:
        subtitulo = f"{subtitulo}\n[bold]{titulo}[/bold]"
    console.print(Panel.fit("[bold]O RATO[/bold]\n" + subtitulo, border_style="green"))


def pausar() -> None:
    console.input("\n[green]Pressione Enter para voltar à toca...[/green]")


def perguntar(texto: str, padrao: Optional[str] = None) -> str:
    sufixo = f" [dim]({padrao})[/dim]" if padrao is not None else ""
    valor = console.input(f"[green]{texto}[/green]{sufixo}: ").strip()
    return valor or (padrao or "")


def confirmar(texto: str, padrao: bool = False) -> bool:
    opcoes = "S/n" if padrao else "s/N"
    resposta = console.input(f"[yellow]{texto}[/yellow] [{opcoes}] ").strip().lower()
    if not resposta:
        return padrao
    return resposta in {"s", "sim", "y", "yes"}


def escolher(texto: str, opcoes: dict[str, str], padrao: str) -> str:
    console.print(f"\n[bold]{texto}[/bold]")
    for chave, descricao in opcoes.items():
        marca = " [dim](padrão)[/dim]" if chave == padrao else ""
        console.print(f"  ({chave}) {descricao}{marca}")
    while True:
        escolha = perguntar("Escolha", padrao).lower()
        if escolha in opcoes:
            return escolha
        console.print("[red]Opção desconhecida.[/red]")


def escolher_por_inicial(texto: str, opcoes: dict[str, str], padrao: str) -> str:
    iniciais: dict[str, str] = {}
    for chave in opcoes:
        inicial = chave[0].lower()
        if inicial in iniciais:
            raise ValueError(f"Inicial repetida nas opções: {inicial}")
        iniciais[inicial] = chave

    console.print(f"\n[bold]{texto}[/bold]")
    for chave, descricao in opcoes.items():
        inicial = chave[0].lower()
        marca = " [dim](padrão)[/dim]" if chave == padrao else ""
        console.print(f"  ({inicial}) {chave} - {descricao}{marca}")

    padrao_inicial = padrao[0].lower()
    while True:
        escolha = perguntar("Inicial ou opção", padrao_inicial).lower()
        if escolha in iniciais:
            return iniciais[escolha]
        if escolha in opcoes:
            return escolha
        console.print("[red]Opção desconhecida.[/red]")


def script(nome: str) -> str:
    return str(SCRIPT_DIR / nome)


def formatar_comando(args: list[str]) -> str:
    return " ".join(shlex.quote(parte) for parte in args)


def rodar(args: list[str], *, confirmar_antes: bool = True) -> Optional[int]:
    console.print("\n[bold green]$ " + formatar_comando(args) + "[/bold green]\n")
    if confirmar_antes and not confirmar("Executar este comando?"):
        console.print("[dim]Comando cancelado.[/dim]")
        pausar()
        return None
    resultado = subprocess.run(args, cwd=PROJECT_ROOT)
    pausar()
    return resultado.returncode


def adicionar_flag(args: list[str], pergunta: str, flag: str, padrao: bool = False) -> None:
    if confirmar(pergunta, padrao):
        args.append(flag)


def caminho_no_projeto(caminho: str) -> Path:
    path = Path(caminho).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def arquivos_markdown(pasta: str) -> list[Path]:
    base = caminho_no_projeto(pasta)
    if not base.is_dir():
        return []
    return sorted(
        (path for path in base.iterdir() if path.is_file() and path.suffix.lower() == ".md"),
        key=lambda path: path.name.lower(),
    )


def arquivos_com_extensao(pasta: str, extensoes: tuple[str, ...]) -> list[Path]:
    base = caminho_no_projeto(pasta)
    if not base.is_dir():
        return []
    extensoes_normalizadas = tuple(ext.lower() for ext in extensoes)
    return sorted(
        (
            path
            for path in base.iterdir()
            if path.is_file() and path.suffix.lower() in extensoes_normalizadas
        ),
        key=lambda path: path.name.lower(),
    )


def caminho_para_comando(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def markdown_convertido_para(entrada: str, saida: str) -> str:
    """Retorna o alvo de limpeza esperado depois de converter PDF(s)."""
    from converter import slug

    entrada_path = caminho_no_projeto(entrada)
    saida_path = caminho_no_projeto(saida)
    if entrada_path.suffix.lower() == ".pdf":
        return caminho_para_comando(saida_path / f"{slug(entrada_path.stem)}.md")
    return caminho_para_comando(saida_path)


def selecionar_com_setas(titulo: str, itens: list[str]) -> Optional[int]:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return -1

    try:
        import curses
    except ImportError:
        return -1

    def desenhar(stdscr: "curses.window") -> Optional[int]:
        curses.curs_set(0)
        stdscr.keypad(True)
        selecionado = 0
        topo = 0

        while True:
            altura, largura = stdscr.getmaxyx()
            visiveis = max(1, altura - 4)
            topo = min(topo, max(0, len(itens) - visiveis))
            if selecionado < topo:
                topo = selecionado
            elif selecionado >= topo + visiveis:
                topo = selecionado - visiveis + 1

            stdscr.erase()
            stdscr.addnstr(0, 0, titulo, largura - 1, curses.A_BOLD)
            stdscr.addnstr(1, 0, "Setas: mover · Enter: escolher · q/Esc: cancelar", largura - 1)

            for linha, item in enumerate(itens[topo : topo + visiveis], start=3):
                indice = topo + linha - 3
                marcador = "› " if indice == selecionado else "  "
                atributo = curses.A_REVERSE if indice == selecionado else curses.A_NORMAL
                stdscr.addnstr(linha, 0, marcador + item, largura - 1, atributo)

            tecla = stdscr.getch()
            if tecla in (curses.KEY_UP, ord("k")):
                selecionado = max(0, selecionado - 1)
            elif tecla in (curses.KEY_DOWN, ord("j")):
                selecionado = min(len(itens) - 1, selecionado + 1)
            elif tecla in (curses.KEY_HOME,):
                selecionado = 0
            elif tecla in (curses.KEY_END,):
                selecionado = len(itens) - 1
            elif tecla in (curses.KEY_NPAGE,):
                selecionado = min(len(itens) - 1, selecionado + visiveis)
            elif tecla in (curses.KEY_PPAGE,):
                selecionado = max(0, selecionado - visiveis)
            elif tecla in (10, 13, curses.KEY_ENTER):
                return selecionado
            elif tecla in (27, ord("q")):
                return None

    try:
        return curses.wrapper(desenhar)
    except curses.error:
        return -1


def selecionar_arquivo_markdown(pasta: str) -> Optional[str]:
    arquivos = arquivos_markdown(pasta)
    if not arquivos:
        console.print("[yellow]Não encontrei arquivos .md nessa pasta.[/yellow]")
        return perguntar("Arquivo específico, vazio para todos")

    itens = ["(processar todos os Markdown da pasta)"] + [path.name for path in arquivos]
    indice = selecionar_com_setas("Escolha o arquivo para roer", itens)
    if indice is None:
        return None
    if indice == -1:
        console.print("\n[bold]Arquivos Markdown[/bold]")
        for numero, item in enumerate(itens):
            console.print(f"  [{numero}] {item}")
        while True:
            escolha = perguntar("Número do arquivo", "0")
            if escolha.isdigit() and 0 <= int(escolha) < len(itens):
                indice = int(escolha)
                break
            console.print("[red]Número inválido.[/red]")

    if indice == 0:
        return ""
    return arquivos[indice - 1].name


def selecionar_alvo_em_pasta(
    titulo: str,
    pasta_padrao: str,
    extensoes: tuple[str, ...],
    rotulo_todos: str,
    prompt_manual: str,
) -> Optional[str]:
    pasta = perguntar("Pasta para listar", pasta_padrao)
    arquivos = arquivos_com_extensao(pasta, extensoes)
    if not arquivos:
        console.print("[yellow]Não encontrei arquivos compatíveis nessa pasta.[/yellow]")
        return perguntar(prompt_manual, pasta)

    itens = [rotulo_todos] + [path.name for path in arquivos]
    indice = selecionar_com_setas(titulo, itens)
    if indice is None:
        return None
    if indice == -1:
        console.print(f"\n[bold]{titulo}[/bold]")
        for numero, item in enumerate(itens):
            console.print(f"  [{numero}] {item}")
        while True:
            escolha = perguntar("Número", "0")
            if escolha.isdigit() and 0 <= int(escolha) < len(itens):
                indice = int(escolha)
                break
            console.print("[red]Número inválido.[/red]")

    if indice == 0:
        return pasta
    return caminho_para_comando(arquivos[indice - 1])


def menu_cacar() -> None:
    cabecalho("CAÇAR")
    console.print("Converter, limpar e catalogar PDFs ou Markdown.\n")
    opcoes = {
        "1": "Converter PDF/pasta em Markdown",
        "2": "Limpar Markdown",
        "3": "Catalogar Markdown/pasta",
        "4": "Converter PDF e limpar/catalogar o Markdown depois",
    }
    escolha = escolher("Etapa", opcoes, "4")

    if escolha == "1":
        entrada = selecionar_alvo_em_pasta(
            "Escolha PDF para converter",
            "PDF",
            (".pdf",),
            "(processar todos os PDFs da pasta)",
            "PDF ou pasta de PDFs",
        )
        if not entrada:
            return
        args = [PYTHON, script("converter.py"), entrada, "--saida", perguntar("Pasta de saída", ".")]
        adicionar_flag(args, "Forçar reconversão se o Markdown já existir?", "--forcar")
        adicionar_flag(args, "Permitir OCR do Docling?", "--com-ocr")
        limite = perguntar("Limite de PDFs, vazio para todos")
        if limite:
            args.extend(["--limite", limite])
        rodar(args)
        return

    if escolha == "2":
        alvo = selecionar_alvo_em_pasta(
            "Escolha Markdown para limpar",
            TEXTOS_PADRAO,
            (".md",),
            "(processar todos os Markdown da pasta)",
            "Arquivo Markdown ou pasta",
        )
        if not alvo:
            return
        if Path(alvo).suffix.lower() == ".md":
            args = [PYTHON, script("limpar.py"), alvo]
        else:
            args = [PYTHON, script("limpar.py"), "--pasta", alvo]
        args.append("--in-place")
        adicionar_flag(args, "Converter notas de rodapé para Markdown?", "--converter-notas", True)
        adicionar_flag(args, "Marcar dúvidas de OCR?", "--marcar-duvidas")
        adicionar_flag(args, "Catalogar ao final?", "--catalogar", True)
        rodar(args)
        return

    if escolha == "3":
        alvo = selecionar_alvo_em_pasta(
            "Escolha Markdown para catalogar",
            TEXTOS_PADRAO,
            (".md",),
            "(processar todos os Markdown da pasta)",
            "Arquivo Markdown ou pasta",
        )
        if not alvo:
            return
        args = [PYTHON, script("catalogar.py"), alvo, "--aplicar"]
        adicionar_flag(args, "Procurar Markdown recursivamente?", "--recursivo")
        adicionar_flag(args, "Forçar recálculo de metadados existentes?", "--forcar")
        limite = perguntar("Limite de arquivos, vazio para todos")
        if limite:
            args.extend(["--limite", limite])
        rodar(args)
        return

    entrada = selecionar_alvo_em_pasta(
        "Escolha PDF para converter",
        "PDF",
        (".pdf",),
        "(processar todos os PDFs da pasta)",
        "PDF ou pasta de PDFs",
    )
    if not entrada:
        return
    saida = perguntar("Pasta de saída dos Markdown", ".")
    converter = [PYTHON, script("converter.py"), entrada, "--saida", saida]
    adicionar_flag(converter, "Forçar reconversão se o Markdown já existir?", "--forcar")
    adicionar_flag(converter, "Permitir OCR do Docling?", "--com-ocr")
    if rodar(converter) == 0:
        alvo_limpeza = markdown_convertido_para(entrada, saida)
        console.print(f"\n[dim]Markdown para limpar/catalogar: {alvo_limpeza}[/dim]")
        if Path(alvo_limpeza).suffix.lower() == ".md":
            limpar = [PYTHON, script("limpar.py"), alvo_limpeza]
        else:
            limpar = [PYTHON, script("limpar.py"), "--pasta", alvo_limpeza]
        limpar.extend(["--in-place", "--converter-notas", "--catalogar"])
        rodar(limpar)


def menu_roer() -> None:
    cabecalho("ROER")
    modo = escolher_por_inicial(
        "Modo",
        {
            "completo": "Leituras, embeddings, SQLite e fichas",
            "indexar": "Embeddings e SQLite",
            "fichar": "Ficha interpretativa",
        },
        "completo",
    )
    if modo == "fichar":
        console.print(
            "\n[yellow]Aviso:[/yellow] o modo fichar usa uma leitura bruta já existente "
            "em leituras-brutas/ e não gera embeddings. Se ainda não houver leitura, "
            "rode indexar ou completo primeiro.\n"
        )
    elif modo == "indexar":
        console.print(
            "\n[dim]O modo indexar gera/atualiza leituras brutas, SQLite e embeddings, "
            "mas não cria ficha final.[/dim]\n"
        )
    pasta = perguntar("Pasta com referências Markdown", TEXTOS_PADRAO)
    saida = perguntar("Pasta de saída das fichas", "fichas")
    args = [
        PYTHON,
        script("roer.py"),
        "--modo",
        modo,
        "--pasta",
        pasta,
        "--saida",
        saida,
        "--leituras",
        perguntar("Pasta de leituras brutas", "leituras-brutas"),
        "--embeddings",
        perguntar("Pasta de embeddings", ".embeddings"),
        "--sqlite",
        perguntar("Banco SQLite", "biblioteca.sqlite"),
        "--modelo-embedding",
        perguntar("Modelo de embeddings", "bge-m3"),
    ]
    if modo != "indexar":
        args.extend(["--modelo", perguntar("Modelo de leitura", "qwen3:8b")])
        modelo_consolidacao = perguntar("Modelo de consolidação/ficha", "qwen3:14b")
        if modelo_consolidacao:
            args.extend(["--modelo-consolidacao", modelo_consolidacao])
    arquivo = selecionar_arquivo_markdown(pasta)
    if arquivo is None:
        return
    if arquivo:
        args.extend(["--arquivo", arquivo])
    limite = perguntar("Limite de arquivos, vazio para todos")
    if limite:
        args.extend(["--limite", limite])
    adicionar_flag(args, "Reprocessar fichas existentes?", "--forcar")
    rodar(args)


def menu_farejar() -> None:
    cabecalho("FAREJAR")
    escolha = escolher(
        "Ação",
        {
            "1": "Indexar biblioteca",
            "2": "Buscar rastros e avaliar relações",
            "3": "Registrar aprendizado",
            "4": "Listar aprendizados",
        },
        "2",
    )
    if escolha == "1":
        args = [
            PYTHON,
            script("farejar.py"),
            "indexar",
            "--pasta",
            perguntar("Pasta com Markdown", TEXTOS_PADRAO),
            "--embedding",
            perguntar("Modelo de embeddings", "bge-m3"),
        ]
    elif escolha == "2":
        consulta = perguntar("Consulta")
        if not consulta:
            return
        busca = escolher(
            "Tipo de busca",
            {"conceitual": "Embeddings", "palavras": "Texto/FTS", "hibrida": "Embeddings + texto"},
            "hibrida",
        )
        args = [
            PYTHON,
            script("farejar.py"),
            "farejar",
            consulta,
            "--busca",
            busca,
            "--embedding",
            perguntar("Modelo de embeddings", "bge-m3"),
            "--modelo-chat",
            perguntar("Modelo para hipóteses", "qwen3:8b"),
            "--modelo-relacoes",
            perguntar("Modelo para avaliar relações", "qwen3:8b"),
            "--pasta-saida",
            perguntar("Pasta para salvar farejadas", "fichas/farejadas"),
        ]
    elif escolha == "3":
        texto = perguntar("Aprendizado/correção")
        if not texto:
            return
        args = [PYTHON, script("farejar.py"), "aprender", texto]
        tags = perguntar("Tags separadas por espaço")
        if tags:
            args.extend(["--tags", *tags.split()])
    else:
        args = [PYTHON, script("farejar.py"), "aprendizados"]
    rodar(args, confirmar_antes=escolha != "4")


def menu_digerir() -> None:
    cabecalho("DIGERIR")
    args = [PYTHON, script("digerir.py")]
    modo = escolher(
        "Fonte",
        {"1": "Chunks recentes do banco", "2": "Arquivos Markdown escolhidos"},
        "1",
    )
    if modo == "1":
        args.extend(["--dias", perguntar("Dias retroativos", "30")])
    else:
        arquivos = perguntar("Arquivos Markdown separados por espaço")
        if not arquivos:
            return
        args.extend(["--arquivos", *shlex.split(arquivos)])
    adicionar_flag(args, "Salvar digestão em fichas/digestoes?", "--salvar", True)
    rodar(args)


def menu_toca() -> None:
    cabecalho("TOCA")
    tabela = Table(title="Estado local", show_header=True)
    tabela.add_column("Item")
    tabela.add_column("Caminho")
    tabela.add_column("Existe?")
    for caminho in [
        "PDF",
        TEXTOS_PADRAO,
        "fichas",
        "leituras-brutas",
        ".embeddings",
        "biblioteca.sqlite",
    ]:
        existe = "sim" if (PROJECT_ROOT / caminho).exists() else "não"
        tabela.add_row(caminho, str(PROJECT_ROOT / caminho), existe)
    console.print(tabela)
    console.print(f"\n[dim]Raiz do projeto: {PROJECT_ROOT}[/dim]")
    pausar()


def menu() -> None:
    while True:
        cabecalho()
        tabela = Table(show_header=False, box=None)
        tabela.add_row("[1]", "[bold]CAÇAR[/bold]", "converter · limpar · catalogar")
        tabela.add_row("[2]", "[bold]ROER[/bold]", "leituras · embeddings · fichas")
        tabela.add_row("[3]", "[bold]FAREJAR[/bold]", "indexar · buscar · avaliar relações · aprender")
        tabela.add_row("[4]", "[bold]DIGERIR[/bold]", "tensões · perguntas · pontos cegos")
        tabela.add_row("[5]", "[bold]TOCA[/bold]", "estado da biblioteca")
        tabela.add_row("[0]", "SAIR", "")
        console.print(tabela)

        escolha = perguntar("Escolha uma opção")
        if escolha == "1":
            menu_cacar()
        elif escolha == "2":
            menu_roer()
        elif escolha == "3":
            menu_farejar()
        elif escolha == "4":
            menu_digerir()
        elif escolha == "5":
            menu_toca()
        elif escolha == "0":
            console.print("\n[green]O rato volta para a toca.[/green]")
            break
        else:
            console.print("[red]Opção desconhecida.[/red]")
            pausar()


if __name__ == "__main__":
    menu()
