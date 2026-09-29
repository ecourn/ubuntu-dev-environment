import io
import hashlib
import json
import os
import stat
import subprocess
import tarfile
import tempfile
import unittest
import urllib.error
import zipfile
from contextlib import redirect_stdout
from email.message import Message
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import install_dev_environment
from install_dev_environment import (
    InstallError,
    TargetUser,
    UbuntuBootstrap,
    extract_archive_member,
    is_version_compatible,
    map_deb_architecture,
    parse_supported_ubuntu,
    resolve_target_user,
    select_latest_compatible_package,
    select_latest_node_lts,
    select_release_asset,
    update_managed_block,
)


class UbuntuSupportTests(unittest.TestCase):
    def test_accepts_only_matching_supported_release(self):
        metadata = """Dist: noble
Version: 24.04.4 LTS
Supported: 1

Dist: jammy
Version: 22.04.5 LTS
Supported: 0
"""
        self.assertTrue(parse_supported_ubuntu(metadata, "noble", "24.04"))
        self.assertFalse(parse_supported_ubuntu(metadata, "jammy", "22.04"))
        self.assertFalse(parse_supported_ubuntu(metadata, "noble", "22.04"))

    def test_refuses_missing_or_malformed_release_data(self):
        with self.assertRaises(InstallError):
            parse_supported_ubuntu("Dist: noble\nSupported: 1\n", "noble", "24.04")

    def test_maps_only_supported_debian_architectures(self):
        self.assertEqual(map_deb_architecture("x86_64"), "amd64")
        self.assertEqual(map_deb_architecture("aarch64"), "arm64")
        with self.assertRaises(InstallError):
            map_deb_architecture("riscv64")


class VersionPolicyTests(unittest.TestCase):
    def test_display_and_apt_versions_reject_prerelease_prefixes(self):
        for output in ("bun 1.2.3-beta.1", "v1.2.3-rc.2", "1.2.3+build", "1.2.3.4"):
            with self.subTest(output=output), self.assertRaises(InstallError):
                install_dev_environment.extract_display_version(output)
        for version in ("1.2.3~rc1-1", "1.2.3-beta.1", "1.2.3+build-1"):
            with self.subTest(version=version), self.assertRaises(InstallError):
                UbuntuBootstrap._semver_from_package_version(version)
        self.assertEqual(UbuntuBootstrap._semver_from_package_version("2:24.9.0-1nodesource1"), "24.9.0")
        self.assertEqual(install_dev_environment.extract_display_version("gh version 2.89.0 (stable)"), "2.89.0")

    def test_selects_newest_lts_with_binary_for_architecture(self):
        releases = [
            {"version": "v24.2.0", "date": "2025-01-01", "lts": "Krypton", "files": ["linux-x64"]},
            {"version": "v22.9.0", "date": "2024-01-01", "lts": "Jod", "files": ["linux-arm64"]},
            {"version": "v25.0.0", "date": "2025-05-01", "lts": False, "files": ["linux-x64", "linux-arm64"]},
            {"version": "v24.3.0", "date": "2025-02-01", "lts": "Krypton", "files": ["linux-x64", "linux-arm64"]},
        ]
        self.assertEqual(select_latest_node_lts(releases, "linux-arm64"), ("24.3.0", "24"))

    def test_refuses_if_no_compatible_lts_exists(self):
        with self.assertRaises(InstallError):
            select_latest_node_lts([{"version": "v24.0.0", "lts": False, "files": ["linux-x64"]}], "linux-x64")

    def test_engine_range_supports_common_semver_forms(self):
        self.assertTrue(is_version_compatible("24.21.0", "^24.15.0 || >=26.0.0"))
        self.assertTrue(is_version_compatible("24.21.0", ">=20.0.0 <25"))
        self.assertFalse(is_version_compatible("24.14.0", "^24.15.0 || >=26.0.0"))
        self.assertTrue(is_version_compatible("24.21.0", "24.x"))

    def test_semver_zero_caret_wildcards_and_invalid_ranges(self):
        self.assertTrue(is_version_compatible("0.2.9", "^0.2.3"))
        self.assertFalse(is_version_compatible("0.3.0", "^0.2.3"))
        self.assertTrue(is_version_compatible("24.1.0", ">23.x"))
        self.assertFalse(is_version_compatible("23.8.0", ">23.x"))
        with self.assertRaises(InstallError):
            is_version_compatible("24.1.0", ">=20 || nonsense")


    def test_selects_latest_stable_registry_version_compatible_with_node(self):
        packument = {
            "dist-tags": {"latest": "3.0.0"},
            "versions": {
                "2.4.0": {"engines": {"node": ">=20"}},
                "2.5.0": {"engines": {"node": "^24.0.0"}},
                "3.0.0": {"engines": {"node": ">=25"}},
                "3.1.0": {"engines": {"node": ">=18"}},
                "3.1.0-beta.1": {"engines": {"node": "*"}},
                "2.6.0": {"engines": {"node": "^26.0.0"}, "deprecated": "unsupported"},
            },
        }
        self.assertEqual(select_latest_compatible_package(packument, "24.21.0"), "2.5.0")

    def test_requires_valid_official_checksum_asset(self):
        release = {
            "tag_name": "v1.2.3",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "tool-linux-amd64.tar.gz",
                    "browser_download_url": "https://github.com/example/tool/releases/download/v1.2.3/tool-linux-amd64.tar.gz",
                    "digest": "sha256:" + "a" * 64,
                    "size": 1234,
                }
            ],
        }
        asset = select_release_asset(release, "example/tool", "tool-linux-amd64.tar.gz")
        self.assertEqual(asset["sha256"], "a" * 64)

    def test_rejects_prerelease_missing_digest_and_untrusted_asset(self):
        base = {
            "tag_name": "v1.2.3",
            "draft": False,
            "prerelease": True,
            "assets": [],
        }
        with self.assertRaises(InstallError):
            select_release_asset(base, "example/tool", "tool.tar.gz")
        base["prerelease"] = False
        base["assets"] = [{
            "name": "tool.tar.gz",
            "browser_download_url": "https://example.invalid/tool.tar.gz",
            "digest": None,
        }]
        with self.assertRaises(InstallError):
            select_release_asset(base, "example/tool", "tool.tar.gz")


