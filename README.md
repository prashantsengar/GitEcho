# gitecho

**The invisible, decentralized git mirror.**

`gitecho` (alias `ge`) is a "set-and-forget" utility that automatically mirrors your git pushes to other providers (GitLab, Bitbucket, Gitea, or custom servers) in the background.



## Motivation

We rely too heavily on GitHub. When it goes down, half the world's open-source development stops.

I built `gitecho` because existing mirroring tools are flawed:
* **CI/CD Pipelines:** Too complex. Requires managing secrets and YAML for every single project.
* **Cron Jobs:** Too slow. Code is mirrored minutes or hours later.
* **Manual Scripts:** Too tedious. You forget to run them.

My goal was an **Invisible Utility**: something you configure once per project and then never think about again. You push to `origin` as usual, and `gitecho` handles the redundancy instantly.

---

## Installation

### Recommended (uv)

Install `uv` first: https://docs.astral.sh/uv/getting-started/installation/

Then install `gitecho` in an isolated tool environment:
```bash
uv tool install gitecho

```

For local development from this repository, prefer editable install so code changes are picked up immediately:

```bash
uv tool install --force --editable .
```

If you already installed from `--from .` and need a hard refresh:

```bash
uv tool install --force --reinstall --refresh --from . gitecho
```

If `ge` is not found after install, add `~/.local/bin` to your shell PATH:

```bash
source "$HOME/.local/bin/env"
```

To make this permanent on zsh:

```bash
echo 'source "$HOME/.local/bin/env"' >> ~/.zshrc
source ~/.zshrc
```

### Standard (pip)

```bash
pip install gitecho

```

### Enable Autocomplete

Get tab completion for commands like `sync` or `logs`.

```bash
ge --install-completion

```

---

## Authentication (Important)

Since `gitecho` runs in the background, it **cannot** ask you for a password. It must be able to push silently.

In background mode, `gitecho` disables interactive git prompts (`GIT_TERMINAL_PROMPT=0`, SSH batch mode). If auth is missing, it fails fast and writes the reason to `ge logs`.

**1. SSH (Best)**
If you use SSH URLs (e.g., `git@gitlab.com:user/repo.git`) and your key is loaded, it works out of the box.

**2. HTTPS (Requires Credential Helper)**
If you use HTTPS URLs (e.g., `https://gitlab.com/user/repo.git`), you must enable the Git Credential Helper so `gitecho` can read your saved token.

* **Mac:** `git config --global credential.helper osxkeychain`
* **Windows:** `git config --global credential.helper manager`
* **Linux:** `git config --global credential.helper store` (or `libsecret`)

**Test it:**
If `git push echo-gitlab-com` works in your terminal without typing a password, `gitecho` will work.

---

## Usage

### 1. Setup a Mirror

Go to your project and link a backup repository.

```bash
cd ~/my-project
ge add git@gitlab.com:username/my-project.git

```

*Output:* `✔ Linked echo-gitlab-com`

If you upgraded `gitecho` and want to refresh the hook, run `ge add` again with the same URL.

*Expected output (already linked case):* `Mirror already linked as <remote-name>.`
Example: `Mirror already linked as echo-remote.`

### 2. The Workflow

Just use git.

```bash
git push origin main

```

**That's it.**
Your code goes to GitHub immediately. `gitecho` silently triggers a background process to push to `echo-gitlab-com`.
By default, it captures the exact refs from `pre-push`, waits for the origin refs to appear, then mirrors those same refs.

### 3. Check Status (Optional)

If you want to verify the mirror is active:

```bash
ge status
```

---

## Power User Features

### Shell Integration (Starship / Zsh)

Want to see if your repo is mirrored right in your prompt? Use the `--short` flag. It returns `✔` if active, `x` if error, or nothing.

**Example (.zshrc, basic prompt):**

```bash
setopt PROMPT_SUBST
PROMPT+=' $(command -v ge >/dev/null 2>&1 && ge status --short 2>/dev/null)'

```

If you use Powerlevel10k, add a custom `gitecho` segment in `~/.p10k.zsh` instead of editing `PROMPT` directly.

### Logs

Since the tool is invisible, failures (like a down server) are silent to avoid interrupting your flow. Check what happened in the background:

```bash
ge logs

```

Show more lines:

```bash
ge logs 30
```

### Origin Rejection Policy

Default behavior is safety-first: if origin does not reflect the pushed refs within the timeout window, `gitecho` skips mirror push and logs a warning.

If you want best-effort mirroring even when origin might have rejected the push, set:

```bash
export GITECHO_CONTINUE_ON_ORIGIN_REJECT=1
```

Or for a single push:

```bash
GITECHO_CONTINUE_ON_ORIGIN_REJECT=1 git push origin main
```

### Sync All

By default, the hook mirrors the exact refs from your push. To force a full sync of all branches and tags:

```bash
ge sync --all
```

---

## Uninstalling

I believe tools should leave no trace. If you want to remove `gitecho` from a project:

```bash
ge nuke

```

This removes the `pre-push` hook and deletes all `echo-*` remotes from the local configuration.

---

## Future Plans

* **Global Watch:** Automatically mirror every repo in a specific directory (e.g., `~/dev`).
* **Auto-Creation:** Detect if the remote repo doesn't exist and create it via API automatically (skipping the "Create Project" step in the browser).
