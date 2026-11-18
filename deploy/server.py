#!/usr/bin/env python3
"""Restricted GitHub deployment entry point. Never execute SSH command text."""
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path('/home/arian/azizam')
REPO = ROOT / 'repository.git'
SHARED = ROOT / 'shared'
URL = 'https://github.com/kouroshsamadof/bbh-mini-app.git'
OBSERVER = 'bbh-observer-observer-1'
BRAIN = 'bbh-observer-chat-brain-1'


def run(args, *, timeout=90, capture=False, log=None):
    options = {'check': True, 'timeout': timeout, 'text': True}
    if capture:
        options['stdout'] = subprocess.PIPE
        options['stderr'] = subprocess.PIPE
    elif log is not None:
        options['stdout'] = log
        options['stderr'] = subprocess.STDOUT
    result = subprocess.run(args, **options)
    return result.stdout.strip() if capture else None


def record(result, sha=None, **extra):
    value = {'result': result, 'commit': sha,
             'time': datetime.datetime.now(datetime.timezone.utc).isoformat(), **extra}
    temp = ROOT / 'status.tmp'
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(ROOT / 'status.json')
    print('AZIZAM_RESULT=' + result, flush=True)
    if sha:
        print('Commit: ' + sha, flush=True)
    return value


def inspect_container(name):
    try:
        return json.loads(run(['docker', 'inspect', name], capture=True))[0]
    except subprocess.CalledProcessError:
        return None


def compose(envfile, *args, log=None):
    return run(['docker', 'compose', '-p', 'azizam', '-f', str(ROOT / 'compose.yml'),
                '--env-file', str(envfile), *args], timeout=240, log=log)


def backup_observer(folder):
    info = inspect_container(OBSERVER)
    if not info or not info['State']['Running']:
        return
    stamp = folder.name
    source = '/data/pre-azizam-' + stamp + '.sqlite3'
    code = '''import os,sqlite3
src=os.environ.get("OBSERVER_DB") or "/data/observer.sqlite3"
dst=%r
with sqlite3.connect("file:"+src+"?mode=ro",uri=True) as a,sqlite3.connect(dst) as b:
 a.backup(b)
 assert b.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
os.chmod(dst,0o600)
''' % source
    run(['docker', 'exec', OBSERVER, 'python', '-c', code])
    run(['docker', 'cp', OBSERVER + ':' + source, str(folder / 'observer.sqlite3')])
    os.chmod(folder / 'observer.sqlite3', 0o600)
    for path in [Path('/home/arian/observer/.env'),
                 Path('/home/arian/observer/deploy/compose.yml')]:
        if path.exists():
            dest = folder / ('observer.env' if path.name == '.env' else 'observer-compose.yml')
            shutil.copy2(path, dest)
            os.chmod(dest, 0o600)


def retire_observer(folder):
    backup_observer(folder)
    for name in [OBSERVER, BRAIN]:
        if inspect_container(name):
            run(['docker', 'update', '--restart=no', name], capture=True)
            run(['docker', 'stop', '--time', '45', name], capture=True)


def backup_bot_data(folder):
    try:
        run(['docker', 'volume', 'inspect', 'azizam_bot-data'], capture=True)
    except subprocess.CalledProcessError:
        return
    observer = inspect_container(OBSERVER)
    if not observer:
        raise RuntimeError('The retained backup helper image is unavailable.')
    # The bot has stopped, so SQLite and other files have a consistent snapshot.
    code = '''import os,tarfile
p="/backup/azizam-data.tar.gz"
with tarfile.open(p,"w:gz") as t: t.add("/source",arcname="data")
os.chown(p,%d,%d)
os.chmod(p,0o600)
''' % (os.getuid(), os.getgid())
    run(['docker', 'run', '--rm', '--network', 'none', '--memory', '128m', '--cpus', '0.25',
         '--entrypoint', 'python', '-v', 'azizam_bot-data:/source:ro',
         '-v', str(folder) + ':/backup', observer['Image'], '-c', code], timeout=300)


