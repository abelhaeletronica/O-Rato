# Scripts do Rato

Esta pasta concentra os scripts operacionais do projeto. A documentacao foi consolidada aqui para evitar READMEs paralelos e instrucoes antigas.

## Fluxo Principal

```bash
# 1. Converter PDF em Markdown
python3 rato/scripts/converter.py PDF/artigo.pdf --saida .

# 2. Limpar o Markdown extraido de PDF
python3 rato/scripts/limpar.py artigo.md --in-place

# 3. Limpar e catalogar em um unico passo
python3 rato/scripts/limpar.py artigo.md --in-place --catalogar

# 4. Indexar biblioteca para busca local
python3 rato/scripts/farejar.py indexar --pasta . --embedding bge-m3

# 5. Buscar rastros
python3 rato/scripts/farejar.py farejar "cuidado materia gesto" --busca hibrida

# 6. Digerir tensoes recentes da biblioteca
python3 rato/scripts/digerir.py --dias 30 --salvar
```

## Scripts Ativos

### `converter.py`

Converte PDF(s) para Markdown usando Docling.

```bash
python3 rato/scripts/converter.py arquivo.pdf --saida .
python3 rato/scripts/converter.py PDF --saida . --limite 5
```

Opcoes uteis:
- `--forcar`: reconverte mesmo se o `.md` ja existir.
- `--com-ocr`: permite OCR do Docling.
- `--timeout N`: timeout por PDF.
- `--docling /caminho/docling`: troca o binario Docling.

### `limpar.py`

Script unico de limpeza de Markdown extraido de PDF. Substitui os antigos `lamber.py`, `limpar_pdf.py`, `limpar_pdf2.py` e `converter_notas.py`.

```bash
python3 rato/scripts/limpar.py arquivo.md
python3 rato/scripts/limpar.py arquivo.md --in-place
python3 rato/scripts/limpar.py arquivo.md --saida arquivo-limpo.md
python3 rato/scripts/limpar.py --pasta . --limite 10
```

Funcionalidades:
- remove lixo editorial/JSTOR e cabecalhos repetidos;
- normaliza travessoes, hifenizacao, pontuacao e dominios;
- une paragrafos quebrados pelo PDF;
- processa notas de rodape;
- marca trechos suspeitos com `--marcar-duvidas`;
- converte notas para Markdown com `--converter-notas`;
- chama Ollama opcionalmente com `--com-ollama`;
- roda catalogacao ao final com `--catalogar`.

Exemplos:

```bash
python3 rato/scripts/limpar.py arquivo.md --in-place --converter-notas
python3 rato/scripts/limpar.py arquivo.md --in-place --catalogar
python3 rato/scripts/limpar.py arquivo.md --com-ollama --ollama-seletivo
```

### `catalogar.py`

Gera ou atualiza frontmatter YAML bibliografico. Continua separado porque tambem pode ser usado sem limpeza.

```bash
python3 rato/scripts/catalogar.py arquivo.md
python3 rato/scripts/catalogar.py arquivo.md --aplicar
python3 rato/scripts/catalogar.py . --limite 20
```

Campos inferidos:
- `title`
- `author`
- `year`
- `lingua`
- `tipo`
- `excerto`
- `metadados-confianca`
- `metadados-revisao`

### `roer.py`

Gera leituras brutas, embeddings e fichas interpretativas.

```bash
python3 rato/scripts/roer.py --modo indexar --pasta . --modelo-embedding bge-m3
python3 rato/scripts/roer.py --modo fichar --arquivo nome-do-arquivo
python3 rato/scripts/roer.py --modo completo --arquivo nome-do-arquivo
```

Saidas comuns:
- `leituras-brutas/LEITURA_*.md`
- `fichas/FICHA_*.md`
- `.embeddings/*.jsonl`

### `farejar.py`

Indexa a biblioteca local, busca rastros, levanta hipoteses e avalia relacoes.

```bash
python3 rato/scripts/farejar.py indexar --pasta . --embedding bge-m3
python3 rato/scripts/farejar.py farejar "reparo manutencao cuidado" --busca conceitual
python3 rato/scripts/farejar.py farejar "argila cuidado" --busca palavras
python3 rato/scripts/farejar.py farejar "gesto material" --busca hibrida
```

As farejadas salvas em `fichas/farejadas/` incluem duas camadas:
- `Hipoteses`: mapa dos rastros, pergunta emergente e resposta provisoria.
- `Avaliando relacoes`: comparacao dos rastros, tensoes, riscos de forcar relacoes e perguntas geradas.

Padrao atual de embeddings: `bge-m3`.

### `digerir.py`

Identifica tensoes, perguntas persistentes e pontos cegos a partir do banco SQLite e/ou de arquivos escolhidos.

```bash
python3 rato/scripts/digerir.py --dias 30
python3 rato/scripts/digerir.py --dias 7 --salvar
python3 rato/scripts/digerir.py --arquivos ficha1.md ficha2.md --salvar
```

Modos:
- sem `--arquivos`: busca chunks recentes no banco;
- com `--arquivos`: le arquivos Markdown e remove frontmatter antes da analise;
- com `--salvar`: grava em `fichas/digestoes/digestao_AAAA-MM-DD.md`.

### `cuidar_memoria.py` e `aplicar_correcoes.py`

Manutencao da memoria conceitual local.

```bash
python3 rato/scripts/cuidar_memoria.py
python3 rato/scripts/aplicar_correcoes.py
```

Use `aplicar_correcoes.py` depois de editar correcoes manuais em `rato/memoria/`.

### `rato_toca.py` e `rato.sh`

Atalhos e wrapper operacional para chamar comandos comuns a partir da raiz do projeto.

```bash
./rato/scripts/rato.sh ajuda
```

## Testes

Atualmente ha uma suite focada em `catalogar.py`:

```bash
cd rato/scripts
python3 -m pytest test_catalogar.py -v
python3 -m pytest test_catalogar.py::TestNormalizacao -v
```

O `test_catalogar.py` cobre normalizacao, validacao de autor, deteccao de titulo, extracao de metadados, lingua e tipo de texto.

## Scripts Consolidados ou Obsoletos

Estes nomes antigos nao devem mais ser usados:

- `lamber.py`: incorporado em `limpar.py`.
- `limpar_pdf.py`: incorporado em `limpar.py`.
- `limpar_pdf2.py`: incorporado em `limpar.py`.
- `converter_notas.py`: incorporado em `limpar.py --converter-notas`.
- `digerir2.py`: consolidado em `digerir.py`.

## Modelos

Modelos usados com mais frequencia:

```bash
ollama pull bge-m3
ollama pull qwen3:8b
ollama pull qwen3:14b
```

Uso recomendado:

| Etapa | Modelo |
| --- | --- |
| Embeddings | `bge-m3` |
| Leitura bruta | `qwen3:8b` |
| Ficha final / consolidacao | `qwen3:14b` |
| Farejar / relacoes | `qwen3:8b` |
| Digestao | `qwen3:14b` |

## Notas de Organizacao

- Mantenha documentacao operacional neste `README.md`.
- Evite criar READMEs por script; prefira uma secao nova neste arquivo.
- Scripts auxiliares devem ficar importaveis e com `--help` funcional.
- Comandos que alteram arquivos devem oferecer `--saida`, `--in-place` ou backup.
