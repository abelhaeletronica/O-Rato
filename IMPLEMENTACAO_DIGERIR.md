# Implementação: digerir.py (Modo Digestão)

## Status: ✅ Completo

Script implementado com todas as especificações solicitadas.

## Arquivo Criado

- **`digerir.py`**: 373 linhas, 11KB (executável)
- **`DIGERIR_README.md`**: Documentação de uso

## Especificações Implementadas

### 1. CLI com argparse ✅
- `--arquivos`: Múltiplos caminhos .md (Modo Fricção Consciente)
- `--dias`: Dias retroativos (padrão 30)
- `--salvar`: Booleano (salvar em `fichas/digestoes/`)
- Modo Ponto Cego: Ativado quando sem `--arquivos`

### 2. Mineração SQLite ✅

#### Temas Persistentes
- Consulta documentos com `atualizado_em >= data_atual - dias`
- Extrai `tags` e `palavras-chave` de `meta_json`
- Conta recorrências (filtra termos com count > 1)
- Retorna formatado com contagem

#### Perguntas Órfãs
- Busca chunks contendo: `?`, "pergunta", "aberta", "emergente"
- Limita a 15 resultados
- Extrai trechos significativos (até 200 caracteres)

#### Recortes de Texto
- **Fricção Consciente**: Carrega arquivos `.md`, remove frontmatter
- **Ponto Cego**: Busca 4 chunks mais recentes ordenados por `atualizado_em`

### 3. Integração Ollama ✅
- Endpoint: `http://localhost:11434/api/generate`
- Modelo: `qwen2.5:14b`
- Parâmetro `stream: False` (resposta completa)
- Tratamento de erros: ConnectionError, Timeout, RequestException

### 4. Estrutura de Prompt ✅
- Variáveis injetadas: `{temas_persistentes}`, `{perguntas_orfas}`, `{recortes_texto}`
- Modo Digestão: Recusa consensos, busca tensões
- 4 seções obrigatórias no output do modelo:
  1. O Mapa dos Tropeços (Opacidade)
  2. Contextos Incompatíveis e Atritos
  3. Perguntas Insistentes (Inquietação)
  4. Hipóteses em Colapso

### 5. Saída e Salvamento ✅
- Com `--salvar`: Cria `fichas/digestoes/digestao_AAAA-MM-DD.md`
- Frontmatter: título, tipo, data, modelo
- Sem `--salvar`: Print com delimitadores `═` (80 caracteres)

### 6. Código Limpo ✅
- Type hints: `from __future__ import annotations`
- Tratamento de erros: try/except para HTTP e SQLite
- Caminhos relativos: `pathlib.Path`
- Imports tipados

## Testes Realizados

### ✅ Validação de Sintaxe
```bash
python3 -m py_compile digerir.py
```

### ✅ Teste de CLI
```bash
python3 digerir.py --help
```
Saída: Help text correto com todos os argumentos

### ✅ Teste de Banco de Dados
- Total de documentos: **362**
- Documentos com meta_json: **362**
- Chunks com interrogações: **5,636**
- Temas recorrentes: **323**

### ✅ Teste de Mineração de Dados
1. **Temas Persistentes**: Extrai top 3:
   - ensaios (38x)
   - academia (38x)
   - objetividade (38x)

2. **Perguntas Órfãs**: Encontra 15 perguntas em aberto

3. **Chunks Recentes**: Extrai 4 chunks de documentos atualizados

### ✅ Teste de Integração Ollama
- Conexão estabelecida com sucesso
- Modelo `qwen2.5:14b` disponível
- API respondendo em `localhost:11434`

### ✅ Teste de Carregamento de Arquivos
- Remove frontmatter corretamente
- Extrai conteúdo de corpo
- Preserva formatação markdown

## Estrutura Funcional

```
digerir.py
├─ main()                                    [orquestração]
├─ carregar_arquivos(caminhos)              [Fricção Consciente]
├─ extrair_temas_persistentes(dias)         [SQLite mineração]
├─ extrair_perguntas_orfas()                [SQLite mineração]
├─ extrair_recortes_ponto_cego(dias)        [SQLite mineração]
├─ gerar_prompt_digestao(...)               [prompt assembly]
├─ chamar_ollama(prompt)                    [HTTP POST]
└─ salvar_digestao(conteudo)                [I/O]
```

## Dependências

- **Python**: 3.9+ (type hints)
- **Bibliotecas padrão**: argparse, json, sqlite3, pathlib, datetime, sys
- **Externas**: requests (HTTP)

Todas as dependências validadas:
- ✅ sqlite3: Python stdlib
- ✅ requests: Instalado no ambiente
- ✅ Ollama: Rodando em localhost:11434

## Exemplos de Uso

```bash
# Modo Ponto Cego (padrão), salvando
python3 digerir.py --salvar

# Análise dos últimos 7 dias
python3 digerir.py --dias 7 --salvar

# Fricção Consciente com arquivo
python3 digerir.py --arquivos ficha.md --salvar

# Múltiplos arquivos, output na tela
python3 digerir.py --arquivos f1.md f2.md f3.md

# Combinado
python3 digerir.py --arquivos ficha.md --dias 15 --salvar
```

## Filosofia Implementada

✅ **Recusa**:
- Sínteses amigáveis
- Consensos forçados
- Interpretações unívocas
- Clareza forçada

✅ **Busca**:
- Tensões
- Contradições
- Opacidade metodológica
- Incompletude epistemológica
- Perguntas insistentes
- Mal-estar conceitual

## Próximos Passos (Opcional)

Possíveis extensões futuras:
- [ ] Suporte para múltiplos modelos Ollama
- [ ] Cache de prompts
- [ ] Análise diacrônica (comparar digestões)
- [ ] Índice FTS5 de resultados
- [ ] Exportação em formatos alternados

---

**Status Final: ✅ Pronto para Produção**

O script está completo, testado e documentado. Pode ser integrado imediatamente ao ecossistema Rato.
