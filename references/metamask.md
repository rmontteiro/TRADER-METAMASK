# MetaMask: o que o usuário vai ver, e o que conferir

## Onde fazer o swap
- **Extensão / app**: botão Swap (ou "Trocar"). Agrega cotações de várias DEXs
  e cobra uma taxa de serviço da MetaMask (por volta de 0,875% em muitos pares;
  ela aparece na cotação). Para trades pequenos isso pesa: some à conta do custo.
- **Portfolio** (portfolio.metamask.io): mesma função num site. Digite o endereço
  à mão ou use o favorito. Anúncio de busca com "MetaMask" é um golpe clássico.
- Alternativa sem a taxa da MetaMask: o site da própria DEX (app.uniswap.org,
  aerodrome.finance na Base etc.) conectando a MetaMask. Mesmo ticket, mesmas
  conferências; a assinatura continua na MetaMask.

## Rede
- O seletor de rede fica no topo. Base, Arbitrum, Optimism, Linea e Polygon
  podem precisar ser adicionadas (chainlist.org, conferindo o chain id).
- Gas é pago na moeda nativa da rede: ETH (Ethereum, Base, Arbitrum, Optimism,
  Linea), POL (Polygon), BNB (BSC). Sem ela, nenhum swap sai. Deixe uma sobra.
- Moedas em redes diferentes são ativos diferentes. Mover entre redes exige uma
  ponte (Bridge na MetaMask/Portfolio); conferir rede de destino e taxa.

## Aprovação (spending cap)
- Para vender um token ERC-20 (USDC, por exemplo) o contrato do swap precisa de
  permissão. A MetaMask pede uma "spending cap": coloque o valor do trade, não
  "ilimitado". Custa uma transação a mais (gas), uma vez por token e contrato.
- Para revogar aprovações antigas: revoke.cash ou o próprio Portfolio.
- Nunca assine "permit", "setApprovalForAll" ou mensagem de texto pedida por um
  site que você não abriu para este trade.

## A cotação
- "Você recebe" é estimativa. O "mínimo" (com slippage) é o que o contrato
  garante; abaixo disso a transação reverte e só o gas é gasto.
- Slippage alto convida sanduíche (MEV): bots compram antes e vendem depois.
  Em tokens líquidos 0,5% basta; acima de 1% em token pequeno é sinal de que o
  pool é raso demais para o tamanho.
- Se a cotação vier pior que o mínimo do ticket: não confirme, volte ao Claude,
  replaneje (plan.py buy de novo) e passe pelo check outra vez.

## Depois de confirmar
- Activity mostra Pending > Confirmed. O hash (Copy transaction ID) vai para
  `ticket.py result --tx`.
- **Presa em Pending**: em geral gas baixo na Ethereum. A MetaMask oferece
  Speed up (mesma transação, mais gas) ou Cancel (transação vazia com o mesmo
  nonce). Quem decide e clica é o usuário.
- **Failed / Reverted**: o preço passou do mínimo ou o token bloqueou a venda.
  O gas foi gasto, os tokens não. Registre com `result --status failed`.
- O token comprado pode não aparecer: Import tokens > colar o contrato.

## Stop e alvo de verdade
A MetaMask Swap é só a mercado, sem ordens pendentes. Opções:
- vigiar com `ticket.py positions` (ou uma tarefa agendada só de leitura que avisa);
- ordens limite assinadas fora da MetaMask, executadas por terceiros:
  CoW Swap (limit orders), 1inch (limit order protocol), Uniswap (limit).
  Elas pedem uma aprovação e uma assinatura; leia o que está assinando.
  Não são stops garantidos: preenchem se houver liquidez e alguém executar.

## Carteira para trade
Use uma conta (ou carteira) separada só para trade, com o valor que aceita
perder. O grosso fica em outra conta, de preferência em carteira de hardware
(Ledger/Trezor conectada à MetaMask).
