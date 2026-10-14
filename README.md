# 💪 Muscle Track - Sistema de Gerenciamento de Treinos

Aplicação web completa desenvolvida em **Django** para gestão de alunos, exercícios e planos de treino personalizados, com timer de treino, histórico de evolução e geração automática de treinos via **IA (ChatGPT)**.

---

## 🎯 Funcionalidades

### 👨‍💼 Área Administrativa
- ✅ **Cadastro de Alunos** - Registro completo com dados pessoais e contato
- ✅ **Cadastro de Exercícios** - Com suporte a vídeos, GIFs e imagens (YouTube, uploads, URLs)
- ✅ **Montagem de Treinos** - Criar planos personalizados associando exercícios
- ✅ **Atribuição de Treinos** - Vincular treinos aos alunos com datas de início/fim
- ✅ **Histórico e Relatórios** - Visualizar evolução dos alunos com gráficos
- ✅ **Geração de Treinos com IA** - Usar ChatGPT para criar treinos automaticamente

### 👤 Área do Aluno
- ✅ **Meus Treinos Ativos** - Visualizar treinos disponíveis
- ✅ **Timer de Treino** - Iniciar sessão com cronômetro automático
- ✅ **Controle de Execução** - Marcar exercícios concluídos e registrar carga utilizada
- ✅ **Histórico Pessoal** - Ver treinos anteriores e evolução

### 🔐 Autenticação
- ✅ **Convite por link** - A personal cadastra o aluno e envia (copiar ou WhatsApp) um link de uso único para ele criar a senha; o mesmo link serve para redefinir o acesso
- ✅ **Django Allauth** - Sistema completo de autenticação

### 🎨 Interface
- ✅ **Design Responsivo** - Bootstrap 5 para desktop e mobile
- ✅ **Interface Intuitiva** - Fácil navegação e uso

---

## 📋 Requisitos

- Python 3.11+
- SQLite (incluído)
- Pip

---

## 🚀 Instalação e Configuração

### 1. Clone o repositório
```bash
git clone <url-do-repositorio>
cd muscle-track2
```

### 2. Crie e ative um ambiente virtual (opcional, mas recomendado)
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

### 3. Instale as dependências
```bash
pip install -r requirements.txt
```

### 4. Configure as variáveis de ambiente
Copie o arquivo `.env.example` para `.env` e edite conforme necessário:
```bash
cp .env.example .env
```

**Configurações principais no `.env`:**
- `SECRET_KEY` - Chave secreta do Django (mude em produção!)
- `DEBUG` - `True` só no seu computador (o padrão é `False`; com `False` o `SECRET_KEY` é obrigatório)
- `OPENAI_API_KEY` - Sua chave da API do OpenAI (para geração de treinos com IA)
- `SITE_URL` - endereço público do app (usado nos links de convite; se vazio, usa o endereço do navegador da personal)
- Configurações de email (opcional: só para o "esqueci a senha")

### 5. Execute as migrations
```bash
python manage.py migrate
```

### 6. Crie a conta da personal (admin) e, opcionalmente, dados iniciais
```bash
python manage.py createsuperuser        # conta da personal trainer (is_staff)
python populate_db.py                   # grupos musculares, exercícios e modelos de treino A/B/C/D
python populate_db.py --demo            # + alunos/sessões de exemplo (apenas desenvolvimento)
```

O script é idempotente. **Não existem mais contas com senha fixa**: a senha dos alunos demo vem de
`DEMO_PASSWORD` ou é gerada e exibida uma única vez. O `build.sh` (deploy) nunca apaga nem popula o banco.

### 7. Execute o servidor
```bash
python manage.py runserver
```

Acesse: **http://localhost:8000**

---

## 🔑 Acesso e papéis

- **Personal trainer (admin):** conta criada com `createsuperuser` (ou qualquer usuário `is_staff`). Acessa alunos, exercícios, treinos e históricos.
- **Aluno:** usuário vinculado a um aluno cadastrado. Só enxerga e altera os **próprios** treinos e sessões (outras contas recebem 403/404).

---

## 📂 Estrutura do Projeto

```
muscle-track2/
├── config/                 # Configurações do Django
│   ├── settings.py        # Settings principais
│   ├── urls.py            # URLs raiz
│   └── wsgi.py
├── core/                   # App principal
│   ├── models.py          # Modelos de dados
│   ├── views.py           # Views
│   ├── forms.py           # Formulários
│   ├── admin.py           # Configuração do Admin
│   ├── urls.py            # URLs do app
│   └── ai_service.py      # Integração com ChatGPT
├── templates/              # Templates HTML
│   ├── base.html          # Template base
│   └── core/              # Templates do app
├── static/                 # Arquivos estáticos (CSS, JS)
├── media/                  # Uploads de mídia
├── .env                    # Variáveis de ambiente
├── .env.example            # Exemplo de variáveis
├── manage.py               # Django management
├── populate_db.py          # Script de dados iniciais
├── requirements.txt        # Dependências Python
└── README.md               # Este arquivo
```

