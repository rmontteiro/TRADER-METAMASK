---
name: trader-metamask
description: >-
  Crypto trades na MetaMask, do setup ao swap: escaneia gráficos dos pools
  (breakout, pullback, tendência, com stop, alvo e gráfico), acha o contrato
  certo e os impostores, checa golpe (honeypot, taxa, dono, liquidez), dimensiona
  pelo stop e perda máxima com slippage e gas, passa por um gate de risco e
  entrega um ticket para VOCÊ assinar na MetaMask. Paper por padrão; verifica o
  swap on-chain, acompanha stop e alvo e mantém o diário. Nunca pede seed nem
  chave. Use sempre que o usuário falar em comprar, vender ou trocar cripto,
  token, memecoin, ETH, USDC, swap, MetaMask, DEX, Uniswap, Base, Arbitrum,
  Polygon, BSC, "tem setup?", "analisa o gráfico", "esse token é golpe?",
  "quanto compro", "minhas posições cripto", mesmo sem citar a MetaMask.
---

# trader-metamask

A irmã cripto do pacote trader-*: mesma disciplina (plano, risco, um "sim" por
ordem, diário), adaptada a um mundo sem corretora. Na MetaMask quem assina é a
pessoa. Claude não tem a chave, não clica em Confirmar e não pode desfazer nada:
uma transação confirmada é final. Por isso o trabalho de Claude aqui é tudo o
que vem **antes** da assinatura (escolher o contrato certo, checar golpe,
dimensionar, conferir o risco, dizer exatamente o que digitar e conferir) e
tudo o que vem **depois** (verificar no chain, acompanhar stop/alvo, P&L).

`${CLAUDE_SKILL_DIR}` abaixo é a pasta desta skill. Os scripts são Python 3.8+,
só biblioteca padrão, e gravam em `crypto/` no diretório atual (ou em
`$TRADER_METAMASK_DIR`). No projeto DAYTRADE, rode com cwd
`/mnt/project-files/daytrade/` para `crypto/` sobreviver entre conversas.

```bash
M="${CLAUDE_SKILL_DIR}/scripts"
python3 $M/scanner.py scan --chain base                       # setups nos gráficos + página com gráficos
python3 $M/market.py search PEPE                              # todos os contratos com esse ticker
python3 $M/market.py token --chain base --address 0x...       # golpe? OK / CAUTION / BLOCK
python3 $M/market.py price --chain base --token WETH
python3 $M/market.py wallet --chain base --address 0xPUBLICO  # saldos, só leitura
python3 $M/market.py gas --chain ethereum
python3 $M/plan.py buy --chain base --token 0x... --with USDC --stop-pct 8 --target-pct 20 --why "..."
python3 $M/plan.py sell --chain base --token 0x... --all --for USDC --why "stop"
python3 $M/plan.py check                                      # gate de risco: PASS / BLOCK
python3 $M/ticket.py show                                     # ticket (live = checklist MetaMask)
python3 $M/ticket.py approve --index 1 --reply "<resposta do usuário, palavra por palavra>"
python3 $M/ticket.py result --index 1 --tx 0x...              # live: confere no chain
python3 $M/ticket.py positions                                # preço vs stop/alvo
python3 $M/alerts.py check                                    # alertas novos de stop/alvo (para agendar)
python3 $M/ticket.py journal
```

Redes: ethereum, base, arbitrum, optimism, polygon, bsc, linea (EVM, onde a
MetaMask opera). Solana e Bitcoin ficam fora: outra carteira, outro modelo.
Fontes, todas públicas e sem chave: GeckoTerminal (velas), DefiLlama (preço), DexScreener (pools,
liquidez, volume), GoPlus e honeypot.is (segurança do contrato), RPCs
publicnode (saldo, gas, recibo). Se uma estiver fora, o script diz qual.

## Achar setups: o scanner

Quando o usuário pedir ideias ("o que está bom pra comprar", "tem setup?",
"analisa o gráfico de X"), use o scanner. Ele lê as velas dos pools da DEX
(GeckoTerminal, grátis) e procura três setups de compra:

- **breakout**: fechou acima da máxima das últimas 20 velas, com volume 1,5x a
  média, acima da EMA50;
- **pullback**: tendência de alta (EMA20 sobre uma EMA50 subindo), o preço voltou
  à EMA20 e fechou acima dela, RSI entre 40 e 60;
- **trend**: preço > EMA20 > EMA50 subindo, RSI 55 a 70, sem estar esticado.

