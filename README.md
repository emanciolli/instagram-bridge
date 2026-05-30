# Instagram Bridge — Mention Monitor

Um pequeno servidor **FastAPI** que expõe a biblioteca não-oficial [`instagrapi`](https://github.com/subzeroid/instagrapi) como uma API REST, para que o site **Mention Monitor** (rodando em Node.js) consiga coletar menções públicas do Instagram.

## Por que existe este servidor?

O Instagram não oferece uma API pública para busca de menções. A única forma realista de capturar posts e hashtags em tempo real é usar uma biblioteca que simula o app oficial — e a melhor opção é `instagrapi`, que é Python. Como o servidor do Mention Monitor roda em Node, criamos esta "ponte" (bridge) que fica em qualquer provedor de containers (Railway, Fly.io, Render, DigitalOcean, etc.) e responde via HTTP autenticado.

## Como funciona

```
Mention Monitor (Node)  ──HTTPS──►  Instagram Bridge (Python)  ──►  Instagram
        ▲                                  │
        └──── menções coletadas ◄─────────┘
```

O Mention Monitor envia o **username e senha do Instagram** (armazenados criptografados no seu banco) a cada chamada. O bridge faz login, mantém a sessão em cache, busca os dados e devolve um JSON.

## Endpoints

| Método | Rota | Descrição |
| --- | --- | --- |
| `GET` | `/health` | Sonda de vida (sem auth). |
| `POST` | `/login` | Valida credenciais e armazena a sessão. |
| `POST` | `/search?keyword=foo` | Busca usuários e medias por palavra-chave. |
| `POST` | `/hashtag/recent?name=foo` | Medias recentes de uma hashtag. |
| `POST` | `/logout` | Descarta a sessão em cache. |

Todas as rotas (exceto `/health`) exigem o header `X-Bridge-Token: <BRIDGE_TOKEN>`.

## Variáveis de ambiente

| Variável | Obrigatório | Descrição |
| --- | --- | --- |
| `BRIDGE_TOKEN` | sim | Token compartilhado para autenticar chamadas vindas do Mention Monitor. Gere uma string longa e aleatória. |
| `PORT` | não | Porta de escuta (default `8000`, sobrescrita pelo Railway/Fly). |
| `SESSIONS_DIR` | não | Pasta para persistir sessões do Instagram (default `./sessions`). |

## Deploy no Railway (recomendado, ~$5/mês)

A forma mais rápida de subir esta ponte é via [Railway](https://railway.app):

1. Crie uma conta em railway.app e clique em **New Project → Deploy from GitHub repo**.
2. Crie um repositório novo no GitHub com **todos os arquivos desta pasta** (`main.py`, `requirements.txt`, `Dockerfile`, `railway.toml`).
3. No Railway, conecte o repo. Ele vai detectar o `Dockerfile` e fazer o build automaticamente.
4. Em **Variables**, adicione: `BRIDGE_TOKEN=<uma string longa aleatória>`. Pode gerar com `openssl rand -hex 32`.
5. Em **Settings → Networking**, clique em **Generate Domain**. Você vai receber uma URL pública como `https://instagram-bridge-production-xxxx.up.railway.app`.
6. Teste: `curl https://<sua-url>/health` deve retornar `{"ok": true, ...}`.
7. No Mention Monitor, vá em **Configurações** e cole a URL do bridge + o `BRIDGE_TOKEN` (peça para o Manus configurar o secret).

## Deploy alternativo no Fly.io

```bash
flyctl launch --copy-config --no-deploy
flyctl secrets set BRIDGE_TOKEN="$(openssl rand -hex 32)"
flyctl deploy
```

## Deploy em VPS (DigitalOcean / Hetzner / AWS Lightsail)

```bash
git clone <seu-repo>
cd instagram-bridge
docker build -t instagram-bridge .
docker run -d \
  -p 8000:8000 \
  -e BRIDGE_TOKEN="$(openssl rand -hex 32)" \
  -v $(pwd)/sessions:/app/sessions \
  --restart unless-stopped \
  --name instagram-bridge \
  instagram-bridge
```

Configure um reverse proxy (Caddy ou Nginx) com TLS na frente da porta 8000.

## Boas práticas

- **Use uma conta secundária do Instagram**, não a sua principal. Instagrapi simula um cliente real e a Meta pode bloquear contas que considerar suspeitas.
- **Habilite 2FA na conta principal** — a secundária você usa só para o monitoramento.
- **Não exponha o `BRIDGE_TOKEN`**. Trate-o como uma senha.
- **Persista a pasta `sessions/`** entre restarts (volume no Docker / Railway Volume) para evitar logins repetidos.
- Se a Meta bloquear a conta, troque por outra e atualize as credenciais no Mention Monitor.

## Limites e responsabilidade

Instagrapi é um cliente **não-oficial** mantido pela comunidade. O Meta pode mudar a API a qualquer momento e quebrar o bridge — quando isso acontecer, será necessário atualizar a versão da biblioteca em `requirements.txt`. O uso é por sua conta e risco; respeite os Termos de Serviço do Instagram.
