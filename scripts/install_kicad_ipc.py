"""Install the pinned official KiCad 11 development bindings in an isolated venv.

The released kicad-python 0.8 package predates schematic IPC. This bootstrap
builds the upstream SDK against the selected native protocol, adapting its
renamed drill-chart enum import. Remove this bootstrap once a release supports
the selected nightly. It does not install or configure KiCad itself.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import venv
from pathlib import Path

SDK = "87b945a0d56b26a78bf547db9771ff9bf3a057ea"
# Match the native build pinned by setup_kicad11_ci.ps1. Generating against an
# older protocol can hide commands that the installed editor already supports.
PROTOCOL = "ff9515f1e9607d7e68f0ebd568f4758318690e0e"
SDK_URL = (
    f"https://gitlab.com/kicad/code/kicad-python/-/archive/{SDK}/"
    f"kicad-python-{SDK}.tar.gz"
)
PROTO_URL = (
    "https://gitlab.com/api/v4/projects/kicad%2Fcode%2Fkicad/repository/"
    f"archive.tar.gz?sha={PROTOCOL}&path=api%2Fproto"
)


def download(url: str, digest: str, destination: Path) -> None:
    """Extract checksum-verified regular files, rejecting paths outside the target."""
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"Archive checksum mismatch: {url}")
    destination = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            parts = Path(member.name).parts[1:]
            target = destination.joinpath(*parts).resolve()
            if not target.is_relative_to(destination):
                raise ValueError(f"Archive path escapes destination: {member.name}")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"Archive has no contents for {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def adapt_sdk(source: Path) -> None:
    """Port the pinned SDK's obsolete enum name to the native protocol.

    KiCad replaced DrillChartAlign with the common HorizontalAlignment enum.
    Keep the actual native schema intact; this build-time SDK source adaptation
    changes only that wrapper's import and annotations, never a wire value.
    """
    path = source / "kipy" / "board_types.py"
    content = path.read_text(encoding="utf-8")
    old = "    DrillChartAlign,\n"
    if content.count(old) != 1 or content.count("DrillChartAlign") != 3:
        raise ValueError("Pinned SDK drill-chart wrapper changed; review adaptation")
    content = content.replace(old, "").replace("DrillChartAlign", "HorizontalAlignment")
    content = content.replace(
        "from kipy.proto.common.types.enums_pb2 import Units",
        "from kipy.proto.common.types.enums_pb2 import HorizontalAlignment, Units",
    )
    path.write_text(content, encoding="utf-8")


def main() -> None:
    """Build upstream bindings and verify that schematic imports are usable."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", type=Path, default=Path("out/kicad-ipc/venv"))
    args = parser.parse_args()
    target = args.venv.resolve()
    venv.EnvBuilder(with_pip=True).create(target)
    windows = os.name == "nt"
    scripts = target / ("Scripts" if windows else "bin")
    python = scripts / ("python.exe" if windows else "python")
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "poetry-core>=2,<3",
            "setuptools>=77",
            "protoletariat==3.3.*",
            "mypy-protobuf==3.6.*",
            "protoc-wheel-0==29",
        ],
        check=True,
    )
    # protoc-wheel's Windows Python launcher returns before the compiler exits.
    # Use the wheel's actual native executable for both upstream build stages.
    result = subprocess.run(
        [
            str(python),
            "-c",
            "import pathlib,protoc; "
            "print(pathlib.Path(protoc.__file__).parent/'data/bin')",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    native = Path(result.stdout.strip())
    environment = {
        **os.environ,
        "PATH": os.pathsep.join((str(native), str(scripts), os.environ["PATH"])),
        # protoletariat parses this value with POSIX shlex even on Windows.
        "PROTOC_PATH": '"'
        + (native / ("protoc.exe" if windows else "protoc")).as_posix()
        + '"',
    }
    with tempfile.TemporaryDirectory(prefix="kicad-flow-sdk-") as directory:
        source = Path(directory) / "sdk"
        download(
            SDK_URL,
            "7173969e08260c66c57bb000b723f6cbfbeaa589ab31a243aa91436a8590b0a2",
            source,
        )
        download(
            PROTO_URL,
            "322452056751f2ca22f77d47eb8a5f6703a39ae97764579304025074ab8f25ad",
            source / "kicad",
        )
        adapt_sdk(source)
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-build-isolation",
                str(source),
            ],
            env=environment,
            check=True,
        )
    subprocess.run(
        [
            str(python),
            "-c",
            "from kipy import KiCad; from kipy.schematic import Schematic; "
            "from kipy.schematic_types import SchematicSymbol; "
            "print('KiCad schematic IPC bindings ready')",
        ],
        check=True,
    )
    print(f"KICAD_FLOW_IPC_PYTHON={python}")


if __name__ == "__main__":
    main()
