import typer
import os
import git
import datetime
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from rich.console import Console
from rich.prompt import Confirm

app = typer.Typer(help="The invisible git mirroring utility.")
console = Console()

# --- Configuration & Logs ---
LOG_FILE = Path.home() / ".gitecho.log"
REMOTE_PREFIX = "echo-"
HOOK_MARKER_START = "# >>> gitecho hook start >>>"
HOOK_MARKER_END = "# <<< gitecho hook end <<<"
ZERO_SHA = "0" * 40
ORIGIN_CONFIRM_TIMEOUT_SECONDS = 12
CONTINUE_ON_ORIGIN_REJECT_ENV = "GITECHO_CONTINUE_ON_ORIGIN_REJECT"
SKIP_HOOK_ENV = "GITECHO_SKIP_HOOK"

def log_event(message, level="INFO"):
    """Appends a timestamped log entry."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] [{level}] {message}\n")

# --- Helpers ---
def get_git_root(show_error: bool = True):
    try:
        repo = git.Repo(search_parent_directories=True)
        return repo
    except git.InvalidGitRepositoryError:
        if show_error:
            console.print("[bold red]Error:[/bold red] Not a git repository.")
        raise typer.Exit(code=1)

def is_mirror_remote(name: str) -> bool:
    return name.startswith(REMOTE_PREFIX)

def should_continue_on_origin_reject() -> bool:
    value = os.getenv(CONTINUE_ON_ORIGIN_REJECT_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on"}

def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback

def _remote_parts(url: str) -> tuple[str, str]:
    host = "remote"
    path = ""
    parsed = urlparse(url)

    if parsed.scheme and parsed.hostname:
        host = parsed.hostname
        path = parsed.path or ""
    else:
        scp_match = re.match(r"^(?:.+@)?([^:/]+):(.+)$", url)
        if scp_match:
            host, path = scp_match.groups()
        else:
            path = url

    repo_name = path.rstrip("/").split("/")[-1] if path else "repo"
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]

    return _slug(host, "remote"), _slug(repo_name, "repo")

def resolve_remote_name(repo: git.Repo, url: str) -> tuple[str, bool]:
    for remote in repo.remotes:
        if remote.url == url:
            return remote.name, True

    domain_slug, repo_slug = _remote_parts(url)
    existing = {r.name for r in repo.remotes}
    domain_name = f"{REMOTE_PREFIX}{domain_slug}"
    if domain_name not in existing:
        return domain_name, False

    repo_name = f"{domain_name}-{repo_slug}"
    if repo_name not in existing:
        return repo_name, False

    counter = 2
    while True:
        candidate = f"{repo_name}-{counter}"
        if candidate not in existing:
            return candidate, False
        counter += 1

def parse_ref_updates(raw: str) -> list[tuple[str, str, str, str]]:
    updates = []
    for line in raw.splitlines():
        parts = line.strip().split()
        if len(parts) == 4:
            updates.append((parts[0], parts[1], parts[2], parts[3]))
    return updates

def load_ref_updates(refs_file: Optional[Path]) -> list[tuple[str, str, str, str]]:
    if refs_file is None:
        return []

    try:
        raw = refs_file.read_text()
    except OSError:
        return []
    finally:
        try:
            refs_file.unlink()
        except OSError:
            pass

    return parse_ref_updates(raw)

def build_refspecs(ref_updates: list[tuple[str, str, str, str]]) -> list[str]:
    refspecs = []
    for local_ref, local_sha, remote_ref, _remote_sha in ref_updates:
        if local_sha == ZERO_SHA:
            refspecs.append(f":{remote_ref}")
        else:
            refspecs.append(f"{local_ref}:{remote_ref}")
    return refspecs

def ls_remote_sha(repo: git.Repo, remote_name: str, remote_ref: str) -> Optional[str]:
    try:
        output = repo.git.ls_remote(remote_name, remote_ref).strip()
    except Exception:
        return None

    if not output:
        return None

    first_line = output.splitlines()[0].split()
    if not first_line:
        return None
    return first_line[0]

def origin_updates_confirmed(
    repo: git.Repo,
    origin_remote: str,
    ref_updates: list[tuple[str, str, str, str]],
    timeout_seconds: int = ORIGIN_CONFIRM_TIMEOUT_SECONDS,
) -> bool:
    if not ref_updates:
        return True

    deadline = time.time() + timeout_seconds
    pending = list(ref_updates)

    while time.time() < deadline:
        unresolved = []
        for _local_ref, local_sha, remote_ref, _remote_sha in pending:
            remote_tip = ls_remote_sha(repo, origin_remote, remote_ref)
            if local_sha == ZERO_SHA:
                if remote_tip is not None:
                    unresolved.append((_local_ref, local_sha, remote_ref, _remote_sha))
            elif remote_tip != local_sha:
                unresolved.append((_local_ref, local_sha, remote_ref, _remote_sha))

        if not unresolved:
            return True

        pending = unresolved
        time.sleep(1)

    return False

def push_with_fail_fast_auth(
    repo: git.Repo,
    remote: git.Remote,
    all_refs: bool = False,
    refspecs: Optional[list[str]] = None,
):
    with repo.git.custom_environment(
        GIT_TERMINAL_PROMPT="0",
        GIT_SSH_COMMAND="ssh -oBatchMode=yes",
        **{SKIP_HOOK_ENV: "1"},
    ):
        if all_refs:
            remote.push(all=True)
            remote.push(tags=True)
            return

        if refspecs:
            remote.push(refspecs)
            return

        remote.push()

def remove_gitecho_hook_block(content: str) -> str:
    updated = content

    if HOOK_MARKER_START in updated and HOOK_MARKER_END in updated:
        start = updated.index(HOOK_MARKER_START)
        end = updated.index(HOOK_MARKER_END) + len(HOOK_MARKER_END)
        while end < len(updated) and updated[end] == "\n":
            end += 1
        updated = updated[:start] + updated[end:]

    legacy_pattern = re.compile(
        r"# GitEcho hook\n"
        r"if command -v ge >/dev/null 2>&1; then\n"
        r"\s*ge sync --bg >> ~/.gitecho\.log 2>&1 &\n"
        r"fi\n"
        r"(?:exit 0\n)?",
        re.MULTILINE,
    )
    updated = legacy_pattern.sub("", updated)

    if updated and not updated.endswith("\n"):
        updated += "\n"
    return updated

def install_hook(repo_path):
    """Installs the pre-push hook."""
    hook_path = Path(repo_path) / ".git" / "hooks" / "pre-push"
    hook_cmd = 'ge sync --bg --origin-remote "$1" --refs-file "$_ge_refs"'

    existing = hook_path.read_text() if hook_path.exists() else "#!/bin/sh\n"
    cleaned = remove_gitecho_hook_block(existing).rstrip("\n")
    script_content = (
        f"{HOOK_MARKER_START}\n"
        f'if [ "${{{SKIP_HOOK_ENV}:-0}}" = "1" ]; then\n'
        "    :\n"
        "elif command -v ge >/dev/null 2>&1; then\n"
        '    case "$1" in\n'
        f'        "{REMOTE_PREFIX}"*) ;;\n'
        '        *)\n'
        '            _ge_refs="$(mktemp "${TMPDIR:-/tmp}/gitecho-refs.XXXXXX")"\n'
        '            cat > "$_ge_refs"\n'
        f"            {hook_cmd} >> ~/.gitecho.log 2>&1 &\n"
        "            ;;\n"
        "    esac\n"
        "fi\n"
        f"{HOOK_MARKER_END}\n"
    )
    with open(hook_path, "w") as f:
        if cleaned:
            f.write(cleaned + "\n")
        f.write(script_content)
    os.chmod(hook_path, 0o755)


# --- Commands ---

@app.command()
def add(url: str):
    """Link a new mirror URL to this repo."""
    repo = get_git_root()
    remote_name, already_linked = resolve_remote_name(repo, url)

    if already_linked:
        install_hook(repo.working_dir)
        console.print(f"[yellow]Mirror already linked as {remote_name}.[/yellow]")
        return

    try:
        repo.create_remote(remote_name, url)
        install_hook(repo.working_dir)
        console.print(f"[green]✔ Linked {remote_name}[/green]")
        log_event(f"Added remote {remote_name} for {repo.working_dir}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")

@app.command()
def sync(
    bg: bool = False,
    all: bool = False,
    refs_file: Optional[Path] = typer.Option(None, hidden=True),
    origin_remote: str = typer.Option("origin", hidden=True),
):
    """Push to mirrors. Use --all for branches+tags."""
    repo = get_git_root()
    mirrors = [r for r in repo.remotes if is_mirror_remote(r.name)]
    ref_updates = load_ref_updates(refs_file)
    refspecs = build_refspecs(ref_updates)
    
    if not mirrors:
        if not bg: console.print("[yellow]No mirrors found.[/yellow]")
        return

    # Background mode logging
    if bg:
        continue_on_reject = should_continue_on_origin_reject()
        log_event(f"Background sync started for {repo.working_dir}")
        if is_mirror_remote(origin_remote):
            log_event(f"Skipped sync for mirror-triggered push on {origin_remote}.")
            return
        if ref_updates:
            confirmed = origin_updates_confirmed(repo, origin_remote, ref_updates)
            if not confirmed:
                if continue_on_reject:
                    log_event(
                        f"Origin refs not confirmed in time, but continuing because {CONTINUE_ON_ORIGIN_REJECT_ENV}=1.",
                        "WARN",
                    )
                else:
                    log_event(
                        (
                            "Skipped mirror sync: origin refs were not confirmed in time "
                            f"(push likely rejected). Set {CONTINUE_ON_ORIGIN_REJECT_ENV}=1 to continue anyway."
                        ),
                        "WARN",
                    )
                    return
            else:
                log_event(f"Origin refs confirmed on {origin_remote}.")
        elif refs_file is not None and not all:
            log_event("No ref updates were captured; falling back to default push behavior.", "WARN")

    for remote in mirrors:
        try:
            if all:
                push_with_fail_fast_auth(repo, remote, all_refs=True)
                if bg:
                    log_event(f"Synced {remote.name} successfully (--all)")
                else:
                    console.print(f"[green]✔ Synced {remote.name}[/green]")
            elif refspecs:
                push_with_fail_fast_auth(repo, remote, refspecs=refspecs)
                if bg:
                    log_event(f"Synced {remote.name} successfully ({len(refspecs)} ref updates)")
                else:
                    console.print(f"[green]✔ Synced {remote.name}[/green]")
            elif not bg:
                with console.status(f"[bold cyan]Echoing to {remote.name}...[/bold cyan]"):
                    push_with_fail_fast_auth(repo, remote)
                console.print(f"[green]✔ Synced {remote.name}[/green]")
            else:
                push_with_fail_fast_auth(repo, remote)
                log_event(f"Synced {remote.name} successfully")
        except Exception as e:
            msg = f"Failed to sync {remote.name}: {e}"
            if bg:
                msg += " (Ensure SSH keys or git credential helper is configured for non-interactive auth.)"
            if not bg: console.print(f"[red]{msg}[/red]")
            else: log_event(msg, "ERROR")

@app.command()
def status(short: bool = False):
    """Show mirror status. Use --short for prompts."""
    try:
        repo = get_git_root(show_error=not short)
        mirrors = [r for r in repo.remotes if is_mirror_remote(r.name)]
        
        if short:
            # Minimal output for prompts: ✔ if mirrors exist, empty if not
            if mirrors: console.print("✔") 
            return

        if not mirrors:
            console.print("[dim]No mirrors active.[/dim]")
        else:
            console.print(f"[bold]Active Mirrors ({len(mirrors)}):[/bold]")
            for m in mirrors:
                console.print(f" • [cyan]{m.name}[/cyan] -> {m.url}")
            console.print(f"\n[dim]Logs: {LOG_FILE}[/dim]")

    except typer.Exit:
        if short:
            return
        raise
    except Exception:
        if short: console.print("x")

@app.command()
def logs(lines: int = typer.Argument(10)):
    """Show recent background activity."""
    if not LOG_FILE.exists():
        console.print("[dim]No logs yet.[/dim]")
        return
    
    # Simple tail implementation
    with open(LOG_FILE, "r") as f:
        all_lines = f.readlines()
        for line in all_lines[-lines:]:
            console.print(line.strip(), highlight=False)

@app.command()
def nuke():
    """Uninstall hooks and remove config from this repo."""
    repo = get_git_root()
    hook_path = Path(repo.working_dir) / ".git" / "hooks" / "pre-push"
    
    if Confirm.ask(f"Remove gitecho hooks from {repo.working_dir}?"):
        if hook_path.exists():
            content = hook_path.read_text()
            cleaned = remove_gitecho_hook_block(content)
            if cleaned != content:
                stripped = cleaned.strip()
                if not stripped or stripped == "#!/bin/sh":
                    os.remove(hook_path)
                else:
                    hook_path.write_text(cleaned)
                console.print(f"[green]✔ Hook updated: {hook_path.name}[/green]")
        
        # Remove remotes
        for r in repo.remotes:
            if is_mirror_remote(r.name):
                repo.delete_remote(r.name)
                console.print(f"[green]✔ Removed remote {r.name}[/green]")

if __name__ == "__main__":
    app()
