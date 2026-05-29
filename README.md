                  .*+                                                            
                 #%#+                                                           
           .+***%@#%. ##**                                                      
   .. ###*+@@@@@@@@##@@@@@=.                                                     
 +@@@@@@@@@. @@@@@%%@*@@@@*...****++++.                                         
 #@@@@@@@@@@@@@@@@@@%@***@@@@@@@@@@@@@@@++.                                     
  *@@@@@@@@@@@@@@@@@@%@@@@@@@@@@@@@@@@@@@@@+*.                                   
   .**##*@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@#.                                
       .++=@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@#                        *++*+++. 
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
                                .+*****+**+++.  .                                

# O Rato

Um rato na biblioteca: percorre os mesmos livros que você lê, deixa rastros, fareja conexões entre prateleiras que você não teria tempo de cruzar sozinho.

O Rato é um sistema local de leitura acadêmica que combina modelos de linguagem, embeddings semânticos e memória conceitual para apoiar a análise de textos longos.

Diferentemente de ferramentas focadas em resumo ou recuperação de informação, o projeto procura preservar recorrências distribuídas, tensões conceituais e relações semânticas que atravessam múltiplos textos.

Atualmente o sistema organiza seu trabalho em quatro operações principais:

- **Caçar** — converter, limpar e catalogar documentos;
- **Roer** — produzir leituras, fichas e memória conceitual;
- **Farejar** — explorar vizinhanças e recorrências semânticas;
- **Cuidar** — manter e corrigir a memória acumulada da biblioteca.

O projeto é local-first e utiliza Ollama para execução dos modelos.

---

## Pré-requisitos

```bash
pip install requests pyyaml tqdm wordninja
ollama serve   # Ollama rodando localmente
```

Modelos recomendados (Mac Mini M4 16 GB):

```bash
ollama pull qwen2.5:7b       # leitura bruta por chunk
ollama pull qwen3:8b         # consolidação final (ficha)
ollama pull nomic-embed-text # embeddings para busca semântica
```

---

## Fluxo

```
PDF → Docling → .md bruto → limpeza OCR
                         → catalogação YAML
                         → rato/scripts/roer.py --modo indexar
                         → leituras-brutas + embeddings
                         → busca semântica / seleção
                         → rato/scripts/roer.py --modo fichar
                         → fichas Obsidian
```

O pipeline pode rodar em duas etapas. Primeiro indexa a biblioteca inteira com leitura bruta e embeddings; depois gera fichas completas apenas para os textos relevantes.

---

## Comando rápido

O jeito mais simples de usar o projeto é pelo executável local:

```bash
./rato.sh ajuda
```

Ele deve ser chamado a partir da raiz da biblioteca, isto é, da pasta onde ficam os arquivos `.md` brutos.

Comandos principais:

```bash
# Leitura bruta + embeddings, sem ficha final
./rato.sh indexar kirci-phenomenology-and-space-in-architecture.md

# Ficha final a partir de uma leitura bruta já existente
./rato.sh fichar kirci-phenomenology-and-space-in-architecture.md

# Fluxo completo: leitura + embeddings + ficha
./rato.sh completo jackson-rethinking-repair-media.md --forcar

# Gerar YAML bibliográfico antes da leitura
./rato.sh catalogar adorno-notas-de-literatura-i.md

# Fareja rastros textuais na biblioteca SQLite
./rato.sh farejar "cuidado matéria gesto"

# Tece relações semânticas a partir dos embeddings
./rato.sh tecer "cuidado como manutenção de relações materiais"
```

Atalhos de manutenção:

```bash
./rato.sh biblioteca-indexar
./rato.sh catalogar arquivo.md --aplicar
./rato.sh cuidar-memoria
./rato.sh corrigir-memoria
./rato.sh limpar-ocr arquivo.md --in-place
```

O wrapper usa estes modelos por padrão:

```bash
RATO_MODELO_LEITURA=qwen2.5:7b
RATO_MODELO_FICHA=qwen3:8b
RATO_MODELO_EMBEDDING=nomic-embed-text
```

Para trocar temporariamente:

```bash
RATO_MODELO_FICHA=qwen2.5-coder:7b ./rato.sh fichar jackson-rethinking-repair-media.md --forcar
```

Se quiser chamar apenas `rato` em vez de `./rato.sh`, crie um alias no shell:

```bash
alias rato='./rato.sh'
```

---

## Scripts

