# Company Sign-In and Recovery

Once people arrive through a company login, the local password form stops being the way in and starts being the way *around*. This page covers the switch that closes it — `local_login_policy` — the emergency account that keeps closing it from becoming a lockout, and the host commands that get you back in when the identity provider or an authenticator is gone.

How identities from a reverse proxy become accounts is described once, in [Authentication](authentication.md#accounts-behind-the-proxy); this page does not repeat it.

## Local sign-in policy

The dashboard's local sign-in policy decides who may still use a password once a company login exists. It lives in the database (Settings → Organisation settings → local sign-in), has no environment variable, and defaults to the behaviour every install has today.

| Policy | Who may sign in with a password | Where the form is |
| --- | --- | --- |
| `enabled` (default) | every active account that holds a password | on the sign-in screen |
| `admins_only` | accounts holding the Admin preset | collapsed behind a "Sign in with a password" link |
| `break_glass_only` | only a **qualifying** emergency account (see below) | nowhere — only at `/login?local=1` |

Tightening the policy also changes how the emergency account signs in: a **break-glass** account always presents its second factor, whatever the install-wide two-factor settings say, its sign-in is recorded as `break_glass_login` at critical severity, and the header marks the session as an emergency session while it lasts.

### The emergency account

The designation (`is_break_glass`) is only half of it. An account **qualifies** as the emergency account when all five of these are true: it is designated, it is active, it holds the Admin preset, it has enrolled two-factor, and it has a local password. (The password is on the list because an account created by the reverse proxy cannot use the local password form at all, so it is no use as a way back in when the proxy is what failed.) Only qualifying accounts count:

- Tightening the policy away from `enabled`, or turning on a sign-in provider with no password fallback, needs at least one qualifying account — otherwise the API answers `409 break_glass_requires_totp` and names the account that would qualify once it enrols.
- Under `break_glass_only` the local form admits a qualifying account, not merely a designated one — a designation without two-factor would otherwise leave the strictest policy with a password-only admin door.
- While the policy is not `enabled`, nothing may take the last qualifying account away. Removing its two-factor, resetting it, removing its password, disabling, deleting or demoting the account, or clearing its designation all answer `409 last_break_glass_protected`.

An install upgraded from the shared-password era carries the designation on its `admin` account already but no two-factor, so the usual first step is to enrol two-factor on it. Until it does, that account still signs in normally under `enabled` and `admins_only` — which is where it enrols — and the policy cannot be tightened past it. The login-policy card shows the qualifying state, the account name and the `/login?local=1` URL — **save both in your password manager before tightening the policy**, because that URL is the only place the form appears under `break_glass_only` and the name is never shown to a signed-out browser.

## Behind a reverse proxy, enrol two-factor first

An administrator who signs in through the proxy and holds neither a password nor two-factor **cannot manage people**. Changing accounts, roles, invites, providers and security settings all require re-confirming your identity within the last five minutes, and an account with no factor has nothing to present: the API answers `403 step_up_unavailable` and the dialog says so. Enrol two-factor under Settings → Access → My sign-in — a reverse-proxy account can do that without a password — before you start adding people. The rule and the factors each kind of account presents are described in [Authentication → Confirming sensitive changes](authentication.md#confirming-sensitive-changes).

## Edge SSO deployment

Any IdP that an authenticating proxy can front (Authelia, oauth2-proxy, and through them SAML-only providers) works today through trusted-header mode: the proxy authenticates, codex-lb reads the header. The deployment side is documented where the rest of the proxy configuration lives — [Remote Access → Reverse proxy](deployment/remote.md#reverse-proxy) for trusted CIDRs, forwarded headers and the `Host` rule, and [Docker → Auth mode examples](deployment/docker.md#auth-mode-examples) for a runnable container. Group headers map to roles from Settings → Organisation settings.

## Host recovery commands

Four commands under the `codex-lb` entry point are the last resort. They act on the configured database directly: no environment variable of their own, no network call, no web application, and — deliberately — none of the gates above, because they exist for the lockout those gates are meant to prevent. Each writes one `break_glass_cli_used` audit row at critical severity with `auth_method=cli`, naming the command and its target; nobody authenticated to run them, so that row is the whole accountability.

| Command | What it does |
| --- | --- |
| `admin reset-password <username>` | Prompts for a new password (never taken from the arguments), stores it and signs the account out everywhere. `--clear-two-factor` also removes the account's authenticator secret, and re-opens local sign-in if clearing it would have left nobody the policy admits. |
| `admin local-login enable` | Puts the local sign-in policy back to `enabled`, with no qualifying-account check. |
| `admin disable-provider <id>` | Turns one company sign-in provider off. Run it with an unknown id to list the providers in the database. |
| `admin reset-login-policy` | Re-opens local sign-in *and* disables every company provider, keeping the password provider. Asks for confirmation, or takes `--yes`. |

Run them on the host that holds the database. Three forms of the same command, one per way of running codex-lb — substitute the arguments from the table above:

```bash
# --- Standard image (the image ships a codex-lb shim) ---
docker exec -it codex-lb codex-lb admin reset-password admin
docker exec -it codex-lb codex-lb admin local-login enable
docker exec -it codex-lb codex-lb admin disable-provider <provider-id>
docker exec -it codex-lb codex-lb admin reset-login-policy --yes

# --- Distroless image (no shell, no shim) ---
docker exec -it codex-lb python -m app.cli admin reset-password admin
docker exec -it codex-lb python -m app.cli admin local-login enable
docker exec -it codex-lb python -m app.cli admin disable-provider <provider-id>
docker exec -it codex-lb python -m app.cli admin reset-login-policy --yes

# --- Kubernetes / Helm ---
kubectl exec -it deploy/codex-lb -- python -m app.cli admin reset-password admin
kubectl exec -it deploy/codex-lb -- python -m app.cli admin local-login enable
kubectl exec -it deploy/codex-lb -- python -m app.cli admin disable-provider <provider-id>
kubectl exec -it deploy/codex-lb -- python -m app.cli admin reset-login-policy --yes

# --- From a source checkout ---
uv run codex-lb admin local-login enable
```

`<provider-id>` is the only argument you will not know by heart: run `disable-provider` with any placeholder and the command prints every provider id in this database, with its kind and whether it is on, then exits without writing. `reset-login-policy` prints its warning and asks `Continue? [y/N]` without `--yes`; a non-interactive shell needs the flag.

Notes that matter in the middle of an incident:

- `reset-password` prompts twice on the terminal, so it needs an interactive session (`docker exec -it`, `kubectl exec -it`). A password passed on a command line would survive in the shell history, in `ps` and in the host's logs, so there is no flag for it.
- A running server picks these changes up within about five seconds; there is nothing to restart.
- On a database that was never migrated the commands say so and stop, instead of a traceback. Run `codex-lb-db upgrade` (or start the server once) first.
- Every command is safe to repeat.

### The identity provider is unreachable

1. Sign in as the emergency account at `/login?local=1` with its password and authenticator code. If that works, you are in — nothing below is needed.
2. Otherwise re-open local sign-in on the host: `codex-lb admin local-login enable`. Any active account that holds a password may sign in again.
3. If sign-ins still land on the provider's screen or come back refused, the provider itself is answering: `codex-lb admin reset-login-policy` re-opens local sign-in and disables every company provider at once (`admin disable-provider <id>` does it one at a time).
4. When the provider is healthy again, turn it back on and re-tighten the policy from Settings → Organisation settings. The dashboard refuses to tighten it until a qualifying emergency account exists, which is the check that keeps step 1 working next time.

### The second-factor secret is lost

1. **Someone else can still sign in.** An administrator resets the lost factor from Settings → Access → People → Reset two-factor. Note that while the policy is not `enabled`, resetting the *last* qualifying emergency account answers `409 last_break_glass_protected` — designate and enrol a second admin first, or re-open local sign-in (step 2) and do it from there.
2. **Nobody can sign in.** On the host: `codex-lb admin local-login enable`, then `codex-lb admin reset-password <username>` for an administrator that has no second factor, and continue from the dashboard.
3. **The locked-out account is the only administrator.** A designated account that holds a secret must always present it, so a new password alone will not let it in. Clear the secret with the password in one step:

   ```bash
   docker exec -it codex-lb codex-lb admin reset-password admin --clear-two-factor
   ```

   Clearing the secret is also what stops the account *qualifying*, and under `break_glass_only` a designation that no longer qualifies is not admitted — the new password would meet a form that refuses it. So the command asks one question after the write: can anybody still use the local password form? When the answer is no it re-opens local sign-in in the same transaction and says so:

   ```text
   - Local sign-in policy: break_glass_only -> enabled (re-opened: no account was left that the policy would let in)
   ```

   A policy that still has a working door is never touched, so this is not a way to relax one; if you want it open regardless, run `admin local-login enable` yourself. The install-wide two-factor requirement is left as configured, so if the install requires two-factor the account is sent straight back to enrolment on its next sign-in. Then, in this order: **enrol a fresh authenticator, and only then re-tighten the policy** from Settings → Organisation settings. Until it enrols, the account is designated but no longer qualifying, and the dashboard refuses to tighten the policy anyway.

## What is not here yet

Native OIDC (the "Continue with your provider" button, the connection wizard and its test sign-in) and SCIM provisioning are later phases. Until they ship, a company login means the reverse-proxy route above.

---

*Specs: [admin-auth](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/admin-auth) · [identity-providers](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/identity-providers) · [dashboard-users](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/dashboard-users)*
