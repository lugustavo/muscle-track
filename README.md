# 💪 Muscle Track

App web **self-hosted** para personal trainers: cadastro de alunos, fichas de treino e acompanhamento da execução (séries, cargas, recordes, timer de descanso). Feito em Django, com PostgreSQL, HTTPS automático (Caddy) e instalável no celular (PWA).

## Funcionalidades

### 👩‍🏫 Personal (admin)
- **Painel de hoje** — quem está há mais de 7 dias sem treinar, quem ainda não aceitou o convite, planos e fichas que vencem em 14 dias, treinos do dia e comentários recentes dos alunos
- **Ficha do aluno** (em abas) — dados, objetivo, saúde/lesões/restrições (em destaque), contato de emergência, plano e vencimento
- **Avaliações físicas** — peso, altura, % de gordura e medidas, com IMC e gráfico de evolução
- **Fotos de progresso** — privadas (só a personal e o próprio aluno abrem), com miniaturas
- **Anotações datadas** — só a personal vê
- **Alunos** — cadastro com validação (data de nascimento, telefone, UF, CEP), aviso de cadastro duplicado, lista paginada com busca (nome, e-mail, telefone sem formatação, `#ID`) e filtros (plano vencendo/vencido, sem treinar, sem treino ativo, convite pendente)
- **Planos (modelos)** — catálogo de planos como o de exercícios: periodicidade, aulas por semana e valor de tabela. Ao atribuir ao aluno, nome e valor são copiados e podem ser ajustados por aluno
- **Valor negociado e valor/hora por aluno** — individual e **sigiloso**: só a personal vê (nunca aparece para o aluno, nem na exportação dele, nem no histórico)
- **Financeiro** — registro manual de pagamentos (valor e período sugeridos, vencimento do plano avança sozinho), recebido/previsto do mês, quem está em atraso e cobrança pelo WhatsApp (com sua chave Pix), exportação CSV para o Excel
- **Agenda** — horários fixos semanais por aluno, semana em colunas, marcar compareceu/faltou/cancelada, remarcar, aulas avulsas (experimental, reposição), aviso de sobreposição e frequência dos últimos 30 dias. Painel: aulas de hoje, faltas da semana e pagamentos em atraso, com WhatsApp pronto
- **Situação do aluno** — Experimental, Ativo, Pausado ou Ex-aluno, com motivo e data; pausado e ex-aluno perdem o acesso ao app e podem ser reativados. O painel avisa quem está em experimental há mais de 14 dias
- **Histórico de alterações** — quem mudou o quê e quando (dados, situação, convites, consentimentos); textos de saúde e anotações internas aparecem só como "alterado", sem o conteúdo
- **LGPD** — tela de consentimento no primeiro acesso (termos, dados de saúde, fotos opcional), registro de consentimento dado fora do app, política de privacidade em `/privacy/`, exportação dos dados (ZIP) e pedido de exclusão pelo aluno
- **Excluir aluno** — apaga conta, treinos, avaliações, anotações e os arquivos de foto (LGPD); para só bloquear o acesso mantendo o histórico, use *Aluno ativo* desmarcado
- **Convite por link** — ao cadastrar o aluno você recebe um link de uso único (copiar ou enviar por WhatsApp) para ele criar a senha; o mesmo link serve para redefinir a senha (funciona sem e-mail configurado) e pode ser **revogado** se foi enviado para a pessoa errada
- **Exercícios** com imagem, GIF, vídeo ou link
- **Modelos de treino** (A/B/C…) e atribuição ao aluno, com cópia editável por aluno (editar não apaga o histórico)
- **Geração de treino com IA** (opcional, OpenAI)
- **Detalhe da sessão** com séries, cargas e os comentários do aluno

