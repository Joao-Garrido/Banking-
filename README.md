# ms-parser — parser de statements Morgan Stanley

`python parse.py statement.pdf` → CSV por secção, reconciliado contra os totais
impressos no próprio documento. Se algum total não bater, o comando falha e não
escreve nada.

**Princípio:** dado financeiro errado é pior que dado financeiro ausente. Tudo
aqui existe para tornar o erro silencioso impossível — daí a tolerância de
reconciliação ser zero, os valores serem `Decimal` e um valor irreconhecível
levantar exceção em vez de virar `0`.

---

## Setup

```bash
pip install -r requirements.txt
pytest                      # 93 testes, sem PDF nenhum é preciso
```

## Comandos

```bash
# Fase 1 — gerar o gabarito de coordenadas (uma vez por desenho de statement)
python -m src.mapper statement.pdf --out layout.json
python -m src.mapper statement.pdf --sample 11      # amostra de extract_words da pág. 11

# Fase 0 — o gate
python verify.py                                    # todas as fixtures de tests/expected.json
python verify.py --section holdings
python verify.py --layout tests/fixtures/layout.synthetic.json

# Fase 4 — produção
python parse.py statement.pdf --out out/
```

`verify.py` e `parse.py` saem com código ≠ 0 quando alguma conferência falha.
`parse.py` só escreve CSV depois de tudo bater; com `--force` escreve à mesma,
mas os ficheiros levam o sufixo `.PARTIAL`.

---

## Estado por fase

| Fase | Estado |
|---|---|
| 0 — arnês (`verify.py`, `expected.json`) | **feito** |
| 1 — pré-check e mapper | **feito** (mecanismo); `layout.json` do statement real ainda por rever à mão |
| 2 — núcleo (`lines`, `normalize`, `router`) | **feito**, com testes unitários |
| 3 — secções | **framework feito**, colunas reais por afinar — uma sessão por secção |
| 4 — entrypoint | **feito** |
| 5 — robustez multi-documento | por fazer (falta um 2.º período) |
| 6 — congelamento | por fazer |

O ciclo completo (PDF → secções → reconciliação → CSV) está fechado e verde
sobre `tests/fixtures/synthetic_statement.pdf`, um documento gerado por
`tests/make_synthetic_fixture.py` que traz de propósito parênteses negativos,
descrição partida em duas linhas e uma secção a atravessar a virada de página.

---

## Estrutura

```
parse.py                    entrypoint
verify.py                   arnês de reconciliação — o gate
layout.json                 gabarito de coordenadas (gerado pelo mapper, revisto à mão)
src/
  precheck.py               tem camada de texto?
  mapper.py                 gera o layout.json
  lines.py                  words → linhas visuais → registos
  normalize.py              (1.234,56) → -1234.56, datas, moeda
  router.py                 página → secção, página → conta
  reconcile.py              vocabulário das conferências
  pipeline.py               PDF + layout → secções
  sections/
    base.py                 extração genérica guiada pelo layout
    holdings.py activity.py income.py fees.py
tests/
  expected.json             totais digitados à mão — o gabarito
  fixtures/                 statements
```

---

## O contrato: `layout.json`

Gerado uma vez, revisto a olho, versionado. Nunca se relê o header da página em
runtime — as colunas vêm daqui, e é por isso que a virada de página não desloca
nada.

```jsonc
{
  "version": 2,
  "statement_year": 2016,          // para as datas que o documento imprime como "12/8"
  "body_band": {"top": 64.4, "bottom": 611.0},
  "accounts": [                    // statement consolidado: página → conta
    {"id": "316-0000XX-042", "label": "Active Assets Account", "pages": [9, 10, 11]}
  ],
  "sections": {
    "holdings": {
      "present": true,
      "pages": [11, 12, 13],
      "columns": [
        {"name": "description", "x0": 37, "x1": 287, "type": "text"},
        {"name": "market_value", "x0": 438, "x1": 523, "type": "amount"}
      ],
      "amount_column": "market_value",
      "row_start": {"kind": "numeric_in", "column": "market_value"},
      "total_label_patterns": ["^Total\\s+Holdings"],
      "ignore_patterns": ["continued"]
    }
  }
}
```

Tipos de coluna: `text`, `amount`, `quantity`, `percent`, `date`, `currency`.
`"optional": true` numa coluna permite célula vazia; sem isso, vazio é erro.
`row_start.kind`: `date` (linha começa por data), `numeric_in` (tem valor naquela
coluna) ou `regex`.

Uma secção que não existe naquele período leva `"present": false` — e o router
diz isso em voz alta em vez de devolver uma secção vazia.

---

## O que fazer quando falha

| Sintoma | Causa provável | O que mexer |
|---|---|---|
| `soma vs total impresso` falha por ~2× o valor de uma linha | parênteses lidos como positivo | `normalize.parse_amount` + teste dedicado |
| colunas deslocadas a partir da página N | faixa x errada | `columns` no `layout.json` (nunca reler header) |
| `linha tratada como continuação mas com valores` | `row_start` não reconhece o início do registo | `row_start` da secção |
| `total fora por um fator estranho` | mais de uma moeda na mesma tabela | coluna `currency` na secção; o check "moeda única" já denuncia |
| `secção X não encontrada` | título diferente neste período | `SECTION_TITLE_PATTERNS` no mapper, re-gerar layout |
| `data sem ano: '12/8'` | tabela de atividade sem ano | `statement_year` no `layout.json` |
| tudo falha de uma vez | o banco mudou o desenho | re-correr `mapper.py`, gerar novo `layout.json` |

---

## Achados do statement real (Dezembro 2016, 50 páginas)

O documento de exemplo obrigou a três mudanças estruturais, todas já feitas:

1. **É consolidado, com duas contas** (`Active Assets Account`, págs. 9–18, e
   `Select UMA Active Assets Account`, págs. 19–50). O modelo plano
   "secção → páginas" somaria posições de duas contas num total que não existe
   em lado nenhum do documento. Daí `accounts` no layout: cada página pertence a
   uma conta, cada registo sai marcado com a sua, e duas contas a reclamar a
   mesma página é erro de layout.
2. **Marca d'água vertical na margem** (texto rodado a x≈12) partilha o `y` com
   as linhas das tabelas. `lines.py` descarta texto não-vertical por omissão.
3. **Datas sem ano** na atividade (`12/8`). `parse_date` só as aceita com
   `statement_year` — adivinhar o ano seria inventar dados.

**Por fazer (Fase 3, uma sessão por secção):** as secções reais têm sub-tabelas
com conjuntos de colunas diferentes — `HOLDINGS` contém `CASH, BANK DEPOSIT
PROGRAM AND MONEY MARKET FUNDS`, `MUTUAL FUNDS`, `STOCKS` e
`EXCHANGE-TRADED & CLOSED-END FUNDS`, cada uma com o seu header. O mapper
propõe hoje um único conjunto de colunas por secção; a próxima sessão deve
tratar cada sub-tabela como uma secção própria no layout. Ordem sugerida:
holdings da conta `Active Assets` (págs. 11–15, reconciliação contra
`TOTAL VALUE (includes accrued interest)` na pág. 15), e só depois a seguinte.

---

## Privacidade

`tests/fixtures/real/` **não é versionado** — statements reais têm nomes,
números de conta e saldos. `out/` também não. O único statement no repositório é
o sintético, que não corresponde a ninguém.
