import io
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
from unittest.mock import MagicMock, patch

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

        output = StringIO()
        with redirect_stdout(output):
            bootstrap.print_report()

        self.assertIn("Docker Engine: 29.0.1", output.getvalue())


class NpmInstallTests(unittest.TestCase):
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

    def test_gpg_key_inspection_uses_approved_binary(self):
        bootstrap = UbuntuBootstrap.__new__(UbuntuBootstrap)
        bootstrap.SYSTEM_PATH = UbuntuBootstrap.SYSTEM_PATH
        fingerprint = "0123456789ABCDEF0123456789ABCDEF01234567"
        completed = subprocess.CompletedProcess(
            ["gpg"], 0, f"fpr:::::::::{fingerprint}:\n".encode(), b""
        )

        with patch("install_dev_environment.shutil.which", return_value="/usr/bin/gpg"), \
                patch("install_dev_environment.subprocess.run", return_value=completed) as run:
            result = bootstrap._gpg_fingerprints(b"key")

        self.assertEqual(result, {fingerprint})
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/gpg")


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


if __name__ == "__main__":
    unittest.main()
