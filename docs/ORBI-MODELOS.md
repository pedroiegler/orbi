# ORBI — Estudo de modelos

Qual IA usar nos testes, qual usar com cliente pagante, quanto cada uma custa e
se faz sentido dar um modelo diferente para cada cliente.

O que está aqui é **medido** onde eu tinha chave, e **pesquisado** onde não
tinha. Cada tabela diz qual é qual — porque decidir com número inventado é pior
do que decidir sem número.

Reproduza qualquer linha com:

```bash
orbi bench --provider gemini --model gemini-3.5-flash-lite --limite 10
```

---

# Antes de tudo: p50 e p95

Você perguntou, e é o conceito que sustenta todo o resto.

Imagine 100 perguntas feitas ao Orbi, e você anota quanto cada uma demorou.
Ordene da mais rápida para a mais lenta:

- **p50** (mediana) é a que está na posição 50. Metade foi mais rápida, metade
  mais lenta. É a **experiência típica**.
- **p95** é a que está na posição 95. Só 5 das 100 foram piores. É a
  **experiência ruim que ainda acontece com frequência**.

Por que não usar a média? Porque ela mente. Se 99 perguntas levam 1 segundo e uma
leva 100 segundos, a média dá 2 segundos — e ninguém viveu "2 segundos": 99
pessoas viveram 1 e uma viveu 100.

**O p95 é o número que decide.** Ele responde: "quão ruim fica quando fica ruim?"
Se o p95 é 15 segundos, um em cada vinte vendedores espera 15 segundos, todo dia.
Esse é o que liga para reclamar.

No Orbi isso tem consequência direta: o orçamento do turno é de 10 segundos. Um
modelo com **p95 acima de 10 segundos perde respostas** — não fica lento, falha.

O `orbi bench` mostra os dois, e avisa quando o p95 estoura o orçamento.

---

# O que decide a escolha (e não é o preço do token)

O ORBI.md já tinha escrito a regra, e ela é contraintuitiva:

```
custo_por_acerto = custo_médio_do_turno / taxa_de_acerto
```

Um modelo 40% mais barato que erra a operação 8% mais vezes é **mais caro na
prática**. Cada erro vira desambiguação, retrabalho e ticket de suporte — e uma
hora do seu tempo custa mais que mil perguntas de qualquer modelo desta lista.

O `orbi bench` calcula essa divisão e a mostra como a última linha.

---

# Quanto o Orbi realmente gasta por pergunta

Medido contra a API real, com o prompt de verdade:

| | Valor |
|---|---|
| Tokens de **entrada** | **~890** (regras + schema das tools + pergunta) |
| Tokens de **saída** | **~24** (só o nome da tool e os argumentos) |

A saída é minúscula de propósito: o modelo não escreve a resposta, ele só escolhe
uma operação. É isso que torna o Orbi barato de rodar — e é consequência direta
da regra "o LLM interpreta, o código redige".

A entrada é quase toda **prefixo estático** (as regras e os schemas, iguais em
toda pergunta do mesmo cliente). Isso é desenhado para prompt caching, que cobra
cerca de 10% pelo trecho repetido. Por isso as tabelas abaixo trazem duas colunas.

---

# Custo por mil perguntas

Calculado sobre a medição acima. Dólar a R$ 5,40 (aproximado).

| Modelo | Fabricante | Grátis? | US$/1k | R$/1k | R$/1k com cache |
|---|---|---|---|---|---|
| gpt-5-nano | OpenAI | não | 0,0541 | 0,29 | **0,11** |
| gpt-4.1-nano | OpenAI | não | 0,0985 | 0,53 | 0,16 |
| gemini-3.1-flash-lite | Google | **sim** | 0,2583 | 1,39 | 0,50 |
| gpt-5-mini | OpenAI | não | 0,2702 | 1,46 | 0,54 |
| **gemini-3.5-flash-lite** | Google | **sim** | 0,3267 | 1,76 | **0,66** |
| gpt-4.1-mini | OpenAI | não | 0,3940 | 2,13 | 0,66 |
| gemini-3.7-flash | Google | **sim** | 0,7567 | 4,09 | 1,33 |
| claude-haiku-4.5 | Anthropic | não | 1,0090 | 5,45 | 1,78 |
| gpt-4.1 | OpenAI | não | 1,9700 | 10,64 | 3,29 |
| claude-sonnet-5 | Anthropic | não | 3,0270 | 16,35 | 5,33 |
| claude-opus-5 | Anthropic | não | 5,0450 | 27,24 | 8,88 |

Preços conferidos em 29/08/2026 nas páginas oficiais. A tabela vive em
`src/orbi/llm/pricing.py` e o `orbi bench` **avisa quando ela passa de 90 dias**.

