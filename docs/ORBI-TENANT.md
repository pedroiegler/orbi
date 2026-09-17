# ORBI — Clientes (tenants)

Criar, configurar, suspender e encerrar um cliente. Tudo por CLI: não existe
painel (D-014).

---

## Criar

```bash
orbi tenant add --tenant construtora-silva \
  --name "Construtora Silva LTDA" \
  --phone "+554330001111" \
  --phone-number-id "<phone_number_id da Cloud API>" \
  --plan time
```

O que acontece: cria o tenant, cria `tenant_settings` com os limiares padrão
(recalibrados no onboarding) e habilita as quatro tools — que o
`orbi erp capabilities` depois desliga sozinho se o ERP não atender.

**Um número por tenant.** A identificação fica determinística e o risco de
banimento fica isolado: um cliente com problema não derruba os outros.

**De quem é o número, o cliente escolhe** (D-043). No Business Portfolio dele —
o padrão, com custo zero para a operação, melhor posição na LGPD e saída limpa —
ou no nosso, quando ele ainda não tem portfólio verificado e a espera mataria a
PoC. O código é indiferente: guarda endereço, `phone_number_id` e token cifrado.
O roteiro das duas está em [ORBI-IMPLANTACAO.md](ORBI-IMPLANTACAO.md).

## Token do canal

```bash
orbi tenant set-token --tenant construtora-silva --token "<token>"
```

Fica cifrado (Fernet, chave em `ORBI_SECRET_KEY`, fora do banco). Nunca aparece
em log nem em `orbi tenant show`.

## Ver a configuração

```bash
orbi tenant list
orbi tenant show --tenant construtora-silva
```

`show` traz plano, número, se há token, os limiares calibrados e quais tools
estão ligadas.

## Suspender e reativar

```bash
orbi tenant deactivate --tenant construtora-silva   # perguntas recebem recusa educada
orbi tenant activate   --tenant construtora-silva
```

Suspender é a primeira regra da Policy Layer: nem chega a olhar a tool. Use em
inadimplência ou em suspeita de incidente.

## Modo debug (piloto)

```bash
orbi tenant debug --tenant construtora-silva --on
orbi tenant debug --tenant construtora-silva --off
```

Acrescenta o código curto do trace no rodapé da resposta. Ligue na primeira
semana; desligue depois.

O caminho de volta é `orbi trace show <código> --tenant <slug>` — sem ele o
código sairia na resposta e não levaria a lugar nenhum.

---

## Planos e teto de consultas

| Plano | Usuários | Consultas/mês | Mensal |
|---|---|---|---|
| Essencial | até 5 | 3.000 | R$ 490 |
| Time | até 15 | 10.000 | R$ 890 |
| Operação | até 30 | 20.000 | R$ 1.490 |

O plano é cumprido pelo código: define o teto de consultas e o limite de
usuários, e o cadastro do sexto usuário num plano de cinco é recusado. A oferta
completa está em [ORBI-COMERCIAL.md](ORBI-COMERCIAL.md).

Cobra-se por usuário, não por consulta — o custo variável real é suporte, não
token.

São **dois limites diferentes**, e confundi-los faz mexer na coluna errada:

| | Onde fica | Padrão | Do que protege |
|---|---|---|---|
| Teto do plano, por mês | `tenants.monthly_query_cap` | do plano (3.000 a 20.000) | o contrato: é o número que o cliente comprou |
| Rate limit, por dia e por minuto | `tenant_settings.rate_limit_per_day` / `_per_minute` | 300 / 12 | rajada e laço: um número em loop não queima o mês inteiro numa tarde |

O teto do plano muda com `orbi tenant set-plan`, nunca editando a coluna à mão —
senão o plano vendido e o teto aplicado se separam.

⚠️ **A partir de outubro de 2026 a Meta volta a cobrar as respostas dentro da
janela de 24 horas** (confirmado na página oficial): ≈ R$ 0,035 por mensagem, com
1.000 grátis por número/mês segundo fontes secundárias. Um cliente Time encostado
no teto perde metade da margem: o teto deixa de ser só proteção contra abuso e
passa a ser controle de margem. Conta e níveis de confirmação em
[ORBI-COMERCIAL.md](ORBI-COMERCIAL.md).

---

## Encerrar um cliente

Ordem importa:

```bash
orbi tenant deactivate --tenant <slug>        # 1. para de responder
orbi user list --tenant <slug>                # 2. registra quem tinha acesso
# 3. o catálogo é desativado, nunca apagado — a auditoria referencia esses ids
orbi maintenance verify-audit --tenant <slug> # 4. prova a integridade antes de arquivar
```

`audit_logs` fica pelos 12 meses de retenção, mesmo com o cliente encerrado. Se
o cliente pedir exclusão de dados pessoais, use `orbi user erase --tenant <slug>
--phone <numero>`: anonimiza o texto e preserva as métricas.
