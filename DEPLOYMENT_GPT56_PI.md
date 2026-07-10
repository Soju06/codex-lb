# GPT-5.6 Pi deployment

This branch pins the Responses Lite proxy implementation tested with Pi's
`openai-completions` adapter and includes GPT-5.6 bootstrap metadata for fresh
databases.

## 1. Build and run codex-lb

```bash
git clone --branch deploy/gpt56-pi-v1 --single-branch \
  https://github.com/hazrid93/codex-lb.git
cd codex-lb
cp .env.example .env.local
# Edit .env.local and set unique production secrets before continuing.

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

Verify readiness:

```bash
curl http://127.0.0.1:2455/health/ready
```

Open the dashboard through a TLS reverse proxy, link the ChatGPT account, and
create a codex-lb API key. Do not copy encryption keys, dashboard passwords, or
API keys into Git.

## 2. Restricted raw-IPv4 nginx endpoint (optional)

Use this only when a domain is blocked and HTTPS cannot be used. API keys travel
in cleartext over this HTTP endpoint.

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

upstream codex_lb_backend {
    server 127.0.0.1:2455;
}

server {
    listen 18080 default_server;
    server_name _;

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

    location /v1/ {
        proxy_pass http://codex_lb_backend;
    }

    location / {
        return 404;
    }
}
```

After installing the site, run `sudo nginx -t` and reload nginx. Restrict port
18080 with a firewall whenever possible.

## 3. Pi provider

Add a custom provider to `~/.pi/agent/models.json`. Replace the base URL and API
key for the new server.

```json
{
  "providers": {
    "codex-lb": {
      "baseUrl": "http://SERVER_IPV4:18080/v1",
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

Pi streams Chat Completions requests by default. Live testing confirmed Sol and
Terra text responses and function tool calls. Luna availability depends on the
linked ChatGPT account and OpenAI rollout.