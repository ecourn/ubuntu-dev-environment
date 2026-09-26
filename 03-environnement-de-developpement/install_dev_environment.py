#!/usr/bin/env python3
"""One-shot Ubuntu developer environment bootstrapper."""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import io
import json
import os
import platform
import pwd
import re
import shutil
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import termios
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping


class InstallError(RuntimeError):
    """An expected, actionable installer failure."""


@dataclass(frozen=True)
class TargetUser:
    username: str
    uid: int
    gid: int
    home: str
    shell: str


def _normalize_user_record(record: Any) -> TargetUser:
    if record is None:
        raise InstallError("Impossible d'identifier le compte utilisateur cible.")
    if isinstance(record, TargetUser):
        user = record
    else:
        try:
            user = TargetUser(
                str(getattr(record, "pw_name")),
                int(getattr(record, "pw_uid")),
                int(getattr(record, "pw_gid")),
                str(getattr(record, "pw_dir")),
                str(getattr(record, "pw_shell")),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise InstallError("La fiche du compte utilisateur cible est invalide.") from exc
    if user.uid <= 0 or not re.fullmatch(r"[A-Za-z0-9_.-]+", user.username):
        raise InstallError("Le compte utilisateur cible n'est pas un compte humain valide.")
    if not os.path.isabs(user.home) or user.home in ("/", "/root"):
        raise InstallError("Le répertoire personnel de l'utilisateur cible est invalide.")
    if not os.path.isabs(user.shell) or Path(user.shell).name not in {"bash", "zsh", "fish"}:
        raise InstallError("Le shell cible doit être Bash, Zsh ou Fish.")
    return user


def resolve_target_user(
    effective_uid: int,
    real_uid: int,
    environment: Mapping[str, str],
    lookup_by_name: Callable[[str], Any],
    lookup_by_uid: Callable[[int], Any],
) -> TargetUser:
    """Resolve the initiating non-root account and reject ambiguous root invocations."""
    if effective_uid == 0:
        username = environment.get("SUDO_USER", "")
        uid_text = environment.get("SUDO_UID", "")
        if not username or username == "root" or not uid_text.isdigit():
            raise InstallError(
                "Lancé en root sans identité SUDO_USER/SUDO_UID fiable; "
                "exécutez le script depuis votre compte avec sudo."
            )
        try:
            record = lookup_by_name(username)
        except (KeyError, LookupError):
            record = None
        target = _normalize_user_record(record)
        if target.uid != int(uid_text):
            raise InstallError("SUDO_USER et SUDO_UID ne désignent pas le même compte.")
        return target

    try:
        record = lookup_by_uid(real_uid)
    except (KeyError, LookupError):
        record = None
    return _normalize_user_record(record)


_SEMVER_RE = re.compile(
    r"^[vV]?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


def _parse_stable_version(value: str) -> tuple[int, int, int] | None:
    match = _SEMVER_RE.fullmatch(str(value))
    if not match or match.group(4):
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _parse_partial_version(value: str) -> tuple[tuple[int, int, int], int]:
    text = value.removeprefix("v").removeprefix("V")
    pieces = text.split(".")
    if len(pieces) > 3 or not pieces or any(part == "" for part in pieces):
        raise InstallError(f"Plage semver Node non reconnue : {value!r}.")
    numbers: list[int] = []
    wildcard = False
    for part in pieces:
        if part in ("x", "X", "*"):
            wildcard = True
            continue
        if wildcard or not re.fullmatch(r"0|[1-9]\d*", part):
            raise InstallError(f"Plage semver Node non reconnue : {value!r}.")
        numbers.append(int(part))
    precision = len(numbers)
    while len(numbers) < 3:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2]), precision


def _next_partial(parts: tuple[int, int, int], precision: int) -> tuple[int, int, int]:
    major, minor, patch = parts
    if precision <= 1:
        return major + 1, 0, 0
    if precision == 2:
        return major, minor + 1, 0
    return major, minor, patch + 1


def _satisfies_token(version: tuple[int, int, int], token: str) -> bool:
    operator = ""
    for candidate in (">=", "<=", ">", "<", "^", "~", "="):
        if token.startswith(candidate):
            operator, token = candidate, token[len(candidate):]
            break
    if token in ("*", "x", "X"):
        if operator:
            raise InstallError(f"Plage semver Node non reconnue : {operator}{token}.")
        return True

    parts, precision = _parse_partial_version(token)
    low = parts
    high = _next_partial(parts, precision) if precision < 3 else None

    if operator in ("^", "~"):
        if operator == "~":
            upper = _next_partial(parts, 1 if precision <= 1 else 2)
        elif precision <= 1 or parts[0] > 0:
            upper = (parts[0] + 1, 0, 0)
        elif parts[1] > 0:
            upper = (0, parts[1] + 1, 0)
        elif precision <= 2:
            upper = (0, 1, 0)
        else:
            upper = (0, 0, parts[2] + 1)
        return low <= version < upper

    if operator == ">=":
        return version >= low
    if operator == ">":
        if high is None:
            return version > low
        return version >= high
    if operator == "<":
        return version < low
    if operator == "<=":
        if high is None:
            return version <= low
        return version < high
    if operator == "=":
        if precision == 3:
            return version == low
        assert high is not None
        return low <= version < high
    if precision == 3:
        return version == low
    assert high is not None
    return low <= version < high


def is_version_compatible(version: str, engine_range: str) -> bool:
    """Evaluate common npm semver range syntax and reject unknown syntax safely."""
    parsed = _parse_stable_version(version)
    if parsed is None:
        raise InstallError(f"Version Node invalide ou instable : {version!r}.")
    if not isinstance(engine_range, str) or not engine_range.strip():
        raise InstallError("La contrainte engines.node est vide ou invalide.")

    matched = False
    for alternative in engine_range.split("||"):
        clause = alternative.strip()
        if not clause:
            raise InstallError(f"Plage semver Node invalide : {engine_range!r}.")
        hyphen = re.fullmatch(r"(\S+)\s+-\s+(\S+)", clause)
        if hyphen:
            lower, _ = _parse_partial_version(hyphen.group(1))
            upper, upper_precision = _parse_partial_version(hyphen.group(2))
            upper_exclusive = _next_partial(upper, upper_precision) if upper_precision < 3 else None
            if parsed >= lower and (
                parsed < upper_exclusive if upper_exclusive is not None else parsed <= upper
            ):
                matched = True
            continue

        clause = re.sub(r"([<>=~^])\s+", r"\1", clause)
        tokens = clause.split()
        if not tokens:
            raise InstallError(f"Plage semver Node invalide : {engine_range!r}.")
        # Evaluate every clause even when an earlier OR branch matched: unknown
        # syntax must never silently become a compatible release.
        results = [_satisfies_token(parsed, token) for token in tokens]
        matched |= all(results)
    return matched


def select_latest_node_lts(releases: list[dict], distribution: str) -> tuple[str, str]:
    """Select the newest stable official Node release marked LTS for this platform."""
    if distribution not in ("linux-x64", "linux-arm64"):
        raise InstallError(f"Distribution Node.js non prise en charge : {distribution}.")
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for release in releases:
        version = release.get("version", "")
        parsed = _parse_stable_version(version)
        if parsed is None or release.get("lts") in (False, None, ""):
            continue
        if distribution not in release.get("files", []):
            continue
        candidates.append((parsed, version.removeprefix("v").removeprefix("V")))
    if not candidates:
        raise InstallError(
            f"Aucune version Node.js LTS stable compatible avec {distribution} n'a été trouvée."
        )
    _, version = max(candidates)
    return version, version.split(".", 1)[0]


def select_latest_compatible_package(packument: dict, node_version: str) -> str:
    """Choose the newest stable npm release at or below the registry's latest tag."""
    versions = packument.get("versions")
    tags = packument.get("dist-tags")
    if not isinstance(versions, dict) or not isinstance(tags, dict):
        raise InstallError("Les métadonnées du registre npm ne contiennent pas versions/dist-tags.")
    latest_tag = tags.get("latest")
    latest_parsed = _parse_stable_version(latest_tag) if isinstance(latest_tag, str) else None
    if latest_parsed is None or latest_tag not in versions:
        raise InstallError("Le tag npm latest est absent ou ne désigne pas une version stable publiée.")
    assert isinstance(latest_tag, str) and latest_parsed is not None

    def compatible(metadata: Any) -> bool:
        if not isinstance(metadata, dict) or metadata.get("deprecated"):
            return False
        engines = metadata.get("engines", {})
        engine_range = engines.get("node") if isinstance(engines, dict) else None
        if engine_range is None:
            return True
        return is_version_compatible(node_version, engine_range)

    if compatible(versions[latest_tag]):
        return latest_tag

    candidates: list[tuple[tuple[int, int, int], str, dict]] = []
    for version, metadata in versions.items():
        parsed = _parse_stable_version(version)
        if parsed is None or parsed > latest_parsed or not isinstance(metadata, dict):
            continue
        candidates.append((parsed, version, metadata))
    for _, version, metadata in sorted(candidates, reverse=True):
        if version != latest_tag and compatible(metadata):
            return version
    raise InstallError(
        f"Aucune version stable compatible de {packument.get('name', 'ce paquet')} "
        f"n'est disponible pour Node.js {node_version}."
    )


def extract_archive_member(data: bytes | Path, archive_format: str, basename: str) -> bytes:
    """Read one safe regular-file member without extracting archive paths to disk."""
    if archive_format not in {"tar.gz", "zip"} or not re.fullmatch(r"[A-Za-z0-9._+-]+", basename):
        raise InstallError("Format ou nom de binaire d'archive invalide.")
    matches: list[bytes] = []

    def validate_name(name: str) -> PurePosixPath:
        if "\\" in name or "\x00" in name or any(part in ("", ".", "..") for part in name.rstrip("/").split("/")):
            raise InstallError("Chemin d'archive non sûr.")
        path = PurePosixPath(name)
        if path.is_absolute() or not path.parts or any(part in {"..", ""} for part in path.parts):
            raise InstallError("Chemin d'archive non sûr.")
        return path

    try:
        if archive_format == "zip":
            with zipfile.ZipFile(data if isinstance(data, Path) else io.BytesIO(data)) as zip_archive:
                for info in zip_archive.infolist():
                    path = validate_name(info.filename)
                    if info.is_dir():
                        continue
                    mode = info.external_attr >> 16
                    if stat.S_IFMT(mode) and not stat.S_ISREG(mode):
                        raise InstallError("L'archive contient un membre non régulier refusé.")
                    if path.name == basename:
                        if info.file_size <= 0 or info.file_size > 250_000_000:
                            raise InstallError(f"Taille invalide pour le binaire {basename}.")
                        with zip_archive.open(info, "r") as stream:
                            matches.append(stream.read(250_000_001))
        else:
            with tarfile.open(name=str(data) if isinstance(data, Path) else None,
                              fileobj=None if isinstance(data, Path) else io.BytesIO(data), mode="r:gz") as tar_archive:
                for member in tar_archive.getmembers():
                    path = validate_name(member.name)
                    if member.isdir():
                        continue
                    if member.issym() or member.islnk() or not member.isfile():
                        raise InstallError("L'archive contient un type de fichier non admis.")
                    if path.name == basename:
                        if member.size <= 0 or member.size > 250_000_000:
                            raise InstallError(f"Taille invalide pour le binaire {basename}.")
                        member_stream = tar_archive.extractfile(member)
                        if member_stream is None:
                            raise InstallError(f"Binaire {basename} illisible dans l'archive.")
                        with member_stream:
                            matches.append(member_stream.read(250_000_001))
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise InstallError(f"Archive {archive_format} invalide ou tronquée.") from exc

    if len(matches) != 1 or len(matches[0]) > 250_000_000:
        raise InstallError(f"Binaire {basename} absent ou ambigu dans l'archive.")
    return matches[0]


