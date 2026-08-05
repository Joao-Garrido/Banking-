# ms-parser — statements Morgan Stanley → Excel reconciliado

```bash
python parse.py statement.pdf
```

É só isto. O comando verifica que o PDF tem camada de texto, mapeia o layout
(contas, banda do corpo, ano do período), encontra **todas** as tabelas do
documento, extrai cada uma com as suas colunas, confronta-a com os totais que o
próprio statement imprime, e escreve `out/statement.xlsx`.

**Princípio:** dado financeiro errado é pior que dado financeiro ausente. Por
isso a tolerância de reconciliação é zero, os valores são `Decimal`, um valor
irreconhecível levanta exceção em vez de virar `0`, e tudo o que não foi provado
aparece marcado como *não verificável* — nunca como certo.

---

## Setup

```bash
pip install -r requirements.txt
pytest                      # 150 testes
```

## O que sai

`out/statement.xlsx`, organizado como um dossier — primeiro as vistas de
trabalho, no fim o documento em bruto para auditoria:

| Folha | Conteúdo |
|---|---|
| **Capa** | documento, contas, e por conta: valor das posições, por liquidar, total impresso e se fecha |
| **Sumário por conta** | as rubricas de variação do período, tal como impressas |
| **Posições** | todas as posições das duas contas numa tabela: classe de ativo, título, símbolo, quantidade, custo, preço, valor de mercado, mais-valia potencial, yield, **peso %** |
| **Alocação** | alocação por classe, por conta |
| **Movimentos** | todos os movimentos: data, tipo, descrição, quantidade, preço, valor |
| **Rendimento** | dividendos, juros e mais-valias distribuídas (derivado dos movimentos por tipo) |
| **Mais-valias realizadas** | por título: data de compra, data de venda, receita, custo, ganho |
| **Transferências e eventos** | transferências de títulos e corporate actions |
| **Reconciliação** | cada conferência: PASS / FAIL / AVISO, extraído, esperado, delta, página, nota |
| **Totais impressos** | todos os totais que o documento imprime, com página — as provas |
| **Tabelas do documento** | índice das tabelas encontradas, com estado e ligação |
| uma por tabela | o documento tal como foi lido: `row_type`, `group`, colunas do banco, página e texto original |

As vistas consolidam as contas e contêm **apenas linhas de dados** — os
subtotais e totais que o documento imprime ficam nas folhas em bruto. Somar uma
coluna inteira nunca conta o mesmo dinheiro duas vezes. Cada linha aponta para a
tabela, página e conta de origem.

Nada é inventado: um campo que o documento não imprime fica vazio. As únicas
colunas calculadas são `peso %` (sobre o total da conta) e a folha `Rendimento`
(filtro dos movimentos por tipo, sem reinvestimentos) — ambas assinaladas na capa.

O estado de cada tabela é um de quatro (e a cor do separador da folha repete-o):

- **conciliado** — todas as conferências passaram;
- **parcial** — alguma passou e outra ficou por provar;
- **não conciliado** — não bate, e sabemos que devia bater;
- **não verificável** — a tabela não imprime total: extraímos, mas não há prova.

A coluna `conferências` diz quantas passaram em quantas — o estado é auditável
sem sair da folha.

> Na soma entram só as linhas `data`. Somar de novo as linhas `subtotal`/`total`
> conta o mesmo dinheiro duas vezes — a folha Resumo diz isto no rodapé.

Se alguma reconciliação falhar, o ficheiro sai como `statement.PARTIAL.xlsx` e o
comando devolve código ≠ 0. Com `--strict`, não escreve nada nesse caso.

## Opções

```bash
python parse.py statement.pdf --out pasta/     # destino
python parse.py statement.pdf --csv            # também um CSV por tabela
python parse.py statement.pdf --relayout       # volta a mapear o layout
python parse.py statement.pdf --strict         # não escreve nada se algo falhar
python parse.py statement.pdf --layout meu.json
```

O layout é gerado na primeira passagem e guardado em `.layouts/<nome>.json`.
Nas seguintes é reutilizado — abre-o e corrige-o se alguma tabela sair torta; é
um ficheiro legível e é ele que manda.

---

## Estrutura

