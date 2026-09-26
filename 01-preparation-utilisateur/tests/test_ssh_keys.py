import os
import pty
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETUP_SCRIPT = ROOT / "01-preparation-utilisateur" / "setup-ubuntu-user.sh"
HARDEN_SCRIPT = ROOT / "02-configuration-serveur" / "durcir-ssh.sh"


class SshKeyPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.initial_home = self.root / "initial"
        self.target_home = self.root / "ubuntu"
        self.initial_home.mkdir()
        self.target_home.mkdir()
        self.key_path = self.root / "client-key"
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "test-client",
                "-f",
                str(self.key_path),
            ],
            check=True,
        )
        self.public_key = Path(f"{self.key_path}.pub").read_text().strip()

    def make_source(self, contents):
        source_directory = self.initial_home / ".ssh"
        source_directory.mkdir(exist_ok=True)
        source_file = source_directory / "authorized_keys"
        if contents is not None:
            source_file.write_text(contents)
        return source_file

    def run_ensure(self, tty_path="/path/that/is/not/a/tty"):
        environment = os.environ.copy()
        environment.update(
            {
                "INITIAL_HOME": str(self.initial_home),
                "TARGET_HOME": str(self.target_home),
                "TTY_PATH": tty_path,
                "SETUP_SCRIPT": str(SETUP_SCRIPT),
                "CHOWN_LOG": str(self.root / "chown.log"),
            }
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$SETUP_SCRIPT"; chown() { printf "%s\\n" "$*" >> "$CHOWN_LOG"; }; '
                'ensure_authorized_keys "$TARGET_HOME" testuser testgroup '
                '"$INITIAL_HOME" "$TTY_PATH"',
            ],
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def run_ensure_with_tty(self, input_line):
        master_fd, slave_fd = pty.openpty()
        self.addCleanup(os.close, master_fd)
        self.addCleanup(os.close, slave_fd)
        tty_path = os.ttyname(slave_fd)
        os.write(master_fd, f"{input_line}\n".encode())
        result = self.run_ensure(tty_path)
        try:
            self.tty_output = os.read(master_fd, 8192).decode()
        except OSError:
            self.tty_output = ""
        return result

    @property
    def installed_keys(self):
        return self.target_home / ".ssh" / "authorized_keys"

    def test_initial_account_valid_authorized_keys_is_installed(self):
        self.make_source(f"{self.public_key}\n")
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.installed_keys.read_text().strip(), self.public_key)

    def test_missing_source_without_tty_fails_with_instructions(self):
        result = self.run_ensure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PuTTYgen", result.stderr)
        self.assertIn(".ppk", result.stderr)
        self.assertFalse(self.installed_keys.exists())

    def test_empty_source_is_distinguished_from_missing_source(self):
        self.make_source("")
        result = self.run_ensure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("est vide", result.stderr)

    def test_invalid_source_is_distinguished_from_missing_source(self):
        self.make_source("ceci n'est pas une clé\n")
        result = self.run_ensure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ne contient aucune clé publique OpenSSH valide", result.stderr)

    def test_comments_and_blank_lines_are_accepted(self):
        self.make_source(f"# commentaire\n\n  \n{self.public_key}\n")
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.installed_keys.read_text().strip(), self.public_key)

    def test_authorized_key_options_are_validated_by_openssh(self):
        self.make_source(f'command="true" {self.public_key}\n')
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'command="true" {self.public_key}', self.installed_keys.read_text())

    def test_interactive_valid_public_key_is_installed(self):
        self.make_source(None)
        result = self.run_ensure_with_tty(self.public_key)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.installed_keys.read_text().strip(), self.public_key)
        self.assertIn("Public key for pasting into OpenSSH authorized_keys file", self.tty_output)
        self.assertIn("Ne collez jamais la clé privée", self.tty_output)

    def test_interactive_invalid_value_is_refused(self):
        self.make_source(None)
        result = self.run_ensure_with_tty("cle-privee-ou-texte-invalide")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("n'est pas une clé publique OpenSSH exploitable", result.stderr)
        self.assertFalse(self.installed_keys.exists())

    def test_optional_puTTY_key_is_added_alongside_existing_initial_key(self):
        self.make_source(f"{self.public_key}\n")
        second_key_path = self.root / "putty-key"
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "putty-client",
                "-f",
                str(second_key_path),
            ],
            check=True,
        )
        putty_public_key = Path(f"{second_key_path}.pub").read_text().strip()
        result = self.run_ensure_with_tty(putty_public_key)
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = self.installed_keys.read_text().splitlines()
        self.assertIn(self.public_key, installed)
        self.assertIn(putty_public_key, installed)

    def test_optional_prompt_can_keep_the_detected_initial_key(self):
        self.make_source(f"{self.public_key}\n")
        result = self.run_ensure_with_tty("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.installed_keys.read_text().strip(), self.public_key)

    def test_existing_ubuntu_key_is_preserved_without_source(self):
        ssh_directory = self.target_home / ".ssh"
        ssh_directory.mkdir()
        self.installed_keys.write_text(f"{self.public_key}\n")
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.installed_keys.read_text(), f"{self.public_key}\n")

    def test_rerun_is_idempotent_and_does_not_duplicate_keys(self):
        self.make_source(f"# comment\n{self.public_key}\n{self.public_key}\n")
        first = self.run_ensure()
        self.assertEqual(first.returncode, 0, first.stderr)
        first_contents = self.installed_keys.read_bytes()
        second = self.run_ensure()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.installed_keys.read_bytes(), first_contents)
        self.assertEqual(self.installed_keys.read_text().splitlines().count(self.public_key), 1)

    def test_new_source_key_is_merged_without_losing_existing_content(self):
        ssh_directory = self.target_home / ".ssh"
        ssh_directory.mkdir()
        original_contents = "# kept comment\ninvalid old entry"
        self.installed_keys.write_text(original_contents)
        self.make_source(f"{self.public_key}\n")
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = self.installed_keys.read_text()
        self.assertTrue(installed.startswith(original_contents))
        self.assertIn(self.public_key, installed)

    def test_source_key_is_merged_with_valid_existing_ubuntu_key(self):
        ssh_directory = self.target_home / ".ssh"
        ssh_directory.mkdir()
        self.installed_keys.write_text(f"{self.public_key}\n")
        second_key_path = self.root / "second-key"
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "second-client",
                "-f",
                str(second_key_path),
            ],
            check=True,
        )
        second_public_key = Path(f"{second_key_path}.pub").read_text().strip()
        self.make_source(f"{self.public_key}\n{second_public_key}\n")
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = self.installed_keys.read_text().splitlines()
        self.assertEqual(installed.count(self.public_key), 1)
        self.assertEqual(installed.count(second_public_key), 1)

    def test_final_permissions_are_restricted(self):
        self.make_source(f"{self.public_key}\n")
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.target_home / ".ssh").stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.installed_keys.stat().st_mode & 0o777, 0o600)
        chown_calls = (self.root / "chown.log").read_text()
        self.assertIn(f"testuser:testgroup {self.target_home / '.ssh'} {self.installed_keys}", chown_calls)

    def test_unexpected_target_ssh_directory_symlink_is_refused(self):
        alternate = self.root / "alternate"
        alternate.mkdir()
        (self.target_home / ".ssh").symlink_to(alternate, target_is_directory=True)
        self.make_source(f"{self.public_key}\n")
        result = self.run_ensure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ne doit pas être un lien symbolique", result.stderr)

    def test_unexpected_target_authorized_keys_symlink_is_refused(self):
        ssh_directory = self.target_home / ".ssh"
        ssh_directory.mkdir()
        alternate = self.root / "alternate-authorized-keys"
        alternate.write_text(f"{self.public_key}\n")
        self.installed_keys.symlink_to(alternate)
        result = self.run_ensure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ne doit pas être un lien symbolique", result.stderr)

    def test_initial_authorized_keys_symlink_is_refused(self):
        alternate = self.root / "alternate-source"
        alternate.write_text(f"{self.public_key}\n")
        source_directory = self.initial_home / ".ssh"
        source_directory.mkdir()
        (source_directory / "authorized_keys").symlink_to(alternate)
        result = self.run_ensure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lien symbolique inattendu", result.stderr)

    def run_hardening_selector(self, contents):
        admin_home = self.root / "admin"
        admin_home.mkdir(exist_ok=True)
        auth_file = admin_home / ".ssh" / "authorized_keys"
        auth_file.parent.mkdir(exist_ok=True)
        auth_file.write_text(contents)
        environment = os.environ.copy()
        environment.update(
            {
                "ADMIN_HOME_TEST": str(admin_home),
                "AUTH_FILE_TEST": str(auth_file),
                "HARDEN_SCRIPT": str(HARDEN_SCRIPT),
            }
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$HARDEN_SCRIPT"; '
                'user_record() { printf "testadmin:x:1000:1000::%s:/bin/bash\\n" "$ADMIN_HOME_TEST"; }; '
                'load_authorized_keys_candidates() { AUTHORIZED_KEYS_CANDIDATES=("$AUTH_FILE_TEST"); }; '
                'is_human_user() { return 0; }; REQUESTED_ADMIN_USER=testadmin; '
                'select_admin_user; printf "%s\\n" "$AUTHORIZED_KEYS_FILE"',
            ],
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def test_hardening_selector_accepts_valid_key_file(self):
        result = self.run_hardening_selector(f"# valid comment\n\n{self.public_key}\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("/.ssh/authorized_keys", result.stdout)

    def test_hardening_refuses_missing_or_invalid_key_with_actionable_error(self):
        result = self.run_hardening_selector("not a public key\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("étape 1 du README", result.stderr)
        self.assertIn("testez une connexion réelle", result.stderr)
        self.assertIn("Ne transmettez jamais votre clé privée", result.stderr)

    def run_hardening_pretest_confirmation(self, answer):
        master_fd, slave_fd = pty.openpty()
        self.addCleanup(os.close, master_fd)
        self.addCleanup(os.close, slave_fd)
        tty_path = os.ttyname(slave_fd)
        os.write(master_fd, f"{answer}\n".encode())
        environment = os.environ.copy()
        environment.update(
            {
                "HARDEN_SCRIPT": str(HARDEN_SCRIPT),
                "TTY_PATH_TEST": tty_path,
            }
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$HARDEN_SCRIPT"; confirm_client_key_pretested "$TTY_PATH_TEST"',
            ],
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def test_hardening_requires_confirmation_of_the_prior_client_test(self):
        result = self.run_hardening_pretest_confirmation("oui")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hardening_stops_before_changes_without_prior_test_confirmation(self):
        result = self.run_hardening_pretest_confirmation("non")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Aucune modification SSH", result.stderr)


if __name__ == "__main__":
    unittest.main()
