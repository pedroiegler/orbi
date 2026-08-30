# ORBI — A oferta

O que vender, por quanto, e o que dizer. Feito para você abrir na frente do
cliente, não para arquivar.

Tudo aqui é **cumprido pelo código**: o plano define o teto de consultas e o
limite de usuários, e o sistema recusa o sexto usuário num plano de cinco. Não
existe promessa comercial que o software não implemente.

---

# Os planos

| Plano | Usuários | Consultas/mês | Mensal |
|---|---|---|---|
| **Essencial** | até 5 | 3.000 | R$ 490 |
| **Time** | até 15 | 10.000 | R$ 890 |
| **Operação** | até 30 | 20.000 | R$ 1.490 |
| *Fundador* | até 15 | 10.000 | *R$ 350* |

Setup de implantação: R$ 1.500 a 2.500 — **dispensável nos primeiros clientes**
em troca de depoimento.

## Por que só duas coisas mudam entre os planos

**Número de usuários** e **teto de consultas**. Nada mais.

Não muda o modelo de IA, não muda a velocidade, não muda a segurança. Três
razões, e todas resistem a pergunta de cliente:

1. **O cliente consegue verificar.** Ele conta quantos vendedores tem. Ele não
   tem como conferir se está recebendo uma "IA melhor" — e vender o que o
   comprador não pode auditar é como esse tipo de produto perde confiança.
2. **Reflete o custo real.** O que cresce com o tamanho do cliente é **suporte**,
   não token. Dez vendedores geram mais dúvida, mais correção de vocabulário e
   mais conversa que três. O LLM custa entre R$ 2 e R$ 29 por cliente/mês
   (medido — veja [ORBI-MODELOS.md](ORBI-MODELOS.md)).
3. **Mantém o produto sustentável.** Um modelo para todos significa um conjunto
   de evals, um comportamento a depurar, um bug reproduzido uma vez para todos.

## O teto de consultas não é para punir

3.000 consultas para 5 usuários dá **600 por pessoa por mês** — cerca de 27 por
dia útil. Um vendedor ativo faz umas 15. O teto está no **dobro do uso esperado**,
de propósito: ele existe para conter uso claramente anormal, não para o cliente
esbarrar no dia 20.

Se um cliente encostar no teto de forma consistente, isso não é problema — é
**sinal de venda**: ele está usando muito, e é hora de subir de plano.

⚠️ **A partir de 1º de outubro de 2026**, a Meta volta a cobrar as respostas
dentro da janela de 24 horas. Aí o teto deixa de ser só proteção contra abuso e
passa a ser **controle de margem** — cada consulta terá custo de mensagem. Os
números acima precisam ser revistos quando a tarifa do Brasil sair.

---

# O primeiro cliente: a oferta de fundador

Não venda o plano cheio para o primeiro. Venda isto:

> **Plano Fundador — R$ 350/mês por 12 meses**
>
> Você recebe o mesmo produto do plano Time (até 15 usuários). Em troca:
> ser referência e dar um depoimento quando estiver funcionando.
>
> Antes de assinar: **PoC gratuita de 3 semanas**, com escopo fechado —
> 3 usuários, as 4 consultas, 1 ERP.

## As quatro regras da PoC, e por que cada uma existe

**1. Prazo fixo de 3 semanas, escrito antes de começar.**
Sem isso vira piloto eterno, e piloto eterno vira usuário que nunca paga.

**2. Escopo fechado: 3 usuários, 4 consultas, 1 ERP.**
Pedido fora disso entra na lista do plano pago. Isso não é rigidez — é o que
impede a PoC de virar desenvolvimento sob medida de graça.

**3. O cliente escreve as 20 perguntas que quer ver funcionando, antes de começar.**
Esta é a mais importante. Elas viram o critério de aprovação automático
(`orbi onboard --acceptance`). Se passarem, ele não tem argumento para dizer "não
me convenceu" — **o critério foi dele**.

**4. Carta de intenção assinada.**
Uma página: se os 20 casos passarem, contrato de 12 meses a R$ 350. Sem isso, você
entrega três semanas de valor e ouve "vou pensar".

## Por que R$ 350 e não de graça

Cliente que paga pouco continua sendo cliente. Cliente que paga zero é usuário —
e usuário não cancela, ele só some. Você descobre pelo silêncio, tarde demais.

Com R$ 350 ele fica no zero a zero para você (o custo é R$ 250 a 470). Não é para
ganhar dinheiro: é para provar que existe alguém disposto a pagar.

---

# É normal limitar usuários?

Você perguntou se isso é praxe. É — e é o padrão dominante no setor.

Salesforce cobra de US$ 25 a 300 por usuário/mês. Jira, US$ 7,75 a 15,25. Slack,
US$ 8,75. Figma, US$ 15 por editor. Todos limitam por pessoa.

