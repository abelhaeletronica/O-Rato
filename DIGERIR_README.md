# digerir.py — Modo Digestão do Ecossistema Rato

## Visão Geral

`digerir.py` implementa o **Modo Digestão** do Rato: uma metodologia de "Inquietação Organizada" que busca tensões, contradições, perguntas persistentes e opacidades em corpora textuais.

O script **não gera resumos ou relatórios executivos**. Sua função é identificar o que escapou à interpretação lógica, onde os autores se contradizem, e quais perguntas continuam em aberto.

## Requisitos

- Python 3.9+
- SQLite (banco `rato/biblioteca_referencias.sqlite`)
- Ollama rodando localmente (`http://localhost:11434`)
- Modelo `qwen2.5:14b` instalado no Ollama
- Biblioteca `requests` (para HTTP)

## Instalação

```bash
# Se não tiver requests instalado:
pip install requests
```

## Uso

### Modo 1: Fricção Consciente (--arquivos)

Analisa um ou mais arquivos `.md` (fichas ou farejadas):

```bash
python3 digerir.py --arquivos ficha1.md ficha2.md fichas/digestoes/*.md
```

O script:
1. Lê os arquivos especificados
2. Remove o frontmatter (linhas entre `---`)
3. Mantém apenas o conteúdo de corpo

### Modo 2: Ponto Cego (padrão)

Se nenhum arquivo for passado, o script busca os 4 chunks mais recentes do banco SQLite:

```bash
python3 digerir.py
```

### Argumentos Adicionais

- `--dias N`: Busca apenas documentos atualizados nos últimos N dias (padrão: 30)
- `--salvar`: Salva o resultado em `fichas/digestoes/digestao_AAAA-MM-DD.md`

### Exemplos Completos

```bash
# Análise de Ponto Cego dos últimos 7 dias, salvando resultado
python3 digerir.py --dias 7 --salvar

# Fricção Consciente com dois arquivos, salvando
python3 digerir.py --arquivos ficha_a.md ficha_b.md --salvar

# Análise dos últimos 60 dias, output na tela
python3 digerir.py --dias 60
```

## Processo de Mineração

O script sempre executa as seguintes etapas (independentemente do modo):

### 1. Temas Persistentes
- Busca documentos atualizados nos últimos `--dias` dias
- Extrai `tags` e `palavras-chave` do campo `meta_json`
- Conta ocorrências de cada termo
- Isola temas que aparecem **mais de uma vez** (obsessões temporais)

### 2. Perguntas Órfãs
- Busca chunks de texto contendo:
  - Interrogações (`?`)
  - Palavras-chave como "pergunta", "aberta", "emergente"
- Limita a 15 resultados
- Objetivo: identificar questões sem resolução

### 3. Recortes de Texto
- **Ficção Consciente**: conteúdo dos arquivos especificados
- **Ponto Cego**: 4 chunks mais recentes do banco (últimos `--dias` dias)

## Output: Estrutura do Relatório de Digestão

O Ollama gera um relatório com 4 seções obrigatórias:

### 1. O Mapa dos Tropeços (Opacidade como Método)
Onde a lógica formal falha. Quais metáforas resistem à interpretação? Onde o modelo "tropeçou"?

### 2. Contextos Incompatíveis e Atritos
Conceitos que aparecem em contextos contraditórios. Autores que desorganizam uns aos outros.

### 3. Perguntas Insistentes (Inquietação Organizada)
Qual pergunta reaparece sob nomes diferentes? O que continua sem solução?

### 4. Hipóteses em Colapso
Que certeza do pesquisador está sendo silenciosamente contradita pelos textos?

## Saída e Salvamento

### Com --salvar
Cria `fichas/digestoes/digestao_AAAA-MM-DD.md` com:
- Frontmatter metadado (título, tipo, data, modelo)
- Output bruto do modelo

### Sem --salvar
Imprime no terminal com delimitadores visuais (`═══`).

## Exemplo de Arquivo Salvo

```markdown
---
titulo: "Digestão: 2026-06-03"
tipo: digestao
data: "2026-06-03"
modelo: qwen2.5:14b
---

## 1. O Mapa dos Tropeços (Opacidade como Método)
[resposta do modelo]

## 2. Contextos Incompatíveis e Atritos
[resposta do modelo]

...
```

## Troubleshooting

### "✗ Erro: Não conseguiu conectar ao Ollama"
- Verifique se Ollama está rodando: `curl http://localhost:11434/api/tags`
- Verifique se o modelo `qwen2.5:14b` está disponível

### "✗ Erro ao ler {arquivo}"
- Verifique se o arquivo existe e é legível
- Verifique a codificação (UTF-8 esperado)

### Arquivo não salvo
- Verifique permissões de escrita em `fichas/`
- A pasta `fichas/digestoes/` será criada automaticamente

## Estrutura do Banco SQLite

- **Tabela `documentos`**: `id`, `caminho`, `titulo`, `tipo`, `meta_json`, `atualizado_em`
- **Tabela `chunks`**: `id`, `documento_id`, `texto`, `parte`

O script consulta estas tabelas para alimentar o análise.

## Filosofia

O script recusa:
- Sínteses amigáveis
- Consensos forçados
- Interpretações unívocas

O script busca:
- Tensões
- Contradições
- Opacidade
- Mal-estar conceitual
- Incompletude

---

**Criado para o Ecossistema Rato**
Metodologia de Inquietação Organizada