def select_release_asset(release: dict, repository: str, asset_name: str) -> dict[str, str | int]:
    """Select a verified asset from a latest, stable GitHub release."""
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        raise InstallError(f"La publication GitHub de {repository} n'est pas une version stable.")
    tag = release.get("tag_name", "")
    if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", tag):
        raise InstallError(f"Tag de publication GitHub invalide pour {repository}.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise InstallError("Identifiant de dépôt GitHub invalide dans le script.")

    assets = release.get("assets")
    if not isinstance(assets, list):
        raise InstallError(f"La publication GitHub de {repository} ne contient pas d'assets.")
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == asset_name]
    if len(matches) != 1:
        raise InstallError(
            f"Asset {asset_name!r} absent ou ambigu dans la publication de {repository}."
        )
    asset = matches[0]
    digest = asset.get("digest")
    digest_match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", digest or "")
    if not digest_match:
        raise InstallError(
            f"Aucun checksum SHA-256 officiel vérifiable pour {repository}/{asset_name}."
        )
    url = asset.get("browser_download_url", "")
    expected_prefix = f"https://github.com/{repository}/releases/download/{tag}/"
    if not isinstance(url, str) or not url.startswith(expected_prefix) or url[len(expected_prefix):] != asset_name:
        raise InstallError(f"URL d'asset inattendue pour {repository}/{asset_name}.")
    size = asset.get("size")
    if not isinstance(size, int) or size <= 0 or size > 250_000_000:
        raise InstallError(f"Taille d'asset invalide pour {repository}/{asset_name}.")
    return {
        "name": asset_name,
        "url": url,
        "sha256": digest_match.group(1).lower(),
        "size": size,
        "tag": tag,
    }


def update_managed_block(existing: str, start: str, end: str, body: str) -> str:
    """Append or replace exactly one managed configuration block without losing user text."""
    if not start or not end or start == end or "\n" in start or "\n" in end:
        raise InstallError("Marqueurs de configuration invalides.")
    if start in body or end in body:
        raise InstallError("Le contenu de configuration contient ses propres marqueurs.")

    lines = existing.splitlines(keepends=True)
    plain_lines = [line.rstrip("\r\n") for line in lines]
    start_indices = [index for index, line in enumerate(plain_lines) if line == start]
    end_indices = [index for index, line in enumerate(plain_lines) if line == end]
    newline = "\r\n" if "\r\n" in existing else "\n"
    block = newline.join([start, *body.splitlines(), end]) + newline

    if not start_indices and not end_indices:
        prefix = existing
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += newline
        elif prefix and not prefix.endswith(newline):
            prefix += newline
        return prefix + block
    if len(start_indices) != 1 or len(end_indices) != 1 or start_indices[0] >= end_indices[0]:
        raise InstallError("Bloc géré absent, incomplet ou dupliqué; configuration non modifiée.")
    return "".join(lines[:start_indices[0]]) + block + "".join(lines[end_indices[0] + 1:])


def parse_supported_ubuntu(metadata: str, codename: str, version_id: str) -> bool:
    """Return whether official Ubuntu release metadata marks this release supported."""
    if not re.fullmatch(r"[a-z][a-z0-9-]*", codename):
        raise InstallError("Le nom de code Ubuntu est invalide.")
    if not re.fullmatch(r"\d{2}\.\d{2}", version_id):
        raise InstallError("L'identifiant de version Ubuntu est invalide.")

    codename_entries: list[dict[str, str]] = []
    for stanza in re.split(r"\n\s*\n", metadata.strip()):
        fields: dict[str, str] = {}
        for line in stanza.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
        if fields.get("Dist") == codename:
            codename_entries.append(fields)

    if not codename_entries:
        raise InstallError(
            f"Le nom de code Ubuntu {codename} est absent des métadonnées officielles."
        )

    matches: list[dict[str, str]] = []
    for fields in codename_entries:
        release_version = fields.get("Version", "")
        match = re.match(r"^(\d{2}\.\d{2})(?:\.\d+)?(?:\s|$)", release_version)
        if not match or "Supported" not in fields:
            raise InstallError(
                f"Les métadonnées Ubuntu officielles pour {codename} sont incomplètes."
            )
        if match.group(1) == version_id:
            matches.append(fields)

    if len(matches) > 1:
        raise InstallError(
            f"Les métadonnées Ubuntu contiennent plusieurs entrées pour {version_id} ({codename})."
        )
    if not matches:
        return False
    return matches[0]["Supported"] == "1"


def map_deb_architecture(machine: str) -> str:
    """Map uname/dpkg architecture names to the only supported Ubuntu architectures."""
    aliases = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    try:
        return aliases[machine]
    except KeyError as exc:
        raise InstallError(
            f"Architecture non prise en charge : {machine}. Seules amd64 et arm64 sont acceptées."
        ) from exc


APP_NAME = "dev-bootstrap"
UBUNTU_META_RELEASE = "https://changelogs.ubuntu.com/meta-release"
NODE_RELEASE_INDEX = "https://nodejs.org/dist/index.json"
NPM_REGISTRY = "https://registry.npmjs.org"
GITHUB_API = "https://api.github.com/repos"
NODESOURCE_BASE = "https://deb.nodesource.com"
DOCKER_BASE = "https://download.docker.com/linux/ubuntu"
GH_CLI_BASE = "https://cli.github.com/packages"
APT_KEY_TRUST_V1 = {
    # Primary fingerprints verified from the official key endpoints (not subkeys).
    "github-cli": frozenset({
        "2C6106201985B60E6C7AC87323F3D4EA75716059",
        "7F38BBB59D064DBCB3D84D725612B36462313325",
    }),
    "nodesource": frozenset({"6F71F525282841EEDAF851B42F59B5F99B1BE0B4"}),
    "docker": frozenset({"9DC858229FC7DD38854AE2D88D81803C0EBFCD88"}),
}
TRUSTED_HOSTS = {
    "changelogs.ubuntu.com", "nodejs.org", "registry.npmjs.org", "api.github.com",
    "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
    "deb.nodesource.com", "download.docker.com", "cli.github.com",
}


def fetch_https_bytes(url: str, *, accept: str = "*/*", max_bytes: int = 20_000_000) -> bytes:
    """Fetch a bounded HTTPS response from a hardcoded trusted project host."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in TRUSTED_HOSTS or parsed.username or parsed.password:
        raise InstallError("URL HTTPS absente de la liste des sources approuvées.")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ubuntu-dev-bootstrap/1.0", "Accept": accept},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                final = urllib.parse.urlsplit(response.geturl())
                if final.scheme != "https" or final.hostname not in TRUSTED_HOSTS:
                    raise InstallError("La redirection HTTPS mène vers une source non approuvée.")
                data = response.read(max_bytes + 1)
                if not data:
                    raise InstallError(f"Réponse HTTPS vide depuis {parsed.hostname}.")
                if len(data) > max_bytes:
                    raise InstallError(f"Réponse trop volumineuse depuis {parsed.hostname}.")
                return data
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(1 << attempt)
                continue
            raise InstallError(f"Échec HTTP {exc.code} depuis {parsed.hostname}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < 2:
                time.sleep(1 << attempt)
                continue
            raise InstallError(f"Échec réseau/TLS depuis {parsed.hostname}: {exc}.") from exc
    raise InstallError(f"Échec réseau répété depuis {parsed.hostname}.")


def download_verified_file(url: str, destination: Path, size: int, sha256: str) -> None:
    """Stream an official asset to a private file; publish only after verification."""
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in TRUSTED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise InstallError("URL d'asset non approuvée.")
    if not 0 < size <= 250_000_000 or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise InstallError("Taille ou SHA-256 d'asset invalide.")
    request = urllib.request.Request(url, headers={
        "User-Agent": "ubuntu-dev-bootstrap/1.0", "Accept": "application/octet-stream",
    })
    fd, temporary_name = tempfile.mkstemp(dir=destination.parent, prefix=".verified-asset-")
    temporary = Path(temporary_name)
    try:
        digest = hashlib.sha256()
        count = 0
        with os.fdopen(fd, "wb") as stream:
            with urllib.request.urlopen(request, timeout=60) as response:
                final = urllib.parse.urlsplit(response.geturl())
                if final.scheme != "https" or final.hostname not in TRUSTED_HOSTS or getattr(response, "status", 200) != 200:
                    raise InstallError("Redirection ou statut d'asset non approuvé.")
                while True:
                    chunk = response.read(min(1_048_576, size - count + 1))
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > size:
                        raise InstallError("Asset plus grand que la taille officielle.")
                    digest.update(chunk)
                    stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if count != size or digest.hexdigest() != sha256.lower():
            raise InstallError("Taille ou SHA-256 officiel de l'asset incorrect.")
        os.replace(temporary, destination)
        fsync_directory(destination.parent)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise InstallError("Téléchargement de l'asset impossible.") from exc
    finally:
        temporary.unlink(missing_ok=True)


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fetch_https_json(url: str, *, accept: str = "application/json", max_bytes: int = 20_000_000) -> Any:
    try:
        return json.loads(fetch_https_bytes(url, accept=accept, max_bytes=max_bytes))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InstallError("Réponse JSON invalide reçue d'une source officielle.") from exc


def probe_https_url(url: str) -> None:
    """Confirm a remote repository index exists without trusting it as an APT key."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in TRUSTED_HOSTS:
        raise InstallError("URL de dépôt non approuvée.")
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "ubuntu-dev-bootstrap/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if response.status != 200 or final.scheme != "https" or final.hostname not in TRUSTED_HOSTS:
                raise InstallError(f"Dépôt officiel indisponible: {parsed.hostname}.")
    except urllib.error.HTTPError as exc:
        if exc.code not in (405, 501):
            raise InstallError(f"Dépôt officiel indisponible (HTTP {exc.code}): {parsed.hostname}.") from exc
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=0-0"}), timeout=20) as response:
                final = urllib.parse.urlsplit(response.geturl())
                if (
                    response.status not in (200, 206)
                    or final.scheme != "https"
                    or final.hostname not in TRUSTED_HOSTS
                ):
                    raise InstallError(f"Dépôt officiel indisponible: {parsed.hostname}.")
        except (urllib.error.URLError, TimeoutError, OSError) as fallback_exc:
            raise InstallError(f"Dépôt officiel inaccessible: {parsed.hostname}.") from fallback_exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InstallError(f"Dépôt officiel inaccessible: {parsed.hostname}.") from exc


def extract_display_version(output: str) -> str:
    match = re.search(r"(?<![\w.])v?(\d+\.\d+\.\d+)(?![\w.+~-])", output)
    if not match:
        raise InstallError("Impossible de lire la version affichée par l'exécutable.")
    return match.group(1)


def parse_ubuntu_csv_support(csv_text: str, codename: str, version_id: str, today: dt.date) -> bool:
    """Fallback to the official Ubuntu distro-info-data installed with the OS."""
    try:
        rows = list(csv.DictReader(io.StringIO(csv_text)))
    except (csv.Error, UnicodeDecodeError) as exc:
        raise InstallError("Métadonnées locales distro-info Ubuntu invalides.") from exc
    for row in rows:
        if row.get("series") != codename:
            continue
        version_match = re.match(r"^(\d{2}\.\d{2})", row.get("version", ""))
        if not version_match or version_match.group(1) != version_id:
            raise InstallError("La version Ubuntu locale ne correspond pas au nom de code détecté.")
        eol = row.get("eol", "")
        try:
            return dt.date.fromisoformat(eol) >= today
        except ValueError as exc:
            raise InstallError("La date de fin de support Ubuntu est absente ou invalide.") from exc
    raise InstallError("La version Ubuntu est absente de distro-info-data.")


def release_version_from_tag(tag: str, prefix: str = "") -> str:
    """Validate and normalize a stable semantic version embedded in a release tag."""
    if prefix and not tag.startswith(prefix):
        raise InstallError(f"Tag de publication inattendu: {tag!r}.")
    version = tag[len(prefix):] if prefix else tag
    version = version.removeprefix("v").removeprefix("V")
    if _parse_stable_version(version) is None:
        raise InstallError(f"La publication {tag!r} ne correspond pas à une version stable.")
    return version


def config_file_for_shell(home: Path, shell: str) -> Path:
    name = Path(shell).name
    if name == "bash":
        return home / ".bashrc"
    if name == "zsh":
        return home / ".zshrc"
    if name == "fish":
        return home / ".config" / "fish" / "config.fish"
    raise InstallError("Seuls Bash, Zsh et Fish sont pris en charge.")


def is_safe_managed_source_transition(path: Path, existing: str, expected: str) -> bool:
    """Allow only an exact generated source file with a changed dynamic release field."""
    if path.name == "dev-bootstrap-nodesource.sources":
        variable_index = 1
        value_pattern = re.compile(r"^URIs: https://deb\.nodesource\.com/node_\d+\.x$")
    elif path.name == "dev-bootstrap-docker.sources":
        variable_index = 2
        value_pattern = re.compile(r"^Suites: [a-z][a-z0-9-]*$")
    else:
        return False

    old_lines = existing.splitlines()
    new_lines = expected.splitlines()
    if len(old_lines) != len(new_lines) or not old_lines:
        return False
    for index, (old_line, new_line) in enumerate(zip(old_lines, new_lines)):
        if index == variable_index:
            if not value_pattern.fullmatch(old_line) or not value_pattern.fullmatch(new_line):
                return False
        elif old_line != new_line:
            return False
    return True