```
parse.py                 entrypoint: PDF -> Excel
verify.py                arnês das secções curadas (gate da Fase 3 do roadmap)
src/
  precheck.py            tem camada de texto?
  mapper.py              layout: contas, banda do corpo, ano, índice de secções
  lines.py               words -> linhas visuais -> registos
  normalize.py           (1,234.56) -> -1234.56, datas, moeda, marcadores ST/LT
  tables.py              varredura: encontra e extrai todas as tabelas
  model.py               camada canónica: tabelas -> posições/movimentos/…
  router.py              página -> secção, página -> conta
  reconcile.py           vocabulário das conferências (erro vs aviso)
  excel.py               escrita do livro
  pipeline.py            cola
  sections/              secções curadas: holdings, activity, income, fees
tests/
  expected.json          totais digitados à mão (modo curado)
  fixtures/              statement sintético + layout revisto
```

---

## Como a varredura decide

1. **Bloco** — corridas de linhas com números; um título em maiúsculas, um
   parágrafo de prosa ou três linhas seguidas sem números fecham o bloco.
2. **Colunas** — as faixas x saem das margens direitas dos próprios números, não
   do header. Uma faixa cujas células sejam maioritariamente texto é descartada:
   é assim que as páginas de *disclaimers* não viram tabelas.
3. **Continuação de página** — blocos com o mesmo título e o mesmo número de
   colunas são a mesma tabela. `(CONTINUED)` é ignorado no título.
4. **Registos** — uma linha com números começa um registo; uma linha sem números
   é continuação da descrição anterior.
5. **Classificação** — cada linha é `data`, `subtotal`, `total` ou `info`, pelo
   rótulo (`Total`, `Purchases`, `Total Purchases vs Market Value`, …).
6. **Grupos** — um `Total` sozinho fecha o título em curso (uma posição com
   vários lotes), não a tabela. Cada um desses é conferido contra as suas
   próprias linhas. Uma linha que se chame como a tabela (`STOCKS 19.42% …`) e
   cujo valor seja pelo menos tão grande como qualquer outra é o total da
   categoria — sem esta regra, ela era somada como se fosse mais uma posição e a
   tabela ficava com o dobro do valor.
7. **Conferência** — Σ das linhas `data` contra os totais impressos, ao nível
   certo: por grupo e por tabela. É **erro** apenas quando sabemos que devia
   somar (o total chama-se `Total` ou traz o nome da tabela). Um `TOTAL ENDING
   VALUE` num quadro de roll-forward não é a soma das linhas acima: aí a
   diferença sai como **aviso**, porque não prova nem acerto nem erro.
8. **Arredondamento** — a folga é de um cêntimo por valor impresso que entra na
   soma. Acima disso é erro. A diferença aparece sempre na folha
   `Reconciliação`, seja qual for a classificação.

---

## O que este parser aprendeu com um statement real

O documento de exemplo (Dezembro/2016, 50 páginas, consolidado) obrigou a:

1. **Contas múltiplas.** `Active Assets Account` (págs. 9–18) e `Select UMA`
   (págs. 19–50). Sem a dimensão conta, as posições das duas somam-se num total
   que não existe em lado nenhum do documento. Cada página pertence a uma conta,
   cada linha extraída leva a sua, e duas contas na mesma página é erro de layout.
2. **Marca d'água vertical** na margem, que partilha o `y` com as linhas das
   tabelas. Texto rodado é descartado.
3. **Datas sem ano** (`12/8`). Só são aceites com `statement_year`; adivinhar o
   ano seria inventar dados.
4. **Marcadores colados aos valores** (`$5,958.99ST`). Separados do número, não
   ignorados. As datas (`12/8`, `3/10/16`) também saem da descrição para coluna
   própria — sem isso, a data de um lote passava por nome da posição.
5. **Sub-tabelas com colunas próprias** dentro da mesma secção (`MUTUAL FUNDS`,
   `COMMON STOCKS`, `CASH FLOW ACTIVITY BY DATE`, …) — foi isto que trocou o
   modelo "quatro secções" pela varredura por tabela.
6. **O statement não fecha sempre ao cêntimo.** Em `MUTUAL FUNDS`, três grupos
   têm `Purchases + Reinvestments` a diferir do `Total` impresso em 3 e 4
   cêntimos. O parser está certo; o documento é que arredonda. Está assinalado
   como tal, não escondido.