### 1. Converter PDFs para Markdown

```bash
./rato.sh converter artigo.pdf --saida .

# Ou converter uma pasta inteira de PDFs
./rato.sh converter /caminho/para/pdfs --saida . --limite 5

# Chamada direta ao script interno
python rato/scripts/converter.py artigo.pdf --saida .
```

Requer Docling instalado no venv:
```
~/.venvs/docling-md-py312/bin/docling
```

Por padrão, o conversor usa CPU, não força OCR, salva os `.md` na pasta indicada por `--saida` e registra o log em `rato/logs/conversao-docling.tsv`. Para trocar o binário do Docling:

```bash
DOCLING_BIN=/caminho/para/docling ./rato.sh converter artigo.pdf --saida .
```

### 2. Limpar OCR

```bash
./rato.sh limpar-ocr arquivo.md

# Para escolher manualmente o destino:
./rato.sh limpar-ocr arquivo.md --saida arquivo-limpo.md

# Para sobrescrever o original:
./rato.sh limpar-ocr arquivo.md --in-place
```

Script interno:

```bash
python rato/scripts/limpar.py arquivo.md
```

Por padrão, a limpeza preserva o original e cria automaticamente um arquivo com sufixo `-limpo`, por exemplo `bergson-materia-e-memoria-limpo.md`.

A limpeza corrige espaços antes de pontuação, pontuação colada à palavra seguinte, hifenização quebrada por OCR, domínios/e-mails com espaços indevidos, cabeçalhos repetidos de página e nomes colados por OCR como `RolfTiedemann`. Quando `wordninja` está instalado, também separa de modo conservador tokens minúsculos muito longos que parecem palavras coladas.

Também existe uma segunda camada experimental com Ollama:

```bash
./rato.sh limpar-ocr arquivo.md --com-ollama
```

Esse modo faz primeiro a limpeza determinística e depois pede ao modelo local uma revisão conservadora. Por padrão, ele salva `arquivo-limpo-ollama.md`. Use em textos muito ruidosos e confira o resultado, porque modelos podem corrigir demais. Para trocar o modelo:

```bash
./rato.sh limpar-ocr arquivo.md --com-ollama --modelo-ollama qwen2.5:7b
```

Para marcar trechos que parecem corrompidos demais para correção automática:

```bash
./rato.sh limpar-ocr arquivo.md --marcar-duvidas
```

Esse modo insere comentários pesquisáveis como `REVISAR OCR` antes de parágrafos suspeitos, sem alterar o trecho marcado. Pode ser combinado com Ollama:

```bash
./rato.sh limpar-ocr arquivo.md --com-ollama --marcar-duvidas
```

Quando combinado com Ollama, a marcação acontece antes da etapa semântica; blocos marcados com `REVISAR OCR` são preservados e não são enviados para correção pelo modelo.

Para chamar o Ollama apenas nos parágrafos que parecem suspeitos:

```bash
./rato.sh limpar-ocr arquivo.md --com-ollama --ollama-seletivo
```

Esse modo usa os mesmos heurísticos de `--marcar-duvidas`, mas tenta corrigir somente os blocos suspeitos. Se também usar `--marcar-duvidas`, todo bloco tocado pelo Ollama seletivo fica marcado para conferência humana, mesmo quando a saída parece limpa.

### 3. Roer textos: indexar e gerar fichas

Antes de roer um texto, vale catalogar seus metadados bibliográficos:

```bash
# Só mostra a prévia do YAML sugerido
./rato.sh catalogar arquivo.md

# Escreve o YAML no arquivo e cria backup em rato/backups/catalogar
./rato.sh catalogar arquivo.md --aplicar

# Catalogar uma leva pequena para revisão
./rato.sh catalogar . --limite 20
```

O catalogador tenta preencher `title`, `author`, `year`, `tipo`, `metadados-fonte`, `metadados-confianca` e `metadados-revisao`. Ele não sobrescreve campos existentes, a menos que você use `--forcar`. Quando autor ou ano forem incertos, marca `metadados-revisao: true`, para que o dado ajude o Ollama sem virar certeza falsa.

Ele também lê o próprio nome do arquivo. Em nomes como `bergson-materia-e-memoria.md`, `alcantara-cidade-e-alma-perspectivas-2017.md` ou `abraham-shaw-dynamics-the-geometry-of-behavior.md`, o script registra uma seção `metadados-arquivo` com `slug`, `autor`, `autor-candidato`, `titulo` e `ano`, conforme o caso. Isso preserva a pista bibliográfica do filename mesmo quando o OCR do começo do texto está cheio de capa, sumário, editora ou paratextos.

