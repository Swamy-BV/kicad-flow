"""Install the pinned CodeQL bundle and run the same Python query suite as CI.

Usage: python scripts/codeql.py [--install-only]
Reports and per-run databases stay under out/codeql; tools use the user cache.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".github" / "codeql-config.yml"
TOOLCHAIN = ROOT / ".github" / "codeql-toolchain.json"


def host_platform() -> str:
    """Select an official platform bundle for this machine."""
    system, machine = platform.system(), platform.machine().lower()
    if system == "Windows" and machine in ("amd64", "x86_64"):
        return "win64"
    if system == "Linux" and machine in ("amd64", "x86_64", "aarch64", "arm64"):
        return "linux-arm64" if machine in ("aarch64", "arm64") else "linux64"
    if system == "Darwin":
        return "osx64"
    raise ValueError(f"Unsupported CodeQL platform: {system} {machine}")


def verify(executable: Path, toolchain: dict[str, Any]) -> Path:
    """Verify the CLI and bundled Python query version before analysis."""
    version = json.loads(
        subprocess.check_output(
            [str(executable), "version", "--format=json"], text=True, encoding="utf-8"
        )
    )["version"]
    if version != toolchain["version"]:
        raise ValueError(f"Expected CodeQL {toolchain['version']}, found {version}")
    suite = (
        executable.parent
        / "qlpacks"
        / "codeql"
        / "python-queries"
        / str(toolchain["python_queries"])
        / "codeql-suites"
        / "python-security-and-quality.qls"
    )
    if not suite.is_file():
        raise ValueError("Expected Python queries missing; install the complete bundle")
    return suite


def install(toolchain: dict[str, Any]) -> Path:
    """Download, checksum and extract GitHub's official CLI and query bundle."""
    host = host_platform()
    # A profile cache stays visible to native CodeQL/Java children when Python
    # comes from the Microsoft Store, which virtualizes AppData paths.
    cache = Path.home() / ".cache" / "kicad-flow" / "codeql"
    cache = cache.resolve()
    target = cache / (str(toolchain["version"]) + "-" + host)
    executable = target / "codeql" / ("codeql.exe" if os.name == "nt" else "codeql")
    if executable.is_file():
        verify(executable, toolchain)
        return executable
    if target.exists():
        raise ValueError(f"Incomplete CodeQL install; inspect this directory: {target}")
    tar = shutil.which("tar")
    if tar is None:
        raise ValueError(
            "Install tar with Zstandard support to extract the CodeQL bundle"
        )
    cache.mkdir(parents=True, exist_ok=True)
    filename = f"codeql-bundle-{host}.tar.zst"
    url = (
        "https://github.com/github/codeql-action/releases/download/"
        f"codeql-bundle-v{toolchain['version']}/{filename}"
    )
    print(f"Installing CodeQL {toolchain['version']} ({host})...", flush=True)
    with tempfile.TemporaryDirectory(prefix="install-", dir=cache) as temporary:
        staging = Path(temporary).resolve()
        archive = staging / filename
        digest = hashlib.sha256()
        request = urllib.request.Request(
            url, headers={"User-Agent": "KiCadFlow-CodeQL"}
        )
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            archive.open("wb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != toolchain["sha256"][host]:
            raise ValueError("CodeQL bundle checksum mismatch; nothing was installed")
        # Reject absolute/traversing member names before invoking the system tar.
        # The pinned digest also authenticates the bundle's link entries.
        listing = subprocess.check_output([tar, "-tf", str(archive)], text=True)
        for member in listing.splitlines():
            normalized = member.replace("\\", "/")
            if (
                PurePosixPath(normalized).is_absolute()
                or PureWindowsPath(member).drive
                or ".." in PurePosixPath(normalized).parts
            ):
                raise ValueError(f"CodeQL archive path escapes extraction: {member}")
        extracted = staging / "bundle"
        extracted.mkdir()
        subprocess.run([tar, "-xf", str(archive), "-C", str(extracted)], check=True)
        installed = extracted / "codeql" / executable.name
        verify(installed, toolchain)
        extracted.rename(target)
    return executable


def run(command: list[str], log: Path, *, environment: dict[str, str]) -> None:
    """Keep full CLI output on disk and print the final lines on failure."""
    with log.open("w", encoding="utf-8") as output:
        result = subprocess.run(
            command,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=environment,
            check=False,
        )
    if result.returncode:
        print(
            "\n".join(log.read_text(encoding="utf-8").splitlines()[-30:]),
            file=sys.stderr,
        )
        raise RuntimeError(f"CodeQL exited {result.returncode}; full log: {log}")


def summarize(sarif: Path, toolchain: dict[str, Any]) -> dict[str, Any]:
    """Count emitted alerts without silently discarding suppressed results."""
    report = json.loads(sarif.read_text(encoding="utf-8"))
    levels: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    suppressed = 0
    for analysis in report.get("runs", []):
        definitions = {
            rule["id"]: rule for rule in analysis["tool"]["driver"].get("rules", [])
        }
        for result in analysis.get("results", []):
            if any(
                s.get("status") == "accepted" for s in result.get("suppressions", [])
            ):
                suppressed += 1
                continue
            rule = result.get("ruleId", "unknown")
            level = result.get(
                "level",
                definitions.get(rule, {})
                .get("defaultConfiguration", {})
                .get("level", "warning"),
            )
            levels[level] += 1
            rules[rule] += 1
    return {
        "codeql": toolchain["version"],
        "python_queries": toolchain["python_queries"],
        "suite": "security-and-quality",
        "source_root": str(ROOT),
        "findings": sum(rules.values()),
        "suppressed": suppressed,
        "levels": dict(levels),
        "rules": dict(sorted(rules.items())),
    }


def main() -> int:
    """Install or scan, returning nonzero for CLI errors or emitted findings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-only", action="store_true")
    parser.add_argument("--codeql", type=Path, help="existing pinned CodeQL executable")
    parser.add_argument("--output", type=Path, default=ROOT / "out" / "codeql")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument(
        "--ram", type=int, default=4096, help="CodeQL memory budget in MB"
    )
    args = parser.parse_args()
    if args.threads < 1 or args.ram < 1024:
        parser.error("Use at least one thread and 1024 MB of RAM")
    try:
        toolchain = json.loads(TOOLCHAIN.read_text(encoding="utf-8"))
        executable = args.codeql.resolve() if args.codeql else install(toolchain)
        suite = verify(executable, toolchain)
        print(
            f"CodeQL {toolchain['version']} "
            f"- Python queries {toolchain['python_queries']}",
            flush=True,
        )
        if args.install_only:
            print(f"Installed: {executable}")
            return 0
        output = args.output.resolve()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        directory = output / (stamp + "-" + uuid.uuid4().hex[:8])
        directory.mkdir(parents=True)
        database = directory / "database"
        environment = {**os.environ, "CODEQL_PYTHON": sys.executable}
        resources = [f"--threads={args.threads}", f"--ram={args.ram}"]
        print("Extracting Python source...", flush=True)
        run(
            [
                str(executable),
                "database",
                "create",
                str(database),
                "--language=python",
                "--build-mode=none",
                f"--source-root={ROOT}",
                f"--codescanning-config={CONFIG}",
                *resources,
            ],
            directory / "create.log",
            environment=environment,
        )
        sarif = directory / "results.sarif"
        print("Running security-and-quality queries...", flush=True)
        run(
            [
                str(executable),
                "database",
                "analyze",
                str(database),
                str(suite),
                "--format=sarif-latest",
                f"--output={sarif}",
                "--sarif-add-baseline-file-info",
                *resources,
            ],
            directory / "analyze.log",
            environment=environment,
        )
        summary = summarize(sarif, toolchain)
        summary["run"] = str(directory)
        (directory / "summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        for name in ("results.sarif", "summary.json"):
            temporary = output / (name + ".tmp")
            shutil.copyfile(directory / name, temporary)
            temporary.replace(output / name)
        print(f"Findings: {summary['findings']} - suppressed: {summary['suppressed']}")
        for rule, count in summary["rules"].items():
            print(f"  {rule}: {count}")
        print(f"Report: {output / 'results.sarif'}")
        print(f"Summary: {output / 'summary.json'}")
        return 1 if summary["findings"] else 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
