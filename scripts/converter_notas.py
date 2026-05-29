import re
import sys
from pathlib import Path
from collections import defaultdict

def converter_notas_com_pareamento(texto):
    linhas = texto.split('\n')
    notas_processadas = {}
    ocorrencias_por_num = defaultdict(list)
    linhas_para_remover = set()
    
    # 1. Encontrar definições explícitas - mais flexível
    for idx, linha in enumerate(linhas):
        # Padrão 1: "- N espaço(s) TEXTO"
        match = re.search(r'^-\s+(\d+)\s+(.+)$', linha)
        if match:
            num = match.group(1)
            texto_def = match.group(2).strip()
            ocorrencias_por_num[num].append(('def_expl', idx, texto_def))
            continue
        
        # Padrão 2: "N espaço(s) TEXTO" (início da linha)
        match = re.search(r'^(\d+)\s+(.+)$', linha)
        if match:
            num = match.group(1)
            texto_def = match.group(2).strip()
            # Verifica se não é um número de página ou número isolado
            # Se o número é pequeno (1-3 dígitos) e tem texto, provavelmente é nota
            if len(num) <= 3 and len(texto_def) > 10:
                ocorrencias_por_num[num].append(('def_expl', idx, texto_def))
                continue
    
    # 2. Encontrar referências
    for idx, linha in enumerate(linhas):
        # Padrão: " N ."
        for match in re.finditer(r'\s(\d+)\s+\.', linha):
            num = match.group(1)
            if num not in notas_processadas:
                notas_processadas[num] = {
                    'tipo': 'ponto',
                    'linha_idx': idx,
                    'match': match,
                    'def_texto': ''
                }
        
        # Padrão: " N ,"
        for match in re.finditer(r'\s(\d+)\s+,', linha):
            num = match.group(1)
            if num not in notas_processadas:
                notas_processadas[num] = {
                    'tipo': 'virgula',
                    'linha_idx': idx,
                    'match': match,
                    'def_texto': ''
                }
        
        # Padrão: "palavra N " ou fim
        for match in re.finditer(r'([\w\'\")\]]) (\d+)(\s|$)', linha):
            num = match.group(2)
            if num not in notas_processadas:
                notas_processadas[num] = {
                    'tipo': 'geral',
                    'linha_idx': idx,
                    'match': match,
                    'grupos': (match.group(1), match.group(3)),
                    'def_texto': ''
                }
    
    # 3. Associar definições
    for num in ocorrencias_por_num:
        def_expl = [o for o in ocorrencias_por_num[num] if o[0] == 'def_expl']
        if def_expl and num in notas_processadas:
            texto_def = def_expl[0][2]
            def_idx = def_expl[0][1]
            notas_processadas[num]['def_texto'] = texto_def
            linhas_para_remover.add(def_idx)
    
    # 4. Aplicar conversões por linha (de trás para frente)
    linhas_editadas = linhas.copy()
    
    conversoes_por_linha = defaultdict(list)
    for num, info in notas_processadas.items():
        if info['def_texto']:
            conversoes_por_linha[info['linha_idx']].append((num, info))
    
    for idx in sorted(conversoes_por_linha.keys()):
        linha = linhas_editadas[idx]
        conversoes = conversoes_por_linha[idx]
        
        # Ordena de trás para frente
        conversoes_ord = sorted(conversoes, key=lambda x: x[1]['match'].start(), reverse=True)
        
        for num, info in conversoes_ord:
            match = info['match']
            tipo = info['tipo']
            
            if tipo == 'ponto':
                linha = linha[:match.start()] + f'[^{num}].' + linha[match.end():]
            elif tipo == 'virgula':
                linha = linha[:match.start()] + f'[^{num}],' + linha[match.end():]
            elif tipo == 'geral':
                g1, g3 = info['grupos']
                linha = linha[:match.start()] + f'{g1}[^{num}]{g3}' + linha[match.end():]
        
        linhas_editadas[idx] = linha
    
    # 5. Remover definições originais
    linhas_editadas = [l for i, l in enumerate(linhas_editadas) if i not in linhas_para_remover]
    
    # 6. Adicionar seção final
    if notas_processadas:
        resultado_linhas = linhas_editadas + ['', '## Notas de Rodapé', '']
        
        for num in sorted(notas_processadas.keys(), key=lambda x: int(x)):
            def_texto = notas_processadas[num]['def_texto']
            if def_texto:
                resultado_linhas.append(f'[^{num}]: {def_texto}')
        
        return '\n'.join(resultado_linhas)
    
    return '\n'.join(linhas_editadas)

def processar_arquivo(caminho):
    path = Path(caminho)
    texto = path.read_text(encoding='utf-8')
    resultado = converter_notas_com_pareamento(texto)
    
    nome_original = path.stem
    novo_nome = f"{nome_original}-notas{path.suffix}"
    novo_caminho = path.parent / novo_nome
    
    novo_caminho.write_text(resultado, encoding='utf-8')
    print(f"✓ {novo_caminho}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Uso: python converter_notas_v4.py arquivo1.md arquivo2.md ...")
        sys.exit(1)
    for arquivo in sys.argv[1:]:
        processar_arquivo(arquivo)
