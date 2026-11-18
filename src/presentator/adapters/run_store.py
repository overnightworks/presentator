"""The runs of the build in the object store every process of the instance shares.

A run is two objects under its name: an archive of its talk and the deck text it
was built from, and its PDF. The PDF is written first, so a run whose archive
stands is whole. boto3 reads the endpoint, the region and the key from the
environment the platform gives the web; the bucket is the one setting of this
instance, and the store is addressed by path, as cluster DNS resolves no bucket
subdomain.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

if TYPE_CHECKING:
    # The stubs are a development dependency; the image carries boto3 alone.
    from types_boto3_s3 import S3Client

_RUNS: Final = "runs/"
_ARCHIVE: Final = "talk.tar"
_PDF: Final = "deck.pdf"
_FAILED_EVENT: Final = "object_store.failed"
# What a missing object answers, as opposed to a store that did not answer.
_ABSENT: Final = frozenset({"404", "NoSuchKey"})

_log = logging.getLogger(__name__)


def object_store_client() -> S3Client:
    """The one client of this process, configured by the environment alone."""
    client: S3Client = boto3.client(
        "s3", config=Config(s3={"addressing_style": "path"})
    )
    return client


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredRuns:
    """The archives and PDFs of the runs, one pair of objects per run."""

    client: S3Client
    bucket: str

    def put(self, run: str, *, archive: Path, pdf: Path) -> bool:
        """Keep that run's archive and PDF, and say whether both were written."""
        try:
            self.client.upload_file(str(pdf), self.bucket, _key(run, _PDF))
            self.client.upload_file(str(archive), self.bucket, _key(run, _ARCHIVE))
        except (BotoCoreError, ClientError, OSError) as failure:
            _failed("put", failure)
            return False
        return True

    def fetch(self, run: str, *, archive: Path, pdf: Path) -> bool:
        """Write that run's archive and PDF to these files, or say it could not."""
        try:
            self.client.download_file(self.bucket, _key(run, _ARCHIVE), str(archive))
            self.client.download_file(self.bucket, _key(run, _PDF), str(pdf))
        except ClientError as failure:
            if _error_code(failure) not in _ABSENT:
                _failed("fetch", failure)
            return False
        except (BotoCoreError, OSError) as failure:
            _failed("fetch", failure)
            return False
        return True

    def names(self) -> frozenset[str] | None:
        """The name of every run with an object in the store, or nothing unlisted."""
        names: set[str] = set()
        try:
            pages = self.client.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=_RUNS
            )
            for page in pages:
                for listed in page.get("Contents", []):
                    key = listed.get("Key", "").removeprefix(_RUNS)
                    names.add(key.partition("/")[0])
        except (BotoCoreError, ClientError) as failure:
            _failed("list", failure)
            return None
        return frozenset(names)

    def delete(self, run: str) -> bool:
        """Delete both objects of that run, and say whether the store did."""
        try:
            for kind in (_ARCHIVE, _PDF):
                self.client.delete_object(Bucket=self.bucket, Key=_key(run, kind))
        except (BotoCoreError, ClientError) as failure:
            _failed("delete", failure)
            return False
        return True


def _key(run: str, kind: str) -> str:
    return f"{_RUNS}{run}/{kind}"


def _error_code(failure: ClientError) -> str:
    return str(failure.response.get("Error", {}).get("Code", ""))


def _failed(operation: str, failure: Exception) -> None:
    """One warning per failed call; what the store said may name the bucket only."""
    _log.warning(
        _FAILED_EVENT,
        extra={"operation": operation, "error_type": type(failure).__name__},
    )