Quando esses campos existem, o `roer` usa `title`, `author` e `year` como identificação curta nos prompts de leitura e consolidação. A ficha continua usando o título normal, mas o modelo recebe mais contexto bibliográfico.

```bash
# Etapa 1: indexar biblioteca ampla
# Gera leituras brutas + embeddings, sem ficha final
python rato/scripts/roer.py \
  --modo indexar \
  --pasta . \
  --saida fichas \
  --leituras leituras-brutas \
  --modelo qwen2.5:7b \
  --modelo-embedding nomic-embed-text

# Etapa 2: fichar um texto já indexado
# Usa LEITURA_*.md existente e chama apenas o consolidator
python rato/scripts/roer.py \
  --modo fichar \
  --pasta . \
  --saida fichas \
  --leituras leituras-brutas \
  --arquivo jackson-rethinking-repair-media \
  --modelo-consolidacao qwen3:8b

# Fluxo completo antigo: leitura + embeddings + ficha
python rato/scripts/roer.py \
  --modo completo \
  --pasta . \
  --saida fichas \
  --leituras leituras-brutas \
  --modelo qwen2.5:7b \
  --modelo-embedding nomic-embed-text \
  --modelo-consolidacao qwen3:8b

# Forçar re-processamento (aproveita cache dos chunks)
python rato/scripts/roer.py ... --forcar

# Testar com N arquivos
python rato/scripts/roer.py ... --limite 3
```

Cada documento gera dois arquivos:
- `fichas/FICHA_<nome>.md` — ficha interpretativa para Obsidian
- `leituras-brutas/LEITURA_<nome>.md` — extração fiel por chunk

Quando embeddings estão ativos, também gera:
- `.embeddings/<nome>.jsonl` — embeddings de chunks originais e leituras brutas

Modos disponíveis:

| Modo | Faz o quê | Quando usar |
|------|-----------|-------------|
| `indexar` | leitura bruta + embeddings | indexar muitos arquivos rapidamente |
| `fichar` | ficha final a partir de `LEITURA_*.md` | fichar apenas textos relevantes |
| `completo` | leitura + embeddings + ficha | teste ou arquivo isolado |

No modo `fichar`, o script não refaz chunks, não chama embeddings e não relê o texto com `qwen2.5:7b`; ele usa a leitura bruta salva.

### 4. Buscar na biblioteca

```bash
python rato/scripts/farejar.py indexar --pasta .
python rato/scripts/farejar.py farejar "reparo manutenção cuidado"
python rato/scripts/farejar.py aprender "Jackson: tratar repair como cuidado material, não só sustentabilidade"
```

### 5. Manutenção da memória conceitual

```bash
# Higienizar autores e normalizar contagens/histórico
./rato.sh cuidar-memoria

# Chamada direta ao script interno
python rato/scripts/cuidar_memoria.py

# Aplicar correções manuais (edite rato/memoria/correcoes-memoria.json antes)
./rato.sh corrigir-memoria

# Alias antigo
./rato.sh memoria-corrigir
```

`cuidar-memoria` faz a limpeza automática. `corrigir-memoria` faz a mesma limpeza e, além disso, aplica as correções manuais definidas em `rato/memoria/correcoes-memoria.json`. Campos vazios nesse JSON são ignorados por segurança.

---

## Estrutura de diretórios

```
referencias-md-bruto/
├── *.md                        # fontes brutas
├── fichas/                     # saída: fichas Obsidian (FICHA_*.md)
├── leituras-brutas/            # saída: leituras por chunk (LEITURA_*.md)
├── rato.sh                     # atalho executável para usar na raiz da biblioteca
├── .embeddings/                # embeddings JSONL por documento
├── .cache_indexador/           # cache por hash (chunk + modelo + prompt)
└── rato/                       # projeto Rato: scripts, memória, logs e backups
    ├── bin/                    # executável local: ./rato/bin/rato
    ├── scripts/                # converter.py, limpar.py, roer.py e utilitários
    ├── memoria/                # memória conceitual e correções manuais
    ├── logs/                   # logs TSV de conversão/OCR
    └── backups/                # backups e arquivos de segurança
```

---

## Memória conceitual

