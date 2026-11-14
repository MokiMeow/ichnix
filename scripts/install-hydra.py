#!/usr/bin/env python3
"""Install a pinned upstream HydraDB image's runtime files for Ubuntu 24.04 amd64."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import tarfile
import tempfile
from urllib.request import HTTPRedirectHandler, Request, build_opener

IMAGE = "hydra-db/hydradb"
INDEX = "sha256:ab71d651fefee13ebb23eb56d72316065bdca563ff7587442e07be663f2bf880"
REGISTRY = "https://ghcr.io/v2/" + IMAGE
DEST = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ichnix-runtime"
FILES = {"usr/local/bin/graph-node"}
LIBS = {"libgraphblas.so.7", "libgraphblas.so.7.4.0", "libcypher-parser.so.8",
        "libcypher-parser.so.8.2.2", "libgomp.so.1", "libgomp.so.1.0.0"}
FILES.update("usr/lib/x86_64-linux-gnu/" + lib for lib in LIBS)


class RegistryRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirect = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirect:
            redirect.remove_header("Authorization")
        return redirect


def main():
    if platform.machine() != "x86_64" or platform.system() != "Linux":
        raise SystemExit("this installer supports Ubuntu 24.04 amd64; supply HYDRA_BINARY for other hosts")
    opener = build_opener(RegistryRedirect())
    auth_url = "https://ghcr.io/token?scope=repository:" + IMAGE + ":pull"
    with opener.open(auth_url, timeout=30) as response:
        token = json.load(response)["token"]

    def get_verified(kind, digest, output):
        request = Request(f"{REGISTRY}/{kind}/{digest}",
                          headers={"Authorization": "Bearer " + token,
                                   "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json"})
        actual = hashlib.sha256()
        with opener.open(request, timeout=120) as response:
            while chunk := response.read(1024 * 1024):
                actual.update(chunk)
                output.write(chunk)
        if "sha256:" + actual.hexdigest() != digest:
            raise ValueError("upstream digest mismatch")
        output.seek(0)

    def manifest(digest):
        with tempfile.TemporaryFile() as stream:
            get_verified("manifests", digest, stream)
            return json.load(stream)

    arch = next(m for m in manifest(INDEX)["manifests"]
                if m.get("platform") == {"architecture": "amd64", "os": "linux"})
    DEST.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DEST) as temp:
        root = Path(temp)
        for layer in manifest(arch["digest"])["layers"]:
            with tempfile.TemporaryFile() as stream:
                get_verified("blobs", layer["digest"], stream)
                with tarfile.open(fileobj=stream) as archive:
                    for entry in archive:
                        if entry.name.removeprefix("./") not in FILES:
                            continue
                        path = root / PurePosixPath(entry.name).name
                        if entry.isfile():
                            with archive.extractfile(entry) as source, path.open("wb") as out:
                                shutil.copyfileobj(source, out)
                            path.chmod(0o755)
                        elif entry.issym() and entry.linkname in LIBS:
                            path.symlink_to(entry.linkname)
        for name in {"graph-node", *LIBS}:
            if not (root / name).is_file():
                raise RuntimeError(f"pinned image missing {name}")
        for path in root.iterdir():
            path.replace(DEST / path.name)
    print(f"installed HydraDB v0.4.0 runtime in {DEST}")


if __name__ == "__main__":
    main()
