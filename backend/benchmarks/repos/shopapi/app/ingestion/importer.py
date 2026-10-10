"""Bring external catalogue repositories into the shop: clone or unpack, then scan files."""
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

SUPPORTED_SUFFIXES = {".csv", ".json"}


class RepositoryImporter:
    def __init__(self, workspace: Path):
        self.workspace = workspace

    def clone_repository(self, url: str) -> Path:
        target = Path(tempfile.mkdtemp(dir=self.workspace))
        subprocess.run(["git", "clone", "--depth", "1", url, str(target)], check=True)
        return target

    def extract_archive(self, archive_path: Path) -> Path:
        target = Path(tempfile.mkdtemp(dir=self.workspace))
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.namelist():
                if member.startswith("/") or ".." in Path(member).parts:
                    raise ValueError(f"unsafe archive member: {member}")
            archive.extractall(target)
        return target

    def scan_files(self, root: Path) -> list[Path]:
        return sorted(p for p in root.rglob("*") if p.suffix in SUPPORTED_SUFFIXES)

    def cleanup(self, root: Path) -> None:
        shutil.rmtree(root, ignore_errors=True)
