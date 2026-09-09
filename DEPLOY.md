# Deploy: Mac → GitHub → GCP

Both **forwarder** and **okx_bot** run on a single GCP VM via Docker Compose. GitHub Actions SSHs in on push to `main` (or manual **Deploy** / `workflow_dispatch`), pulls code, and rebuilds containers.

Secrets and Telegram sessions stay on the VM — never commit them.

```text
Mac (git push) → GitHub main → Actions SSH → VM: git reset + docker compose up -d --build
```

## GitHub secrets

Add these under **Settings → Secrets and variables → Actions**:

| Secret | Example | Purpose |
|--------|---------|---------|
| `GCP_HOST` | `34.x.x.x` or DNS name | VM external IP / hostname |
| `GCP_USER` | `user` | SSH login user |
| `GCP_SSH_KEY` | private key PEM | Deploy key (full private key body) |
| `GCP_APP_DIR` | `/home/user/tele-forwarder` | Absolute path to the git clone on the VM |

Generate a deploy-only key on your Mac (do not reuse your personal laptop key if you prefer isolation):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/gcp_deploy -N "" -C "github-actions-deploy"
```

On the VM, append the **public** key:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "ssh-ed25519 AAAA... github-actions-deploy" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Put the **private** key contents into `GCP_SSH_KEY`.

## One-time VM bootstrap

### 1. Install Docker + git

```bash
# Debian/Ubuntu example
sudo apt-get update
sudo apt-get install -y git ca-certificates curl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
# log out and back in so docker works without sudo
```

### 2. Clone the repo

```bash
git clone https://github.com/quicksxope/tele-forwarder.git ~/tele-forwarder
cd ~/tele-forwarder
git checkout main
```

Use the same absolute path you will store in `GCP_APP_DIR`.

### 3. Host UID/GID for compose

```bash
printf 'UID=%s\nGID=%s\n' "$(id -u)" "$(id -g)" > .env
```

### 4. Forwarder data (Telegram)

On the VM (or copy from a trusted machine):

```bash
mkdir -p data
chmod 700 data
# Option A — run wizard on the VM (needs uv + interactive OTP):
#   uv sync --extra tui && cp config.example.yaml data/config.yaml
#   uv run python -m tui setup
# Option B — scp an already-seeded data/ (secrets.yaml + *.session) from Mac:
#   scp -r data/secrets.yaml data/*.session data/config.yaml user@HOST:~/tele-forwarder/data/
```

Never commit `data/`.

### 5. okx_bot config

```bash
cp okx_bot/.env.example okx_bot/.env
cp okx_bot/channels.example.yaml okx_bot/channels.yaml
chmod 600 okx_bot/.env
# edit okx_bot/.env (exchange keys, ACTIVE_CHANNEL, CREDENTIAL_ENCRYPTION_KEY, …)
# edit okx_bot/channels.yaml (chat_id / parser)
```

**Create `okx_bot/channels.yaml` as a real file before the first `compose up`.** If the path is missing, Docker may create a directory and the mount will break.

Telegram for okx_bot uses `data/secrets.yaml` plus a dedicated user session:

```bash
# On the VM with uv (host), after secrets.yaml exists:
uv sync
uv run python login_user.py --name okx_user --send-otp
uv run python login_user.py --name okx_user --keep-session --code <OTP>
# writes data/okx_user.session — do not share with forwarder.session
```

### 6. First manual bring-up

```bash
cd ~/tele-forwarder   # or GCP_APP_DIR
docker compose up -d --build
docker compose ps
docker compose logs -f forwarder
docker compose logs -f okx_bot
```

Confirm both containers are healthy before relying on Actions.

## Day-to-day deploy

1. Develop on Mac, push to `main` (or merge a PR).
2. GitHub Actions runs tests, then SSHs to the VM and runs `docker compose up -d --build`.
3. Or: **Actions → Deploy → Run workflow** (`workflow_dispatch`).

Feature branches do **not** auto-deploy.

## Layout on the VM

```text
~/tele-forwarder/          # git clone (GCP_APP_DIR)
  .env                     # UID/GID for compose only
  data/                    # secrets, sessions, forwarder DB (gitignored)
  okx_bot/.env             # exchange + bot env (gitignored)
  okx_bot/channels.yaml    # active channel config (gitignored)
  docker-compose.yml       # forwarder + okx_bot
```

## Troubleshooting

| Symptom | Check |
|---------|--------|
| Actions SSH fails | `GCP_HOST` / `GCP_USER` / key in `authorized_keys`; firewall allows 22 |
| `channels.yaml` is a directory | Remove it, `cp okx_bot/channels.example.yaml okx_bot/channels.yaml` |
| okx_bot exits on auth | `data/okx_user.session` + `data/secrets.yaml` present; file ownership matches `UID`/`GID` |
| Permission denied on `/data` | Ensure compose `.env` UID/GID match the owner of `./data` |
| Deploy pulled wrong commit | Confirm VM remote tracks GitHub and `GCP_APP_DIR` is the intended clone |
