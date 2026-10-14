# 💪 Muscle Track

App web **self-hosted** para personal trainers: cadastro de alunos, fichas de treino e acompanhamento da execução (séries, cargas, recordes, timer de descanso). Feito em Django, com PostgreSQL, HTTPS automático (Caddy) e instalável no celular (PWA).

## Funcionalidades

### 👩‍🏫 Personal (admin)
- **Alunos** — cadastro, ficha, histórico e evolução
- **Convite por link** — ao cadastrar o aluno você recebe um link de uso único (copiar ou enviar por WhatsApp) para ele criar a senha; o mesmo link serve para redefinir o acesso
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

## Segurança — o que já está coberto
- Área da personal restrita a contas `is_staff`; cada aluno só enxerga e altera os **próprios** treinos e sessões
- Cadastro público fechado; acesso por convite de uso único (7 dias)
- HTTPS automático, cookies `Secure`, HSTS, `X-Content-Type-Options`
- Container da aplicação roda sem root; só o Caddy expõe portas (banco e app ficam na rede interna)
- Nenhuma senha fixa; `SECRET_KEY` obrigatório

## Backlog
- **Backup automático** (pg_dump diário + `media`, retenção e restauração testada; cópia fora do servidor)
- E-mail (SMTP) para "esqueci minha senha"
- Ficha completa da personal: objetivo/lesões, avaliações físicas, fotos de progresso, notas, vencimento do plano e painel "quem sumiu"
- Séries por tempo/técnicas (drop-set, bi-set), periodização
- Monitoramento/alertas e logs centralizados
- Servir `media/` direto pelo Caddy

## Estrutura

```
config/            settings, urls, wsgi
core/              app principal (models, views, forms, permissions, invites, ai_service, tests/)
templates/         HTML (core/, account/, pwa/)
static/            CSS/JS/imagens do app e vendor/ (libs locais)
Dockerfile, docker-compose.yml, Caddyfile, entrypoint.sh
docs/              documentos históricos
```
