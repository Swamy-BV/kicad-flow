"""Install the pinned official KiCad 11 development bindings in an isolated venv.

The released kicad-python 0.8 package predates schematic IPC. This bootstrap
builds the upstream SDK without modifying its sources; remove it once a release
includes the schematic protocol. It does not install or configure KiCad itself.
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
PROTOCOL = "dcadf9baa30d43d7d1b3d0342345f05ec0da26e1"
SDK_URL = (
    f"https://gitlab.com/kicad/code/kicad-python/-/archive/{SDK}/"
    f"kicad-python-{SDK}.tar.gz"
)
PROTO_URL = (
    f"https://gitlab.com/kicad/code/kicad/-/archive/{PROTOCOL}/"
    f"kicad-{PROTOCOL}.tar.gz?path=api/proto"
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
            "bf9ffee0c2895bb6cdc26278c00b6aad0d50466fe7933e25392246f223acf78a",
            source / "kicad",
        )
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
