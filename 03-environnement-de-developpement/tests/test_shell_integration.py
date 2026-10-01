import contextlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from install_dev_environment import InstallError, TargetUser, UbuntuBootstrap


SHELL = Path(__file__).resolve().parents[1] / 'shell'


class BashIntegrationTests(unittest.TestCase):
    def bootstrap(self, home, shell='bash'):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.home = home
        bootstrap.target = TargetUser('tester', os.getuid(), os.getgid(), str(home), '/bin/' + shell)
        bootstrap.status = {}
        bootstrap.set_step = lambda step: None
        bootstrap.target_file_privileges = contextlib.nullcontext
        bootstrap._ensure_user_directory = lambda path: path.mkdir(parents=True, exist_ok=True)
        bootstrap._atomic_user_write = lambda path, data, mode: path.write_bytes(data)
        return bootstrap

    def test_install_preserves_personal_settings_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bootstrap = self.bootstrap(home)
            path = home / '.bashrc'
            path.write_text('alias personal="echo before"\n')
            bootstrap._configure_shell()
            path.write_text(path.read_text() + 'export PERSONAL=after\n')
            before = path.read_bytes()
            bootstrap._configure_shell()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(path.read_text().count(bootstrap.MANAGED_START), 1)
            self.assertIn('source ', path.read_text())
            self.assertNotIn('mkcd()', path.read_text())

    def test_zsh_also_prepares_bash(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.bootstrap(home, 'zsh')._configure_shell()
            self.assertIn('config.bash', (home / '.bashrc').read_text())
            self.assertNotIn('config.bash', (home / '.zshrc').read_text())

    def test_fish_parent_symlink_is_rejected_before_any_rc_write(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            external = home / 'external'
            external.mkdir()
            (home / '.config').symlink_to(external, target_is_directory=True)
            rc = home / '.bashrc'
            rc.write_text('# personal\n')
            with self.assertRaises(InstallError):
                self.bootstrap(home, 'fish')._configure_shell()
            self.assertEqual(rc.read_text(), '# personal\n')
            self.assertEqual(list(external.iterdir()), [])

    def test_comment_quoting_marker_is_preserved_and_verification_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bootstrap = self.bootstrap(home)
            rc = home / '.bashrc'
            personal = '# Documentation: ' + bootstrap.MANAGED_START + '\n'
            rc.write_text(personal)
            bootstrap._configure_shell()
            bootstrap._verify_shell_configuration()
            self.assertTrue(rc.read_text().startswith(personal))
            before = rc.read_bytes()
            bootstrap._configure_shell()
            self.assertEqual(rc.read_bytes(), before)

    def test_ambiguous_markers_and_legacy_block_are_left_intact(self):
        for text in ('# >>> dev-bootstrap managed >>>\n',
                     '# Alias et fonctions Bash Pareto, Ubuntu\nalias cls=clear\n'):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                path = home / '.bashrc'
                path.write_text(text)
                with self.assertRaises(InstallError):
                    self.bootstrap(home)._configure_shell()
                self.assertEqual(path.read_text(), text)

    def test_config_reloading_does_not_duplicate_path(self):
        command = 'source "$1"; source "$1"; printf "%s" "$PATH"; declare -F dev-shell-update mkcd'
        env = dict(os.environ, HOME='/tmp/test home', PATH='/usr/bin:/bin')
        result = subprocess.run(['bash', '--noprofile', '--norc', '-c', command, 'test', str(SHELL / 'config.bash')],
                                env=env, text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.count('/tmp/test home/.local/bin'), 1)
        self.assertIn('dev-shell-update', result.stdout)
        self.assertIn('mkcd', result.stdout)

    def test_generated_bashrc_loads_tools_and_functions_from_another_directory(self):
        with tempfile.TemporaryDirectory(prefix="shell home '") as directory:
            home = Path(directory)
            self.bootstrap(home)._configure_shell()
            bin_dir = home / '.local/share/dev-bootstrap/bin'
            bin_dir.mkdir(parents=True)
            for name, variable in (('zoxide', 'ZOXIDE_INIT'), ('fzf', 'FZF_INIT')):
                executable = bin_dir / name
                executable.write_text('#!/bin/bash\nprintf "export ' + variable + '=loaded\\n"\n')
                executable.chmod(0o755)
            env = {key: value for key, value in os.environ.items() if key != 'BASH_ENV'}
            env.update(HOME=str(home), PATH='/usr/bin:/bin')
            command = ('source "$HOME/.bashrc"; source "$HOME/.bashrc"; '
                       'printf "%s:%s\\n%s\\n" "$ZOXIDE_INIT" "$FZF_INIT" "$PATH"; '
                       'declare -F dev-shell-update mkcd; alias eb')
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', command],
                                    cwd='/', env=env, capture_output=True, text=True, check=True)
            self.assertEqual(result.stderr, '')
            self.assertIn('loaded:loaded', result.stdout)
            self.assertEqual(result.stdout.count(str(bin_dir)), 1)
            self.assertIn('dev-shell-update', result.stdout)
            self.assertIn('mkcd', result.stdout)

    def test_source_path_is_quoted(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        with patch('install_dev_environment.__file__', "/tmp/a b'c/install_dev_environment.py"):
            body = bootstrap._shell_config_body('bash', '')
        result = subprocess.run(['bash', '-n'], input=body, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class ShellUpdateTests(unittest.TestCase):
    def git(self, cwd, *args):
        return subprocess.run(['git', '-C', str(cwd), *args], env=self.env,
                              check=True, capture_output=True, text=True).stdout.strip()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.home = root / 'home'
        self.home.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('GIT_') and key != 'BASH_ENV'}
        self.env.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / '.config'),
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                        GIT_TERMINAL_PROMPT='0')
        self.remote, self.author, self.user = root / 'remote.git', root / 'author', root / 'user'
        self.git(root, 'init', '--bare', str(self.remote))
        self.git(root, 'clone', str(self.remote), str(self.author))
        for key, value in [('user.name', 'Test'), ('user.email', 'test@example.test')]:
            self.git(self.author, 'config', key, value)
        assets = self.author / '03-environnement-de-developpement/shell'
        assets.mkdir(parents=True)
        for name in ('config.bash', 'update.bash'):
            shutil.copyfile(SHELL / name, assets / name)
        self.git(self.author, 'add', '.')
        self.git(self.author, 'commit', '-m', 'initial')
        self.git(self.author, 'push', '-u', 'origin', 'HEAD')
        self.git(root, 'clone', str(self.remote), str(self.user))
        self.rc = self.home / '.bashrc'
        self.rc.write_text('# Personal settings\n')

    def publish(self, suffix):
        path = self.author / '03-environnement-de-developpement/shell/config.bash'
        path.write_text(path.read_text() + suffix)
        self.git(self.author, 'add', '.')
        self.git(self.author, 'commit', '-m', 'update')
        self.git(self.author, 'push')

    def update(self):
        return subprocess.run(['bash', str(self.user / '03-environnement-de-developpement/shell/update.bash')],
                              env=self.env, capture_output=True, text=True)

    def test_updates_from_remote_without_touching_bashrc(self):
        self.publish('\nalias newshared="echo updated"\n')
        result = self.update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git(self.user, 'rev-parse', 'HEAD'), self.git(self.author, 'rev-parse', 'HEAD'))
        self.assertEqual(self.rc.read_text(), '# Personal settings\n')
        self.assertIn('exec bash', result.stdout)
        self.assertEqual(self.update().returncode, 0)

    def test_rejects_invalid_remote_before_changing_head(self):
        original = self.git(self.user, 'rev-parse', 'HEAD')
        self.publish('\nif then\n')
        result = self.update()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.user, 'rev-parse', 'HEAD'), original)

    def test_preserves_local_changes(self):
        path = self.user / '03-environnement-de-developpement/shell/config.bash'
        path.write_text(path.read_text() + '\n# local settings\n')
        original = path.read_bytes()
        self.publish('\n# upstream settings\n')
        self.assertNotEqual(self.update().returncode, 0)
        self.assertEqual(path.read_bytes(), original)

    def test_refuses_local_commits(self):
        self.git(self.user, 'config', 'user.name', 'Test')
        self.git(self.user, 'config', 'user.email', 'test@example.test')
        (self.user / 'local.txt').write_text('local commit')
        self.git(self.user, 'add', '.')
        self.git(self.user, 'commit', '-m', 'local')
        original = self.git(self.user, 'rev-parse', 'HEAD')
        self.assertNotEqual(self.update().returncode, 0)
        self.assertEqual(self.git(self.user, 'rev-parse', 'HEAD'), original)