class ArchiveExtractionTests(unittest.TestCase):
    def test_rejects_zip_nonregular_member_even_when_not_selected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            info = zipfile.ZipInfo("other-device")
            info.create_system = 3
            info.external_attr = (stat.S_IFCHR | 0o755) << 16
            archive.writestr(info, "device")
            archive.writestr("bun", "good")
        with self.assertRaises(InstallError):
            extract_archive_member(buffer.getvalue(), "zip", "bun")

    def test_extracts_expected_binary_from_tar_and_zip(self):
        payload = b"fake executable"
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as archive:
            info = tarfile.TarInfo("zoxide")
            info.size = len(payload)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(payload))
        self.assertEqual(extract_archive_member(tar_buffer.getvalue(), "tar.gz", "zoxide"), payload)

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as archive:
            archive.writestr("bun-linux-x64/bun", payload)
        self.assertEqual(extract_archive_member(zip_buffer.getvalue(), "zip", "bun"), payload)

    def test_rejects_path_traversal_and_ambiguous_members(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            info = tarfile.TarInfo("../zoxide")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(InstallError):
            extract_archive_member(buffer.getvalue(), "tar.gz", "zoxide")


class TargetUserTests(unittest.TestCase):
    def setUp(self):
        self.user = TargetUser("alice", 1001, 1001, "/home/alice", "/bin/bash")
        self.by_name = lambda name: self.user if name == "alice" else None
        self.by_uid = lambda uid: self.user if uid == 1001 else None

    def test_root_must_have_a_matching_sudo_user_identity(self):
        with self.assertRaises(InstallError):
            resolve_target_user(0, 0, {}, self.by_name, self.by_uid)
        with self.assertRaises(InstallError):
            resolve_target_user(0, 0, {"SUDO_USER": "alice", "SUDO_UID": "1002"}, self.by_name, self.by_uid)

    def test_nonroot_targets_the_current_account_not_sudo_user(self):
        target = resolve_target_user(1001, 1001, {"SUDO_USER": "root"}, self.by_name, self.by_uid)
        self.assertEqual(target.username, "alice")
        self.assertEqual(target.home, "/home/alice")


class ShellConfigurationTests(unittest.TestCase):
    def test_final_shell_verification_rejects_lookalikes_outside_managed_block(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / ".bashrc"
            path.write_text('echo "zoxide init bash; fzf --bash"\n')
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = home
            bootstrap.bin_dir = home / ".local/share/dev-bootstrap/bin"
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            with self.assertRaises(InstallError):
                bootstrap._verify_shell_configuration()

    def test_shell_body_ignores_untrusted_lookalikes_outside_block(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.bin_dir = Path("/home/alice/.local/share/dev-bootstrap/bin")
        text = '# zoxide init bash\necho "fzf --bash"\nexport OTHER=.local/share/dev-bootstrap/bin\n'
        body = bootstrap._shell_config_body("bash", text)
        self.assertIn('eval "$(zoxide init bash)"', body)
        self.assertIn('eval "$(fzf --bash)"', body)
        self.assertIn('export PATH=', body)

    def test_adds_and_replaces_managed_block_idempotently(self):
        start, end = "# >>> managed >>>", "# <<< managed <<<"
        original = "# user settings\n"
        once = update_managed_block(original, start, end, "export EXAMPLE=1")
        twice = update_managed_block(once, start, end, "export EXAMPLE=1")
        updated = update_managed_block(twice, start, end, "export EXAMPLE=2")
        self.assertEqual(twice, once)
        self.assertIn("# user settings", updated)
        self.assertIn("export EXAMPLE=2", updated)
        self.assertNotIn("export EXAMPLE=1", updated)

    def test_refuses_ambiguous_or_unclosed_managed_blocks(self):
        with self.assertRaises(InstallError):
            update_managed_block("# >>> managed >>>\n", "# >>> managed >>>", "# <<< managed <<<", "x")
        with self.assertRaises(InstallError):
            update_managed_block("# >>> managed >>>\n# <<< managed <<<\n# >>> managed >>>\n# <<< managed <<<\n", "# >>> managed >>>", "# <<< managed <<<", "x")

    def test_shell_path_uses_home_expansion_not_unescaped_user_path(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.bin_dir = Path("/home/alice's/.local/share/dev-bootstrap/bin")

        bash_body = bootstrap._shell_config_body("bash", "")
        fish_body = bootstrap._shell_config_body("fish", "")

        self.assertIn("$HOME/.local/share/dev-bootstrap/bin", bash_body)
        self.assertIn('"$HOME/.local/share/dev-bootstrap/bin"', fish_body)
        self.assertNotIn("alice's", bash_body + fish_body)


class BootstrapSafetyTests(unittest.TestCase):
    def test_preflight_checks_sudo_noninteractively_before_install(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.dry_run = False
        bootstrap.target_path = "/usr/bin"
        bootstrap._check_tty = lambda: setattr(bootstrap, "auth_tty_available", True)
        bootstrap.validate_environment = MagicMock()
        bootstrap.resolve_official_plan = MagicMock()
        bootstrap._check_node_manager_conflicts = MagicMock()
        bootstrap._check_docker_conflicts = MagicMock()
        bootstrap._check_apt_source_conflicts = MagicMock()
        bootstrap._check_npm_prefix = MagicMock()
        bootstrap._gh_is_authenticated = lambda: False
        bootstrap._system_executable = lambda name: f"/usr/bin/{name}"
        bootstrap._privileged_env = lambda: {"PATH": "/usr/bin"}
        bootstrap.set_step = MagicMock()
        result = subprocess.CompletedProcess(["sudo", "-n", "true"], 0)

        with patch("install_dev_environment.os.geteuid", return_value=1000), \
             patch("install_dev_environment.shutil.which", return_value="/usr/bin/git"), \
             patch("install_dev_environment.subprocess.run", return_value=result) as run:
            bootstrap.preflight()

        self.assertEqual(run.call_args.args[0], ["/usr/bin/sudo", "-n", "true"])
        self.assertEqual(run.call_args.kwargs["env"], {"PATH": "/usr/bin"})

    def test_preflight_stops_when_noninteractive_sudo_is_unavailable(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.dry_run = False
        bootstrap.target_path = "/usr/bin"
        bootstrap._check_tty = lambda: setattr(bootstrap, "auth_tty_available", True)
        bootstrap.validate_environment = MagicMock()
        bootstrap.resolve_official_plan = MagicMock()
        bootstrap._check_node_manager_conflicts = MagicMock()
        bootstrap._check_docker_conflicts = MagicMock()
        bootstrap._check_apt_source_conflicts = MagicMock()
        bootstrap._check_npm_prefix = MagicMock()
        bootstrap._gh_is_authenticated = lambda: False
        bootstrap._system_executable = lambda name: f"/usr/bin/{name}"
        bootstrap._privileged_env = lambda: {"PATH": "/usr/bin"}
        bootstrap.set_step = MagicMock()
        result = subprocess.CompletedProcess(["sudo", "-n", "true"], 1)

        with patch("install_dev_environment.os.geteuid", return_value=1000), \
             patch("install_dev_environment.shutil.which", return_value="/usr/bin/git"), \
             patch("install_dev_environment.subprocess.run", return_value=result) as run, \
             self.assertRaisesRegex(InstallError, "sudo non interactif"):
            bootstrap.preflight()

        self.assertEqual(run.call_args.args[0], ["/usr/bin/sudo", "-n", "true"])

    def test_preflight_requires_terminal_before_any_install_work(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.dry_run = False
        bootstrap._check_tty = lambda: setattr(bootstrap, "auth_tty_available", False)
        bootstrap.validate_environment = MagicMock()

        with self.assertRaisesRegex(InstallError, "terminal interactif"):
            bootstrap.preflight()

        bootstrap.validate_environment.assert_not_called()

    def test_interruption_after_binary_publish_is_recoverable(self):
        for upgrade in (False, True):
            with self.subTest(upgrade=upgrade), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
                bootstrap.home = home
                bootstrap.prefix = home / ".local/share/dev-bootstrap"
                bootstrap.bin_dir = bootstrap.prefix / "bin"
                bootstrap.state_path = home / ".local/state/dev-bootstrap/manifest.json"
                bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
                bootstrap.target_path = str(bootstrap.bin_dir)
                bootstrap.manifest = {"schema": 2, "tools": {}}
                bootstrap._version_at_path = lambda path, label: (
                    "2.0.0" if Path(path).read_bytes() == b"new" else "1.0.0"
                )
                destination = bootstrap.bin_dir / "bun"
                if upgrade:
                    bootstrap.bin_dir.mkdir(parents=True)
                    destination.write_bytes(b"old")
                    bootstrap.manifest["tools"]["bun"] = {
                        "version": "1.0.0", "sha256": bootstrap._file_sha256(destination),
                    }
                    bootstrap._save_manifest()
                save = bootstrap._save_manifest
                attempts = []
                def interrupted_save():
                    attempts.append(1)
                    if len(attempts) == 2:
                        raise KeyboardInterrupt()
                    save()
                bootstrap._save_manifest = interrupted_save
                with self.assertRaises(KeyboardInterrupt):
                    bootstrap._install_managed_binary("bun", "2.0.0", b"new")
                retry = UbuntuBootstrap.__new__(UbuntuBootstrap)
                retry.home, retry.prefix, retry.bin_dir = home, bootstrap.prefix, bootstrap.bin_dir
                retry.state_path, retry.target = bootstrap.state_path, bootstrap.target
                retry.target_path = str(retry.bin_dir)
                retry.dry_run = False
                retry._version_at_path = bootstrap._version_at_path
                retry._load_manifest()
                with self.assertRaises(InstallError):
                    retry.uninstall()
                self.assertEqual(retry._install_managed_binary("bun", "2.0.0", b"new"), "mis à jour" if upgrade else "installé")
                self.assertEqual(destination.read_bytes(), b"new")
                self.assertNotIn("pending_binary", retry.manifest)

    def test_bad_binary_version_does_not_publish_or_replace_existing_binary(self):
        for upgrade in (False, True):
            with self.subTest(upgrade=upgrade), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
                bootstrap.home = home
                bootstrap.prefix = home / ".local/share/dev-bootstrap"
                bootstrap.bin_dir = bootstrap.prefix / "bin"
                bootstrap.state_path = home / ".local/state/dev-bootstrap/manifest.json"
                bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
                bootstrap.target_path = str(bootstrap.bin_dir)
                bootstrap.manifest = {"schema": 2, "tools": {}}
                destination = bootstrap.bin_dir / "bun"
                if upgrade:
                    bootstrap.bin_dir.mkdir(parents=True)
                    destination.write_bytes(b"old")
                    bootstrap.manifest["tools"]["bun"] = {
                        "version": "1.0.0", "sha256": bootstrap._file_sha256(destination),
                    }
                    bootstrap._save_manifest()
                bootstrap._version_at_path = lambda path, label: (
                    "1.0.0" if Path(path).read_bytes() == b"old" else "0.0.0"
                )
                with self.assertRaisesRegex(InstallError, "version stable attendue"):
                    bootstrap._install_managed_binary("bun", "2.0.0", b"bad")
                if upgrade:
                    self.assertEqual(destination.read_bytes(), b"old")
                    self.assertEqual(bootstrap.manifest["tools"]["bun"]["version"], "1.0.0")
                else:
                    self.assertFalse(destination.exists())
                self.assertFalse(list(bootstrap.bin_dir.glob(".bun.*")))

    def test_same_version_changed_official_asset_replaces_managed_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = home
            bootstrap.prefix = home / ".local/share/dev-bootstrap"
            bootstrap.bin_dir = bootstrap.prefix / "bin"
            bootstrap.state_path = home / ".local/state/dev-bootstrap/manifest.json"
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap.target_path = str(bootstrap.bin_dir)
            bootstrap.manifest = {"schema": 2, "tools": {}}
            bootstrap._version_at_path = lambda path, label: "1.2.3"
            bootstrap._install_managed_binary("bun", "1.2.3", b"official-old")
            self.assertEqual(bootstrap._install_managed_binary("bun", "1.2.3", b"official-new"), "mis à jour")
            self.assertEqual((bootstrap.bin_dir / "bun").read_bytes(), b"official-new")
            self.assertEqual(bootstrap._install_managed_binary("bun", "1.2.3", b"official-new"), "déjà conforme")

    def test_external_matching_version_needs_official_binary_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            outside = home / "outside"
            outside.mkdir()
            external = outside / "bun"
            external.write_bytes(b"untrusted")
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = home
            bootstrap.prefix = home / ".local/share/dev-bootstrap"
            bootstrap.bin_dir = bootstrap.prefix / "bin"
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap.target_path = str(outside)
            bootstrap.manifest = {"schema": 2, "tools": {}}
            bootstrap._version_at_path = lambda path, label: "1.2.3"
            bootstrap._save_manifest = MagicMock()
            with patch("install_dev_environment.shutil.which", return_value=str(external)):
                result = bootstrap._install_managed_binary("bun", "1.2.3", b"official")
            self.assertNotEqual(result, "déjà conforme")
            self.assertEqual((bootstrap.bin_dir / "bun").read_bytes(), b"official")
            self.assertEqual(external.read_bytes(), b"untrusted")

    def test_atomic_user_write_fsyncs_parent_after_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = home
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            events = []
            original_replace = os.replace
            original_fsync = os.fsync
            def replace(source, destination):
                events.append("replace")
                return original_replace(source, destination)
            def fsync(fd):
                events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
                return original_fsync(fd)
            with patch("install_dev_environment.os.replace", side_effect=replace), \
                 patch("install_dev_environment.os.fsync", side_effect=fsync):
                bootstrap._atomic_user_write(home / "state", b"content")
            self.assertEqual(events, ["file", "replace", "directory"])

    def test_verified_asset_download_fsyncs_directory_after_publish(self):
        payload = b"verified"
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return "https://github.com/oven-sh/bun/releases/download/bun-v1.0.0/bun.zip"
        with tempfile.TemporaryDirectory() as directory:
            events = []
            original_fsync = os.fsync
            original_replace = os.replace
            def fsync(fd):
                events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
                return original_fsync(fd)
            def replace(source, destination):
                events.append("replace")
                return original_replace(source, destination)
            with patch("install_dev_environment.os.fsync", side_effect=fsync), \
                 patch("install_dev_environment.os.replace", side_effect=replace), \
                 patch("install_dev_environment.urllib.request.urlopen", return_value=Response(payload)):
                install_dev_environment.download_verified_file(
                    Response().geturl(), Path(directory) / "asset", len(payload), hashlib.sha256(payload).hexdigest(),
                )
            self.assertEqual(events, ["file", "replace", "directory"])

    def test_dry_run_rejects_node_binary_shadowing(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.dry_run = True
        bootstrap.target_path = "/home/alice/.nvm/bin:/usr/bin"
        bootstrap.home = Path("/home/alice")
        with patch("install_dev_environment.os.geteuid", return_value=1001), \
             patch("install_dev_environment.shutil.which", return_value="/home/alice/.nvm/bin/node"):
            with self.assertRaisesRegex(InstallError, "masquage"):
                bootstrap._check_node_manager_conflicts()

    def test_root_dry_run_rejects_node_binary_shadowing(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.dry_run = True
        bootstrap.target_path = "/home/alice/.local/bin:/usr/bin"
        bootstrap.home = Path("/home/alice")
        with patch("install_dev_environment.os.geteuid", return_value=0), \
             patch("install_dev_environment.shutil.which", return_value="/home/alice/.local/bin/node"):
            with self.assertRaisesRegex(InstallError, "masquage"):
                bootstrap._check_node_manager_conflicts()

    def test_clean_uninstall_does_not_create_manifest_or_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = home
            bootstrap.prefix = home / ".local/share/dev-bootstrap"
            bootstrap.bin_dir = bootstrap.prefix / "bin"
            bootstrap.state_path = home / ".local/state/dev-bootstrap/manifest.json"
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap.dry_run = False
            bootstrap.uninstall()
            self.assertFalse((home / ".local").exists())

    def test_existing_github_auth_is_preserved_by_default(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.set_step = lambda message: None
        bootstrap.status = {}
        bootstrap.gh_authenticated = True
        bootstrap.auth_tty_available = True
        bootstrap._gh_is_authenticated = lambda: True
        bootstrap._write_tty = MagicMock()
        bootstrap._ask_yes_no = MagicMock(return_value=False)
        bootstrap.user_command = MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""))
        with patch.object(bootstrap, "_read_token_masked", side_effect=AssertionError("unexpected prompt")):
            bootstrap._authenticate_github()
        self.assertEqual(bootstrap.status["gh_auth"], "déjà authentifié")
        bootstrap.user_command.assert_called_once()
        self.assertEqual(
            bootstrap.user_command.call_args.args[0],
            ["gh", "auth", "setup-git", "--hostname", "github.com"],
        )
        bootstrap._ask_yes_no.assert_called_once_with(
            "Effectuer une nouvelle authentification ?", default=False
        )

    def test_unauthenticated_github_reads_token_from_stdin_not_process_arguments(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.set_step = lambda message: None
        bootstrap.status = {}
        bootstrap.gh_authenticated = False
        bootstrap.auth_tty_available = True
        bootstrap._write_tty = MagicMock()
        bootstrap._gh_is_authenticated = MagicMock(side_effect=[False, True])
        bootstrap._read_token_masked = MagicMock(return_value="ghp-example-secret")
        calls = []

        def user_command(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        bootstrap.user_command = user_command
        bootstrap._authenticate_github()

        self.assertEqual(bootstrap.status["gh_auth"], "authentifié")
        self.assertEqual(len(calls), 2)
        command, options = calls[0]
        self.assertEqual(command, ["gh", "auth", "login", "--hostname", "github.com", "--with-token"])
        self.assertNotIn("ghp-example-secret", command)
        self.assertEqual(options["input_data"], "ghp-example-secret\n")
        self.assertEqual(calls[1][0], ["gh", "auth", "setup-git", "--hostname", "github.com"])

    def test_token_prompt_retries_after_empty_input(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap._read_tty_line = MagicMock(side_effect=["  ", "ghp-example-secret"])
        bootstrap._write_tty = MagicMock()

        token = bootstrap._read_token_masked()

        self.assertEqual(token, "ghp-example-secret")
        self.assertEqual(bootstrap._read_tty_line.call_count, 2)
        self.assertTrue(all(call.kwargs["hidden"] for call in bootstrap._read_tty_line.call_args_list))

    def test_git_email_validation_rejects_malformed_addresses(self):
        for email in ("", "no-at-sign", "name@localhost", ".name@example.com", "name..x@example.com",
                      "name@-example.com", "name@example-.com", "name @example.com"):
            with self.subTest(email=email):
                self.assertFalse(UbuntuBootstrap._valid_git_email(email))
        for email in ("name@example.com", "first.last+git@example.co.uk", "élise@example.fr"):
            with self.subTest(email=email):
                self.assertTrue(UbuntuBootstrap._valid_git_email(email))

    def test_git_configuration_preserves_existing_identity_and_sets_main_branch(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.set_step = lambda message: None
        bootstrap.status = {}
        values = {
            "user.name": "Existing Name",
            "user.email": "existing@example.com",
            "init.defaultBranch": "develop",
        }

        def user_command(command, **kwargs):
            key = command[-1] if "--get" in command else command[-2]
            if "--get" in command:
                if key not in values:
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
                return subprocess.CompletedProcess(command, 0, stdout=values[key] + "\n", stderr="")
            values[key] = command[-1]
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        bootstrap.user_command = user_command
        bootstrap._prompt_value = MagicMock(side_effect=lambda _label, current="": current)

        bootstrap._configure_git()

        self.assertEqual(values, {
            "user.name": "Existing Name",
            "user.email": "existing@example.com",
            "init.defaultBranch": "main",
        })
        self.assertEqual(bootstrap.git_config, {
            "name": "Existing Name",
            "email": "existing@example.com",
            "default_branch": "main",
        })
        self.assertEqual(bootstrap.status["git"], "configuré")

    def test_git_configuration_prompts_when_identity_is_missing(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.set_step = lambda message: None
        bootstrap.status = {}
        values = {}

        def user_command(command, **kwargs):
            key = command[-1] if "--get" in command else command[-2]
            if "--get" in command:
                if key not in values:
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
                return subprocess.CompletedProcess(command, 0, stdout=values[key] + "\n", stderr="")
            values[key] = command[-1]
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        bootstrap.user_command = user_command
        bootstrap._prompt_value = MagicMock(side_effect=["Ada Lovelace", "ada@example.com"])

        bootstrap._configure_git()

        self.assertEqual(values, {
            "user.name": "Ada Lovelace",
            "user.email": "ada@example.com",
            "init.defaultBranch": "main",
        })
        self.assertEqual(bootstrap._prompt_value.call_args_list, [
            call("Nom ou pseudo Git", ""),
            call("Adresse e-mail Git", ""),
        ])

    def test_dry_run_plan_discloses_apt_version_is_not_yet_resolved(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.plan = {"node": "24.2.0", "npm": "11.0.0", "pnpm": "10.0.0",
                          "bun": {"version": "1.0.0"}, "uv": {"version": "1.0.0"},
                          "zoxide": {"version": "1.0.0"}, "fzf": {"version": "1.0.0"}, "gh": "2.0.0"}
        bootstrap.version_id, bootstrap.codename, bootstrap.arch = "24.04", "noble", "amd64"
        bootstrap.gh_authenticated = False
        output = StringIO()
        with redirect_stdout(output):
            bootstrap.print_plan()
        self.assertIn("APT", output.getvalue())
        self.assertIn("non vérifié", output.getvalue())

    def test_atomic_user_write_rejects_symlink_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            real = home / "real"
            real.mkdir()
            (home / "link").symlink_to(real, target_is_directory=True)
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = home
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            with self.assertRaises(InstallError):
                bootstrap._atomic_user_write(home / "link" / "config", b"data")
            self.assertFalse((real / "config").exists())

    def test_dry_run_stops_after_preflight_without_installing(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.dry_run = True
        bootstrap.preflight = MagicMock()
        bootstrap.print_plan = MagicMock()
        bootstrap.download_verified_artifacts = MagicMock(side_effect=AssertionError("download"))
        bootstrap._install_sources = MagicMock(side_effect=AssertionError("APT"))
        self.assertEqual(bootstrap.run(), 0)
        bootstrap.preflight.assert_called_once()
        bootstrap.print_plan.assert_called_once()

    def test_existing_root_directory_mode_is_not_broadened(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keys"
            path.mkdir(mode=0o700)
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            real_stat = Path.stat
            def root_owned(item, *args, **kwargs):
                original = real_stat(item, *args, **kwargs)
                values = list(original)
                values[4] = 0
                return os.stat_result(values)
            with patch("install_dev_environment.os.geteuid", return_value=0), \
                 patch("install_dev_environment.Path.stat", autospec=True, side_effect=root_owned):
                bootstrap._ensure_root_directory(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_uninstall_removes_only_verified_user_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            prefix = home / ".local/share/dev-bootstrap"
            bin_dir = prefix / "bin"
            bin_dir.mkdir(parents=True)
            binary = bin_dir / "bun"
            binary.write_bytes(b"binary")
            unrelated = bin_dir / "user"
            unrelated.write_bytes(b"mine")
            state = home / ".local/state/dev-bootstrap/manifest.json"
            state.parent.mkdir(parents=True)
            digest = install_dev_environment.hashlib.sha256(binary.read_bytes()).hexdigest()
            state.write_text(json.dumps({"schema": 2, "tools": {"bun": {"version": "1.0.0", "sha256": digest}}}))
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home, bootstrap.prefix, bootstrap.bin_dir, bootstrap.state_path = home, prefix, bin_dir, state
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap.dry_run = False
            bootstrap.uninstall()
            self.assertFalse(binary.exists())
            self.assertEqual(unrelated.read_bytes(), b"mine")
            self.assertEqual(json.loads(state.read_text())["tools"], {})

    def test_manifest_schema_one_migrates_to_two_in_memory_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            state = home / ".local/state/dev-bootstrap/manifest.json"
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps({"schema": 1, "tools": {}}))
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = home
            bootstrap.prefix = home / ".local/share/dev-bootstrap"
            bootstrap.state_path = state
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap._load_manifest()
            self.assertEqual(bootstrap.manifest["schema"], 2)
            self.assertEqual(json.loads(state.read_text())["schema"], 1)

    def test_uninstall_dry_run_preserves_managed_binary_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            prefix = home / ".local/share/dev-bootstrap"
            bin_dir = prefix / "bin"
            bin_dir.mkdir(parents=True)
            binary = bin_dir / "bun"
            binary.write_bytes(b"owned binary")
            state = home / ".local/state/dev-bootstrap/manifest.json"
            state.parent.mkdir(parents=True)
            digest = install_dev_environment.hashlib.sha256(binary.read_bytes()).hexdigest()
            state.write_text(json.dumps({"schema": 1, "tools": {"bun": {"version": "1.0.0", "sha256": digest}}}))
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home, bootstrap.prefix, bootstrap.bin_dir, bootstrap.state_path = home, prefix, bin_dir, state
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap.manifest = {"schema": 1, "tools": {"bun": {"version": "1.0.0", "sha256": digest}}}
            bootstrap.dry_run = True
            bootstrap.uninstall()
            self.assertTrue(binary.exists())
            self.assertTrue(state.exists())

    def test_uninstall_rejects_tampered_binary_without_deleting_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            prefix = home / ".local/share/dev-bootstrap"
            bin_dir = prefix / "bin"
            bin_dir.mkdir(parents=True)
            binary = bin_dir / "bun"
            binary.write_bytes(b"tampered")
            state = home / ".local/state/dev-bootstrap/manifest.json"
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps({"schema": 2, "tools": {"bun": {"version": "1.0.0", "sha256": "a" * 64}}}))
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home, bootstrap.prefix, bootstrap.bin_dir, bootstrap.state_path = home, prefix, bin_dir, state
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap.dry_run = False
            with self.assertRaises(InstallError):
                bootstrap.uninstall()
            self.assertTrue(binary.exists())
            self.assertTrue(state.exists())

    def test_atomic_user_write_preserves_existing_mode_and_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_bytes(b"old")
            path.chmod(0o600)
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = Path(directory)
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap._atomic_user_write(path, b"new")
            self.assertEqual(path.read_bytes(), b"new")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_uid, os.getuid())

    def test_root_file_context_drops_supplementary_groups_then_restores(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.target = TargetUser("alice", 1001, 1002, "/home/alice", "/bin/bash")
        events = []
        with patch("install_dev_environment.os.geteuid", return_value=0), \
             patch("install_dev_environment.os.getegid", return_value=0), \
             patch("install_dev_environment.os.getgroups", return_value=[0, 27]), \
             patch("install_dev_environment.os.setgroups", side_effect=lambda groups: events.append(("groups", groups))), \
             patch("install_dev_environment.os.setegid", side_effect=lambda gid: events.append(("gid", gid))), \
             patch("install_dev_environment.os.seteuid", side_effect=lambda uid: events.append(("uid", uid))):
            with bootstrap.target_file_privileges():
                self.assertEqual(events[:3], [("groups", []), ("gid", 1002), ("uid", 1001)])
        self.assertEqual(events[-3:], [("uid", 0), ("gid", 0), ("groups", [0, 27])])

    def test_existing_user_directory_permissions_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = os.path.join(temporary, "home")
            os.mkdir(home)
            directory = os.path.join(home, ".config")
            os.mkdir(directory, 0o700)
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = Path(home)
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), home, "/bin/bash")

            bootstrap._ensure_user_directory(Path(directory), 0o755)

            self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), 0o700)

    def test_report_uses_verified_docker_engine_version(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), "/tmp", "/bin/bash")
        bootstrap.verified_versions = {"Docker Engine": "29.0.1", "Docker Compose": "5.0.0"}
        bootstrap.status = {}
        bootstrap.git_config = {
            "name": "Test User",
            "email": "test@example.com",
            "default_branch": "main",
        }

        output = StringIO()
        with redirect_stdout(output):
            bootstrap.print_report()

        self.assertIn("Docker Engine: 29.0.1", output.getvalue())


class NpmInstallTests(unittest.TestCase):
    def test_keyboard_interrupt_during_fresh_npm_write_is_inventoried_for_retry(self):
        self._assert_interrupted_npm_retry(upgrade=False)

    def test_keyboard_interrupt_during_npm_upgrade_is_inventoried_for_retry(self):
        self._assert_interrupted_npm_retry(upgrade=True)

    def _assert_interrupted_npm_retry(self, *, upgrade):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = home
            bootstrap.prefix = home / ".local/share/dev-bootstrap"
            bootstrap.bin_dir = bootstrap.prefix / "bin"
            bootstrap.state_path = home / ".local/state/dev-bootstrap/manifest.json"
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap.target_path = str(bootstrap.bin_dir)
            bootstrap.plan = {"npm": "11.0.0", "pnpm": "10.0.0"}
            bootstrap.manifest = {"schema": 2, "tools": {}}
            bootstrap.status, bootstrap.verified_versions = {}, {}
            bootstrap.set_step = lambda message: None
            calls = []
            bootstrap._version_at_path = lambda path, label: (
                "9.0.0" if upgrade and not calls and label == "npm" else bootstrap.plan[label]
            )
            bootstrap._tool_version = lambda name: "9.0.0" if upgrade else None
            package_file = bootstrap.prefix / "lib/node_modules/npm/package.json"
            binary = bootstrap.bin_dir / "npm"
            if upgrade:
                package_file.parent.mkdir(parents=True)
                bootstrap.bin_dir.mkdir()
                package_file.write_bytes(b"old package")
                binary.write_bytes(b"old binary")
                bootstrap.manifest["npm_packages"] = {"npm": bootstrap._npm_package_snapshot("npm")}
                bootstrap.manifest["tools"]["npm"] = {
                    "version": "9.0.0", "sha256": bootstrap._file_sha256(binary),
                }
                bootstrap._save_manifest()
            def install(command, **kwargs):
                calls.append(command)
                package_name = next(
                    (name for name in ("npm", "pnpm")
                     if any(argument.startswith(f"{name}@") for argument in command)),
                    None,
                )
                if package_name:
                    package = bootstrap.prefix / "lib/node_modules" / package_name
                    package.mkdir(parents=True, exist_ok=True)
                    (package / "package.json").write_bytes(b"new package")
                    bootstrap.bin_dir.mkdir(exist_ok=True)
                    (bootstrap.bin_dir / package_name).write_bytes(b"new binary")
                if len(calls) == 1:
                    raise KeyboardInterrupt()
                return subprocess.CompletedProcess(command, 0)
            bootstrap.user_command = install
            with patch("install_dev_environment.shutil.which", return_value="/usr/bin/npm"):
                with self.assertRaises(KeyboardInterrupt):
                    bootstrap._configure_npm_tools()
                retry = UbuntuBootstrap.__new__(UbuntuBootstrap)
                retry.home, retry.prefix, retry.bin_dir = home, bootstrap.prefix, bootstrap.bin_dir
                retry.state_path, retry.target = bootstrap.state_path, bootstrap.target
                retry._load_manifest()
                self.assertIn("npm", retry.manifest["pending_npm"]["packages"])
                retry._check_npm_prefix()
                package_file.write_bytes(b"user edit")
                with self.assertRaises(InstallError):
                    retry._check_npm_prefix()
                package_file.write_bytes(b"new package")
                bootstrap.manifest = retry.manifest
                bootstrap._configure_npm_tools()
            self.assertEqual(len(calls), 4)
            self.assertNotIn("pending_npm", bootstrap.manifest)

    def test_matching_untracked_npm_binary_is_not_declared_conforming(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = Path(directory)
            bootstrap.prefix = bootstrap.home / ".local/share/dev-bootstrap"
            bootstrap.bin_dir = bootstrap.prefix / "bin"
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap.target_path = directory
            bootstrap.plan = {"npm": "11.0.0", "pnpm": "10.0.0"}
            bootstrap.manifest = {"schema": 2, "tools": {}}
            bootstrap.status, bootstrap.verified_versions = {}, {}
            bootstrap.set_step = lambda message: None
            bootstrap._tool_version = lambda name: bootstrap.plan[name]
            bootstrap._version_at_path = lambda path, label: bootstrap.plan[label]
            bootstrap._save_manifest = lambda: None
            commands = []
            def install(command, **kwargs):
                commands.append(command)
                package_name = next(
                    (name for name in ("npm", "pnpm")
                     if any(argument.startswith(f"{name}@") for argument in command)),
                    None,
                )
                if package_name:
                    package = bootstrap.prefix / "lib/node_modules" / package_name
                    package.mkdir(parents=True, exist_ok=True)
                    (package / "package.json").write_bytes(b"official")
                    bootstrap.bin_dir.mkdir(exist_ok=True)
                    (bootstrap.bin_dir / package_name).write_bytes(b"binary")
                return subprocess.CompletedProcess(command, 0)
            bootstrap.user_command = install
            with patch("install_dev_environment.shutil.which", return_value="/usr/bin/npm"):
                bootstrap._configure_npm_tools()
            self.assertEqual(len(commands), 3)
            self.assertIn("npm@11.0.0", commands[0])
            self.assertIn("pnpm@10.0.0", commands[1])
            self.assertIn("rebuild", commands[2])

    def test_failed_global_install_tracks_partial_tree_for_safe_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = Path(directory)
            bootstrap.prefix = bootstrap.home / ".local/share/dev-bootstrap"
            bootstrap.bin_dir = bootstrap.prefix / "bin"
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap.target_path = str(bootstrap.bin_dir)
            bootstrap.plan = {"npm": "11.0.0", "pnpm": "10.0.0"}
            bootstrap.manifest = {"schema": 2, "tools": {}}
            bootstrap.status, bootstrap.verified_versions = {}, {}
            bootstrap.set_step = lambda message: None
            bootstrap._tool_version = lambda name: None
            bootstrap._version_at_path = lambda path, label: bootstrap.plan[label]
            bootstrap._save_manifest = lambda: None
            calls = []
            def install(command, **kwargs):
                calls.append(command)
                package_name = next(
                    (name for name in ("npm", "pnpm")
                     if any(argument.startswith(f"{name}@") for argument in command)),
                    None,
                )
                if package_name:
                    package = bootstrap.prefix / "lib/node_modules" / package_name
                    package.mkdir(parents=True, exist_ok=True)
                    (package / "package.json").write_bytes(b"partial")
                    bootstrap.bin_dir.mkdir(exist_ok=True)
                    (bootstrap.bin_dir / package_name).write_bytes(b"binary")
                if len(calls) == 1:
                    raise InstallError("npm failed")
                return subprocess.CompletedProcess(command, 0)
            bootstrap.user_command = install
            with patch("install_dev_environment.shutil.which", return_value="/usr/bin/npm"):
                with self.assertRaises(InstallError):
                    bootstrap._configure_npm_tools()
                bootstrap._check_npm_prefix()
                bootstrap._configure_npm_tools()
            self.assertEqual(len(calls), 4)
            self.assertNotIn("pending_npm", bootstrap.manifest)

    def test_failed_npm_install_keeps_partial_unmodified_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = Path(directory)
            bootstrap.prefix = bootstrap.home / ".local/share/dev-bootstrap"
            bootstrap.bin_dir = bootstrap.prefix / "bin"
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap.plan = {"npm": "11.0.0", "pnpm": "10.0.0"}
            bootstrap.manifest = {"schema": 2, "tools": {}}
            bootstrap.status, bootstrap.verified_versions = {}, {}
            bootstrap.set_step = lambda message: None
            bootstrap._tool_version = lambda name: None
            bootstrap._save_manifest = lambda: None
            package_file = bootstrap.prefix / "lib/node_modules/npm/package.json"
            def fail(command, **kwargs):
                package_file.parent.mkdir(parents=True)
                package_file.write_bytes(b"partial")
                raise InstallError("npm failed")
            bootstrap.user_command = fail
            with patch("install_dev_environment.shutil.which", return_value="/usr/bin/npm"):
                with self.assertRaises(InstallError):
                    bootstrap._configure_npm_tools()
            self.assertEqual(package_file.read_bytes(), b"partial")
            package_file.write_bytes(b"user change")
            with self.assertRaises(InstallError):
                bootstrap._check_npm_prefix()
            self.assertEqual(package_file.read_bytes(), b"user change")

    def test_npm_success_exit_with_wrong_version_remains_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = Path(directory)
            bootstrap.prefix = bootstrap.home / ".local/share/dev-bootstrap"
            bootstrap.bin_dir = bootstrap.prefix / "bin"
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap.plan = {"npm": "11.0.0", "pnpm": "10.0.0"}
            bootstrap.manifest = {"schema": 2, "tools": {}}
            bootstrap.status, bootstrap.verified_versions = {}, {}
            bootstrap.set_step = lambda message: None
            bootstrap._tool_version = lambda name: None
            bootstrap._version_at_path = lambda path, label: "0.0.1"
            bootstrap._save_manifest = lambda: None
            def install(command, **kwargs):
                if "npm@11.0.0" in command:
                    package = bootstrap.prefix / "lib/node_modules/npm"
                    package.mkdir(parents=True, exist_ok=True)
                    (package / "package.json").write_bytes(b"partial")
                return subprocess.CompletedProcess(command, 0)
            bootstrap.user_command = install
            with patch("install_dev_environment.shutil.which", return_value="/usr/bin/npm"), \
                 self.assertRaises(InstallError):
                bootstrap._configure_npm_tools()
            bootstrap._check_npm_prefix()
            self.assertIn("npm", bootstrap.manifest["pending_npm"]["packages"])

    def test_npm_auxiliary_links_are_inventoried_for_next_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = Path(directory)
            bootstrap.prefix = bootstrap.home / ".local/share/dev-bootstrap"
            bootstrap.bin_dir = bootstrap.prefix / "bin"
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap.plan = {"npm": "11.0.0", "pnpm": "10.0.0"}
            bootstrap.manifest = {"schema": 2, "tools": {}}
            bootstrap.status, bootstrap.verified_versions = {}, {}
            bootstrap.set_step = lambda message: None
            bootstrap._tool_version = lambda name: None
            bootstrap._version_at_path = lambda path, label: bootstrap.plan[label]
            bootstrap._save_manifest = lambda: None
            def install(command, **kwargs):
                package_name = next(
                    (name for name in ("npm", "pnpm")
                     if any(argument.startswith(f"{name}@") for argument in command)),
                    None,
                )
                if package_name:
                    package = bootstrap.prefix / "lib/node_modules" / package_name
                    package.mkdir(parents=True, exist_ok=True)
                    (package / "package.json").write_bytes(b"binary")
                    bootstrap.bin_dir.mkdir(exist_ok=True)
                    (bootstrap.bin_dir / package_name).write_bytes(b"binary")
                    auxiliary = "npx" if package_name == "npm" else "pnpx"
                    (bootstrap.bin_dir / auxiliary).write_bytes(b"auxiliary")
                return subprocess.CompletedProcess(command, 0)
            bootstrap.user_command = install
            with patch("install_dev_environment.shutil.which", return_value="/usr/bin/npm"):
                bootstrap._configure_npm_tools()
            bootstrap._check_npm_prefix()

    def test_global_packages_are_forced_to_official_registry(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.plan = {"npm": "12.1.0", "pnpm": "12.6.0"}
        bootstrap.status = {}
        bootstrap.verified_versions = {}
        bootstrap.manifest = {"schema": 1, "tools": {}}
        bootstrap.prefix = Path("/home/tester/.local/share/dev-bootstrap")
        bootstrap.bin_dir = bootstrap.prefix / "bin"
        bootstrap._package_binary_is_managed = lambda name: False
        bootstrap._tool_version = lambda name: None
        versions = iter(("12.1.0", "12.6.0"))
        bootstrap._version_at_path = lambda path, label: next(versions)
        bootstrap._file_sha256 = lambda path: "a" * 64
        bootstrap._npm_package_snapshot = lambda name: {".": "test"}
        bootstrap._save_manifest = lambda: None
        commands = []

        def record_command(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        bootstrap.user_command = record_command
        with patch("install_dev_environment.shutil.which", return_value="/usr/bin/npm"):
            bootstrap._configure_npm_tools()

        command = commands[0]
        self.assertIn("--registry", command)
        registry_index = command.index("--registry")
        self.assertEqual(command[registry_index + 1], "https://registry.npmjs.org")


class SystemCommandTests(unittest.TestCase):
    def test_apt_candidate_rejects_foreign_higher_priority_same_version(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.arch, bootstrap.codename = "amd64", "noble"
        bootstrap.plan = {"node_major": "24"}
        policy = """nodejs:
  Installed: (none)
  Candidate: 24.9.0-1
  Version table:
     24.9.0-1 900
        900 https://evil.example/node_24.x nodistro/main amd64 Packages
        500 https://deb.nodesource.com/node_24.x nodistro/main amd64 Packages
"""
        bootstrap.system_command = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, policy)
        with self.assertRaisesRegex(InstallError, "provenance|dépôt officiel"):
            bootstrap._apt_candidate("nodejs", "deb.nodesource.com/node_24.x")

    def test_apt_candidate_rejects_hostname_substring_and_untrusted_same_version(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.arch, bootstrap.codename = "amd64", "noble"
        bootstrap.plan = {"node_major": "24"}
        for source in ("https://evil.example/deb.nodesource.com/node_24.x",
                       "https://deb.nodesource.com.evil.example/node_24.x"):
            policy = ("nodejs:\n  Installed: (none)\n  Candidate: 24.9.0-1\n  Version table:\n"
                      f"     24.9.0-1 500\n        500 {source} nodistro/main amd64 Packages\n")
            bootstrap.system_command = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, policy)
            with self.subTest(source=source), self.assertRaises(InstallError):
                bootstrap._apt_candidate("nodejs", "deb.nodesource.com/node_24.x")

    def test_apt_candidate_accepts_only_exact_repository_suite_component_and_arch(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.arch, bootstrap.codename = "amd64", "noble"
        bootstrap.plan = {"node_major": "24"}
        for package, marker, source in (
            ("nodejs", "deb.nodesource.com/node_24.x", "https://deb.nodesource.com/node_24.x nodistro/main amd64"),
            ("gh", "cli.github.com/packages", "https://cli.github.com/packages stable/main amd64"),
            ("docker-ce", "download.docker.com/linux/ubuntu", "https://download.docker.com/linux/ubuntu noble/stable amd64"),
        ):
            policy = (f"{package}:\n  Installed: (none)\n  Candidate: 1.2.3-1\n  Version table:\n"
                      f"     1.2.3-1 500\n        500 {source} Packages\n")
            bootstrap.system_command = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, policy)
            with self.subTest(package=package):
                self.assertEqual(bootstrap._apt_candidate(package, marker), "1.2.3-1")

    def test_apt_candidate_rejects_foreign_same_priority_and_wrong_suite(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.arch, bootstrap.codename = "amd64", "noble"
        bootstrap.plan = {"node_major": "24"}
        for extra in ("500 https://evil.example/repo nodistro/main amd64 Packages",
                      "500 https://deb.nodesource.com/node_24.x evil/main amd64 Packages"):
            policy = ("nodejs:\n  Installed: (none)\n  Candidate: 24.9.0-1\n  Version table:\n"
                      "     24.9.0-1 500\n"
                      "        500 https://deb.nodesource.com/node_24.x nodistro/main amd64 Packages\n"
                      f"        {extra}\n")
            bootstrap.system_command = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, policy)
            with self.subTest(extra=extra), self.assertRaises(InstallError):
                bootstrap._apt_candidate("nodejs", "deb.nodesource.com/node_24.x")

    def test_lagging_node_candidate_reselects_compatible_npm_and_pnpm(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.plan = {"node": "24.10.0", "node_major": "24", "gh": "2.90.0",
                          "npm": "12.0.0", "pnpm": "11.0.0"}
        bootstrap.apt_candidates = {}
        bootstrap.set_step = lambda message: None
        versions = {name: "1.2.3-1" for name in bootstrap.APT_PACKAGES}
        versions.update(nodejs="24.9.0-1", gh="2.89.0-1")
        bootstrap._apt_candidate = lambda name, marker: versions[name]
        npm = {"dist-tags": {"latest": "12.0.0"}, "versions": {
            "12.0.0": {"engines": {"node": ">=24.10"}},
            "11.9.0": {"engines": {"node": ">=24.0"}}}}
        pnpm = {"dist-tags": {"latest": "11.0.0"}, "versions": {
            "11.0.0": {"engines": {"node": ">=24.10"}},
            "10.5.0": {"engines": {"node": ">=24.0"}}}}
        with patch("install_dev_environment.fetch_https_json", side_effect=[npm, pnpm]):
            bootstrap._resolve_apt_candidates()
        self.assertEqual((bootstrap.plan["npm"], bootstrap.plan["pnpm"]), ("11.9.0", "10.5.0"))

    def test_existing_apt_directory_rejects_world_writable_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keys"
            path.mkdir(mode=0o777)
            path.chmod(0o777)
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            real_stat = Path.stat
            def root_owned(item, *args, **kwargs):
                values = list(real_stat(item, *args, **kwargs))
                values[4] = 0
                return os.stat_result(values)
            with patch("install_dev_environment.Path.stat", autospec=True, side_effect=root_owned), \
                 self.assertRaises(InstallError):
                bootstrap._ensure_root_directory(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o777)

    def test_lagging_apt_candidates_same_supported_branch_are_allowed(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.plan = {"node": "24.10.0", "node_major": "24", "gh": "2.90.0",
                          "npm": "11.0.0", "pnpm": "10.0.0"}
        bootstrap.apt_candidates = {}
        bootstrap.set_step = lambda message: None
        versions = {name: "1.2.3-1" for name in bootstrap.APT_PACKAGES}
        versions.update(nodejs="24.9.0-1", gh="2.89.0-1")
        bootstrap._apt_candidate = lambda name, marker: versions[name]
        npm = {"dist-tags": {"latest": "11.0.0"}, "versions": {
            "11.0.0": {"engines": {"node": ">=24"}}}}
        pnpm = {"dist-tags": {"latest": "10.0.0"}, "versions": {
            "10.0.0": {"engines": {"node": ">=24"}}}}
        with patch("install_dev_environment.fetch_https_json", side_effect=[npm, pnpm]):
            bootstrap._resolve_apt_candidates()
        self.assertEqual(bootstrap.apt_candidates["nodejs"], "24.9.0-1")
        self.assertEqual(bootstrap.apt_candidates["gh"], "2.89.0-1")
        self.assertEqual(bootstrap.plan["node"], "24.9.0")
        self.assertEqual(bootstrap.plan["gh"], "2.89.0")

    def test_apt_installs_exact_resolved_versions_and_checks_each_result(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.APT_PACKAGES = ("nodejs", "gh")
        bootstrap.apt_candidates = {}
        bootstrap.status = {}
        bootstrap._resolve_apt_candidates = lambda: bootstrap.apt_candidates.update({"nodejs": "24.1-1", "gh": "2.3-1"})
        versions = iter((None, None, "24.1-1", "2.3-2"))
        bootstrap._package_installed_version = lambda name: next(versions)
        commands = []
        bootstrap.system_command = lambda command, **kwargs: (commands.append(command) or subprocess.CompletedProcess(command, 0, "", ""))
        with self.assertRaises(InstallError):
            bootstrap._install_apt_packages()
        self.assertIn("nodejs=24.1-1", commands[0])
        self.assertIn("gh=2.3-1", commands[0])

    def test_codex_sandbox_ready_without_apparmor_changes(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.status = {}
        bootstrap.set_step = lambda message: None
        bootstrap._package_installed_version = lambda name: "1.0"
        installs = []
        bootstrap._safe_apt_install = lambda packages, label: installs.append(packages)
        bootstrap.user_command = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", "")
        with patch("install_dev_environment.shutil.which", return_value="/usr/bin/bwrap"):
            bootstrap._prepare_codex_sandbox()
        self.assertEqual(installs, [["bubblewrap"]])
        self.assertEqual(bootstrap.status["bwrap_sandbox"], "opérationnel")

    def test_codex_sandbox_retries_after_apparmor_profile_load(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.status = {}
        bootstrap.set_step = lambda message: None
        bootstrap._package_installed_version = lambda name: "1.0"
        installs = []
        commands = []
        bootstrap._safe_apt_install = lambda packages, label: installs.append(packages)
        bootstrap.system_command = lambda command, **kwargs: commands.append(command)
        results = iter((1, 0))
        bootstrap.user_command = lambda command, **kwargs: subprocess.CompletedProcess(
            command, next(results), "", ""
        )
        with patch("install_dev_environment.shutil.which", return_value="/usr/bin/bwrap"), \
             patch("install_dev_environment.Path.exists", return_value=True), \
             patch("install_dev_environment.Path.is_file", return_value=True):
            bootstrap._prepare_codex_sandbox()
        self.assertEqual(installs, [["bubblewrap"], ["apparmor-utils"]])
        self.assertEqual(commands[0][:2], ["apparmor_parser", "-r"])
        self.assertEqual(bootstrap.status["bwrap_sandbox"], "opérationnel")

    def test_gpg_rejects_subkey_fingerprints_and_accepts_primary(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        primary = "2C6106201985B60E6C7AC87323F3D4EA75716059"
        subkey = "7F38BBB59D064DBCB3D84D725612B36462313325"
        output = f"pub:::::::::\nfpr:::::::::{primary}:\nsub:::::::::\nfpr:::::::::{subkey}:\n"
        completed = subprocess.CompletedProcess(["gpg"], 0, output.encode(), b"")
        with patch("install_dev_environment.shutil.which", return_value="/usr/bin/gpg"), \
                patch("install_dev_environment.subprocess.run", return_value=completed):
            self.assertEqual(bootstrap._gpg_fingerprints(b"key"), {primary})

    def test_rotated_github_key_is_accepted_without_static_binary_digest(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.set_step = lambda message: None
        bootstrap._gpg_fingerprints = lambda raw: {"7F38BBB59D064DBCB3D84D725612B36462313325"}
        written = []
        bootstrap._write_root_file = lambda *args: written.append(args)
        with patch("install_dev_environment.fetch_https_bytes", return_value=b"rotated key"), \
                patch("install_dev_environment.Path.exists", return_value=False):
            bootstrap._prepare_key("github-cli", "https://cli.github.com/packages/key.gpg")
        self.assertEqual(len(written), 1)

    def test_existing_trusted_github_key_can_rotate_to_new_primary(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.set_step = lambda message: None
        old, new = sorted(install_dev_environment.APT_KEY_TRUST_V1["github-cli"])
        bootstrap._gpg_fingerprints = lambda data: {old} if data == b"old" else {new}
        writes = []
        bootstrap._write_root_file = lambda *args, **kwargs: writes.append((args, kwargs))
        key_path = Path("/etc/apt/keyrings/dev-bootstrap-github-cli.gpg")
        with patch("install_dev_environment.fetch_https_bytes", return_value=b"new"), \
             patch("install_dev_environment.Path.exists", autospec=True,
                   side_effect=lambda path: path == key_path), \
             patch("install_dev_environment.Path.is_symlink", return_value=False), \
             patch("install_dev_environment.Path.is_file", return_value=True), \
             patch("install_dev_environment.Path.read_bytes", return_value=b"old"), \
             patch.object(bootstrap, "_check_root_file"):
            bootstrap._prepare_key("github-cli", "https://cli.github.com/packages/key.gpg")
        self.assertTrue(writes)
        self.assertTrue(writes[0][1]["allow_key_rotation"])
    def test_system_commands_use_a_fixed_executable_path_and_path_env(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.step = "test"
        completed = subprocess.CompletedProcess(["apt-get"], 0, "", "")

        with patch("install_dev_environment.os.geteuid", return_value=0), \
                patch("install_dev_environment.shutil.which", return_value="/usr/bin/apt-get"), \
                patch("install_dev_environment.subprocess.run", return_value=completed) as run:
            bootstrap._run(["apt-get", "update"], capture=True)

        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertEqual(command[0], "/usr/bin/apt-get")
        self.assertEqual(environment["PATH"], bootstrap.SYSTEM_PATH)

    def test_privileged_commands_and_gpg_discard_inherited_configuration(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.step = "test"
        result = subprocess.CompletedProcess([], 0, b"", b"")
        hostile = {"APT_CONFIG": "/tmp/evil", "DEBIAN_FRONTEND": "readline",
                   "GNUPGHOME": "/tmp/evil", "GPG_AGENT_INFO": "evil",
                   "LD_PRELOAD": "/tmp/evil.so", "BASH_ENV": "/tmp/evil",
                   "http_proxy": "http://evil", "HOME": "/tmp/evil"}
        with patch.dict(os.environ, hostile), patch("install_dev_environment.os.geteuid", return_value=0), \
             patch("install_dev_environment.shutil.which", return_value="/usr/bin/tool"), \
             patch("install_dev_environment.subprocess.run", return_value=result) as run:
            bootstrap._run(["apt-get", "update"])
            bootstrap._gpg_fingerprints = UbuntuBootstrap._gpg_fingerprints.__get__(bootstrap)
            with self.assertRaises(InstallError):
                bootstrap._gpg_fingerprints(b"invalid")
        self.assertEqual(len(run.call_args_list), 2)
        command_env, gpg_env = [call.kwargs["env"] for call in run.call_args_list]
        inherited_settings = ("APT_CONFIG", "GPG_AGENT_INFO", "LD_PRELOAD", "BASH_ENV", "http_proxy")
        for env in (command_env, gpg_env):
            self.assertEqual(env["PATH"], bootstrap.SYSTEM_PATH)
            self.assertTrue(all(key not in env for key in inherited_settings))
            self.assertEqual(env["DEBIAN_FRONTEND"], "noninteractive")
        self.assertEqual(command_env["HOME"], "/root")
        self.assertNotEqual(gpg_env["HOME"], hostile["HOME"])
        self.assertEqual(gpg_env["GNUPGHOME"], gpg_env["HOME"])

    def test_root_file_equal_content_rejects_wrong_owner_or_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dev-bootstrap-test.sources"
            path.write_bytes(b"same")
            if os.geteuid() == 0:
                os.chown(path, 65534, 65534)
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            with self.assertRaisesRegex(InstallError, "non sûr"):
                bootstrap._write_root_file(path, b"same")

    def test_preflight_rejects_unsafe_existing_managed_key(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap._expected_sources = dict
        bootstrap._check_root_directory = lambda path: None
        key = Path("/etc/apt/keyrings/dev-bootstrap-docker.asc")
        with patch("install_dev_environment.Path.exists", autospec=True, side_effect=lambda path: path == key), \
             patch("install_dev_environment.Path.is_symlink", return_value=False), \
             patch.object(bootstrap, "_check_root_file", side_effect=InstallError("unsafe key")), \
             self.assertRaisesRegex(InstallError, "unsafe key"):
            bootstrap._check_apt_source_conflicts()

    def test_apt_install_refuses_removal_before_install(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.APT_PACKAGES = ("nodejs", "gh")
        bootstrap.apt_candidates = {}
        bootstrap.status = {}
        bootstrap._resolve_apt_candidates = lambda: bootstrap.apt_candidates.update({"nodejs": "24.1-1", "gh": "2.3-1"})
        bootstrap._package_installed_version = lambda name: None
        commands = []
        def command(args, **kwargs):
            commands.append(args)
            return subprocess.CompletedProcess(args, 0, "Remv unrelated [1.0]\n", "")
        bootstrap.system_command = command
        with self.assertRaises(InstallError):
            bootstrap._install_apt_packages()
        self.assertFalse(any("install" in args and "-s" not in args for args in commands))

    def test_root_target_path_retains_initiating_users_node_manager(self):
        target = TargetUser("alice", 1001, 1001, "/home/alice", "/bin/bash")
        with patch("install_dev_environment.resolve_target_user", return_value=target), \
             patch("install_dev_environment.os.geteuid", return_value=0), \
             patch.dict(os.environ, {"PATH": "/home/alice/.volta/bin:/usr/bin", "SUDO_USER": "alice"}):
            bootstrap = UbuntuBootstrap()
        self.assertIn("/home/alice/.volta/bin", bootstrap.target_path)

    def test_node_manager_detected_when_sudo_secure_path_hides_volta(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = Path(directory)
            bootstrap.target_path = UbuntuBootstrap.SYSTEM_PATH
            manager = bootstrap.home / ".volta/bin"
            manager.mkdir(parents=True)
            (manager / "node").write_bytes(b"node")
            (manager / "node").chmod(0o755)
            with patch("install_dev_environment.shutil.which", side_effect=lambda name, path=None: None), \
                 self.assertRaises(InstallError):
                bootstrap._check_node_manager_conflicts()

    def test_npm_rejects_untracked_package_before_global_install(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.home = Path(directory)
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap.prefix = bootstrap.home / ".local/share/dev-bootstrap"
            bootstrap.bin_dir = bootstrap.prefix / "bin"
            package = bootstrap.prefix / "lib/node_modules/npm"
            package.mkdir(parents=True)
            (package / "package.json").write_text('{"version":"11.0.0"}')
            bootstrap.manifest = {"schema": 2, "tools": {}}
            bootstrap.plan = {"npm": "11.0.0", "pnpm": "10.0.0"}
            bootstrap.status, bootstrap.verified_versions = {}, {}
            bootstrap._tool_version = lambda name: None
            bootstrap.set_step = lambda message: None
            bootstrap.user_command = MagicMock()
            with patch("install_dev_environment.shutil.which", return_value="/usr/bin/npm"), \
                 self.assertRaises(InstallError):
                bootstrap._configure_npm_tools()
            bootstrap.user_command.assert_not_called()

    def test_npm_snapshot_rejects_modified_nested_package_file(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            bootstrap.prefix = Path(directory) / "dev-bootstrap"
            bootstrap.bin_dir = bootstrap.prefix / "bin"
            nested = bootstrap.prefix / "lib/node_modules/pnpm/dist/index.js"
            nested.parent.mkdir(parents=True)
            nested.write_bytes(b"original")
            baseline = bootstrap._npm_package_snapshot("pnpm")
            bootstrap.manifest = {"schema": 2, "tools": {}, "npm_packages": {"pnpm": baseline}}
            bootstrap._check_npm_prefix()
            nested.write_bytes(b"changed")
            with self.assertRaises(InstallError):
                bootstrap._check_npm_prefix()

    def test_npm_snapshot_fails_closed_on_unreadable_subtree(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
            bootstrap.prefix = Path(directory)
            bootstrap.target = TargetUser("tester", os.getuid(), os.getgid(), directory, "/bin/bash")
            package = bootstrap.prefix / "lib/node_modules/npm"
            package.mkdir(parents=True)
            def unreadable(root, followlinks=False, onerror=None):
                if onerror:
                    onerror(PermissionError("denied"))
                return iter(())
            with patch("install_dev_environment.os.walk", side_effect=unreadable), \
                 self.assertRaises(InstallError):
                bootstrap._npm_package_snapshot("npm")

    def test_preflight_rejects_untracked_npm_before_mutation(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.validate_environment = MagicMock()
        bootstrap.resolve_official_plan = MagicMock()
        bootstrap._check_node_manager_conflicts = MagicMock()
        bootstrap._check_docker_conflicts = MagicMock()
        bootstrap._check_apt_source_conflicts = MagicMock()
        bootstrap._check_npm_prefix = MagicMock(side_effect=InstallError("untracked npm"))
        bootstrap.dry_run = True
        with self.assertRaises(InstallError):
            bootstrap.preflight()

    def test_dry_run_help_discloses_unresolved_apt_candidates(self):
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            install_dev_environment.main(["--help"])
        self.assertIn("amont", output.getvalue())
        self.assertIn("APT", output.getvalue())

    def test_gpg_key_inspection_uses_approved_binary(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.SYSTEM_PATH = UbuntuBootstrap.SYSTEM_PATH
        fingerprint = "0123456789ABCDEF0123456789ABCDEF01234567"
        completed = subprocess.CompletedProcess(
            ["gpg"], 0, f"pub:::::::::\nfpr:::::::::{fingerprint}:\n".encode(), b""
        )

        with patch("install_dev_environment.shutil.which", return_value="/usr/bin/gpg"), \
                patch("install_dev_environment.subprocess.run", return_value=completed) as run:
            result = bootstrap._gpg_fingerprints(b"key")

        self.assertEqual(result, {fingerprint})
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/gpg")
        self.assertIn("--no-options", run.call_args.args[0])


class SourceTransitionTests(unittest.TestCase):
    def test_allows_only_dynamic_release_fields_to_change(self):
        nodesource_path = Path("/etc/apt/sources.list.d/dev-bootstrap-nodesource.sources")
        nodesource_old = (
            "Types: deb\nURIs: https://deb.nodesource.com/node_24.x\n"
            "Suites: nodistro\nComponents: main\nArchitectures: amd64\n"
            "Signed-By: /etc/apt/keyrings/dev-bootstrap-nodesource.gpg\n"
        )
        nodesource_new = nodesource_old.replace("node_24.x", "node_26.x")
        self.assertTrue(install_dev_environment.is_safe_managed_source_transition(
            nodesource_path, nodesource_old, nodesource_new
        ))
        self.assertFalse(install_dev_environment.is_safe_managed_source_transition(
            nodesource_path, nodesource_old + "Trusted: yes\n", nodesource_new
        ))

        docker_path = Path("/etc/apt/sources.list.d/dev-bootstrap-docker.sources")
        docker_old = (
            "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\n"
            "Suites: noble\nComponents: stable\nArchitectures: amd64\n"
            "Signed-By: /etc/apt/keyrings/dev-bootstrap-docker.asc\n"
        )
        docker_new = docker_old.replace("Suites: noble", "Suites: resolute")
        self.assertTrue(install_dev_environment.is_safe_managed_source_transition(
            docker_path, docker_old, docker_new
        ))


class NetworkValidationTests(unittest.TestCase):
    def test_streamed_asset_rejects_non_success_status(self):
        class Response(io.BytesIO):
            status = 404
            def geturl(self):
                return "https://github.com/oven-sh/bun/releases/download/bun-v1.0.0/bun.zip"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "asset"
            with patch("install_dev_environment.urllib.request.urlopen", return_value=Response(b"a")):
                with self.assertRaises(InstallError):
                    install_dev_environment.download_verified_file(
                        Response().geturl(), destination, 1, hashlib.sha256(b"a").hexdigest(),
                    )
            self.assertFalse(destination.exists())

    def test_verified_file_can_be_read_as_archive_after_streaming(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("bun-linux-x64/bun", b"executable")
        payload = buffer.getvalue()
        class Response(io.BytesIO):
            def geturl(self):
                return "https://github.com/oven-sh/bun/releases/download/bun-v1.0.0/bun.zip"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "asset"
            with patch("install_dev_environment.urllib.request.urlopen", return_value=Response(payload)):
                install_dev_environment.download_verified_file(
                    Response().geturl(), destination, len(payload), hashlib.sha256(payload).hexdigest(),
                )
            self.assertEqual(extract_archive_member(destination, "zip", "bun"), b"executable")

    def test_verified_download_streams_to_disk_and_rejects_digest_mismatch(self):
        payload = b"large archive simulated"
        response = io.BytesIO(payload)
        response.geturl = lambda: "https://github.com/oven-sh/bun/releases/download/bun-v1.0.0/bun.zip"
        response.__enter__ = lambda this: this
        response.__exit__ = lambda this, *args: None
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "asset"
            with patch("install_dev_environment.urllib.request.urlopen", return_value=response):
                with self.assertRaises(InstallError):
                    install_dev_environment.download_verified_file(response.geturl(), destination, len(payload), "0" * 64)
            self.assertFalse(destination.exists())

    def test_range_fallback_rejects_redirect_to_untrusted_host(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 206
        response.geturl.return_value = "https://example.invalid/InRelease"
        head_error = urllib.error.HTTPError(
            "https://deb.nodesource.com/InRelease", 405, "Method not allowed", Message(), None
        )
        with patch(
            "install_dev_environment.urllib.request.urlopen",
            side_effect=[head_error, response],
        ):
            with self.assertRaises(InstallError):
                install_dev_environment.probe_https_url(
                    "https://deb.nodesource.com/node_24.x/dists/nodistro/InRelease"
                )
        head_error.close()


if __name__ == "__main__":
    unittest.main()