E o modelo que o Orbi usa — **assinatura com teto de uso** — é hoje o **mais
comum em software B2B, com 37% do mercado**, tendo subido de 25% no ano anterior.
Ou seja: você não está inventando nada estranho. Está usando o formato que o
mercado convergiu.

Um detalhe de contexto: cliente brasileiro paga em média 12% menos que o
americano pelo mesmo software. Os preços acima já estão calibrados para o Brasil.

## O que o cliente vai perguntar, e o que responder

**"E se eu tiver 16 vendedores?"**
Sobe para Operação, ou tira alguém que não usa. O sistema conta usuários **ativos**
— desativar quem saiu da empresa libera a vaga.

**"Posso pagar por consulta em vez de por usuário?"**
Não, e por um motivo que favorece você: por consulta, seu custo fica imprevisível
e você acaba evitando usar. Por usuário, você usa à vontade dentro do teto.

**"Por que não tem plano ilimitado?"**
Porque ilimitado é um convite a um único cliente entusiasmado dobrar a conta de
todo mundo. O teto é o dobro do uso esperado — se você encostar nele, a gente
conversa, e provavelmente é hora de subir de plano.

**"O que acontece se eu estourar o teto?"**
As consultas param até o mês virar, e você recebe o aviso antes. Na prática nunca
acontece: o teto é o dobro do uso normal.

---

# A frase que abre a conversa

> "Seu ERP te deu um assistente. Ele funciona para você. O Orbi funciona para
> seus 12 vendedores — cada um vendo só o que pode, e você vendo tudo que foi
> perguntado."

E a oferta que abre a porta:

> "Me dá acesso de leitura ao seu ERP por uma semana. Em 4 horas seus 3
> vendedores estão perguntando estoque e preço pelo WhatsApp. Se não servir,
> desligo e não custou nada."

Funciona porque é verdade: `orbi onboard` roda em horas.

## A justificativa de valor

Um distribuidor com 10 vendedores que economizam 20 minutos por dia deixando de
ligar para o escritório recupera **mais de 60 horas por mês**. Cobrar R$ 890 por
isso é conversa fácil.

---

# O que **não** dizer

**Nunca prometa "integração automática".** É falso e quebra na primeira semana.
A formulação correta:

> "A integração é feita uma vez por ERP, não uma vez por cliente. Do segundo
> cliente em diante, colocar no ar leva horas."

**Nunca prometa que a IA nunca erra.** O que o Orbi promete é diferente e melhor:
quando está em dúvida, ele **pergunta** em vez de chutar. E toda resposta mostra
qual produto foi usado, então erro vira visível e corrigível.

**Nunca diga que o estoque é em tempo real sem qualificar.** O Orbi consulta o ERP
a cada pergunta — não há cache. Mas se o ERP do cliente estiver desatualizado, a
resposta reflete isso. A fonte da verdade continua sendo o ERP dele.

**Nunca omita o que o número de WhatsApp custa.** Ele é do cliente, e a partir de
outubro de 2026 as mensagens têm custo. Dizer isso na venda evita a conversa ruim
em novembro.

---

# Os comandos que sustentam a oferta

```bash
# criar cliente já com o plano e o teto certos
orbi tenant add --tenant construtora-silva --name "Construtora Silva LTDA" \
  --phone "+554330001111" --plan fundador

# o limite de usuários é cumprido: o sexto num plano de cinco é recusado
orbi user add --tenant construtora-silva --phone "+5543999990001" --role sales_rep

# subir de plano quando ele crescer
orbi tenant set-plan --tenant construtora-silva --plan time

# ver consumo contra o limite
orbi tenant show --tenant construtora-silva

# o critério de aprovação escrito pelo próprio cliente
orbi onboard --tenant construtora-silva --acceptance aceite-cliente.json
```

O roteiro completo do go-live está em [ORBI-IMPLANTACAO.md](ORBI-IMPLANTACAO.md).

---

# Onde a margem fica

| Situação | Receita | Custo | Margem |
|---|---|---|---|
| 1 cliente fundador (R$ 350) | R$ 350 | R$ 250–470 | negativa a zero |
| 1 cliente pagante (R$ 890) | R$ 890 | R$ 250–470 | 47–72% |
| 3 clientes | ~R$ 2.200 | R$ 500–800 | 64–77% |
| 10 clientes | ~R$ 7.500 | R$ 1.200–2.500 | 67–84% |

O primeiro cliente não fecha a conta, e não precisa: ele existe para provar. **O
segundo cliente a preço cheio já deixa a operação no azul** — porque o custo
marginal do décimo é praticamente o mesmo do segundo.

Números de mensagem da Meta não estão nessa tabela ainda. Quando a tarifa sair,
some R$ 130 a 265 por cliente e recalcule.
