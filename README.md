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

## Começar

Requer Python 3.10 ou superior.

```bash
git clone https://github.com/Joao-Garrido/Banking-.git
cd Banking-

python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

pytest                                  # 187 testes; 1 salta (precisa de um statement real)
python parse.py tests/fixtures/synthetic_statement.pdf   # experimenta no exemplo incluído
```

O último comando escreve `out/synthetic_statement.xlsx` e mostra
`Sem contradições entre o extraído e os totais impressos` — é assim que se
confirma que a instalação está boa antes de apontar para um documento a sério.

Depois, o teu statement:

```bash
python parse.py /caminho/para/statement.pdf
```

A primeira passagem de cada documento mapeia o layout (~15 s numas 50 páginas) e
guarda-o em `.layouts/`; as seguintes reaproveitam-no.

## O que sai

`out/statement.xlsx`, organizado como um dossier — primeiro o que se carrega no
sistema, depois as vistas de trabalho, no fim o documento em bruto para auditoria:

| Folha | Conteúdo |
|---|---|
| **Capa** | documento, contas, e por conta: valor das posições, por liquidar, total impresso e se fecha |
| **POSICOES** | layout do consolidador: `IDCLIENTE`, `IDATIVO`, `DESCRICAO_ATIVO`, `MOEDA`, `DT_POSICAO`, `QUANTIDADE`, `PU`, `VLR_CUSTO_MOEDA_ORIGINAL`, `VLR_BRUTO_MOEDA_ORIGINAL`, `VALOR_IR`, `VALOR_IOF`, `VALOR_LIQUIDO`, … |
| **MOVIMENTOS** | `DT_MOVIMENTO`, `TIPO_MOVIMENTO` (A, RP, JUROS, DIV, TRF, TX…), `TIPO_ORIGINAL`, `IDATIVO`, `QUANTIDADE`, `PU`, `VALOR` |
| **De-para** | campo a campo: de onde veio, o que ficou vazio e porquê, e a tradução dos tipos de movimento |
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
  export.py              layout do consolidador + fundo exclusivo
  diagnostico.py         relatório de estrutura, sem valores nem nomes
  router.py              página -> secção, página -> conta
  reconcile.py           vocabulário das conferências (erro vs aviso)
  excel.py               escrita do livro
  pipeline.py            cola
  sections/              secções curadas: holdings, activity, income, fees
exemplos/
  clientes.json          conta -> idcliente (modelo)
  fundos-exclusivos.json carteira dos fundos exclusivos (modelo)
tests/
  expected.json          totais digitados à mão (modo curado)
  make_ms2026_fixture.py statement sintético no desenho de 2026, a partir do guia
  fixtures/              statements sintéticos + layout revisto
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

## O desenho de 2026, reconstruído a partir do guia

Quando o documento não pode sair de onde está, `tests/make_ms2026_fixture.py`
gera um statement sintético com a **estrutura** descrita no guia do desenho de
2026 — `EXCHANGE-TRADED & CLOSED-END FUNDS`, `CORPORATE BONDS`,
`FIXED-RATE CAPITAL SECURITIES`, `GOVERNMENT SECURITIES` com `TREASURY
SECURITIES` por baixo, lotes fechados por `Total`, `()` para negativo e `—`/`N/A`
para ausente. Correr o parser contra ele apanhou cinco erros que o statement de
2016 nunca teria mostrado:

1. **`—` e `N/A` não formavam coluna.** A tabela de caixa desaparecia inteira e o
   travessão colava-se ao valor do lado. É a causa mais provável de "faltam
   posições".
2. **A coluna de valor era escolhida pelo nome.** Com um cabeçalho ilegível caía
   na última coluna — que num quadro de posições é o *yield*: a tabela somava
   percentagens. Agora, se o nome não decidir, escolhe-se a coluna que **bate com
   o total impresso** — provada, não adivinhada.
3. **Secção e sub-secção.** `GOVERNMENT SECURITIES` / `TREASURY SECURITIES`: a
   linha que fecha a secção traz o nome da secção, não o da sub-secção. Guarda-se
   a cadeia dos dois.
4. **`CORPORATE FIXED INCOME` impresso por cima de `TOTAL CORPORATE FIXED
   INCOME`**: a primeira linha é tão total como a segunda, e estava a ser contada
   como posição.
5. **`Total 21,477.613`**: a quantidade cola-se ao rótulo quando não forma coluna,
   e o total do título deixava de ser reconhecido como tal.

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

## O layout do consolidador

As folhas `POSICOES` e `MOVIMENTOS` estão no formato que os sistemas de
consolidação esperam, para serem carregadas sem trabalho manual.

```bash
python parse.py statement.pdf \
    --clientes exemplos/clientes.json \
    --fundos-exclusivos exemplos/fundos-exclusivos.json \
    --explosao nao --explosao consolidado --explosao detalhado \
    --moeda USD
```

**`IDCLIENTE`** vem do mapeamento de contas em `--clientes`; sem ele fica o
número da conta. **`IDATIVO`** é o ticker quando existe e, quando não existe, a
descrição normalizada — determinístico entre execuções, que é o que permite
ligar a posição de hoje à de ontem. **`DT_POSICAO`** é o fim do período lido do
statement.

**Campo que o documento não dá fica vazio, nunca a zero.** `VALOR_IR`,
`VALOR_IOF` e `VALOR_LIQUIDO` saem vazios num statement de custódia
norte-americano: escrever `0` afirmaria que o imposto foi zero. A folha
`De-para` diz isto campo a campo, e também que `AM`, `RT` e `COME_COTAS` nunca
ocorrem neste custodiante — a ausência é do documento, não do parser.

**`TIPO_MOVIMENTO` é tradução, não interpretação.** O tipo impresso vai ao lado
em `TIPO_ORIGINAL`, e o que não tiver correspondência clara sai como `OUTROS`.
No statement de exemplo os 490 movimentos ficam todos codificados: 415 `A`,
33 `TRF`, 18 `RP`, 18 `DIV`, 6 `JUROS`.

### Fundo exclusivo

`--fundos-exclusivos` recebe a carteira de cada fundo (o statement de custódia
mostra a cota, não o que está dentro dela). Com ela, `--explosao` gera as
visões pedidas — `nao`, `consolidado` (carteira agregada por classe) e
`detalhado` (ativo a ativo) — cada uma na sua folha, com a coluna
`ORIGEM_EXPLOSAO` a dizer de que fundo veio cada linha.

Explodir muda a composição, nunca o total: **a soma da carteira explodida tem
de repor exatamente o valor da cota que substituiu**, e é conferido. Sem
fundos declarados sai só a visão `nao`, com nota a dizer porquê.

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

## Quando alguma coisa falha (e não podes partilhar o documento)

```bash
python parse.py statement.pdf --diagnostico
```

Escreve `out/statement-diagnostico.txt` e mostra-o no ecrã. É um relatório de
**estrutura**: que tabelas foram encontradas, em que páginas, com quantas
colunas e linhas, o que conciliou, o que falhou — e **que páginas não deram
tabela nenhuma**, que é onde quase sempre está a secção em falta.

Os dígitos são mascarados com `#` e não há valores, nomes de titulares nem
nomes de títulos: o ficheiro pode ser partilhado com quem te ajuda a resolver
sem sair nada do cliente. (`--com-valores` desliga a máscara, para uso interno.)

A última secção do relatório — **O QUE MEXER** — liga cada sintoma ao sítio
exato: título não reconhecido → `_HEADING` em `src/tables.py`; total não
reconhecido → `TOTAL_PATTERNS`; e por aí.

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