Cada setup vem com stop abaixo da estrutura e alvo de pelo menos 2,5R. Os
candidatos já passam pelos filtros de liquidez, volume e idade do config, os
melhores passam pela checagem de golpe (BLOCK sai da lista), e cada um ganha um
gráfico SVG com velas, EMAs, volume, entrada, stop e alvo. A página
`crypto/charts/scan.html` junta todos: entregue-a ao usuário para ele ver antes
de decidir.

```bash
python3 $M/scanner.py scan --chain base                    # trending + maior volume, velas de 1 h
python3 $M/scanner.py scan --chain ethereum --tf 4h --all  # --all lista também quem não tem setup
python3 $M/scanner.py scan --chain base --tokens 0x...,0x...
python3 $M/scanner.py chart --chain base --token 0x... --tf day
```

Cada setup imprime a linha `plan.py buy ... --stop ... --target ...` pronta.
Ela segue o fluxo normal abaixo (gate de risco, ticket, "sim"). Um setup é um
padrão no preço passado, não uma previsão; diga isso, e nunca chame um setup de
"compra garantida". Cada scan gasta uma chamada por pool (cerca de 2 s cada, pelo
limite da API); `--limit` controla quantas.

## O fluxo

1. **Achar o contrato.** Um ticker não identifica um token: qualquer um cria um
   "PEPE" ou um "USDC". Para stablecoins e wrapped (USDC, USDT, DAI, WETH,
   WBTC, cbBTC, ARB, OP, WBNB) os scripts já sabem o endereço oficial; escreva
   o símbolo. Para o resto, `market.py search` lista todos os contratos com
   aquele nome, mais líquido primeiro, marca OFFICIAL os que a CoinGecko lista
   para a moeda principal desse ticker e diz em quais redes ela NÃO existe
   (lá, qualquer token com o nome é outro projeto). Mostre a lista e confirme
   o endereço com o usuário. Nunca escolha sozinho entre contratos parecidos;
   se a moeda não existe na rede pedida, diga isso e ofereça a rede oficial.
2. **Checar o token.** `market.py token`. BLOCK = não entra (honeypot, taxa
   alta, dono oculto, liquidez ou volume baixos, pool novo demais, código não
   verificado). CAUTION = pode entrar, mas mostre cada motivo ao usuário em
   palavras simples. A checagem vale 24 h.
3. **Planejar.** `plan.py buy` com stop (`--stop-pct` ou `--stop` preço) e alvo.
   O tamanho sai da perda máxima por trade, contando slippage na entrada e na
   saída e o gas das duas pernas e da aprovação; nunca passa de `max_trade_usd`.
   Se o usuário não deu stop, pergunte. Se insistir em ficar sem, o gate bloqueia
   enquanto `require_stop` for true, e isso é o usuário que muda no config.
4. **Gate de risco.** `plan.py check`. Uma ordem BLOCK não vira ticket. Leia o
   motivo para o usuário e ofereça o ajuste (stop mais perto, tamanho menor,
   outro token), sem contornar a regra.
5. **Ticket e o "sim".** `ticket.py show`, UMA ordem por vez, e pergunte:
   "Executar esta ordem? Responda sim ou não." Passe a resposta ao `approve`
   exatamente como veio. Se imprimir "Not recorded", pergunte de novo; nunca
   transforme "ok", "pode ser", "sim pra todos" ou "sim, mas..." num sim.
6. **Executar.**
   - **paper** (padrão): o `approve` simula o swap ao preço ao vivo, com metade
     do slippage contra e o gas descontado. Nada toca a carteira.
   - **live**: o `approve` imprime o passo a passo da MetaMask (rede, contrato
     colado e não escolhido pelo nome, slippage, mínimo a receber, aprovação com
     valor exato, conferir o site e a taxa). Quem clica em Confirmar é o
     usuário. Depois ele cola o hash; `ticket.py result --tx` confere no chain
     se a transação passou, de qual carteira saiu e quanto entrou.
7. **Acompanhar.** A MetaMask não tem stop nem take-profit: o swap é à vista.
   `ticket.py positions` mostra todas as posições contra o stop e o alvo, e
   `alerts.py check` é a versão para rodar agendada (veja "Alertas" abaixo).
   A saída é sempre um novo `plan.py sell`, novo check e novo sim. Ordens limite
   de verdade existem fora da MetaMask (CoW Swap, 1inch); veja
   `references/metamask.md`.
8. **Registrar.** `journal` mostra trades, P&L realizado, risco usado hoje e o
   caixa do paper.

## Alertas de stop e alvo

`alerts.py check` lê as posições abertas, pega o preço e avisa STOP HIT, TARGET
HIT, NEAR STOP (a menos de `--near-pct`, padrão 2%, do stop) ou NO STOP. Cada
alerta sai uma vez só por posição; volta a avisar se o preço sair da zona e
voltar (`crypto/state/alerts.json`). Código 3 = alertas novos, 0 = nada novo.
É só leitura: nunca planeja, aprova ou executa nada.