### 🏋️ Aluno
- **Treino de hoje** — o app sugere o próximo treino pelo rodízio
- **Controle por série** — carga e repetições já preenchidas com os valores da última vez
- **Timer de descanso** (som e vibração) e **timer do treino com pausa**; a tela fica acesa
- **Recordes e evolução** por exercício, 🏆 ao bater recorde de carga
- **Observações** por exercício e do treino
- **Funciona com internet fraca** — séries marcadas offline sincronizam depois
- **Instalável no celular** ("Adicionar à tela inicial")

## Instalação em um servidor (VPS)

**Você precisa de:** um servidor Linux com [Docker](https://docs.docker.com/engine/install/) (e o plugin Compose), um **domínio** apontando para o IP do servidor (registro `A`) e as portas **80 e 443** abertas.

```bash
git clone https://github.com/lugustavo/muscle-track.git
cd muscle-track
cp .env.example .env
nano .env          # preencha DOMAIN, SECRET_KEY e POSTGRES_PASSWORD (instruções no arquivo)
docker compose up -d --build
```

Na primeira subida o Caddy emite o certificado HTTPS sozinho (leva ~1 min). Depois crie a conta da personal:

```bash
docker compose exec web python manage.py createsuperuser
```

Pronto: abra `https://SEU-DOMINIO`, entre com essa conta e cadastre o primeiro aluno.

Opcional — grupos musculares, exercícios e treinos modelo prontos:

```bash
docker compose exec web python populate_db.py
```

(`populate_db.py --demo` cria também alunos de exemplo; **não use em produção**.)

### Operação do dia a dia

| O que | Comando |
|---|---|
| Ver logs | `docker compose logs -f web` |
| Estado dos serviços | `docker compose ps` |
| Atualizar para a versão mais nova | `git pull && docker compose up -d --build` (as migrações rodam sozinhas) |
| Reiniciar | `docker compose restart` |
| Parar | `docker compose down` (os dados ficam nos volumes) |
| Abrir um shell Django | `docker compose exec web python manage.py shell` |

Os dados vivem em volumes do Docker: `pgdata` (banco), `media` (fotos e mídia dos exercícios) e `caddy_data` (certificados).

> ⚠️ **Backup ainda não está automatizado** (está no backlog abaixo). Enquanto isso, o volume `pgdata` é a única cópia dos seus dados. Um backup manual rápido:
> ```bash
> docker compose exec -T db pg_dump -U muscle_track muscle_track > backup-$(date +%F).sql
> docker run --rm -v muscle-track_media:/m -v "$PWD":/b alpine tar czf /b/media-$(date +%F).tgz -C /m .
> ```

### Checklist pós-instalação
- [ ] `https://SEU-DOMINIO` abre com cadeado e `http://` redireciona para `https://`
- [ ] Login da personal funciona
- [ ] Cadastrar um aluno gera um link `https://…/invite/…` e o aluno consegue criar a senha
- [ ] No celular do aluno: abrir o site → "Adicionar à tela inicial"
- [ ] Anote o `.env` num lugar seguro (sem o `SECRET_KEY` e a senha do banco, não há como restaurar)

## Configuração (`.env`)

| Variável | Para quê |
|---|---|
| `DOMAIN` | Domínio público. Deriva hosts permitidos, origens CSRF e o endereço dos convites; liga cookies seguros e HSTS |
| `SECRET_KEY` | Chave secreta do Django (obrigatória em produção) |
| `POSTGRES_PASSWORD` | Senha do banco (só letras e números) |
| `ALLOW_PUBLIC_SIGNUP` | `False` (padrão): só entra quem foi convidado |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | Geração de treino com IA (opcional) |
| `PRIVACY_CONTROLLER_NAME`, `PRIVACY_CONTACT_EMAIL` | Nome e e-mail do responsável pelos dados, exibidos na política de privacidade |
| `PIX_KEY` | Chave Pix incluída na mensagem de cobrança pelo WhatsApp (opcional) |
| `EMAIL_*` | SMTP, só para o "esqueci minha senha" (opcional) |
| `SITE_URL`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SECURE_HSTS_SECONDS` | Avançado: sobrescrevem os valores derivados de `DOMAIN` |

## Desenvolvimento local (sem Docker)

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # deixe DOMAIN vazio e defina DEBUG=True (usa SQLite)
python manage.py migrate
python manage.py createsuperuser
python populate_db.py           # opcional
python manage.py runserver
python manage.py test           # suíte de testes
```

Bootstrap, ícones, Chart.js e SortableJS são servidos localmente de `static/vendor/` (sem CDN); veja `static/vendor/README.md`.

## Convenções de navegação (para telas novas)

"Voltar" sempre leva para **onde o usuário estava**, não para um lugar fixo (`core/navigation.py`):

1. **Links** para páginas de detalhe/formulário usam `{% nav_url 'core:view' args %}` — ele acrescenta `?next=<página atual, com busca/filtros>`.
2. **Voltar/Cancelar** usam `{% back_button default_url %}` — vai para `next` (se for um caminho local válido) ou para o `default_url` da tela; o rótulo vem do destino ("Voltar ao painel", "Voltar ao aluno"…).
3. **Depois de salvar/excluir**, a view redireciona com `after_save_url(request, url_da_entidade)`: volta para `next` se ele for a própria entidade; senão vai para a entidade repassando `next`.

**Breadcrumbs** (hierarquia; o Voltar é o histórico): ficam num registro único, `core/breadcrumbs.py` (`TRAILS`), montado a partir do nome da URL e dos objetos que a view já colocou no contexto — sem consultas extras e sem mudar as views. Tela nova = uma função no registro (ou entrada em `NO_BREADCRUMB`); um teste falha se uma rota nova não estiver em nenhum dos dois. Rótulos iguais aos do menu (Alunos, Exercícios, Treinos).

Formulários ficam sem `action` (o POST mantém o `?next=`). O teste `core/tests/test_navigation_global.py` percorre todos os links com `next=` e confere o botão de voltar de cada página.

## Segurança — o que já está coberto
- Área da personal restrita a contas `is_staff`; cada aluno só enxerga e altera os **próprios** treinos e sessões
- Cadastro público fechado; acesso por convite de uso único (7 dias), revogável
- Consentimento LGPD registrado (versão do texto, data, quem), exportação e exclusão de dados; histórico não guarda conteúdo de saúde
- Valor/hora e valor negociado só na área da personal
- Anotações internas da personal nunca aparecem nem são editáveis pela aluna
- HTTPS automático, cookies `Secure`, HSTS, `X-Content-Type-Options`
- Container da aplicação roda sem root; só o Caddy expõe portas (banco e app ficam na rede interna)
- Nenhuma senha fixa; `SECRET_KEY` obrigatório

## Backlog
- **Backup automático** (pg_dump diário + `media`, retenção e restauração testada; cópia fora do servidor) — a pasta `media` inclui as fotos de progresso, que são dados sensíveis
- Configurar SMTP no servidor para o "esqueci minha senha" chegar por e-mail (hoje o caminho é a personal gerar um novo link)
- Revisar o texto da política de privacidade (`templates/core/privacy_policy.html`, é um modelo) com apoio jurídico; subir `CURRENT_VERSION` em `core/consent.py` quando o texto mudar
- Cobrança automática com gateway (Pix/cartão)
- Lembrete automático de plano vencendo, de aula e de reavaliação (depende das notificações abaixo)
- Metas com prazo e lembrete de reavaliação, marcação do aniversário, importação/exportação de alunos em CSV
- Aluno ver as próprias avaliações e fotos de progresso; comparação lado a lado de fotos
- Séries por tempo/técnicas (drop-set, bi-set), periodização
- Monitoramento/alertas e logs centralizados
- Servir `media/` direto pelo Caddy

### Agenda — próximos passos
- **Recorrência e várias datas (como no Google Agenda):** hoje o horário fixo é só "dia da semana + hora". Aceitar repetir a cada N semanas, vários dias da semana, início e fim, e "termina: nunca / em uma data / após N ocorrências"; e criar várias aulas avulsas de uma vez (intervalo de datas). Ao editar uma aula de uma série, perguntar "só esta / esta e as próximas / todas", sem regenerar aulas canceladas.
- **Arrastar e soltar:** mover a aula para outro dia ou horário direto na grade (e ajustar a duração), com o efeito de "remarcar" (a original fica remarcada, a nova é criada), aviso de sobreposição e desfazer. Tem que funcionar bem no celular.
- **Tipos de vista:** mês, semana, dia, previsão (próximos dias) e lista/agenda no estilo do calendário do Sonarr/Radarr, com seletor e a escolha lembrada. A semana atual continua sendo a padrão.
- **Visão do aluno:** o aluno ver a própria agenda e pedir remarcação (a personal aprova), com prazo de cancelamento.

### Notificações dos alunos
- **Quando avisar:** ao marcar falta, cancelamento ou remarcação na agenda; depois, lembrete de aula, vencimento do plano e reavaliação.
- **Canais:** web push, Telegram e e-mail, só pelos que o aluno autorizou. Hoje não existe seção de notificações: criar **Notificações** no perfil do aluno, com liga/desliga por canal e por tipo de aviso.
  - *Web push:* assinatura do navegador/PWA (chaves VAPID, `pywebpush`), aproveitando o service worker que já existe.
  - *Telegram:* bot; o aluno vincula o próprio chat por um link ou código de uso único gerado no app.
  - *E-mail:* precisa do SMTP configurado no servidor (já no backlog).
- **LGPD:** cada canal é um consentimento próprio (versão e data, como em `core/consent.py`), revogável a qualquer momento; sem consentimento não se envia nada. Mensagens sem dados sensíveis (sem valores nem saúde).
- **Infra:** envio assíncrono com tentativas e registro de entrega/falha, e o aviso registrado no histórico do aluno. Se nenhum canal estiver autorizado, a personal segue com o botão de WhatsApp que já existe.
- *Ordem sugerida:* seção de notificações e consentimentos → e-mail (só depende de SMTP) → web push → Telegram.

### Normalização de UX
- **Multi-idioma (pt-BR e inglês primeiro):** usar o i18n do Django (`gettext_lazy`, `{% trans %}`/`{% blocktrans %}`, `LocaleMiddleware`, `locale/`) com seletor de idioma (cookie/preferência do usuário). Cobrir templates, mensagens das views, labels/choices de models e forms, textos em JS (alertas do treino), o texto do convite por WhatsApp e o idioma do prompt da IA; formatos de data/número por idioma. Hoje há **0** textos marcados para tradução (todo o português está fixo em ~35 templates, views, forms e JS). Os testes devem rodar nos dois idiomas.
- **Temas claro / escuro / automático:** Bootstrap 5.3 `data-bs-theme`, seletor no menu (guardado no navegador/perfil), "automático" via `prefers-color-scheme` e script no `<head>` para evitar o "flash". Trocar cores fixas por variáveis CSS, revisar `bg-light`/`text-dark`/estilos inline, cores do Chart.js e o `theme-color` do PWA; checar contraste (WCAG AA).
- *Ordem sugerida:* temas → idiomas (o maior; mexe em todas as strings, melhor com o layout estável). Os breadcrumbs já estão padronizados.

## Estrutura

```
config/            settings, urls, wsgi
core/              app principal (models, views, forms, permissions, invites, ai_service, tests/)
templates/         HTML (core/, account/, pwa/)
static/            CSS/JS/imagens do app e vendor/ (libs locais)
Dockerfile, docker-compose.yml, Caddyfile, entrypoint.sh
docs/              documentos históricos
```
