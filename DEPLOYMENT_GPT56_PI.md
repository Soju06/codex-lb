# GPT-5.6 Pi deployment

This branch pins the Responses Lite proxy implementation tested with Pi's
`openai-completions` adapter and includes GPT-5.6 bootstrap metadata for a fresh
database.

## Requirements

- A Linux server with Docker Engine, nginx, Git, and Certbot.
- A DNS `A` record such as `gateway.example.com` pointing to the server's public
  IPv4 address. Add `AAAA` only if nginx and the firewall also serve IPv6.
- Inbound TCP 22, 80, and 443. Port 18080 is needed only for the optional,
  unencrypted raw-IPv4 endpoint.
- A ChatGPT account authorized through codex-lb OAuth.
- Unique dashboard password, codex-lb API key, encryption key, and TLS private
  key. Never commit or copy these values into Git.

## 1. Build and run codex-lb

```bash
git clone --branch deploy/gpt56-pi-v1 --single-branch \
  https://github.com/hazrid93/codex-lb.git
cd codex-lb

umask 077
cp .env.example .env.local
sed -i 's#^CODEX_LB_DATABASE_URL=.*#CODEX_LB_DATABASE_URL=sqlite+aiosqlite:////var/lib/codex-lb/store.db#' .env.local
printf '%s\n' 'CODEX_LB_ENCRYPTION_KEY_FILE=/var/lib/codex-lb/encryption.key' >> .env.local

docker build -t codex-lb:gpt56-pi-v1 .
docker volume create codex-lb-data
docker run -d \
  --name codex-lb \
  --restart unless-stopped \
  --env-file .env.local \
  -p 127.0.0.1:2455:2455 \
  -p 127.0.0.1:1455:1455 \
  -v codex-lb-data:/var/lib/codex-lb \
  codex-lb:gpt56-pi-v1
```

The SQLite database, generated encryption key, OAuth tokens, settings, and API
key hashes now persist in `codex-lb-data`. Back up this volume securely.

Verify readiness:

```bash
curl http://127.0.0.1:2455/health/ready
```

## 2. Production nginx and TLS

Replace `gateway.example.com` before running these commands:

```bash
sudo apt-get update
sudo apt-get install -y nginx certbot python3-certbot-nginx

sudo tee /etc/nginx/sites-available/codex-lb >/dev/null <<'NGINX'
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

upstream codex_lb_backend {
    server 127.0.0.1:2455;
}

server {
    listen 80;
    listen [::]:80;
    server_name gateway.example.com;

    location / {
        proxy_pass http://codex_lb_backend;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }
}
NGINX

sudo ln -sfn /etc/nginx/sites-available/codex-lb /etc/nginx/sites-enabled/codex-lb
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d gateway.example.com --redirect
sudo nginx -t
curl https://gateway.example.com/health/ready
```

Certbot installs the certificate and HTTPS server block and configures automatic
renewal. DNS must already resolve to this server, and ports 80/443 must be
reachable during certificate issuance.

## 3. Dashboard secret, OAuth account, and API key

1. Open `https://gateway.example.com` and initialize a unique dashboard password.
2. Keep the OAuth callback tunnel open on the laptop running the browser:

   ```bash
   ssh -N -L 1455:127.0.0.1:1455 ubuntu@SERVER_IPV4
   ```

   Then add the ChatGPT account in the dashboard. The fixed OAuth redirect
   `http://localhost:1455/auth/callback` reaches the container through this
   tunnel. A dashboard device-code flow can be used instead when offered.
3. Create a new `sk-clb-...` API key in the dashboard.
4. In dashboard Settings, enable **API-key authentication**. Creating a key alone
   does not enable proxy authentication.
5. Verify without printing the full key:

   ```bash
   export CODEX_LB_API_KEY='sk-clb-REPLACE_ME'
   curl https://gateway.example.com/v1/models \
     -H "Authorization: Bearer $CODEX_LB_API_KEY"
   ```

Required secrets/state and where they live:

| Secret/state | Location |
|---|---|
| Dashboard password/TOTP | Encrypted application database in Docker volume |
| OAuth access/refresh tokens | Encrypted application database in Docker volume |
| Encryption key | `/var/lib/codex-lb/encryption.key` in Docker volume |
| `sk-clb` API key | Shown once; only its verifier is stored by codex-lb |
| TLS private key | `/etc/letsencrypt` on the host |

Do not expose container ports 2455 or 1455 publicly. The nginx HTTPS endpoint is
the normal production entry point.

## 4. Optional restricted raw-IPv4 endpoint

Use this only when the DNS name is blocked and HTTPS cannot be used. The API key
and request content travel in cleartext. Add the following server block inside
`/etc/nginx/sites-available/codex-lb`, then test and reload nginx:

```nginx
server {
    listen 18080 default_server;
    server_name _;

    location /v1/ {
        proxy_pass http://codex_lb_backend;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }

    location / {
        return 404;
    }
}
```

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Restrict port 18080 to trusted source IPs with the host/cloud firewall whenever
possible.

## 5. Pi provider

Add a custom provider to `~/.pi/agent/models.json`. Prefer the TLS URL. Use the
raw IPv4 URL only when necessary.

```json
{
  "providers": {
    "codex-lb": {
      "baseUrl": "https://gateway.example.com/v1",
      "api": "openai-completions",
      "apiKey": "sk-clb-REPLACE_ME",
      "models": [
        {
          "id": "gpt-5.6-sol",
          "name": "GPT-5.6-Sol (Codex LB)",
          "reasoning": true,
          "thinkingLevelMap": { "xhigh": "xhigh" },
          "input": ["text", "image"],
          "contextWindow": 372000,
          "maxTokens": 128000
        },
        {
          "id": "gpt-5.6-terra",
          "name": "GPT-5.6-Terra (Codex LB)",
          "reasoning": true,
          "thinkingLevelMap": { "xhigh": "xhigh" },
          "input": ["text", "image"],
          "contextWindow": 372000,
          "maxTokens": 128000
        },
        {
          "id": "gpt-5.6-luna",
          "name": "GPT-5.6-Luna (Codex LB)",
          "reasoning": true,
          "thinkingLevelMap": { "xhigh": "xhigh" },
          "input": ["text", "image"],
          "contextWindow": 372000,
          "maxTokens": 128000
        }
      ]
    }
  }
}
```

Pi streams Chat Completions requests by default. Clean-room testing confirmed
Sol and Terra streaming responses. Luna availability depends on the linked
ChatGPT account and OpenAI rollout.