## A conclusão que muda a estratégia

Pegue um cliente do plano Time (**R$ 890/mês**) com 10 vendedores fazendo 15
perguntas por dia útil — 3.300 perguntas por mês:

| Modelo | Custo mensal de LLM | % da receita |
|---|---|---|
| gpt-5-nano | R$ 0,36 | 0,04% |
| gemini-3.5-flash-lite | R$ 2,19 | 0,25% |
| claude-haiku-4.5 | R$ 5,86 | 0,66% |
| claude-sonnet-5 | R$ 17,58 | 1,98% |
| **claude-opus-5** (o mais caro) | **R$ 29,31** | **3,29%** |

Leia de novo: **o modelo mais caro do mercado custaria R$ 29 por mês** num cliente
que paga R$ 890. A diferença entre o mais barato e o mais caro é **R$ 29**.

Compare com o que realmente pesa:

| Custo | Por cliente/mês |
|---|---|
| Mensagens do WhatsApp (a partir de out/2026) | **R$ 130 a 265** |
| VPS, banco, backup (rateado) | R$ 120 a 250 |
| **LLM, mesmo no modelo mais caro** | **R$ 29** |
| Uma hora sua resolvendo um ticket | mais que tudo acima |

**O LLM não é o custo relevante deste produto.** Otimizar modelo por preço é
otimizar a linha errada da planilha.

A consequência prática: escolha o modelo por **qualidade e latência**, e trate o
preço como empate técnico. Um erro a menos por semana vale mais que a economia
de um ano inteira de tokens.

---

# Os números medidos

## Gemini — medido com a chave do projeto

Conjunto de 10 perguntas do eval L1/L2, camada gratuita:

| Modelo | Acerto de tool | Acerto de args | p50 | p95 | Tokens |
|---|---|---|---|---|---|
| **gemini-3.5-flash-lite** | 90% | 80% | **706 ms** | **1.215 ms** | 821+22 |
| gemini-3.1-flash-lite | **100%** | 90% | 2.522 ms | **16.361 ms** | 910+22 |
| gemini-3.7-flash | — | — | 8.500 a 17.900 ms | — | — |
| gemini-2.5-flash-lite | descontinuado (404) | | | | |

**Ressalva que importa mais que os números:** dez amostras na camada gratuita
medem *a fila do Google*, não o modelo. O p95 de 16 segundos do `3.1-flash-lite`
provavelmente é congestionamento, não o modelo sendo lento. Os números de
**acerto**, sim, são confiáveis — eles não dependem de fila.

Conclusão honesta: para escolher por latência, é preciso medir na camada paga,
com pelo menos 100 amostras. O `orbi bench` faz isso no dia em que houver billing.

## OpenAI e Anthropic — **não medidos**

Não tenho chave desses dois. O que posso afirmar:

- os preços da tabela são das páginas oficiais, conferidos hoje;
- a integração está implementada e testada estruturalmente;
- **a latência e o acerto deles neste produto são desconhecidos.**

No dia em que você tiver uma chave:

```bash
OPENAI_API_KEY=sk-... orbi bench --provider openai --model gpt-4.1-mini
ANTHROPIC_API_KEY=sk-ant-... orbi bench --provider anthropic --model claude-haiku-4-5
```

E compare a última linha da tabela. Isso encerra a pendência "bake-off dos
modelos" que o ORBI.md lista desde o começo.

## O provedor determinístico — a régua de baixo

| | |
|---|---|
| Acerto de tool | **100%** |
| Acerto de argumentos | **100%** |
| Latência | 0 ms |
| Custo | R$ 0 |

Ele não é IA: são regras escritas à mão para o português do domínio. Serve para
o CI e para demonstrar sem gastar cota — e como **piso de comparação**: um modelo
pago que acerte menos que ele no nosso conjunto não deveria entrar em produção.

Rodar o benchmark contra ele encontrou um defeito real: em *"qual o preço do tubo
pvc 100?"*, ele lia o **100 do nome do produto como quantidade**. Corrigido: agora
número só vira quantidade quando vem com unidade de venda ("50 sacos", "10 un").

---

# Recomendação

## Para desenvolvimento e CI
```bash
ORBI_LLM_PRIMARY=rule_based
```
Zero custo, zero cota, zero rede, determinístico. É o que roda nos 379 testes.

## Para PoC com cliente (hoje)
```bash
ORBI_LLM_PRIMARY=gemini
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_THINKING_BUDGET=-1
```
Camada gratuita, p50 de 706 ms, 90% de acerto. A cota de 20 perguntas/dia por
modelo é o limite real — dá para uma demonstração, **não** para um cliente com
10 vendedores.

