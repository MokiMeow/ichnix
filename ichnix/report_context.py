"""Private, bounded Markdown exports and optional S3-compatible report archives."""

from contextlib import closing
from io import BytesIO
from zipfile import ZipFile
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

REPORT_FIELDS = ("id", "title", "service", "component", "revision", "owner")
MAX_DOCUMENT = 8 * 1024 * 1024
MAX_MARKDOWN = 256 * 1024


def report_readme(report):
    # Literal values in a Markdown code block keep markup and links inert.
    lines = [f"    {field}: {json.dumps(report[field], ensure_ascii=True)}" for field in REPORT_FIELDS]
    return "# Report context\n\nUntrusted intake metadata\n\n" + "\n".join(lines) + "\n"


def archive_readme(data):
    """Conditionally put a content-addressed object before publishing the graph case."""
    bucket = os.environ["ICHNIX_ARCHIVE_BUCKET"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise RuntimeError("invalid archive bucket configuration")
    endpoint = os.getenv("ICHNIX_ARCHIVE_ENDPOINT") or None
    if endpoint:
        url = urlsplit(endpoint)
        loopback = url.hostname in ("127.0.0.1", "::1", "localhost")
        if (
            not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path not in ("", "/")
            or not (url.scheme == "https" or loopback and url.scheme == "http")
        ):
            raise RuntimeError("invalid archive endpoint configuration")
    try:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        raise RuntimeError("install the optional report dependencies") from None

    config = Config(
        signature_version="s3v4",
        ignore_configured_endpoint_urls=True,
        connect_timeout=3,
        read_timeout=5,
        retries={"mode": "standard", "total_max_attempts": 2},
        request_checksum_calculation="when_required",
    )
    try:
        # A session per operation avoids sharing a mutable boto3 session across HTTP threads.
        with closing(boto3.Session().client("s3", endpoint_url=endpoint, config=config)) as client:
            client.put_object(
                Bucket=bucket,
                Key="reports/" + hashlib.sha256(data).hexdigest() + "/README.md",
                Body=data,
                ContentType="text/markdown; charset=utf-8",
                IfNoneMatch="*",
            )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "PreconditionFailed":
            raise RuntimeError("report archive unavailable") from None
    except BotoCoreError:
        raise RuntimeError("report archive unavailable") from None


def private_export(path, data):
    # Linking a complete 0600 temporary file also rejects pre-existing files and symlinks.
    with NamedTemporaryFile(dir=path.parent) as temp:
        temp.write(data)
        temp.flush()
        os.fsync(temp.fileno())
        os.link(temp.name, path)


def convert_document(path, *, bundle=False):
    """Run AnyDoc offline with a private environment and hard limits on the Linux host."""
    if sys.platform != "linux":
        raise RuntimeError("use the Linux workspace or its container for document conversion")
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("expected a regular document")
        data = stream.read(MAX_DOCUMENT + 1)
    if not 0 < len(data) <= MAX_DOCUMENT:
        raise ValueError("document exceeds intake limit")
    try:
        child = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).resolve()), path.suffix.lower()],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={},
            timeout=15,
            check=True,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        raise ValueError("local document conversion failed") from None
    if not bundle:
        return child.stdout
    # Preserve the same source bytes that were converted, including embedded images.
    suffix = path.suffix.lower()
    if suffix not in {".pdf", ".docx", ".pptx", ".xlsx", ".odt", ".ods", ".odp", ".rtf", ".epub", ".csv"}:
        suffix = ".bin"
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        output.writestr("README.md", child.stdout)
        output.writestr("original" + suffix, data)
    return archive.getvalue()


if __name__ == "__main__":
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    import anydoc

    data = sys.stdin.buffer.read(MAX_DOCUMENT + 1)
    if not 0 < len(data) <= MAX_DOCUMENT:
        sys.exit(1)
    fmt = "csv" if sys.argv[1] == ".csv" else None
    markdown = anydoc.to_markdown_bytes(data, fmt, ocr="reject").encode()
    if not markdown.strip() or len(markdown) > MAX_MARKDOWN:
        sys.exit(1)
    sys.stdout.buffer.write(markdown)