class UbuntuBootstrap:
    APT_PACKAGES = (
        "nodejs", "gh", "docker-ce", "docker-ce-cli", "containerd.io",
        "docker-buildx-plugin", "docker-compose-plugin",
    )
    REPO_DOMAINS = {
        "nodesource": "deb.nodesource.com",
        "github-cli": "cli.github.com/packages",
        "docker": "download.docker.com",
    }
    MANAGED_START = "# >>> dev-bootstrap managed >>>"
    MANAGED_END = "# <<< dev-bootstrap managed <<<"
    SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.target = resolve_target_user(
            os.geteuid(), os.getuid(), os.environ, pwd.getpwnam, pwd.getpwuid
        )
        self.home = Path(self.target.home)
        self.prefix = self.home / ".local" / "share" / APP_NAME
        self.bin_dir = self.prefix / "bin"
        self.state_path = self.home / ".local" / "state" / APP_NAME / "manifest.json"
        inherited_path = os.environ.get("PATH", self.SYSTEM_PATH)
        if os.geteuid() == 0:
            user_paths = [str(self.home / ".local" / "bin"), str(self.home / ".bun" / "bin")]
            inherited_path = inherited_path + ":" + ":".join(user_paths)
        self.target_path = ":".join([str(self.bin_dir), inherited_path])
        self.target_env = {
            "HOME": str(self.home),
            "USER": self.target.username,
            "LOGNAME": self.target.username,
            "PATH": self.target_path,
        }
        self.step = "vérification des prérequis"
        self.os_release: dict[str, str] = {}
        self.arch = ""
        self.codename = ""
        self.version_id = ""
        self.plan: dict[str, Any] = {}
        self.status: dict[str, str] = {}
        self.apt_candidates: dict[str, str] = {}
        self.apt_before: dict[str, str | None] = {}
        self.manifest: dict[str, Any] = {"schema": 2, "tools": {}}
        self.gh_authenticated = False
        self.auth_tty_available = False
        self.verified_versions: dict[str, str] = {}
        self.warnings: list[str] = []

    def say(self, message: str) -> None:
        print(f"==> {message}", flush=True)

    def set_step(self, message: str) -> None:
        self.step = message
        self.say(message)

    def _system_executable(self, name: str) -> str:
        executable = shutil.which(name, path=self.SYSTEM_PATH)
        if not executable:
            raise InstallError(f"Commande système absente du PATH approuvé : {name}.")
        return executable

    def _privileged_env(self) -> dict[str, str]:
        """Never inherit APT/GPG, loader, proxy, or shell settings across privilege boundaries."""
        return {"PATH": self.SYSTEM_PATH, "HOME": "/root", "LANG": "C.UTF-8",
                "DEBIAN_FRONTEND": "noninteractive"}

    def _run(
        self,
        command: list[str],
        *,
        as_user: bool = False,
        capture: bool = False,
        input_data: str | bytes | None = None,
        check: bool = True,
        timeout: int | None = None,
        label: str | None = None,
    ) -> subprocess.CompletedProcess:
        env = {**os.environ, **self.target_env} if as_user else self._privileged_env()
        actual = list(command)
        if not as_user:
            actual = [self._system_executable(command[0]), *command[1:]]
        if as_user and os.geteuid() == 0:
            runuser = shutil.which("runuser", path=self.SYSTEM_PATH)
            if runuser:
                actual = [runuser, "-u", self.target.username, "--", "env"]
            else:
                sudo = shutil.which("sudo", path=self.SYSTEM_PATH)
                if not sudo:
                    raise InstallError("runuser et sudo sont tous deux absents.")
                actual = [sudo, "-H", "-u", self.target.username, "--", "env"]
            actual += [f"{key}={value}" for key, value in self.target_env.items()]
            actual += command
        elif not as_user and os.geteuid() != 0:
            sudo = shutil.which("sudo", path=self.SYSTEM_PATH)
            if not sudo:
                raise InstallError("sudo est nécessaire pour les opérations système.")
            actual = [sudo, "--", *actual]
        try:
            result = subprocess.run(
                actual,
                input=input_data,
                text=isinstance(input_data, str) or input_data is None,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.PIPE if capture else None,
                env=env,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise InstallError(f"{label or self.step}: délai dépassé.") from exc
        except OSError as exc:
            detail = exc.strerror or str(exc)
            raise InstallError(
                f"{label or self.step}: commande impossible à lancer "
                f"({exc.__class__.__name__}, errno={exc.errno}: {detail})."
            ) from exc
        if check and result.returncode != 0:
            detail = ""
            if capture:
                raw_detail = result.stderr or result.stdout or ""
                text = raw_detail.decode("utf-8", errors="replace").strip() if isinstance(raw_detail, bytes) else raw_detail.strip()
                if text:
                    detail = ": " + " | ".join(text.splitlines()[-3:])[:800]
            raise InstallError(
                f"{label or self.step} a échoué (code {result.returncode}){detail}."
            )
        return result

    def user_command(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return self._run(command, as_user=True, **kwargs)

    def system_command(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return self._run(command, as_user=False, **kwargs)

    @contextlib.contextmanager
    def target_file_privileges(self) -> Iterator[None]:
        """Temporarily drop effective privileges while modifying user-owned files."""
        if os.geteuid() != 0:
            yield
            return
        old_euid, old_egid, old_groups = os.geteuid(), os.getegid(), os.getgroups()
        try:
            os.setgroups([])
            os.setegid(self.target.gid)
            os.seteuid(self.target.uid)
            yield
        finally:
            os.seteuid(old_euid)
            os.setegid(old_egid)
            os.setgroups(old_groups)

    def _read_os_release(self) -> dict[str, str]:
        values: dict[str, str] = {}
        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                parsed = shlex.split(value, posix=True)
                values[key] = parsed[0] if parsed else ""
        except (OSError, ValueError) as exc:
            raise InstallError("Impossible de lire /etc/os-release.") from exc
        return values

    def _check_ubuntu_support(self) -> None:
        try:
            metadata = fetch_https_bytes(UBUNTU_META_RELEASE, accept="text/plain", max_bytes=5_000_000).decode("utf-8")
            supported = parse_supported_ubuntu(metadata, self.codename, self.version_id)
        except (InstallError, UnicodeDecodeError) as remote_error:
            local_csv = Path("/usr/share/distro-info/ubuntu.csv")
            if not local_csv.is_file():
                raise InstallError(
                    f"Le statut de support Ubuntu n'a pas pu être vérifié: {remote_error}"
                ) from remote_error
            try:
                csv_text = local_csv.read_text(encoding="utf-8")
            except OSError as exc:
                raise InstallError("Impossible de lire les métadonnées locales Ubuntu.") from exc
            supported = parse_ubuntu_csv_support(
                csv_text, self.codename, self.version_id, dt.datetime.now(dt.timezone.utc).date()
            )
        if not supported:
            raise InstallError(
                f"Ubuntu {self.version_id} ({self.codename}) n'est pas marqué comme pris en charge."
            )

    def validate_environment(self) -> None:
        self.set_step("vérification d'Ubuntu, du compte cible et des prérequis")
        self.os_release = self._read_os_release()
        if self.os_release.get("ID") != "ubuntu":
            raise InstallError("Ce script ne prend en charge que la distribution Ubuntu.")
        self.version_id = self.os_release.get("VERSION_ID", "")
        self.codename = self.os_release.get("UBUNTU_CODENAME") or self.os_release.get("VERSION_CODENAME", "")
        if not re.fullmatch(r"\d{2}\.\d{2}", self.version_id) or not re.fullmatch(r"[a-z][a-z0-9-]*", self.codename):
            raise InstallError("VERSION_ID ou nom de code Ubuntu absent/invalide dans /etc/os-release.")
        if not self.home.is_dir():
            raise InstallError("Le répertoire personnel du compte cible n'existe pas.")
        try:
            home_stat = self.home.stat()
        except OSError as exc:
            raise InstallError("Impossible de lire le répertoire personnel cible.") from exc
        if home_stat.st_uid != self.target.uid:
            raise InstallError("Le répertoire personnel cible n'appartient pas à l'utilisateur identifié.")
        if not os.access(self.home, os.W_OK | os.X_OK):
            raise InstallError("Le répertoire personnel cible n'est pas accessible en écriture.")
        if not Path(self.target.shell).is_file() or not os.access(self.target.shell, os.X_OK):
            raise InstallError("Le shell de connexion du compte cible est absent ou non exécutable.")

        for command in ("apt-get", "apt-cache", "dpkg", "dpkg-query", "systemctl", "sudo"):
            if shutil.which(command, path=self.SYSTEM_PATH) is None:
                raise InstallError(f"Prérequis système absent : {command}.")
        if os.geteuid() == 0 and shutil.which("runuser", path=self.SYSTEM_PATH) is None:
            raise InstallError("Prérequis absent pour cibler le compte utilisateur : runuser.")
        if os.geteuid() != 0 and shutil.which("sudo", path=self.SYSTEM_PATH) is None:
            raise InstallError("sudo est absent.")

        uname_arch = map_deb_architecture(platform.machine())
        result = self.system_command(["dpkg", "--print-architecture"], capture=True)
        dpkg_arch = map_deb_architecture(result.stdout.strip())
        if uname_arch != dpkg_arch:
            raise InstallError("L'architecture noyau ne correspond pas à l'architecture de dpkg.")
        self.arch = dpkg_arch
        self._check_ubuntu_support()

        if not self.dry_run and not Path("/run/systemd/system").is_dir():
            raise InstallError("systemd n'est pas actif; le service Docker ne peut pas être garanti.")
        self._load_manifest()

    def _target_shell_rcs(self) -> list[Path]:
        return [
            self.home / ".bashrc", self.home / ".zshrc",
            self.home / ".profile", self.home / ".config" / "fish" / "config.fish",
        ]

    def _check_node_manager_conflicts(self) -> None:
        manager = re.compile(r"\b(nvm|fnm|volta|asdf|mise)\b|NVM_DIR|\.nvm|\.volta|\.asdf|mise activate|fnm env")
        current = shutil.which("node", path=self.target_path)
        if current and not current.startswith(("/usr/bin/", "/bin/")):
            raise InstallError(
                f"Node.js actif depuis {current}; installation NodeSource interrompue pour éviter un masquage."
            )
        # sudo may replace PATH with secure_path; inspect common manager shims directly.
        for relative in (".volta/bin/node", ".asdf/shims/node", ".local/share/mise/shims/node",
                         ".local/share/fnm/aliases/default/bin/node", ".nvm/current/bin/node"):
            manager_node = self.home / relative
            if manager_node.exists() or manager_node.is_symlink():
                raise InstallError(f"Gestionnaire Node.js détecté hors PATH sudo: {manager_node}.")
        for path in self._target_shell_rcs():
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise InstallError(f"Impossible d'inspecter {path} sans modifier la configuration.") from exc
            if any(manager.search(line) for line in lines if not line.lstrip().startswith("#")):
                raise InstallError(
                    f"Un gestionnaire de versions Node.js semble configuré dans {path}; "
                    "retirez-le ou migrez-le manuellement avant d'utiliser NodeSource."
                )

    def _check_tty(self) -> None:
        try:
            fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
            try:
                if not os.isatty(fd):
                    raise OSError("/dev/tty n'est pas un terminal")
                termios.tcgetattr(fd)
            finally:
                os.close(fd)
            self.auth_tty_available = True
        except OSError:
            self.auth_tty_available = False

    def _gh_is_authenticated(self) -> bool:
        executable = shutil.which("gh", path=self.target_path)
        if not executable:
            return False
        result = self.user_command(
            [executable, "auth", "status", "--hostname", "github.com"],
            capture=True, check=False, timeout=20, label="vérification de l'authentification GitHub",
        )
        return result.returncode == 0

    def _expected_sources(self) -> dict[Path, str]:
        key_dir = Path("/etc/apt/keyrings")
        source_dir = Path("/etc/apt/sources.list.d")
        values = {
            source_dir / "dev-bootstrap-nodesource.sources": (
                "Types: deb\n"
                f"URIs: {NODESOURCE_BASE}/node_{self.plan['node_major']}.x\n"
                "Suites: nodistro\nComponents: main\n"
                f"Architectures: {self.arch}\n"
                f"Signed-By: {key_dir / 'dev-bootstrap-nodesource.gpg'}\n"
            ),
            source_dir / "dev-bootstrap-github-cli.sources": (
                "Types: deb\n"
                f"URIs: {GH_CLI_BASE}\n"
                "Suites: stable\nComponents: main\n"
                f"Architectures: {self.arch}\n"
                f"Signed-By: {key_dir / 'dev-bootstrap-github-cli.gpg'}\n"
            ),
            source_dir / "dev-bootstrap-docker.sources": (
                "Types: deb\n"
                f"URIs: {DOCKER_BASE}\n"
                f"Suites: {self.codename}\nComponents: stable\n"
                f"Architectures: {self.arch}\n"
                f"Signed-By: {key_dir / 'dev-bootstrap-docker.asc'}\n"
            ),
        }
        return values

    def _load_manifest(self) -> None:
        with self.target_file_privileges():
            if self.prefix.exists() and (self.prefix.is_symlink() or not self.prefix.is_dir()):
                raise InstallError("Le répertoire dédié dev-bootstrap existe mais n'est pas un dossier sûr.")
            if self.prefix.exists() and self.prefix.stat().st_uid != self.target.uid:
                raise InstallError("Le répertoire dev-bootstrap n'appartient pas au compte cible.")
            if self.state_path.is_symlink():
                raise InstallError("Le manifeste dev-bootstrap est un lien symbolique; refus de le suivre.")
            if self.state_path.exists():
                if not self.state_path.is_file() or self.state_path.stat().st_uid != self.target.uid:
                    raise InstallError("Le manifeste dev-bootstrap n'est pas un fichier utilisateur sûr.")
                try:
                    manifest = json.loads(self.state_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise InstallError("Le manifeste dev-bootstrap est illisible ou invalide.") from exc
                if not isinstance(manifest, dict) or manifest.get("schema") not in (1, 2) or not isinstance(manifest.get("tools"), dict):
                    raise InstallError("Schéma du manifeste dev-bootstrap inconnu; aucune mise à jour effectuée.")
                manifest["schema"] = 2  # Legacy v1 is migrated only when a write is needed.
                self.manifest = manifest
            else:
                if self.prefix.exists() and any(self.prefix.iterdir()):
                    raise InstallError(
                        "Le répertoire dev-bootstrap contient des fichiers sans manifeste; "
                        "ils ne seront pas écrasés."
                    )
                self.manifest = {"schema": 2, "tools": {}}

    def uninstall(self) -> None:
        """Remove verified user binaries only; leave shared APT and npm packages alone."""
        self._load_manifest()
        if "pending_binary" in self.manifest or "pending_npm" in self.manifest:
            raise InstallError("Transaction utilisateur inachevée; reprenez l'installation avant désinstallation.")
        tools = self.manifest["tools"]
        with self.target_file_privileges():
            for name, record in tools.items():
                if (not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name)
                        or not isinstance(record, dict)
                        or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", "")))):
                    raise InstallError("Entrée de manifeste invalide; désinstallation interrompue.")
                path = self.bin_dir / name
                if path.is_symlink():
                    if os.readlink(path) != record.get("link"):
                        raise InstallError(f"Lien géré modifié: {path}.")
                    resolved = path.resolve(strict=True)
                    if not resolved.is_relative_to(self.prefix.resolve()) or self._file_sha256(resolved) != record["sha256"]:
                        raise InstallError(f"Binaire géré modifié: {path}.")
                elif (not path.is_file() or path.stat().st_uid != self.target.uid
                      or self._file_sha256(path) != record["sha256"]):
                    raise InstallError(f"Binaire géré modifié ou absent: {path}.")
            if self.dry_run:
                print("Désinstallation à blanc: binaires utilisateur vérifiés; aucun paquet APT ou npm supprimé.")
                return
            for name in tools:
                (self.bin_dir / name).unlink()
            if tools:
                self.manifest["tools"] = {}
                self._save_manifest()
            print("Binaires gérés retirés. Paquets APT, npm globaux et configuration shell conservés.")

    def _check_apt_source_conflicts(self) -> None:
        for directory in (Path("/etc/apt/keyrings"), Path("/etc/apt/sources.list.d")):
            if directory.exists() or directory.is_symlink():
                self._check_root_directory(directory)
        expected = self._expected_sources()
        for name in ("dev-bootstrap-nodesource.gpg", "dev-bootstrap-github-cli.gpg",
                     "dev-bootstrap-docker.asc"):
            key_path = Path("/etc/apt/keyrings") / name
            if key_path.exists() or key_path.is_symlink():
                self._check_root_file(key_path)
        for managed_path, expected_content in expected.items():
            if managed_path.exists() or managed_path.is_symlink():
                self._check_root_file(managed_path)
                try:
                    current_content = managed_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    raise InstallError(f"Impossible de lire la configuration APT gérée {managed_path}.") from exc
                if current_content != expected_content and not is_safe_managed_source_transition(
                    managed_path, current_content, expected_content
                ):
                    raise InstallError(f"Configuration APT gérée modifiée ou inattendue: {managed_path}.")
        source_paths = [Path("/etc/apt/sources.list"), *Path("/etc/apt/sources.list.d").glob("*")]
        for path in source_paths:
            if not path.is_file() or path in expected:
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                raise InstallError(f"Impossible d'inspecter les dépôts APT dans {path}.") from exc
            for line in lines:
                active = line.strip()
                if not active or active.startswith("#"):
                    continue
                if any(domain in active for domain in self.REPO_DOMAINS.values()):
                    raise InstallError(
                        f"Un dépôt officiel concerné existe déjà hors du fichier géré ({path}); "
                        "vérifiez-le ou retirez-le manuellement pour éviter un doublon."
                    )

    def _package_installed_version(self, package: str) -> str | None:
        result = self.system_command(
            ["dpkg-query", "-W", "-f=${db:Status-Status} ${Version}", package],
            capture=True, check=False, label=f"lecture de l'état du paquet {package}",
        )
        if result.returncode != 0 or not result.stdout.startswith("installed "):
            return None
        return result.stdout.split(" ", 1)[1].strip()

    def _check_docker_conflicts(self) -> None:
        conflicts = (
            "docker.io", "docker-compose", "docker-compose-v2", "docker-doc",
            "podman-docker", "containerd", "runc",
        )
        present = [name for name in conflicts if self._package_installed_version(name)]
        if present:
            raise InstallError(
                "Paquets incompatibles déjà installés: " + ", ".join(present) + ". "
                "Aucun paquet ne sera supprimé; résolvez le conflit puis relancez."
            )

    def _resolve_release(self, repository: str, asset_name: str, prefix: str = "") -> dict[str, Any]:
        release = fetch_https_json(
            f"{GITHUB_API}/{repository}/releases/latest",
            accept="application/vnd.github+json",
            max_bytes=5_000_000,
        )
        version = release_version_from_tag(release.get("tag_name", ""), prefix)
        asset = select_release_asset(release, repository, asset_name)
        return {"version": version, "repository": repository, "asset": asset}

    def resolve_official_plan(self) -> None:
        self.set_step("résolution des dernières versions officielles compatibles")
        node_data = fetch_https_json(NODE_RELEASE_INDEX, max_bytes=8_000_000)
        if not isinstance(node_data, list):
            raise InstallError("L'index officiel des versions Node.js a un format inattendu.")
        node_dist = "linux-x64" if self.arch == "amd64" else "linux-arm64"
        node_version, node_major = select_latest_node_lts(node_data, node_dist)
        self.plan["node"] = node_version
        self.plan["node_major"] = node_major
        self.plan["npm"] = select_latest_compatible_package(
            fetch_https_json(
                f"{NPM_REGISTRY}/npm",
                accept="application/vnd.npm.install-v1+json",
                max_bytes=20_000_000,
            ),
            node_version,
        )
        self.plan["pnpm"] = select_latest_compatible_package(
            fetch_https_json(
                f"{NPM_REGISTRY}/pnpm",
                accept="application/vnd.npm.install-v1+json",
                max_bytes=20_000_000,
            ),
            node_version,
        )

        if self.arch == "amd64":
            bun_asset = "bun-linux-x64-baseline.zip"
            uv_asset = "uv-x86_64-unknown-linux-gnu.tar.gz"
            zoxide_arch = "x86_64"
            fzf_arch = "amd64"
        else:
            bun_asset = "bun-linux-aarch64.zip"
            uv_asset = "uv-aarch64-unknown-linux-gnu.tar.gz"
            zoxide_arch = "aarch64"
            fzf_arch = "arm64"
        self.plan["bun"] = self._resolve_release("oven-sh/bun", bun_asset, "bun-v")
        self.plan["uv"] = self._resolve_release("astral-sh/uv", uv_asset)
        zoxide_release = fetch_https_json(
            f"{GITHUB_API}/ajeetdsouza/zoxide/releases/latest",
            accept="application/vnd.github+json", max_bytes=5_000_000,
        )
        zoxide_version = release_version_from_tag(zoxide_release.get("tag_name", ""))
        zoxide_asset_name = f"zoxide-{zoxide_version}-{zoxide_arch}-unknown-linux-musl.tar.gz"
        self.plan["zoxide"] = {
            "version": zoxide_version,
            "repository": "ajeetdsouza/zoxide",
            "asset": select_release_asset(zoxide_release, "ajeetdsouza/zoxide", zoxide_asset_name),
        }
        fzf_release = fetch_https_json(
            f"{GITHUB_API}/junegunn/fzf/releases/latest",
            accept="application/vnd.github+json", max_bytes=5_000_000,
        )
        fzf_version = release_version_from_tag(fzf_release.get("tag_name", ""))
        fzf_asset_name = f"fzf-{fzf_version}-linux_{fzf_arch}.tar.gz"
        self.plan["fzf"] = {
            "version": fzf_version,
            "repository": "junegunn/fzf",
            "asset": select_release_asset(fzf_release, "junegunn/fzf", fzf_asset_name),
        }
        self.plan["gh"] = release_version_from_tag(
            fetch_https_json(
                f"{GITHUB_API}/cli/cli/releases/latest",
                accept="application/vnd.github+json", max_bytes=5_000_000,
            ).get("tag_name", "")
        )

        node_repo = f"{NODESOURCE_BASE}/node_{node_major}.x/dists/nodistro"
        probe_https_url(f"{node_repo}/InRelease")
        probe_https_url(f"{node_repo}/main/binary-{self.arch}/Packages.gz")
        docker_repo = f"{DOCKER_BASE}/dists/{self.codename}"
        probe_https_url(f"{docker_repo}/InRelease")
        probe_https_url(f"{docker_repo}/stable/binary-{self.arch}/Packages.gz")
        gh_repo = f"{GH_CLI_BASE}/dists/stable"
        probe_https_url(f"{gh_repo}/InRelease")
        probe_https_url(f"{gh_repo}/main/binary-{self.arch}/Packages.gz")

    def preflight(self) -> None:
        if not self.dry_run:
            self._check_tty()
            if not self.auth_tty_available:
                raise InstallError(
                    "L'installation standard requiert un terminal interactif pour configurer Git et GitHub CLI."
                )
            if not shutil.which("git", path=self.target_path):
                raise InstallError("Git est introuvable pour le compte utilisateur cible.")
        self.validate_environment()
        self.resolve_official_plan()
        self._check_node_manager_conflicts()
        self._check_docker_conflicts()
        self._check_apt_source_conflicts()
        self._check_npm_prefix()
        self.gh_authenticated = self._gh_is_authenticated()
        if not self.dry_run and os.geteuid() != 0:
            self.set_step("validation de sudo avant toute modification")
            result = subprocess.run(
                [self._system_executable("sudo"), "-n", "true"],
                check=False,
                env=self._privileged_env(),
            )
            if result.returncode != 0:
                raise InstallError("L'accès sudo non interactif a échoué; aucune installation n'a commencé.")

    def print_plan(self) -> None:
        print("\nVersions stables résolues (aucune modification effectuée):")
        for tool in ("node", "npm", "pnpm", "bun", "uv", "zoxide", "fzf", "gh"):
            value = self.plan[tool] if isinstance(self.plan.get(tool), str) else self.plan[tool]["version"]
            print(f"  {tool}: {value}")
        print("  APT: candidat installable non vérifié en mode à blanc; Node/GitHub ci-dessus sont les dernières releases amont.")
        print("  Docker: dernière version stable publiée dans le dépôt APT officiel Docker")
        print("  Docker Compose: dernière version stable publiée dans le dépôt APT officiel Docker")
        print(f"  Ubuntu: {self.version_id} ({self.codename}), architecture {self.arch}")
        print("  Git/GitHub CLI: configuration interactive après installation (aucune question en mode à blanc)")

    @staticmethod
    def _check_root_directory(path: Path) -> None:
        if path.is_symlink() or not path.is_dir():
            raise InstallError(f"Répertoire système non sûr: {path}.")
        metadata = path.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise InstallError(f"Répertoire système non sûr (propriétaire ou écriture de groupe/autres): {path}.")

    @staticmethod
    def _check_root_file(path: Path) -> None:
        if (path.is_symlink() or not path.is_file() or path.stat().st_uid != 0
                or stat.S_IMODE(path.stat().st_mode) != 0o644):
            raise InstallError(f"Fichier système non sûr (propriétaire ou mode): {path}.")

    def _ensure_root_directory(self, path: Path, mode: int = 0o755) -> None:
        existed = path.exists() or path.is_symlink()
        if existed:
            self._check_root_directory(path)
        if os.geteuid() == 0:
            path.mkdir(mode=mode, parents=True, exist_ok=True)
            self._check_root_directory(path)
            if not existed:
                os.chmod(path, mode)
        else:
            if not existed:
                self.system_command(["install", "-d", "-m", f"{mode:04o}", str(path)])
                self._check_root_directory(path)

    def _write_root_file(
        self,
        path: Path,
        data: bytes,
        mode: int = 0o644,
        *,
        allow_managed_transition: bool = False,
        allow_key_rotation: bool = False,
    ) -> None:
        if path.is_symlink():
            raise InstallError(f"Fichier système non sûr: {path}.")
        if path.exists():
            if not path.is_file():
                raise InstallError(f"Fichier système non sûr: {path}.")
            metadata = path.stat()
            if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != mode:
                raise InstallError(f"Fichier système non sûr (propriétaire ou mode): {path}.")
            try:
                old_data = path.read_bytes()
                if old_data == data:
                    return
            except OSError as exc:
                raise InstallError(f"Impossible de lire {path}.") from exc
            if (
                not (allow_key_rotation and path.parent == Path("/etc/apt/keyrings")
                     and path.name.startswith("dev-bootstrap-"))
                and not (allow_managed_transition
                         and path.parent == Path("/etc/apt/sources.list.d")
                         and is_safe_managed_source_transition(
                             path, old_data.decode("utf-8", errors="replace"), data.decode("utf-8", errors="replace")
                         ))
            ):
                raise InstallError(f"Refus d'écraser le fichier système existant {path}.")
        self._ensure_root_directory(path.parent)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        if temporary.exists() or temporary.is_symlink():
            raise InstallError(f"Fichier temporaire système déjà présent: {temporary}.")
        try:
            if os.geteuid() == 0:
                with temporary.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, mode)
                os.replace(temporary, path)
                self._fsync_directory(path.parent)
            else:
                self.system_command(["tee", str(temporary)], input_data=data, capture=True)
                self.system_command(["chmod", f"{mode:04o}", str(temporary)])
                self.system_command(["sync", str(temporary)])
                self.system_command(["mv", "--", str(temporary), str(path)])
                self._fsync_directory(path.parent)
        except (OSError, InstallError) as exc:
            if os.geteuid() == 0:
                temporary.unlink(missing_ok=True)
            raise InstallError(f"Impossible d'installer le fichier système {path}.") from exc

    @contextlib.contextmanager
    def _isolated_gpg_environment(self):
        # Provide GnuPG with an isolated, private home directory.
        with tempfile.TemporaryDirectory(prefix="dev-bootstrap-gpg-") as gnupg_home:
            os.chmod(gnupg_home, 0o700)
            env = self._privileged_env()
            env["HOME"] = gnupg_home
            env["GNUPGHOME"] = gnupg_home
            yield gnupg_home, env

    def _gpg_fingerprints(self, key_data: bytes) -> set[str]:
        try:
            with self._isolated_gpg_environment() as (gnupg_home, env):
                result = subprocess.run(
                    [
                        self._system_executable("gpg"),
                        "--no-options",
                        "--batch",
                        "--no-tty",
                        "--homedir",
                        gnupg_home,
                        "--show-keys",
                        "--with-colons",
                        "--fingerprint",
                        "-",
                    ],
                    input=key_data,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    env=env,
                )
        except OSError as exc:
            raise InstallError(
                "Impossible de lancer gpg pour vérifier une clé APT."
            ) from exc

        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise InstallError(
                "Une clé APT téléchargée est invalide ou illisible par gpg."
                + (f" Détail : {detail}" if detail else "")
            )

        fingerprints: set[str] = set()
        primary = False
        for line in result.stdout.decode("utf-8", errors="replace").splitlines():
            fields = line.split(":")
            if fields[0] in ("pub", "sub"):
                primary = fields[0] == "pub"
            elif fields[0] == "fpr" and primary:
                if len(fields) <= 9 or not re.fullmatch(r"[0-9A-Fa-f]{40}", fields[9]):
                    raise InstallError("Empreinte primaire APT invalide.")
                fingerprints.add(fields[9].upper())
                primary = False

        if not fingerprints:
            raise InstallError("Aucune empreinte n'a été trouvée dans une clé APT.")
        return fingerprints

    def _prepare_key(self, name: str, url: str, *, dearmor: bool = False, expected_sha256: str | None = None) -> None:
        self.set_step(f"vérification et installation de la clé APT officielle {name}")
        raw = fetch_https_bytes(url, max_bytes=2_000_000)
        if expected_sha256:
            actual = hashlib.sha256(raw).hexdigest()
            if actual != expected_sha256:
                raise InstallError(f"Le checksum SHA-256 officiel de la clé {name} ne correspond pas.")
        fingerprints = self._gpg_fingerprints(raw)
        trusted = APT_KEY_TRUST_V1.get(name)
        if not trusted or not fingerprints or not fingerprints.issubset(trusted):
            raise InstallError(f"Empreinte primaire inconnue dans la clé APT {name} (trust v1).")
        if dearmor:
            try:
                with self._isolated_gpg_environment() as (gnupg_home, env):
                    result = subprocess.run(
                        [
                            self._system_executable("gpg"),
                            "--no-options",
                            "--batch",
                            "--no-tty",
                            "--homedir",
                            gnupg_home,
                            "--dearmor",
                        ],
                        input=raw,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                        env=env,
                    )
            except OSError as exc:
                raise InstallError(
                    f"Impossible de lancer gpg pour convertir la clé APT {name}."
                ) from exc

            if result.returncode != 0 or not result.stdout:
                detail = result.stderr.decode("utf-8", errors="replace").strip()
                raise InstallError(
                    f"La clé APT {name} n'a pas pu être convertie au format binaire."
                    + (f" Détail : {detail}" if detail else "")
                )

            key_data = result.stdout
            key_path = Path("/etc/apt/keyrings/dev-bootstrap-nodesource.gpg")
        elif name == "docker":
            key_data = raw
            key_path = Path("/etc/apt/keyrings/dev-bootstrap-docker.asc")
        else:
            key_data = raw
            key_path = Path("/etc/apt/keyrings/dev-bootstrap-github-cli.gpg")
        if key_path.exists():
            self._check_root_file(key_path)
            old_fingerprints = self._gpg_fingerprints(key_path.read_bytes())
            if not old_fingerprints.issubset(trusted):
                raise InstallError(
                    f"La clé existante {key_path} n'appartient pas à la liste de confiance v1."
                )
            self._write_root_file(key_path, key_data, 0o644, allow_key_rotation=True)
        else:
            self._write_root_file(key_path, key_data, 0o644)

    def _install_sources(self) -> None:
        self.set_step("configuration des dépôts APT officiels avec Signed-By")
        if shutil.which("gpg", path=self.SYSTEM_PATH) is None:
            self.system_command(["apt-get", "update"], label="mise à jour des index Ubuntu requis pour gpg")
            self._safe_apt_install(["ca-certificates", "gnupg"], "installation des prérequis de signature APT")
        self._ensure_root_directory(Path("/etc/apt/keyrings"))
        self._ensure_root_directory(Path("/etc/apt/sources.list.d"))
        self._prepare_key(
            "nodesource", f"{NODESOURCE_BASE}/gpgkey/nodesource-repo.gpg.key", dearmor=True
        )
        self._prepare_key(
            "github-cli", f"{GH_CLI_BASE}/githubcli-archive-keyring.gpg",
        )
        self._prepare_key("docker", f"{DOCKER_BASE}/gpg")
        for path, content in self._expected_sources().items():
            self._write_root_file(
                path, content.encode("utf-8"), 0o644, allow_managed_transition=True
            )
        self.set_step("vérification cryptographique des index APT officiels")
        self.system_command(["apt-get", "update"], label="apt-get update après ajout des dépôts officiels")

    def _apt_candidate(self, package: str, repository_marker: str) -> str:
        policy = self.system_command(["apt-cache", "policy", package], capture=True)
        candidate_match = re.search(r"^\s*Candidate:\s*(\S+)", policy.stdout, re.MULTILINE)
        if not candidate_match or candidate_match.group(1) == "(none)":
            raise InstallError(f"Aucun candidat APT officiel n'est disponible pour {package}.")
        candidate = candidate_match.group(1)
        if package == "nodejs":
            expected_url, suite_component = f"{NODESOURCE_BASE}/node_{self.plan['node_major']}.x", "nodistro/main"
        elif package == "gh":
            expected_url, suite_component = GH_CLI_BASE, "stable/main"
        elif package in self.APT_PACKAGES:
            expected_url, suite_component = DOCKER_BASE, f"{self.codename}/stable"
        else:
            raise InstallError(f"Paquet APT inattendu: {package}.")
        if repository_marker != expected_url.removeprefix("https://"):
            raise InstallError(f"Dépôt APT inattendu pour {package}.")

        in_candidate = False
        sources: list[tuple[int, str, str, str]] = []
        for line in policy.stdout.splitlines():
            version_line = re.fullmatch(r"\s*(?:\*\*\*\s+)?(\S+)\s+(-?\d+)\s*", line)
            if version_line:
                in_candidate = version_line.group(1) == candidate
                continue
            if not in_candidate:
                continue
            source_line = re.fullmatch(r"\s+(-?\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+Packages\s*", line)
            if source_line:
                sources.append((int(source_line.group(1)), source_line.group(2),
                                source_line.group(3), source_line.group(4)))
            elif re.fullmatch(r"\s+\d+\s+/var/lib/dpkg/status\s*", line):
                continue
            elif line.strip() and not line.lstrip().startswith(("release ", "origin ")):
                raise InstallError(f"Provenance APT du candidat {package} non reconnue: {line.strip()}.")
        if not sources or any(
            url != expected_url or suite != suite_component or arch != self.arch
            for _, url, suite, arch in sources
        ):
            raise InstallError(
                f"La provenance APT du candidat {package}={candidate} ne correspond pas exclusivement au dépôt officiel attendu ({expected_url})."
            )
        return candidate

    @staticmethod
    def _semver_from_package_version(version: str) -> str:
        match = re.fullmatch(r"(?:\d+:)?(\d+\.\d+\.\d+)(?:-[0-9][A-Za-z0-9.+~]*)?", version)
        if not match:
            raise InstallError(f"Impossible d'extraire une version stable depuis le paquet APT {version!r}.")
        return match.group(1)

    def _resolve_apt_candidates(self) -> None:
        self.set_step("contrôle des candidats des dépôts APT signés")
        node_marker = f"deb.nodesource.com/node_{self.plan['node_major']}.x"
        candidates = {
            "nodejs": self._apt_candidate("nodejs", node_marker),
            "gh": self._apt_candidate("gh", "cli.github.com/packages"),
            "docker-ce": self._apt_candidate("docker-ce", "download.docker.com/linux/ubuntu"),
            "docker-ce-cli": self._apt_candidate("docker-ce-cli", "download.docker.com/linux/ubuntu"),
            "containerd.io": self._apt_candidate("containerd.io", "download.docker.com/linux/ubuntu"),
            "docker-buildx-plugin": self._apt_candidate("docker-buildx-plugin", "download.docker.com/linux/ubuntu"),
            "docker-compose-plugin": self._apt_candidate("docker-compose-plugin", "download.docker.com/linux/ubuntu"),
        }
        node_candidate = self._semver_from_package_version(candidates["nodejs"])
        gh_candidate = self._semver_from_package_version(candidates["gh"])
        parsed_node = _parse_stable_version(node_candidate)
        parsed_upstream_node = _parse_stable_version(self.plan["node"])
        if (parsed_node is None or parsed_upstream_node is None
                or node_candidate.split(".", 1)[0] != self.plan["node_major"]
                or parsed_node > parsed_upstream_node):
            raise InstallError(
                f"NodeSource propose {node_candidate}, hors branche LTS {self.plan['node_major']}; "
                "aucun paquet n'a été installé."
            )
        parsed_gh = _parse_stable_version(gh_candidate)
        parsed_upstream_gh = _parse_stable_version(self.plan["gh"])
        if parsed_gh is None or parsed_upstream_gh is None or parsed_gh > parsed_upstream_gh:
            raise InstallError(f"Candidat GitHub CLI instable: {gh_candidate}.")
        if node_candidate != self.plan["node"]:
            for package in ("npm", "pnpm"):
                packument = fetch_https_json(
                    f"{NPM_REGISTRY}/{package}",
                    accept="application/vnd.npm.install-v1+json", max_bytes=20_000_000,
                )
                self.plan[package] = select_latest_compatible_package(packument, node_candidate)
        self.apt_candidates = candidates
        # The signed repository may lag behind upstream; report and verify the
        # version actually selected for installation rather than upstream latest.
        self.plan["node"] = node_candidate
        self.plan["gh"] = gh_candidate

    def _safe_apt_install(self, packages: list[str], label: str) -> None:
        # Simulation is advisory; --no-remove remains a hard guard at execution time.
        options = ["install", "--yes", "--no-install-recommends", "--no-remove", *packages]
        simulated = self.system_command(["apt-get", "-s", *options], capture=True, label=label)
        if any(line.startswith("Remv ") for line in simulated.stdout.splitlines()):
            raise InstallError(f"{label}: APT retirerait des paquets existants; intervention manuelle requise.")
        self.system_command(["apt-get", *options], label=label)

    def _install_apt_packages(self) -> None:
        self._resolve_apt_candidates()
        self.set_step("installation ou mise à jour des paquets APT officiels")
        self.apt_before = {package: self._package_installed_version(package) for package in self.APT_PACKAGES}
        self._safe_apt_install(
            [f"{package}={self.apt_candidates[package]}" for package in self.APT_PACKAGES],
            "installation des paquets NodeSource, GitHub CLI et Docker",
        )
        for package in self.APT_PACKAGES:
            after = self._package_installed_version(package)
            if after is None:
                raise InstallError(f"Le paquet {package} n'est pas installé après apt-get.")
            if after != self.apt_candidates[package]:
                raise InstallError(f"{package}: version installée {after}, attendue {self.apt_candidates[package]}.")
            before = self.apt_before[package]
            self.status[package] = "déjà conforme" if before == after else ("mis à jour" if before else "installé")

    def _ensure_docker_service(self) -> None:
        self.set_step("activation et démarrage du service Docker")
        self.system_command(["systemctl", "enable", "--now", "docker.service"], label="activation de docker.service")
        result = self.system_command(
            ["systemctl", "is-active", "--quiet", "docker.service"],
            capture=True, check=False, label="vérification de docker.service",
        )
        if result.returncode != 0:
            raise InstallError("docker.service n'est pas actif après son démarrage.")

    def download_verified_artifacts(self) -> None:
        self.set_step("téléchargement et vérification SHA-256 des binaires officiels")
        self.artifacts: dict[str, Path] = {}
        for tool in ("bun", "uv", "zoxide", "fzf"):
            asset = self.plan[tool]["asset"]
            destination = Path(self._artifact_directory) / tool
            download_verified_file(
                str(asset["url"]), destination, int(asset["size"]), str(asset["sha256"])
            )
            self.artifacts[tool] = destination

    def _ensure_user_directory(self, path: Path, mode: int = 0o755) -> None:
        with self.target_file_privileges():
            chain = [path, *path.parents]
            for item in chain:
                if item == self.home.parent:
                    break
                if item.exists() and item.is_symlink():
                    raise InstallError(f"Répertoire utilisateur symbolique refusé: {item}.")
            existed = path.exists()
            path.mkdir(mode=mode, parents=True, exist_ok=True)
            if not path.is_dir() or path.stat().st_uid != self.target.uid:
                raise InstallError(f"Répertoire utilisateur non sûr: {path}.")
            if not existed:
                os.chmod(path, mode)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        fsync_directory(path)

    def _atomic_user_write(self, path: Path, data: bytes, mode: int = 0o644) -> None:
        if not path.is_relative_to(self.home):
            raise InstallError(f"Écriture hors du répertoire utilisateur: {path}.")
        self._ensure_user_directory(path.parent, 0o700)
        with self.target_file_privileges():
            if path.is_symlink():
                raise InstallError(f"Fichier utilisateur symbolique refusé: {path}.")
            if path.exists() and (not path.is_file() or path.stat().st_uid != self.target.uid):
                raise InstallError(f"Fichier utilisateur non sûr: {path}.")
            old_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else mode
            fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            temp = Path(temp_name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temp, old_mode)
                os.replace(temp, path)
                self._fsync_directory(path.parent)
            except OSError as exc:
                temp.unlink(missing_ok=True)
                raise InstallError(f"Impossible d'écrire atomiquement {path}.") from exc

    def _save_manifest(self) -> None:
        payload = (json.dumps(self.manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        self._atomic_user_write(self.state_path, payload, 0o600)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1_048_576), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise InstallError(f"Impossible de vérifier le fichier {path}.") from exc
        return digest.hexdigest()

    def _version_at_path(self, path: Path, label: str) -> str:
        result = self.user_command(
            [str(path), "--version"], capture=True, check=False, timeout=20,
            label=f"lecture de version de {label}",
        )
        if result.returncode != 0:
            raise InstallError(f"L'exécutable {label} ne peut pas être vérifié.")
        return extract_display_version(result.stdout)

    def _install_managed_binary(self, name: str, version: str, data: bytes) -> str:
        self._ensure_user_directory(self.bin_dir)
        destination = self.bin_dir / name
        tools = self.manifest.setdefault("tools", {})
        record = tools.get(name)
        new_hash = hashlib.sha256(data).hexdigest()
        pending = self.manifest.get("pending_binary")
        if pending is not None:
            if pending != {"name": name, "version": version, "sha256": new_hash}:
                raise InstallError("Transition binaire en attente incompatible; intervention manuelle requise.")
            if destination.exists() and not destination.is_symlink() and destination.is_file():
                if self._file_sha256(destination) == new_hash:
                    if self._version_at_path(destination, name) != version:
                        raise InstallError(f"Binaire {name} publié mais version invalide.")
                    tools[name] = {"version": version, "sha256": new_hash}
                    self.manifest.pop("pending_binary")
                    self._save_manifest()
                    return "mis à jour" if isinstance(record, dict) else "installé"
        if destination.exists():
            if destination.is_symlink() or not destination.is_file():
                raise InstallError(f"La cible utilisateur {destination} n'est pas un binaire régulier.")
            if not isinstance(record, dict):
                raise InstallError(f"Le binaire {destination} existe sans état géré; refus de l'écraser.")
            old_hash = self._file_sha256(destination)
            if old_hash != record.get("sha256"):
                raise InstallError(f"Le binaire géré {destination} a été modifié hors script.")
            old_version = self._version_at_path(destination, name)
            if old_version != record.get("version"):
                raise InstallError(f"La version du binaire géré {name} ne correspond pas au manifeste.")
            if old_version == version and old_hash == new_hash:
                return "déjà conforme"
            outcome = "mis à jour"
        else:
            external = shutil.which(name, path=self.target_path)
            if external:
                current_version = self._version_at_path(Path(external), name)
                # A matching version string is not evidence of an official asset.
                if (current_version == version and Path(external).is_file()
                        and not Path(external).is_symlink()
                        and self._file_sha256(Path(external)) == new_hash):
                    return "déjà conforme"
                outcome = "mis à jour"
            else:
                outcome = "installé"

        # Verify the candidate before publishing it: a failing or interrupted
        # executable must not replace the previously recorded binary.
        with self.target_file_privileges():
            fd, staged_name = tempfile.mkstemp(prefix=f".{name}.verify-", dir=self.bin_dir)
            staged = Path(staged_name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(staged, 0o755)
                actual = self._version_at_path(staged, name)
                if actual != version:
                    raise InstallError(f"{name} affiche {actual}, version stable attendue {version}.")
            finally:
                staged.unlink(missing_ok=True)
        self.manifest["pending_binary"] = {"name": name, "version": version, "sha256": new_hash}
        self._save_manifest()
        self._atomic_user_write(destination, data, 0o755)
        tools[name] = {"version": version, "sha256": new_hash}
        self.manifest.pop("pending_binary")
        self._save_manifest()
        return outcome

    def _install_release_tools(self) -> None:
        self.set_step("installation des versions stables Bun, uv, zoxide et fzf")
        bun_version = self.plan["bun"]["version"]
        bun = extract_archive_member(self.artifacts["bun"], "zip", "bun")
        self.status["bun"] = self._install_managed_binary("bun", bun_version, bun)
        self.verified_versions["Bun"] = bun_version

        uv_version = self.plan["uv"]["version"]
        uv_archive = self.artifacts["uv"]
        uv = extract_archive_member(uv_archive, "tar.gz", "uv")
        uvx = extract_archive_member(uv_archive, "tar.gz", "uvx")
        self.status["uv"] = self._install_managed_binary("uv", uv_version, uv)
        self._install_managed_binary("uvx", uv_version, uvx)
        self.verified_versions["uv"] = uv_version

        for tool in ("zoxide", "fzf"):
            version = self.plan[tool]["version"]
            binary = extract_archive_member(self.artifacts[tool], "tar.gz", tool)
            self.status[tool] = self._install_managed_binary(tool, version, binary)
            self.verified_versions[tool] = version

    def _tool_version(self, name: str) -> str | None:
        executable = shutil.which(name, path=self.target_path)
        if not executable:
            return None
        result = self.user_command(
            [executable, "--version"], capture=True, check=False, timeout=20,
            label=f"vérification de {name}",
        )
        if result.returncode != 0:
            return None
        try:
            return extract_display_version(result.stdout)
        except InstallError:
            return None

    def _package_binary_is_managed(self, name: str) -> bool:
        path = self.bin_dir / name
        if not path.exists() and not path.is_symlink():
            return False
        record = self.manifest.get("tools", {}).get(name)
        if not isinstance(record, dict):
            if name in self.manifest.get("pending_npm", {}).get("bins", {}):
                return False
            raise InstallError(f"Le binaire npm/pnpm {path} existe sans correspondance fiable au manifeste.")
        if path.is_symlink():
            link = os.readlink(path)
            if link != record.get("link"):
                raise InstallError(f"Le lien npm/pnpm {path} a été modifié hors script.")
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                raise InstallError(f"Le lien npm/pnpm {path} est cassé.") from exc
            if not resolved.is_relative_to(self.prefix.resolve()):
                raise InstallError(f"Le lien npm/pnpm {path} sort du préfixe géré.")
            target = resolved
        else:
            if not path.is_file():
                raise InstallError(f"Le binaire npm/pnpm géré {path} est de type inattendu.")
            target = path
        if self._file_sha256(target) != record.get("sha256"):
            raise InstallError(f"Le binaire npm/pnpm {path} a été modifié hors script.")
        return True

    def _npm_package_snapshot(self, name: str) -> dict[str, str]:
        """Inventory every entry in a managed npm package, not merely its executable."""
        root = self.prefix / "lib" / "node_modules" / name
        if root.is_symlink() or not root.is_dir():
            raise InstallError(f"Paquet npm géré absent ou symbolique: {root}.")
        entries: dict[str, str] = {}
        try:
            def traversal_error(error: OSError) -> None:
                raise error

            for parent, directories, files in os.walk(root, followlinks=False, onerror=traversal_error):
                for item in sorted([Path(parent), *(Path(parent) / part for part in directories + files)]):
                    relative = str(item.relative_to(root))
                    metadata = item.lstat()
                    if metadata.st_uid != self.target.uid:
                        raise InstallError(f"Propriétaire inattendu dans le paquet npm: {item}.")
                    mode = stat.S_IMODE(metadata.st_mode)
                    if item.is_symlink():
                        value = f"link:{mode:o}:{os.readlink(item)}"
                    elif item.is_dir():
                        value = f"dir:{mode:o}"
                    elif item.is_file():
                        value = f"file:{mode:o}:{self._file_sha256(item)}"
                    else:
                        raise InstallError(f"Entrée npm non sûre: {item}.")
                    entries[relative] = value
        except OSError as exc:
            raise InstallError(f"Impossible d'inventorier le paquet npm {root}.") from exc
        return entries

    def _check_npm_prefix(self) -> None:
        """Reject unknown prefix contents before npm can rewrite any of them."""
        if not self.prefix.exists():
            return
        if self.prefix.is_symlink() or not self.prefix.is_dir() or self.prefix.stat().st_uid != self.target.uid:
            raise InstallError("Préfixe npm non sûr.")
        for parent, names in ((self.prefix, {"bin", "lib"}),
                              (self.prefix / "lib", {"node_modules"}),
                              (self.prefix / "lib" / "node_modules", {"npm", "pnpm"})):
            if parent.exists():
                if parent.is_symlink() or not parent.is_dir() or parent.stat().st_uid != self.target.uid:
                    raise InstallError(f"Répertoire npm non sûr: {parent}.")
                if {p.name for p in parent.iterdir()} - names:
                    raise InstallError(f"Contenu non suivi dans le préfixe npm: {parent}.")
        package_records = self.manifest.get("npm_packages", {})
        if not isinstance(package_records, dict):
            raise InstallError("Inventaire npm invalide.")
        pending = self.manifest.get("pending_npm", {})
        if (not isinstance(pending, dict) or set(pending) - {"packages", "bins"}
                or not isinstance(pending.get("packages", {}), dict)
                or not isinstance(pending.get("bins", {}), dict)):
            raise InstallError("Inventaire npm en attente invalide.")
        pending_packages = pending.get("packages", {})
        pending_bins = pending.get("bins", {})
        if set(pending_packages) - {"npm", "pnpm"} or set(pending_bins) - {"npm", "pnpm", "npx", "pnpx", "pn", "pnx"}:
            raise InstallError("Entrée npm en attente inattendue.")
        for name in ("npm", "pnpm"):
            package = self.prefix / "lib" / "node_modules" / name
            if package.exists() or package.is_symlink():
                if (package_records.get(name) if name not in pending_packages else pending_packages[name]) != self._npm_package_snapshot(name):
                    raise InstallError(f"Paquet npm {name} absent du manifeste ou modifié; refus d'écraser.")
            elif name in package_records or name in pending_packages:
                raise InstallError(f"Paquet npm {name} absent malgré son inventaire.")
        if self.bin_dir.exists():
            if self.bin_dir.is_symlink() or not self.bin_dir.is_dir() or self.bin_dir.stat().st_uid != self.target.uid:
                raise InstallError("Répertoire binaire npm non sûr.")
            if {p.name for p in self.bin_dir.iterdir()} - set(self.manifest.get("tools", {})) - set(pending_bins):
                raise InstallError("Binaire non suivi dans le préfixe npm.")
            for name, record in pending_bins.items():
                if not isinstance(record, dict) or record != self._npm_binary_snapshot(name):
                    raise InstallError(f"Binaire npm {name} en attente modifié; refus d'écraser.")
            for name in ("npx", "pnpx", "pn", "pnx"):
                if name in self.manifest.get("tools", {}) and name not in pending_bins:
                    record = self.manifest["tools"][name]
                    if not isinstance(record, dict) or record != self._npm_binary_snapshot(name):
                        raise InstallError(f"Binaire npm auxiliaire {name} modifié; refus d'écraser.")

    def _npm_binary_snapshot(self, name: str) -> dict[str, str]:
        path = self.bin_dir / name
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.prefix.resolve()):
                raise InstallError(f"Lien npm hors du préfixe géré: {path}.")
            return {"link": os.readlink(path), "sha256": self._file_sha256(resolved)}
        if not path.is_file() or path.stat().st_uid != self.target.uid:
            raise InstallError(f"Binaire npm non sûr: {path}.")
        return {"sha256": self._file_sha256(path)}

    def _record_failed_npm_install(self) -> None:
        """Remember only the exact partial files created by this attempted install."""
        packages: dict[str, dict[str, str]] = {}
        bins: dict[str, dict[str, str]] = {}
        for name in ("npm", "pnpm"):
            package = self.prefix / "lib/node_modules" / name
            if package.exists() or package.is_symlink():
                packages[name] = self._npm_package_snapshot(name)
        for name in ("npm", "pnpm", "npx", "pnpx", "pn", "pnx"):
            binary = self.bin_dir / name
            if binary.exists() or binary.is_symlink():
                bins[name] = self._npm_binary_snapshot(name)
        self.manifest["pending_npm"] = {"packages": packages, "bins": bins}
        self._check_npm_prefix()
        self._save_manifest()

    def _configure_npm_tools(self) -> None:
        self.set_step("installation de npm et pnpm compatibles dans l'espace utilisateur")
        self._check_npm_prefix()
        expected = {"npm": self.plan["npm"], "pnpm": self.plan["pnpm"]}
        outdated: list[str] = []
        previously_present: dict[str, bool] = {}
        package_manager = shutil.which("npm", path="/usr/bin:/bin")
        if not package_manager:
            raise InstallError("npm fourni par NodeSource est absent après l'installation de Node.js.")
        for name, wanted in expected.items():
            pending_name = (name in self.manifest.get("pending_npm", {}).get("packages", {})
                            or name in self.manifest.get("pending_npm", {}).get("bins", {}))
            managed = False if pending_name else self._package_binary_is_managed(name)
            # A pending npm installation may contain a deliberately incomplete executable
            # (notably pnpm's preinstall placeholder). Do not execute it before the recovery
            # path has had a chance to reinstall/rebuild the package.
            current = None if pending_name else self._tool_version(name)
            previously_present[name] = managed or current is not None or pending_name
            if current == wanted and managed and not pending_name:
                if name not in self.manifest.get("npm_packages", {}):
                    raise InstallError(f"Paquet npm {name} sans inventaire vérifié.")
                self.status[name] = "déjà conforme"
                self.verified_versions[name] = wanted
                continue
            if managed:
                record = self.manifest["tools"][name]
                actual = self._version_at_path(self.bin_dir / name, name)
                if actual != record.get("version"):
                    raise InstallError(f"Le binaire géré {name} ne correspond pas au manifeste.")
            outdated.append(f"{name}@{wanted}")
        if outdated:
            npm_outdated = [item for item in outdated if item.split("@", 1)[0] != "pnpm"]
            pnpm_outdated = [item for item in outdated if item.split("@", 1)[0] == "pnpm"]
            try:
                if npm_outdated:
                    self.user_command(
                        [
                            package_manager, "install", "--global", "--prefix", str(self.prefix),
                            "--registry", NPM_REGISTRY,
                            "--engine-strict", "--ignore-scripts", "--no-audit", "--no-fund",
                            *npm_outdated,
                        ],
                        label="installation de npm dans le préfixe utilisateur",
                        timeout=900,
                    )
                if pnpm_outdated:
                    self.user_command(
                        [
                            package_manager, "install", "--global", "--prefix", str(self.prefix),
                            "--registry", NPM_REGISTRY,
                            "--engine-strict", "--allow-scripts=pnpm", "--strict-allow-scripts",
                            "--no-audit", "--no-fund",
                            *pnpm_outdated,
                        ],
                        label="installation de pnpm dans le préfixe utilisateur",
                        timeout=900,
                    )
                    # npm can consider an already-present pending pnpm version up to date and
                    # skip its lifecycle hook. Rebuild explicitly so pnpm's preinstall replaces
                    # the JavaScript placeholder with the platform-native executable.
                    self.user_command(
                        [
                            package_manager, "rebuild", "--global", "--prefix", str(self.prefix),
                            "--allow-scripts=pnpm", "--strict-allow-scripts", "pnpm",
                        ],
                        label="finalisation du binaire natif pnpm",
                        timeout=900,
                    )
            except (InstallError, KeyboardInterrupt):
                self._record_failed_npm_install()
                raise
            try:
                for name, wanted in expected.items():
                    if name not in {item.split("@", 1)[0] for item in outdated}:
                        continue
                    executable = self.bin_dir / name
                    current = self._version_at_path(executable, name)
                    if current != wanted:
                        raise InstallError(f"{name} affiche {current}, version stable attendue {wanted}.")
                    self.manifest.setdefault("tools", {})[name] = {
                        "version": wanted,
                        "sha256": self._file_sha256(executable.resolve()),
                        "link": os.readlink(executable) if executable.is_symlink() else None,
                    }
                    self.manifest.setdefault("npm_packages", {})[name] = self._npm_package_snapshot(name)
                    self.status[name] = "mis à jour" if previously_present[name] else "installé"
                    self.verified_versions[name.capitalize()] = wanted
                for name in ("npx", "pnpx", "pn", "pnx"):
                    binary = self.bin_dir / name
                    if binary.exists() or binary.is_symlink():
                        self.manifest.setdefault("tools", {})[name] = self._npm_binary_snapshot(name)
                    elif name in self.manifest.get("tools", {}):
                        raise InstallError(f"Binaire npm auxiliaire {name} absent après installation.")
            except (InstallError, KeyboardInterrupt):
                self._record_failed_npm_install()
                raise
            self.manifest.pop("pending_npm", None)
            self._save_manifest()

    def _outside_managed_config(self, text: str) -> str:
        lines = text.splitlines(keepends=True)
        plain = [line.rstrip("\r\n") for line in lines]
        starts = [i for i, line in enumerate(plain) if line == self.MANAGED_START]
        ends = [i for i, line in enumerate(plain) if line == self.MANAGED_END]
        if not starts and not ends:
            return text
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise InstallError("Bloc de configuration géré mal formé; fichier shell laissé intact.")
        return "".join(lines[:starts[0]]) + "".join(lines[ends[0] + 1:])

    def _shell_config_body(self, shell_name: str, existing: str) -> str:
        self._outside_managed_config(existing)  # Reject malformed markers; never trust lookalike commands.
        body: list[str] = []
        shell_managed_bin = f"$HOME/.local/share/{APP_NAME}/bin"
        if shell_name == "fish":
            body.append(
                f'if not contains -- "{shell_managed_bin}" $PATH; '
                f'set -gx PATH "{shell_managed_bin}" $PATH; end'
            )
            body.append("zoxide init fish | source")
        else:
            body.append(
                f'case ":$PATH:" in *":{shell_managed_bin}:"*) ;; '
                f'*) export PATH="{shell_managed_bin}:$PATH" ;; esac'
            )
            body.append(f'eval "$(zoxide init {shell_name})"')
        if shell_name == "bash":
            body.append('eval "$(fzf --bash)"')
        elif shell_name == "zsh":
            body.append("source <(fzf --zsh)")
        else:
            body.append("fzf --fish | source")
        return "\n".join(body)

    def _configure_shell(self) -> None:
        self.set_step("configuration idempotente du shell utilisateur pour zoxide et fzf")
        path = config_file_for_shell(self.home, self.target.shell)
        with self.target_file_privileges():
            if path.is_symlink():
                raise InstallError(f"Le fichier shell {path} est un lien symbolique; il ne sera pas modifié.")
            if path.exists() and (not path.is_file() or path.stat().st_uid != self.target.uid):
                raise InstallError(f"Le fichier shell {path} n'appartient pas au compte cible.")
            try:
                existing = path.read_text(encoding="utf-8") if path.exists() else ""
            except (OSError, UnicodeDecodeError) as exc:
                raise InstallError(f"Impossible de lire le fichier shell {path}.") from exc
        shell_name = Path(self.target.shell).name
        body = self._shell_config_body(shell_name, existing)
        updated = update_managed_block(existing, self.MANAGED_START, self.MANAGED_END, body)
        if updated != existing:
            self._ensure_user_directory(path.parent)
            self._atomic_user_write(path, updated.encode("utf-8"), 0o644)
            self.status["shell"] = "configuré"
        else:
            self.status["shell"] = "déjà conforme"
        self._verify_shell_configuration()

    def _write_tty(self, message: str) -> None:
        try:
            fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        except OSError as exc:
            raise InstallError("Impossible d'écrire dans le terminal interactif.") from exc
        try:
            os.write(fd, message.encode("utf-8"))
        finally:
            os.close(fd)

    def _read_tty_line(self, prompt: str, *, hidden: bool = False) -> str:
        try:
            fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        except OSError as exc:
            raise InstallError("Impossible d'ouvrir un terminal sûr pour lire la saisie.") from exc

        original = None
        hidden_enabled = False
        try:
            if not os.isatty(fd):
                raise InstallError("Le terminal de saisie n'est pas interactif.")
            original = termios.tcgetattr(fd)
            if hidden:
                hidden_attributes = list(original)
                hidden_attributes[3] &= ~termios.ECHO
                termios.tcsetattr(fd, termios.TCSAFLUSH, hidden_attributes)
                hidden_enabled = True
            os.write(fd, prompt.encode("utf-8"))
            with os.fdopen(os.dup(fd), "r", encoding="utf-8", errors="strict") as stream:
                value = stream.readline()
            if value == "":
                raise InstallError("La lecture du terminal a été interrompue.")
            return value.rstrip("\r\n")
        finally:
            try:
                if hidden_enabled and original is not None:
                    termios.tcsetattr(fd, termios.TCSAFLUSH, original)
                    os.write(fd, b"\n")
            finally:
                os.close(fd)

    def _prompt_value(self, label: str, current_value: str = "") -> str:
        while True:
            if current_value:
                prompt = f"{label} [{current_value}] (Entrée pour conserver) : "
            else:
                prompt = f"{label} : "
            value = self._read_tty_line(prompt).strip()
            if not value and current_value:
                return current_value
            if value:
                return value
            self._write_tty("La valeur ne peut pas être vide.\n")

    @staticmethod
    def _valid_git_email(email: str) -> bool:
        if email.count("@") != 1 or any(character.isspace() for character in email):
            return False
        local_part, domain = email.rsplit("@", 1)
        if (not local_part or not domain or local_part.startswith(".")
                or local_part.endswith(".") or ".." in local_part):
            return False
        if any(not (character.isalnum() or character in "_%+.-") for character in local_part):
            return False
        return re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+",
            domain,
        ) is not None

    def _prompt_git_email(self, current_value: str = "") -> str:
        while True:
            value = self._prompt_value("Adresse e-mail Git", current_value)
            if self._valid_git_email(value):
                return value
            self._write_tty("Adresse e-mail invalide. Exemple attendu : nom@example.com\n")
            current_value = ""

    def _git_global_value(self, key: str) -> str:
        result = self.user_command(
            ["git", "config", "--global", "--get", key],
            capture=True, check=False, timeout=20, label=f"lecture de la configuration Git {key}",
        )
        if result.returncode == 1:
            return ""
        if result.returncode != 0:
            raise InstallError(f"Impossible de lire la configuration Git globale {key}.")
        return result.stdout.rstrip("\n")

    def _configure_git(self) -> None:
        self.set_step("configuration interactive de Git")
        current_name = self._git_global_value("user.name")
        current_email = self._git_global_value("user.email")
        git_name = self._prompt_value("Nom ou pseudo Git", current_name)
        git_email = self._prompt_git_email(current_email)

        values = {
            "user.name": git_name,
            "user.email": git_email,
            "init.defaultBranch": "main",
        }
        for key, value in values.items():
            result = self.user_command(
                ["git", "config", "--global", "--replace-all", key, value],
                capture=True, check=False, timeout=20,
                label=f"configuration Git {key}",
            )
            if result.returncode != 0:
                raise InstallError(f"Impossible de configurer la valeur Git globale {key}.")

        for key, expected in values.items():
            if self._git_global_value(key) != expected:
                raise InstallError(f"La vérification de la configuration Git {key} a échoué.")

        self.git_config = {
            "name": git_name,
            "email": git_email,
            "default_branch": "main",
        }
        self.status["git"] = "configuré"

    def _ask_yes_no(self, prompt: str, *, default: bool = False) -> bool:
        while True:
            suffix = " [O/n] : " if default else " [o/N] : "
            answer = self._read_tty_line(prompt + suffix).strip().casefold()
            if not answer:
                return default
            if answer in {"o", "oui", "y", "yes"}:
                return True
            if answer in {"n", "non", "no"}:
                return False
            self._write_tty("Répondez par oui ou non.\n")

    def _read_token_masked(self) -> str:
        while True:
            token = self._read_tty_line("Personal Access Token GitHub classique (saisie masquée) : ", hidden=True)
            if token.strip():
                return token
            self._write_tty("Le token ne peut pas être vide.\n")

    def _authenticate_github(self) -> None:
        self.set_step("vérification puis authentification GitHub CLI")
        if self._gh_is_authenticated():
            self.gh_authenticated = True
            self._write_tty("GitHub CLI possède déjà une authentification valide pour github.com.\n")
            if not self._ask_yes_no("Effectuer une nouvelle authentification ?", default=False):
                self.status["gh_auth"] = "déjà authentifié"
                return
        else:
            self._write_tty("Aucune authentification GitHub valide n'a été confirmée pour github.com.\n")

        token = self._read_token_masked()
        try:
            result = self.user_command(
                ["gh", "auth", "login", "--hostname", "github.com", "--with-token"],
                input_data=token + "\n", capture=True, check=False, timeout=120,
                label="authentification GitHub CLI",
            )
        finally:
            token = ""
        if result.returncode != 0:
            raise InstallError(
                "gh auth login a échoué; vérifiez la validité et les permissions du token, puis relancez."
            )
        if not self._gh_is_authenticated():
            raise InstallError("gh auth status ne confirme pas l'authentification GitHub.")
        self.gh_authenticated = True
        self.status["gh_auth"] = "authentifié"

    def _verify_command_version(self, name: str, expected: str, command: list[str]) -> str:
        self.set_step(f"vérification de {name}")
        result = self.user_command(command, capture=True, check=False, timeout=30, label=f"version de {name}")
        if result.returncode != 0:
            raise InstallError(f"{name} n'est pas exécutable dans l'environnement du compte cible.")
        actual = extract_display_version(result.stdout)
        if actual != expected:
            raise InstallError(f"{name}: version active {actual}, version attendue {expected}.")
        return actual

    def _verify_shell_configuration(self) -> None:
        path = config_file_for_shell(self.home, self.target.shell)
        if not path.is_file() or path.is_symlink() or path.stat().st_uid != self.target.uid:
            raise InstallError("Le fichier de configuration du shell est absent ou non sûr.")
        text = path.read_text(encoding="utf-8")
        shell_name = Path(self.target.shell).name
        canonical = update_managed_block(
            text, self.MANAGED_START, self.MANAGED_END,
            self._shell_config_body(shell_name, text),
        )
        if (text.count(self.MANAGED_START) != 1 or text.count(self.MANAGED_END) != 1
                or canonical != text):
            raise InstallError("Le bloc shell canonique zoxide/fzf est absent ou modifié.")

    def verify_final_state(self) -> None:
        self.set_step("validation finale des versions, du service et de l'authentification")
        node = self._verify_command_version("Node.js", self.plan["node"], ["node", "--version"])
        npm = self._verify_command_version("npm", self.plan["npm"], ["npm", "--version"])
        pnpm = self._verify_command_version("pnpm", self.plan["pnpm"], ["pnpm", "--version"])
        bun = self._verify_command_version("Bun", self.plan["bun"]["version"], ["bun", "--version"])
        uv = self._verify_command_version("uv", self.plan["uv"]["version"], ["uv", "--version"])
        zoxide = self._verify_command_version("zoxide", self.plan["zoxide"]["version"], ["zoxide", "--version"])
        fzf = self._verify_command_version("fzf", self.plan["fzf"]["version"], ["fzf", "--version"])
        gh = self._verify_command_version("GitHub CLI", self.plan["gh"], ["gh", "--version"])
        docker_expected = self._semver_from_package_version(self.apt_candidates["docker-ce"])
        compose_expected = self._semver_from_package_version(self.apt_candidates["docker-compose-plugin"])
        docker = self._verify_command_version("Docker Engine", docker_expected, ["docker", "--version"])
        compose = self._verify_command_version("Docker Compose", compose_expected, ["docker", "compose", "version"])
        self._verify_command_version(
            "Docker Buildx", self._semver_from_package_version(self.apt_candidates["docker-buildx-plugin"]),
            ["docker", "buildx", "version"],
        )
        if self.gh_authenticated and not self._gh_is_authenticated():
            raise InstallError("L'authentification GitHub ne passe pas la vérification gh auth status.")
        expected_git_config = self.git_config
        for key, expected in (
            ("user.name", expected_git_config["name"]),
            ("user.email", expected_git_config["email"]),
            ("init.defaultBranch", expected_git_config["default_branch"]),
        ):
            if self._git_global_value(key) != expected:
                raise InstallError(f"La configuration Git globale {key} a changé avant la vérification finale.")
        active = self.system_command(
            ["systemctl", "is-active", "--quiet", "docker.service"],
            capture=True, check=False, label="état final du service Docker",
        )
        if active.returncode != 0:
            raise InstallError("Le service Docker n'est pas actif lors du contrôle final.")
        self._verify_shell_configuration()
        self.verified_versions.update({
            "Node.js": node, "npm": npm, "pnpm": pnpm, "Bun": bun, "uv": uv,
            "Docker Engine": docker, "Docker Compose": compose, "GitHub CLI": gh,
            "zoxide": zoxide, "fzf": fzf,
        })

    def print_report(self) -> None:
        rows = [
            ("Node.js", "nodejs"), ("npm", "npm"), ("pnpm", "pnpm"),
            ("Bun", "bun"), ("uv", "uv"), ("Docker Engine", "docker-ce"),
            ("Docker Compose", "docker-compose-plugin"), ("GitHub CLI", "gh"),
            ("zoxide", "zoxide"), ("fzf", "fzf"),
        ]
        print("\nBilan final:")
        for label, key in rows:
            version = self.verified_versions.get(label, "version non relevée")
            status = self.status.get(key, self.status.get(label.lower(), "vérifié"))
            print(f"  {label}: {version} — {status}")
        print(f"  Git name: {self.git_config['name']}")
        print(f"  Git e-mail: {self.git_config['email']}")
        print(f"  Git branche par défaut: {self.git_config['default_branch']}")
        print(f"  Docker service: actif; authentification GitHub: {self.status.get('gh_auth', 'vérifiée')}")
        print(f"  Shell {Path(self.target.shell).name}: {self.status.get('shell', 'configuré')}")
        print("  Ouvrez un nouveau shell pour charger zoxide, fzf et le PATH utilisateur.")
        print("  Docker reste utilisable avec sudo; aucun droit docker-group n'a été accordé.")

    def run(self) -> int:
        try:
            self.preflight()
            if self.dry_run:
                self.print_plan()
                return 0
            with tempfile.TemporaryDirectory(prefix="dev-bootstrap-assets-") as artifact_directory:
                self._artifact_directory = artifact_directory
                self.download_verified_artifacts()
                self._install_sources()
                self._install_apt_packages()
                self._ensure_docker_service()
                self._install_release_tools()
                self._configure_npm_tools()
                self._configure_shell()
                self._configure_git()
                self._authenticate_github()
                self.verify_final_state()
                self.print_report()
            return 0
        except KeyboardInterrupt:
            print(f"\nInterruption pendant {self.step}; les étapes achevées sont conservées.", file=sys.stderr)
            return 130
        except InstallError as exc:
            print(f"\nÉCHEC — {self.step}: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"\nÉCHEC inattendu — {self.step}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Installe les versions LTS/stables courantes des outils de développement sur Ubuntu."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Résout les releases amont et valide les sources sans modification ; candidats APT non résolus (pas de --plan).",
    )
    parser.add_argument("--uninstall", action="store_true", help="Retire les binaires utilisateur vérifiés (conserve APT et npm).")
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 10):
            detected = ".".join(str(part) for part in sys.version_info[:3])
            raise InstallError(f"Python 3.10 ou supérieur est requis; version détectée : {detected}.")
        bootstrap = UbuntuBootstrap(dry_run=args.dry_run)
        if args.uninstall:
            bootstrap.uninstall()
            return 0
        return bootstrap.run()
    except InstallError as exc:
        print(f"ÉCHEC — prérequis: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())



