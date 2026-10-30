# Golpes comuns e o que dizer

| sinal | o que é | o que fazer |
| --- | --- | --- |
| alguém pede a seed / frase de recuperação / chave privada | golpe, sempre. Nenhum suporte real pede | não responder; se já deu, carteira comprometida (abaixo) |
| "suporte MetaMask" no Telegram, Discord, X, WhatsApp | falso: a MetaMask não atende por DM | bloquear |
| token aparece sozinho na carteira (airdrop) com site para "resgatar" | isca: o site pede aprovação ou assinatura que esvazia a carteira | ignorar o token, não abrir o site, não tentar vender |
| token com mesmo nome de um famoso, endereço diferente | impostor | usar só o contrato confirmado (market.py search + site oficial) |
| compra funciona, venda falha ou taxa de 99% | honeypot | market.py token antes de qualquer compra |
| "liquidez travada", "dev renunciou" sem prova | marketing | olhar GoPlus/explorer; liquidez baixa ainda é BLOCK |
| link de anúncio no Google para MetaMask/Uniswap | site clone | digitar o endereço ou usar favorito |
| pedido de assinar mensagem para "verificar carteira" | permit/phishing | não assinar nada fora do swap do ticket |
| sinal de grupo "vai subir 10x hoje" | pump and dump | sem plano e stop não entra; o gate de risco decide |

## Carteira comprometida (seed vazou, ou aprovação maliciosa assinada)
1. Criar uma carteira nova (nova seed, anotada em papel, offline).
2. Mover já o que restou para ela, começando pelo de maior valor; o gas tem de
   estar na carteira velha.
3. Revogar aprovações da velha em revoke.cash (se a seed vazou, isso não basta:
   abandone a carteira).
4. Nunca pagar "serviço de recuperação": é o segundo golpe.
5. Se houver valor alto, registrar boletim de ocorrência e guardar os hashes.

Claude nunca recebe a seed, nem para "ajudar a recuperar". Se o usuário colar uma,
diga para apagar a mensagem e seguir os passos acima, sem repetir as palavras.
