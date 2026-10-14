> **Documento histórico** — escrito antes das Fases 0–4 e pode estar desatualizado (login social, senhas fixas e e-mail de ativação foram removidos). A referência atual é o [README](../README.md).

# Fluxo de Cadastro de Alunos

## Visão Geral

O sistema implementa um fluxo completo de cadastro de alunos com confirmação de email via django-allauth.

## Processo de Cadastro

### 1. Admin cadastra o aluno

O administrador acessa o formulário de cadastro e preenche:
- Nome completo
- **Email** (campo obrigatório)
- Avatar (opcional)
- Endereço, cidade, estado, CEP
- Telefone
- Data de nascimento
- Observações
- Status (ativo/inativo)

### 2. Sistema cria usuário automaticamente

Quando o formulário é salvo:
1. Um usuário Django é criado com:
   - `username = email` (sempre sincronizado)
   - `email = email fornecido`
   - `is_active = False` (inativo até confirmar email)

2. Um registro Person é criado associado ao usuário

3. Um EmailAddress (allauth) é criado:
   - `verified = False`
   - `primary = True`

### 3. Email de confirmação é enviado

O sistema envia automaticamente um email para o endereço fornecido com:
- Link de confirmação único
- Instruções para ativar a conta

### 4. Aluno confirma o email

O aluno:
1. Recebe o email
2. Clica no link de confirmação
3. É redirecionado para definir senha
4. Conta é ativada (`is_active = True`)
5. Email é marcado como verificado

## Edição de Aluno

Quando o admin edita um aluno existente:

- Se o **email NÃO for alterado**: apenas os dados do Person são atualizados
- Se o **email for alterado**:
  1. Email e username do User são atualizados (sempre sincronizados)
  2. EmailAddress é marcado como não verificado
  3. Novo email de confirmação é enviado
  4. Aluno precisa confirmar o novo email

## Configuração de Email

### Desenvolvimento (padrão)

O sistema usa `console.EmailBackend` que exibe emails no console/terminal.

```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

### Produção

Configure um servidor SMTP real no arquivo `.env`:

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=seu-email@gmail.com
EMAIL_HOST_PASSWORD=sua-senha-de-app
DEFAULT_FROM_EMAIL=noreply@muscle-track.com
```

**Nota**: Para Gmail, você precisa:
1. Ativar autenticação de 2 fatores
2. Gerar uma "Senha de app"
3. Usar essa senha em `EMAIL_HOST_PASSWORD`

## Validações

### Email único
- O sistema verifica se o email já está cadastrado
- Não permite duplicatas
- Na edição, permite manter o email atual

### Username = Email
- O username é SEMPRE igual ao email
- Sincronização automática em criação e edição
- Garante que o login sempre use o email

## Arquivos Modificados

- **core/forms.py**: PersonForm com campo email
- **core/views.py**: student_create e student_edit com lógica de User
- **core/models.py**: Person com get_avatar_url()
- **templates/core/student_form.html**: Formulário com email e avatar

## Fluxo Técnico

```
Admin → Formulário → PersonForm.clean_email()
                           ↓
                    Validação de duplicidade
                           ↓
                    student_create/edit view
                           ↓
                    User.objects.create_user()
                    (username=email, is_active=False)
                           ↓
                    Person.objects.create()
                    (user=user_criado)
                           ↓
                    EmailAddress.objects.create()
                    (verified=False)
                           ↓
                    send_email_confirmation()
                           ↓
                    Email enviado ao aluno
                           ↓
                    Aluno clica no link
                           ↓
                    Conta ativada + Email verificado
```

## Mensagens ao Admin

- **Cadastro com sucesso**: "Aluno cadastrado com sucesso! Um email de confirmação foi enviado para {email}."
- **Edição sem alteração de email**: "Aluno atualizado com sucesso!"
- **Edição com alteração de email**: "Aluno atualizado com sucesso! Um novo email de confirmação foi enviado para {email}."

## Segurança

- Aluno não pode fazer login até confirmar email
- Link de confirmação é único e temporário
- Senha é definida apenas pelo próprio aluno
- Admin nunca tem acesso à senha do aluno
