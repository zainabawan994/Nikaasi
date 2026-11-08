# Deploying Nikaasi for free (Oracle Cloud Always Free)

The result is a public, always-on Nikaasi at `https://<your-name>.duckdns.org`, with:
- automatic HTTPS (Let's Encrypt via Caddy)
- PostgreSQL that is never exposed to the internet
- daily database backups

It runs on Oracle's **Always Free** ARM server. Cost: **$0**. Oracle asks for a card to verify your identity, but Always Free resources are not charged.

Time: about 45–60 minutes the first time.

```
internet ──HTTPS──▶ Caddy ──▶ Nikaasi app ──▶ PostgreSQL      (backup container: daily pg_dump)
```

> Cloud dashboards change. If a button below has moved, look for the same idea nearby.

---

## 1. Create the Oracle Cloud account
1. Go to **oracle.com/cloud/free** and choose **Start for free**.
2. Pick a **Home Region** close to Pakistan, such as *India West (Mumbai)* or *UAE East (Dubai)*. You can't change it later. If a region keeps showing "out of capacity" in step 2, try another one when you create a new account.
3. Finish verification. You're on the free tier unless you choose to upgrade.

## 2. Create the server
**Compute → Instances → Create instance**

| Setting | Value |
|---|---|
| Name | `nikaasi` |
| Image | **Canonical Ubuntu 24.04** |
| Shape | **Ampere → VM.Standard.A1.Flex**, **2 OCPUs, 12 GB RAM** (Always Free covers up to 4 OCPUs / 24 GB) |
| Networking | Create a new virtual cloud network with a **public subnet**, and tick **Assign a public IPv4 address** |
| SSH keys | **Generate a key pair** and **download the private key**. Keep it safe: it's your only way in |

Click **Create**, wait until it shows *Running*, and copy the **Public IP address**.

If you see *"Out of host capacity"*, retry later, or pick another *Availability Domain* in the placement section.

## 3. Open ports 80 and 443 in Oracle's firewall
**Networking → Virtual cloud networks → (your VCN) → Security Lists → Default Security List → Add Ingress Rules.** Add three rules, each with source CIDR `0.0.0.0/0`:

| Protocol | Destination port |
|---|---|
| TCP | 80 |
| TCP | 443 |
| UDP | 443 |

(SSH on port 22 is already allowed.)

## 4. Get a free domain name (DuckDNS)
1. Go to **duckdns.org** and sign in (GitHub or Google).
2. Add a subdomain, for example `nikaasi-g10`. That gives you `nikaasi-g10.duckdns.org`.
3. Put your server's **public IP** in the *current ip* box and click **update ip**.

## 5. Install Nikaasi on the server
On Windows, open PowerShell where you saved the private key:

```powershell
ssh -i .\ssh-key-nikaasi.key ubuntu@YOUR_PUBLIC_IP
```

(If SSH complains that the key is "too open", run `icacls .\ssh-key-nikaasi.key /inheritance:r /grant:r "$($env:USERNAME):R"` once.)

Then, on the server:

```bash
git clone https://github.com/zainabawan994/Nikaasi.git
cd Nikaasi/deploy
sudo bash setup-server.sh nikaasi-g10.duckdns.org you@example.org
```

The script:
- installs Docker
- opens ports 80/443 in the server's own firewall
- turns on automatic security updates
- creates `deploy/.env` with a random database password
- starts everything

It ends by printing **"Nikaasi is running."**

## 6. Create your admin account
```bash
cd ~/Nikaasi/deploy
sudo docker compose exec app python -m app.cli create-user you@example.org --role admin
```
Type a password (at least 10 characters, with letters and a number or symbol). Your typing is hidden.

Open **https://nikaasi-g10.duckdns.org**. The first visit can take up to a minute while the certificate is issued. Then sign in at `/login.html`. Add other staff on the **Users** page. Give them the `authority` role unless they need to manage users.

## 7. Optional: Gemini
```bash
sudo nano ~/Nikaasi/deploy/.env        # set GEMINI_API_KEY=...
cd ~/Nikaasi/deploy && sudo docker compose up -d
```

---

## Day-to-day

| Task | Command (in `~/Nikaasi/deploy`) |
|---|---|
| Status | `sudo docker compose ps` |
| Logs | `sudo docker compose logs -f app` (or `caddy`, `db`, `backup`) |
| Update to the latest GitHub version | `git pull && sudo docker compose up -d --build` |
| Restart | `sudo docker compose restart app` |
| Reset a user's password | `sudo docker compose exec app python -m app.cli set-password user@example.org` |
| List users | `sudo docker compose exec app python -m app.cli list-users` |

### Backups
A backup is written every day to `~/Nikaasi/deploy/backups/` and kept for 14 days (`BACKUP_KEEP_DAYS` in `deploy/.env`). Backups on the same server don't protect you if the server is lost, so copy them to your PC now and then:

```powershell
scp -i .\ssh-key-nikaasi.key "ubuntu@YOUR_PUBLIC_IP:~/Nikaasi/deploy/backups/*.dump" .
```

**Restore** a backup. This replaces the current data:

```bash
cd ~/Nikaasi/deploy
sudo docker compose stop app
sudo docker compose exec -T db pg_restore -U nikaasi_app -d nikaasi --clean --if-exists < backups/nikaasi-YYYYMMDD-HHMM.dump
sudo docker compose start app
```

### If your server's IP changes
Update it on duckdns.org. Caddy renews the certificate by itself.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Browser can't connect at all | Check the step 3 ingress rules, and that DuckDNS shows the server's current IP. `sudo docker compose ps` should show `caddy` running |
| Certificate error / "not secure" | `sudo docker compose logs caddy`. Usually DNS isn't pointing at the server yet, or port 80 is blocked (Let's Encrypt needs it). Fix that and run `sudo docker compose restart caddy` |
| `app` keeps restarting | `sudo docker compose logs app`. Most often a mistake in `deploy/.env` |
| Can't sign in | Create the admin as in step 6. Sign-in only works over **https://** (secure cookies) |
| Oracle stopped the instance | Oracle may reclaim Always Free servers that sit almost **idle** for 7 days. Nikaasi is light, so this can happen. Upgrading the account to *Pay As You Go* stops this; you're still not charged while you stay within Always Free limits |

## Security notes
- Only Caddy (ports 80/443) is reachable from outside. The app and database are on internal Docker networks, and the database network has no internet access at all.
- `deploy/.env` holds the secrets (permissions `600`, git-ignored). Never paste it anywhere.
- The app runs as a non-root, read-only container with all Linux capabilities dropped.
- See [SECURITY.md](SECURITY.md) for the application's security model and checklist.