O arquivo `rato/memoria/memoria-conceitos.json` acumula conceitos, autores e ocorrências entre sessões de indexação. É injetado no prompt de consolidação apenas na seção **Possíveis relações com minha pesquisa**.

Conceitos-âncora da pesquisa: `gesto`, `repair`, `precariedade`, `manutenção`, `cuidado`, `matéria`, `continuidade`.

---

## Notas sobre modelos

| Etapa | Modelo | Observação |
|-------|--------|------------|
| Leitura bruta (chunks) | `qwen2.5:7b` | rápido, fiel, bom para tarefa estruturada |
| Embeddings | `nomic-embed-text` | vetor 768; usado para busca semântica |
| Consolidação (ficha final) | `qwen3:8b` | melhor para humanidades; mais lento |

`qwen2.5-coder:7b` pode ser usado como consolidator rápido para comparação, mas `qwen3:8b` tem sido melhor em operadores, tensões e relações conceituais.

O cache é invalidado automaticamente ao trocar de modelo (hash inclui nome do modelo + prompt). Para re-gerar apenas a ficha final mantendo os chunks em cache, use `--forcar` sem mudar `--modelo`.

Os embeddings são gerados em sublotes para evitar erros no Ollama. Textos muito longos são truncados para embedding, preservando começo e fim; o hash do texto original continua salvo no `.jsonl`.

---

## Tecer relações semanticamente

```bash

# Apenas buscar trechos semanticamente próximos

python rato/scripts/tecer.py \

  --consulta "cuidado como manutenção de relações materiais" \

  --embeddings .embeddings \

  --somente-busca

# Buscar + gerar análise relacional em Markdown

python rato/scripts/tecer.py \

  --consulta "cuidado como manutenção de relações materiais" \

  --embeddings .embeddings \

  --saida relacoes

```

O script:

- gera embedding da consulta usando `nomic-embed-text`;

- compara semanticamente com os chunks indexados;

- recupera os trechos mais próximos da biblioteca;

- pede ao `qwen3:8b` uma análise relacional entre os textos;

- salva um `.md` interpretativo na pasta indicada em `--saida`.

Isso permite tecer aproximações conceituais entre autores mesmo quando eles não usam o mesmo vocabulário explícito.

# Scripts - O Rato

Coleção de scripts utilitários para processamento de arquivos markdown.

## `converter_notas.py`

### O que faz?

Converte notas de rodapé de arquivos markdown convertidos de PDF para o formato padrão markdown com referências footnote.

**Transforma:**
```
Texto com referência 1 .

- 1  Definição da nota um
```

**Em:**
```
Texto com referência[^1].

## Notas de Rodapé

[^1]: Definição da nota um
```

### Recursos

- ✅ **Pareamento automático**: primeira ocorrência de número = referência, segunda = definição
- ✅ **Múltiplos padrões suportados**:
  - Definições com traço: `- 4  Texto...`
  - Definições sem traço: `8  Texto...`
  - Referências antes de ponto: ` 4 .` → `[^4].`
  - Referências antes de vírgula: ` 4 ,` → `[^4],`
  - Referências gerais: `palavra 4` → `palavra[^4]`
- ✅ **Preserva original**: gera novo arquivo com sufixo `-notas`
- ✅ **Agrupa definições**: todas as notas ficam em seção `## Notas de Rodapé` no final

### Como usar

#### Um arquivo por vez:
```bash
python3 script/converter_notas.py arquivo.md
```

Gera: `arquivo-notas.md`

#### Vários arquivos:
```bash
python3 script/converter_notas.py arquivo1.md arquivo2.md arquivo3.md
```

Gera: `arquivo1-notas.md`, `arquivo2-notas.md`, `arquivo3-notas.md`

#### Todos os .md da pasta:
```bash
cd /Volumes/Documentos\ HD/Documento\ HD/Meu\ Trabalho/Estudos\ Acadêmicos/o-rato
python3 script/converter_notas.py *.md
```

### Exemplo

```bash
$ python3 script/converter_notas.py alcindor-correia-new-tools.md
✓ alcindor-correia-new-tools-notas.md
```

O arquivo original permanece intacto. O novo arquivo `-notas.md` contém todo o texto com as notas convertidas para o formato markdown padrão.

### Saída

Arquivo processado com:
- Referências convertidas para `[^n]` inline
- Definições agrupadas em seção `## Notas de Rodapé` no final
- Arquivo original preservado
