# trader-metamask

Skill do Claude para trades de cripto na MetaMask. É a irmã cripto do pacote
[day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill): mesma
disciplina (plano, gate de risco, um "sim" por ordem, diário), adaptada a uma
carteira em que **quem assina é você**.

Claude nunca tem a chave, nunca clica em Confirmar e nunca pede a seed. O papel
dele é tudo o que vem antes da assinatura e tudo o que vem depois.

## O que faz

| etapa | script | o quê |
| --- | --- | --- |
| achar setups | `scanner.py scan` / `chart` | velas dos pools (GeckoTerminal): breakout, pullback e tendência com EMA20/50, RSI, ATR e volume; stop e alvo sugeridos; gráfico SVG de cada candidato em `crypto/charts/scan.html` |
| achar o token | `market.py search` | lista todo contrato com o ticker, marca o OFFICIAL (CoinGecko) e as redes onde a moeda não existe |
| checar golpe | `market.py token` | honeypot, taxas, poderes do dono, mint/pause/blacklist, liquidez, volume, idade do pool: OK / CAUTION / BLOCK |
| carteira | `market.py wallet` | saldos de um endereço **público**, em USD (só leitura) |
| gas | `market.py gas` | preço do gas e custo de um swap |
| planejar | `plan.py buy` / `sell` | preço ao vivo, stop, alvo, tamanho pela perda máxima (com slippage e gas), mínimo a receber |
| gate de risco | `plan.py check` | PASS / BLOCK contra os limites do `config.json` e o diário do dia |
| ticket e "sim" | `ticket.py show` / `approve` | um ticket por vez; no modo live, o passo a passo da MetaMask; recusa "sim pra todos", "ok", "sim, mas..." |
| verificar | `ticket.py result --tx` | confere no chain se o swap passou, de qual carteira e quanto entrou |
| acompanhar | `ticket.py positions` | preço vs stop e alvo (a MetaMask não tem stop) |
| alertas | `alerts.py check` | STOP HIT, TARGET HIT, NEAR STOP, NO STOP; cada um uma vez só; feito para rodar agendado (tarefa na nuvem ou cron com `notify-send`) |
| diário | `ticket.py journal` | trades, P&L realizado, risco usado hoje |

Redes: Ethereum, Base, Arbitrum, Optimism, Polygon, BSC, Linea (EVM). Solana e
Bitcoin ficam fora.

Fontes públicas, sem chave de API: GeckoTerminal, DefiLlama, DexScreener, GoPlus, honeypot.is,
CoinGecko e RPCs publicnode.

## Instalação

Claude Code (Linux/macOS):

```bash
git clone git@github.com:<seu-usuario>/trader-metamask.git ~/.claude/skills/trader-metamask
```

Ou descompacte o `trader-metamask.skill` (é um zip) em `~/.claude/skills/`.
Requer Python 3.8+, sem dependências.

## Uso

Fale normalmente com o Claude: "tem algum setup bom na Base?", "quero comprar 100 dólares de PEPE na Base",
"esse token é golpe? 0x...", "como estão minhas posições cripto". A skill dispara
sozinha. Os dados ficam em `crypto/` no diretório atual (ou em
`$TRADER_METAMASK_DIR`).

```bash
M=~/.claude/skills/trader-metamask/scripts
python3 $M/scanner.py scan --chain base
python3 $M/market.py search PEPE
python3 $M/market.py token --chain ethereum --address 0x6982508145454Ce325dDbE47a25d4ec3d2311933
python3 $M/plan.py buy --chain base --token WETH --with USDC --stop-pct 5 --target-pct 12
python3 $M/plan.py check
python3 $M/ticket.py show
python3 $M/ticket.py approve --index 1 --reply "sim"
python3 $M/ticket.py positions
```

## Alertas agendados

A MetaMask não vende sozinha no stop. Agende a checagem, por exemplo com cron
no Ubuntu, a cada 15 minutos, com notificação na área de trabalho:

```bash
*/15 * * * * cd ~/daytrade && DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus python3 ~/.claude/skills/trader-metamask/scripts/alerts.py check --notify "notify-send Cripto"
```

O alerta só avisa. A venda continua passando por `plan.py sell`, `plan.py check` e o seu "sim".

## Configuração

Copie `config.example.json` para `crypto/config.json` e ajuste. Os padrões são
pequenos de propósito: US$1.000 de orçamento, até US$200 por trade, perda máxima
de US$20 por trade e US$50 por dia, 3 entradas por dia, reward:risk 2,
slippage até 1%, liquidez de pelo menos US$250 mil e volume de pelo menos US$50 mil.

**Paper** é o modo padrão: os fills são simulados ao preço real e nada toca a
carteira. Mudar `"mode"` para `"live"` é decisão sua, no seu arquivo.

## Segurança

- Nunca peça, cole ou guarde seed phrase ou chave privada. Se colou, trate a
  carteira como comprometida (veja `references/seguranca.md`).
- Aprovações com valor exato, nunca ilimitadas. Revogue as antigas em revoke.cash.
- Use uma carteira separada só para trade.
- Ferramenta e educação, não recomendação de investimento. Um token "OK" é só um
  token sem sinais de golpe, não um bom trade.

## Testes

`evals/` tem 3 casos no formato do `claude plugin eval`: token impostor,
ticket em paper e seed colada no chat. Na primeira rodada, a skill passou em
15 de 15 critérios e a linha de base sem a skill em 9 de 15.

## Licença

MIT