## Para o primeiro cliente pagante
Ative billing no Google (a mesma chave passa a ter cota de produção) **ou** vá
para OpenAI. Meça antes de decidir:

```bash
orbi bench --provider gemini --model gemini-3.5-flash-lite
orbi bench --provider openai --model gpt-4.1-mini
orbi bench --provider openai --model gpt-5-mini
```

E configure o fallback de **outro fabricante** — o Orbi recusa subir com os dois
do mesmo, porque a queda seria correlacionada.

## Quando subir para um modelo mais caro
Quando o resumo diário mostrar taxa de ambiguidade alta que **não** seja culpa do
catálogo, ou o eval L1 cair abaixo de 90%. Aí o custo de R$ 29/mês do Opus 5
compra qualidade — e a essa altura você já sabe que ele resolve, porque mediu.

---

# Modelo por cliente ou modelo único?

Você perguntou qual é melhor. A resposta é **modelo único**, e por três motivos.

## 1. Não economiza nada
A diferença entre o modelo mais barato e o mais caro é R$ 29/mês por cliente.
Dar um modelo pior ao cliente do plano básico economizaria uns R$ 5 e criaria
todo o resto dos problemas abaixo.

## 2. Multiplica o que você precisa testar
Hoje o eval roda contra um modelo. Com três modelos em produção, todo ajuste de
prompt precisa passar em três — e o L5 (adversarial, tolerância zero) também.
Você triplica o trabalho de qualidade para economizar R$ 5.

## 3. Torna o suporte impossível de raciocinar
"O Orbi errou" passa a exigir "qual modelo esse cliente usa?" antes de qualquer
outra pergunta. Com um modelo só, o comportamento é o mesmo para todos e um bug
reproduzido uma vez está reproduzido para todos.

## O que **deve** diferenciar um plano

| Plano | Usuários | Mensal | O que muda |
|---|---|---|---|
| Essencial | até 5 | R$ 490 | teto de consultas menor |
| Time | até 15 | R$ 890 | teto maior |
| Operação | até 30 | R$ 1.490 | teto maior, prioridade no suporte |

O que diferencia é **número de usuários e teto de consultas** — coisas que o
cliente entende e que refletem custo real (suporte cresce com gente, não com
modelo). O teto já está implementado em `tenants.monthly_query_cap` e é validado
pela Policy Layer.

Quando o Plantão existir, ele será o plano superior — e aí a diferença é
**funcionalidade**, não qualidade de IA. Vender "IA melhor" por mais dinheiro é
prometer algo que o cliente não consegue verificar.

## E se um cliente precisar mesmo de outro modelo?

Acontece: um catálogo com vocabulário muito atípico pode se dar melhor com um
modelo maior. Nesse dia, o suporte a modelo por cliente é **pequeno de
implementar** — `tenant_settings` já existe e já guarda configuração por cliente;
seria acrescentar duas colunas e o Runtime escolher o provedor por tenant.

Mas isso entra **quando um cliente real precisar**, com o número medido que
justifique. É a mesma regra que manteve Redis fora do projeto: não adicionar
tecnologia sem um problema real que a justifique.

---

# Trocar de modelo: como e com que risco

Trocar é uma linha no `.env`. Provado, mesma pergunta, três configurações:

```
rule_based                → R$ 34,00 ·  331 ms
gemini-3.5-flash-lite     → R$ 34,00 · 1280 ms
gemini-flash-lite-latest  → R$ 34,00 · 1213 ms
```

Resposta idêntica. Isso funciona porque tudo passa pelo `LLMPort`: o Runtime não
sabe qual provedor está atrás, e cada provedor traduz a resposta para o mesmo
`ToolCallEnvelope`.

**O ritual ao trocar**, porque trocar de modelo é mudança de comportamento:

```bash
orbi bench --provider <novo> --model <novo>     # 1. mede antes
orbi evals --layer all --tenant <slug>          # 2. as cinco camadas
orbi maintenance shadow-evals --tenant <slug>   # 3. compara com o que já respondeu
```

O terceiro é o mais importante: ele pega as perguntas reais que seus clientes já
fizeram e verifica se o novo modelo escolheria a mesma operação. Regressão
aparece antes do usuário.

---

# Resumo em cinco linhas

1. **p95 é o número que decide** — é a experiência ruim que acontece toda hora.
2. **O LLM custa R$ 2 a 29 por cliente/mês.** Não é a linha que importa.
3. **Escolha por acerto e latência**, não por preço de token.
4. **Um modelo para todos.** Plano se diferencia por usuários e teto, não por IA.
5. **Meça antes de trocar** — `orbi bench` existe exatamente para isso.