def deploy(sha):
    if (SHARED / 'pause-deployments').exists():
        record('paused', sha)
        return
    if not REPO.exists():
        run(['git', 'init', '--bare', str(REPO)], capture=True)
        run(['git', '--git-dir=' + str(REPO), 'remote', 'add', 'origin', URL])
    git = ['git', '--git-dir=' + str(REPO)]
    run([*git, 'fetch', '--quiet', '--depth=1', 'origin', 'main'], timeout=120)
    head = run([*git, 'rev-parse', 'FETCH_HEAD'], capture=True)
    if sha != head:
        record('skipped_outdated_commit', sha, latest_main=head)
        return
    files = run([*git, 'ls-tree', '-r', '--name-only', sha], capture=True).splitlines()
    if 'Dockerfile' not in files:
        record('waiting_for_bot_code', sha)
        print('Add the bot source and a root Dockerfile. Observer stays running.')
        return
    if any(Path(p).name == '.env' for p in files):
        raise RuntimeError('A private .env file is tracked in Git. Remove it before deployment.')
    releases = ROOT / 'releases'
    releases.mkdir(exist_ok=True, mode=0o700)
    release = releases / sha
    if not release.exists():
        with tempfile.TemporaryDirectory(dir=releases) as work:
            work = Path(work)
            archive = subprocess.Popen([*git, 'archive', sha], stdout=subprocess.PIPE)
            try:
                subprocess.run(['tar', '-x', '-C', str(work)], stdin=archive.stdout,
                               check=True, timeout=60)
                archive.stdout.close()
                if archive.wait(timeout=60):
                    raise RuntimeError('Could not unpack the source commit.')
                work.rename(release)
            finally:
                if archive.poll() is None:
                    archive.kill()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder = ROOT / 'backups' / (stamp + '-' + sha[:12])
    folder.mkdir(parents=True, mode=0o700)
    image = 'azizam-bot:' + sha
    try:
        run(['docker', 'image', 'inspect', image], capture=True)
    except subprocess.CalledProcessError:
        print('Building Azizam; build details stay in the private server log.', flush=True)
        with (folder / 'build.log').open('w') as log:
            run(['docker', 'build', '--label', 'org.opencontainers.image.revision=' + sha,
                 '-t', image, str(release)], timeout=900, log=log)
    config = json.loads(run(['docker', 'image', 'inspect', image], capture=True))[0]['Config']
    probe = config.get('Healthcheck', {}).get('Test', [])
    if not probe or probe[0] == 'NONE':
        raise RuntimeError('The bot Dockerfile needs a working HEALTHCHECK before launch.')
    if not (SHARED / 'activation-ready').exists():
        record('staged_waiting_for_first_handover', sha, image=image)
        print('Image built. Private settings, data migration and laptop shutdown must be ready first.')
        return
    envfile = SHARED / 'bot.env'
    settings = [s for s in envfile.read_text().splitlines()
                if s.strip() and not s.lstrip().startswith('#') and '=' in s]
    if not settings or envfile.stat().st_mode & 0o077:
        raise RuntimeError('bot.env must contain private settings and have mode 600.')
    current = ROOT / 'current.env'
    existing = inspect_container('azizam-bot-1')
    if (current.exists() and current.read_text() == 'AZIZAM_IMAGE=' + image + '\n'
            and existing and existing['State'].get('Health', {}).get('Status') == 'healthy'):
        retire_observer(folder)
        record('already_deployed', sha, image=image)
        return
    candidate = ROOT / 'candidate.env'
    candidate.write_text('AZIZAM_IMAGE=' + image + '\n')
    if current.exists():
        shutil.copy2(current, folder / 'previous.env')
    with (folder / 'startup.log').open('w') as log:
        stopped = False
        try:
            compose(current if current.exists() else candidate, 'stop', 'bot', log=log)
            stopped = True
            backup_bot_data(folder)
            # A newer push during the build must supersede this release.
            latest = run(['git', 'ls-remote', URL, 'refs/heads/main'], capture=True).split()[0]
            if latest != sha:
                if current.exists():
                    compose(current, 'up', '--wait', '--wait-timeout', '120', '--no-build',
                            '--no-deps', 'bot', log=log)
                record('skipped_outdated_commit', sha, latest_main=latest)
                return
            compose(candidate, 'up', '--wait', '--wait-timeout', '120', '--no-build',
                    '--no-deps', 'bot', log=log)
        except Exception:
            if stopped:
                try:
                    if current.exists():
                        compose(current, 'up', '--wait', '--wait-timeout', '120', '--no-build',
                                '--no-deps', 'bot', log=log)
                    else:
                        compose(candidate, 'stop', 'bot', log=log)
                except Exception:
                    record('rollback_failed_needs_attention', sha)
                    raise RuntimeError('Startup and recovery failed. Inspect private startup.log.') from None
            record('startup_failed_previous_release_restored' if current.exists()
                   else 'first_startup_failed_bot_stopped', sha)
            raise RuntimeError('Release failed. Inspect the private server startup.log.') from None
    if current.exists():
        shutil.copy2(current, ROOT / 'previous.env')
    candidate.replace(current)
    retire_observer(folder)
    record('deployed', sha, image=image)
    print('Azizam is healthy. Observer and its dedicated model are stopped; their data is retained.')


def main():
    os.umask(0o077)
    ROOT.mkdir(exist_ok=True, mode=0o700)
    command = os.environ.get('SSH_ORIGINAL_COMMAND', '')
    if command == 'status':
        path = ROOT / 'status.json'
        print(path.read_text() if path.exists() else '{"result":"not_deployed"}')
        return
    match = re.fullmatch(r'deploy ([0-9a-f]{40})', command)
    if not match:
        print('This key only accepts: deploy <commit-sha> or status.', file=sys.stderr)
        sys.exit(64)
    with (ROOT / 'deployment.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        deploy(match.group(1))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Never relay application logs or private settings to public Actions logs.
        print('Deployment could not finish: ' + str(error), file=sys.stderr)
        sys.exit(1)