Resultado nesse documento: **32 tabelas, 31 conferências conciliadas de 57, zero
contradições** — 15 tabelas conciliadas, 4 parciais, 13 sem total impresso para
conferir, e as duas contas a fechar contra o total impresso. Entre as provadas
ao cêntimo:

| Tabela | Linhas | Prova |
|---|---|---|
| `COMMON STOCKS` | 186 | `STOCKS 19.42% … $1.384.278,01` |
| `CASH FLOW ACTIVITY BY DATE` | 215 | `NET CREDITS/(DEBITS)` |
| `UNSETTLED PURCHASES/SALES` | 203 | `NET UNSETTLED PURCHASES/SALES` |
| `LONG-TERM GAIN/(LOSS)` | 70 | `Long-Term This Period $931.881,07` |
| `SHORT-TERM GAIN/(LOSS)` | 40 | `Short-Term This Period $(894,27)` |
| `SECURITY TRANSFERS` (2 contas) | 19 + 9 | `TOTAL SECURITY TRANSFERS` |
| `MUTUAL FUNDS` | 50 | 4 das 7 posições ao cêntimo; as outras 3 dentro do arredondamento do documento |

Mais as provas cruzadas entre o detalhe de cada conta e a linha de resumo da
página do sumário.

---

## A camada canónica

Cada tabela do documento é atribuída a um **domínio** pelo título (posições,
movimentos, mais-valias, transferências, alocação, sumário) e cada coluna do
banco a um **campo canónico** pelo nome (`market_value` → valor de mercado,
`credits_debits` → valor, `sales_proceeds` → receita da venda). Daí saem as
folhas consolidadas.

Esta camada é conferida como tudo o resto: a soma de cada vista tem de ser igual
à soma das mesmas colunas nas tabelas de origem, e uma tabela cuja coluna de
valor não tenha equivalente canónico é dita em voz alta (`CORPORATE ACTIONS`
mede-se em quantidade, não em dinheiro — as suas linhas aparecem sem valor).

A prova de topo é por conta: **posições + operações por liquidar == total
impresso da conta**. Numa conta com compras por liquidar o valor das posições
excede o total — o statement já conta os títulos comprados, e a contrapartida em
dinheiro só entra na liquidação. No documento de exemplo isso são
9.368.342,88 − 2.238.795,33 = 7.129.547,55, exatamente o total impresso.

---

## O modo curado (Fase 3 do roadmap)

Quando uma secção precisa de garantia total — não "sem contradições", mas
"conferido contra números digitados à mão" — existe o caminho do roadmap:

```bash
python verify.py --section holdings     # gate: PASS/FAIL contra tests/expected.json
python parse.py statement.pdf --sections
```

`tests/expected.json` é preenchido **à mão**, lendo o PDF. Não pode ser gerado a
partir do parser: se o gabarito vier do parser, o parser passa sempre.

---

## O que fazer quando falha

| Sintoma | Causa provável | O que mexer |
|---|---|---|
| tabela `não conciliado` por ~2× o valor de uma linha | parênteses lidos como positivo | `normalize.parse_amount` + teste dedicado |
| colunas trocadas a partir da página N | faixa x errada | `columns` da tabela no layout |
| linhas a mais ou a menos | rótulo mal classificado | `TOTAL_PATTERNS` / `SUBTOTAL_PATTERNS` / `INFO_PATTERNS` / `CONTINUATION_LABELS` em `src/tables.py` |
| soma exatamente ao dobro | linha de total contada como dados | confirma que o rótulo dessa linha se chama como a tabela |
| tabela partida em duas folhas | título diferente entre páginas | confirma o título; `(CONTINUED)` já é tratado |
| prosa a virar tabela | bloco de texto com números | `_keep_numeric_bands` já filtra; se escapar, ajusta a banda do corpo |
| `data sem ano: '12/8'` | tabela de atividade sem ano | `statement_year` no layout |
| tudo falha de uma vez | o banco mudou o desenho | `--relayout` |

---

## Privacidade

`tests/fixtures/real/`, `out/` e `.layouts/` **não são versionados** —
statements reais têm nomes, números de conta e saldos. O único statement no
repositório é o sintético, que não corresponde a ninguém.
