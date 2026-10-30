---
name: trader-metamask
description: >-
  Crypto trades na MetaMask, do token ao swap: acha o contrato certo (e os
  impostores com o mesmo ticker), checa se o token é golpe (honeypot, taxa,
  dono com poderes, liquidez, idade do pool), dimensiona o trade pelo seu
  stop e perda máxima contando slippage e gas, passa por um gate de risco,
  e entrega um ticket passo a passo para VOCÊ assinar na MetaMask. Paper
  trading por padrão. Verifica o swap on-chain pelo hash, acompanha stop e
  alvo (a MetaMask não tem stop) e mantém o diário de P&L. Lê saldos de uma
  carteira pública, nunca chave nem seed. Use sempre que o usuário falar em
  comprar, vender ou trocar cripto, token, memecoin, altcoin, ETH, USDC, swap,
  MetaMask, carteira, DEX, Uniswap, Base, Arbitrum, Polygon, BSC, "esse token
  é golpe?", "quanto compro", "meu stop", "minhas posições cripto", mesmo sem
  citar a MetaMask. Crypto trades in MetaMask: token safety, sizing, swap
  tickets, paper trading.
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
python3 $M/ticket.py journal
```

Redes: ethereum, base, arbitrum, optimism, polygon, bsc, linea (EVM, onde a
MetaMask opera). Solana e Bitcoin ficam fora: outra carteira, outro modelo.
Fontes, todas públicas e sem chave: DefiLlama (preço), DexScreener (pools,
liquidez, volume), GoPlus e honeypot.is (segurança do contrato), RPCs
publicnode (saldo, gas, recibo). Se uma estiver fora, o script diz qual.

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
   `ticket.py positions` compara o preço com o stop e o alvo e grita STOP HIT /
   TARGET HIT (sai com código 3). Para vigiar sem ninguém olhar, agende uma
   checagem (só leitura) e avise o usuário; a saída é um novo `plan.py sell`,
   novo check e novo sim. Ordens limite de verdade existem fora da MetaMask
   (CoW Swap, 1inch); veja `references/metamask.md`.
8. **Registrar.** `journal` mostra trades, P&L realizado, risco usado hoje e o
   caixa do paper.

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