---

## 🗄️ Modelo de Dados

### Principais Modelos

**Person** (Aluno)
- Informações pessoais (nome, endereço, telefone, data nascimento)
- Vinculado a um User (autenticação)

**Exercise** (Exercício)
- Nome, descrição, grupo muscular
- Séries e repetições padrão
- Mídia (imagem, GIF, vídeo, YouTube)
- Thumbnail para visualização

**Routine** (Treino/Modelo)
- Nome do treino
- Descrição e nível de dificuldade
- Duração estimada

**RoutineExercise** (Exercícios do Treino)
- Vincula exercícios a um treino
- Define ordem, séries, reps, descanso

**PersonRoutine** (Treino do Aluno)
- Atribui um treino a um aluno
- Datas de início/fim
- Status ativo/inativo

**PersonRoutineExercise** (Execução)
- Controle de execução de cada exercício
- Carga utilizada
- Status de conclusão

**WorkoutSession** (Sessão de Treino)
- Timer automático
- Duração calculada
- Histórico de treinos realizados

---

## 🤖 Geração de Treinos com IA

Para usar a funcionalidade de geração automática de treinos:

1. Obtenha uma chave da API do OpenAI em: https://platform.openai.com/api-keys
2. Configure no arquivo `.env`:
   ```
   OPENAI_API_KEY=sk-proj-...
   ```
3. Acesse no menu: **Treinos > Gerar com IA**
4. Descreva o treino desejado e a IA criará automaticamente

---

## 🔧 Login social (SSO)

Removido por enquanto: os botões eram apenas placeholders. O login é por e-mail e senha.

---

## 🚀 Deploy em Produção

### Configurações Importantes

1. **Altere o SECRET_KEY** no `.env`
2. **Configure DEBUG=False**
3. **Configure ALLOWED_HOSTS** com seu domínio
4. **Configure email SMTP real** (não use console backend)
5. **Use um servidor web** (Gunicorn + Nginx)

### Exemplo com Gunicorn
```bash
gunicorn config.wsgi:application --bind 0.0.0.0:8000
```

### Colete arquivos estáticos
```bash
python manage.py collectstatic --noinput
```

---

## 📱 Uso da Aplicação

### Para Administradores/Instrutores:

1. **Faça login** com credenciais de admin
2. **Cadastre exercícios** com vídeos/imagens
3. **Crie treinos** montando os exercícios
4. **Cadastre alunos**: ao salvar você recebe um link de convite para enviar ao aluno (botão *Convite* na ficha dele)
5. **Atribua treinos** aos alunos
6. **Acompanhe evolução** no histórico

### Para Alunos:

1. **Abra o link de convite** enviado pela personal, crie a senha (depois entre normalmente pelo login)
2. **Acesse "Meus Treinos"**
3. **Inicie um treino** (timer começa automaticamente)
4. **Marque exercícios concluídos** e registre a carga
5. **Finalize o treino** (duração é salva)
6. **Veja seu histórico** e evolução

---

## 🛠️ Tecnologias Utilizadas

- **Backend:** Django 5.0
- **Database:** SQLite (pode migrar para PostgreSQL/MySQL)
- **Frontend:** Bootstrap 5, HTML5, CSS3, JavaScript
- **Autenticação:** Django Allauth
- **IA:** OpenAI GPT-4
- **Forms:** Django Crispy Forms
- **Deploy:** Whitenoise (static files), Gunicorn

---

## 📝 Notas Adicionais

- O sistema usa **SQLite** por padrão, ideal para desenvolvimento e pequenas instalações
- Para produção com muitos usuários, considere migrar para **PostgreSQL** ou **MySQL**
- As imagens/vídeos são armazenadas na pasta `media/`
- O timer funciona com JavaScript no frontend e salva duração no backend
- A geração de treinos com IA requer créditos na API da OpenAI

---

## 🐛 Troubleshooting

### Erro "MultipleObjectsReturned" ao executar populate_db.py
Isso ocorre quando há registros duplicados. Solução:
```bash
# Limpe o banco e refaça
rm db.sqlite3
python manage.py migrate
python populate_db.py
```

A versão corrigida do script é idempotente e pode ser executada múltiplas vezes.

### Erro ao fazer migrations
```bash
# Limpe o banco e refaça
rm db.sqlite3
python manage.py migrate
python populate_db.py
```

### Erro de encoding no Windows
O script `populate_db.py` já trata isso automaticamente.

### Erro ao carregar imagens
Verifique se a pasta `media/` tem permissões corretas.

---

## 📧 Suporte

Para dúvidas ou problemas, entre em contato através do repositório.

---

## 📄 Licença

Este projeto foi desenvolvido para fins educacionais e de demonstração.

---

**Desenvolvido com Django + Bootstrap + OpenAI** 💪🔥