```bash
python3 $M/alerts.py check                 # só os alertas novos
python3 $M/alerts.py check --all           # todas as posições com o status
python3 $M/alerts.py check --notify "notify-send Cripto"   # roda um comando com o texto
```

Para agendar:
- **Numa sessão do Claude na nuvem:** uma tarefa agendada que roda
  `alerts.py check` na pasta onde está `crypto/` e só fala com o usuário quando
  o código for 3. Uma checagem agendada nunca opera: ninguém está lá para dizer sim.
- **No computador do usuário (Linux/macOS), com cron**, a cada 15 minutos e
  com notificação na área de trabalho:
  `*/15 * * * * cd ~/daytrade && DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus python3 ~/.claude/skills/trader-metamask/scripts/alerts.py check --notify "notify-send Cripto"`

O alerta só enxerga as posições do `crypto/` daquela pasta: rode os trades e os
alertas na mesma pasta.

## Arquivos em crypto/

| arquivo | o quê |
| --- | --- |
| `config.json` | limites e modo; copie `config.example.json` e ajuste com o usuário |
| `orders.json` | ordens planejadas, com o veredito do risco e a impressão digital |
| `portfolio.json` | último retrato de cada carteira lida por `market.py wallet` |
| `state/tokens.json` | checagens de segurança dos tokens |
| `state/journal.json` | todo sim, não, fill, execução, falha, com hash e P&L (só cresce) |
| `state/positions.json` | posições abertas e fechadas, preço médio, stop e alvo |
| `state/paper.json` | caixa do paper trading |
| `state/alerts.json` | quais alertas já foram dados, para não repetir |

Os limites padrão (sem `config.json`) são pequenos de propósito: orçamento
US$1.000, até US$200 por trade, perda máxima US$20 por trade e US$50 por dia,
3 entradas por dia, reward:risk 2, slippage até 1%, gas até 3% do trade,
liquidez ≥ US$250 mil, volume 24 h ≥ US$50 mil, pool com 7+ dias, taxa do
token até 5%.

## Paper e live

`"mode": "paper"` é o padrão. Passar para `"live"` é decisão do usuário, editando
o próprio `crypto/config.json`; Claude não muda o modo. No live, `plan.py check`
exige um retrato recente da carteira (`market.py wallet` com o endereço
**público**) para conferir o saldo. Antes do primeiro trade live, sugira um
trade pequeno de teste e uma carteira separada só para trade, com pouco dinheiro.

## Se houver um navegador conectado

Com Claude in Chrome ou o navegador embutido, Claude pode abrir
portfolio.metamask.io/swap e preencher rede, tokens, valor e slippage do
ticket. Não clica em Confirmar, Aprovar ou Assinar na janela da MetaMask, não
digita senha e não desbloqueia a carteira: esse clique é do usuário, depois de
conferir o ticket. Antes, leia a skill do navegador correspondente.

## Segurança: o que esta skill nunca faz

- Nunca pede, aceita, lê ou guarda seed phrase (as 12/24 palavras), chave
  privada ou senha. Se o usuário colar uma, diga para apagar a mensagem e tratar
  a carteira como comprometida: criar uma nova e mover os fundos já.
  `references/seguranca.md` tem o roteiro.
- Nunca assina, envia ou "acelera" transações, nem pede para o usuário assinar
  mensagens (`eth_sign`, permit) fora do swap do ticket.
- Nunca recomenda aprovação ilimitada; manda revogar as antigas em revoke.cash.
- Nunca opera de tarefa agendada: sem ninguém para dizer sim, só leitura e aviso.
- Nunca entra em token com BLOCK, nem escolhe sozinho entre contratos de mesmo nome.
- Nunca promete preço: a cotação muda até o bloco, e o stop é um aviso, não uma ordem.
- Ferramenta e educação, não recomendação de investimento. Um PASS é uma checagem
  de regras; um token "OK" é um token sem sinais de golpe, não um bom trade. A
  maioria de quem faz day trade perde dinheiro, e em memecoin mais ainda.

Leia `references/metamask.md` quando o usuário estiver na MetaMask com dúvida
(rede, gas, aprovação, transação presa, ordem limite, ponte entre redes) e
`references/seguranca.md` quando aparecer golpe, link suspeito, airdrop
estranho, "suporte" pedindo seed, ou carteira possivelmente comprometida.
Para impostos no Brasil, diga que o diário (`journal`) tem datas, valores e
hashes, e que as regras mudam: confirme com um contador.
