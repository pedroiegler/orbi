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
**O número é do cliente**, não do Orbi — custo zero para a operação e melhor
posição na LGPD.

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

---

## Planos e teto de consultas

| Plano | Usuários | Mensal |
|---|---|---|
| Essencial | até 5 | R$ 490 |
| Time | até 15 | R$ 890 |
| Operação | até 30 | R$ 1.490 |

Cobra-se por usuário, não por consulta — o custo variável real é suporte, não
token. O teto existe para conter abuso: um cliente entusiasmado sozinho dobra a
conta de LLM. Ele fica em `tenant_settings.rate_limit_per_day`.

⚠️ **A partir de outubro de 2026 a Meta volta a cobrar as respostas dentro da
janela de 24 horas.** Quando a tabela do Brasil sair, o custo por cliente precisa
ser recalculado e o teto deixa de ser só proteção contra abuso: passa a ser
controle de margem.

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
