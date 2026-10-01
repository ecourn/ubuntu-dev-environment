"""Régressions adversariales des commandes Bash et de leur mise à jour."""
import os
import subprocess
import time
import tempfile
import unittest
from pathlib import Path

import test_shell_integration as integration


class ShellCommandTests(unittest.TestCase):
    def run_shell(self, directory, command, *args):
        return subprocess.run(
            ['bash', '--noprofile', '--norc', '-c',
             'source "$1"; shift; ' + command,
             'test', str(integration.SHELL / 'config.bash'), *args],
            cwd=directory, capture_output=True, text=True,
        )

    def test_creates_branch_and_rejects_invalid_names(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(['git', 'init', directory], check=True, capture_output=True)
            result = self.run_shell(directory, 'gcb feature/review')
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(['git', '-C', directory, 'symbolic-ref', '--short', 'HEAD'],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), 'feature/review')
            for branch in ('--orphan', 'bad name', 'a..b'):
                result = self.run_shell(directory, 'gcb "$1"', branch)
                self.assertNotEqual(result.returncode, 0)

    def test_agents_preserves_existing_file_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'AGENTS.md'
            result = self.run_shell(directory, 'agents')
            self.assertEqual(result.returncode, 0, result.stderr)
            path.write_text('personal instructions\n')
            self.assertNotEqual(self.run_shell(directory, 'agents').returncode, 0)
            self.assertEqual(path.read_text(), 'personal instructions\n')
            path.unlink()
            target = Path(directory) / 'personal.md'
            target.write_text('personal target\n')
            path.symlink_to(target)
            self.assertNotEqual(self.run_shell(directory, 'agents').returncode, 0)
            self.assertEqual(target.read_text(), 'personal target\n')

    def test_search_supports_directory_starting_with_dash(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / '-documents'
            folder.mkdir()
            (folder / 'note.txt').write_text('needle\n')
            for command in ('ff note -documents', 'ftext needle -documents'):
                result = self.run_shell(directory, command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('note.txt', result.stdout)

    def test_sources_with_existing_aliases_and_expansion_enabled(self):
        command = ('shopt -s expand_aliases; alias mkcd="echo personal"; '
                   'alias ff="echo personal"; source "$1"; '
                   'declare -F mkcd ff dev-shell-update')
        result = subprocess.run(['bash', '--noprofile', '--norc', '-c', command,
                                 'test', str(integration.SHELL / 'config.bash')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('dev-shell-update', result.stdout)

    def test_recent_files_limits_orders_and_escapes_names(self):
        with tempfile.TemporaryDirectory() as directory:
            future = int(time.time()) + 1000
            for index in range(32):
                name = f'{index:02d}.md' if index < 31 else 'odd|name\n.md'
                path = Path(directory) / name
                path.write_text('content')
                os.utime(path, (future + index, future + index))
            excluded = Path(directory) / '.git'
            excluded.mkdir()
            (excluded / 'hidden.md').write_text('hidden')
            result = self.run_shell(directory, 'mdrecent <<< 4')
            self.assertEqual(result.returncode, 0, result.stderr)
            records = [line for line in result.stdout.splitlines() if line.startswith('MODIFIED')]
            self.assertEqual(len(records), 30)
            self.assertIn('odd|name\\n.md', records[0])
            self.assertIn('./02.md', records[-1])
            self.assertNotIn('./00.md', result.stdout)
            self.assertNotIn('hidden.md', result.stdout)

    def test_recent_files_reports_search_errors_without_changing_shell_options(self):
        with tempfile.TemporaryDirectory() as directory:
            command = ('function find { return 7; }; mdrecent <<< 4; '
                       'result=$?; [[ $result -eq 7 ]] || exit 1; '
                       "[[ $(set -o | awk '$1 == \"pipefail\" {print $2}') == off ]]")
            result = self.run_shell(directory, command)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_preserves_personal_node_options_including_empty_value(self):
        for value in ('--trace-warnings --max-old-space-size=8192', ''):
            command = 'NODE_OPTIONS=$1; source "$2"; printf "%s" "$NODE_OPTIONS"'
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', command,
                                     'test', value, str(integration.SHELL / 'config.bash')],
                                    text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout, value)


class HardenedShellUpdateTests(unittest.TestCase):
    git = integration.ShellUpdateTests.git
    setUp = integration.ShellUpdateTests.setUp
    update = integration.ShellUpdateTests.update
    publish = integration.ShellUpdateTests.publish

    def publish_all(self):
        self.git(self.author, 'add', '.')
        self.git(self.author, 'commit', '-m', 'adversarial update')
        self.git(self.author, 'push')

    def test_rejects_remote_symlink_and_missing_script(self):
        original = self.git(self.user, 'rev-parse', 'HEAD')
        path = self.author / '03-environnement-de-developpement/shell/config.bash'
        path.unlink()
        path.symlink_to('../external.bash')
        self.publish_all()
        self.assertNotEqual(self.update().returncode, 0)
        self.assertEqual(self.git(self.user, 'rev-parse', 'HEAD'), original)
        path.unlink()
        self.publish_all()
        self.assertNotEqual(self.update().returncode, 0)
        self.assertEqual(self.git(self.user, 'rev-parse', 'HEAD'), original)

    def test_fetches_followed_branch_with_custom_refspec(self):
        self.git(self.user, 'config', 'remote.origin.fetch',
                 '+refs/heads/other:refs/remotes/origin/other')
        self.publish('\n# custom fetch mapping\n')
        result = self.update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git(self.user, 'rev-parse', 'HEAD'),
                         self.git(self.author, 'rev-parse', 'HEAD'))

    def test_preserves_ignored_file_that_remote_would_track(self):
        original = self.git(self.user, 'rev-parse', 'HEAD')
        (self.user / '.git/info/exclude').write_text('private.txt\n')
        local = self.user / 'private.txt'
        local.write_text('local ignored data\n')
        (self.author / 'private.txt').write_text('remote data\n')
        self.publish_all()
        result = self.update()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git(self.user, 'rev-parse', 'HEAD'), original)
        self.assertEqual(local.read_text(), 'local ignored data\n')

    def test_refuses_detached_head_and_untracked_changes(self):
        original = self.git(self.user, 'rev-parse', 'HEAD')
        self.git(self.user, 'checkout', '--detach')
        self.assertNotEqual(self.update().returncode, 0)
        self.assertEqual(self.git(self.user, 'rev-parse', 'HEAD'), original)
        self.git(self.user, 'checkout', '-')
        (self.user / 'untracked.txt').write_text('personal\n')
        self.assertNotEqual(self.update().returncode, 0)
        self.assertEqual(self.git(self.user, 'rev-parse', 'HEAD'), original)
