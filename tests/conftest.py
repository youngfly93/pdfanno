"""Session-scoped fixtures 入口。

每个 pytest session 在临时目录构造一套 PDF fixture，测试只读取、不修改；
任何要修改的测试都应当先 copy 到各自的 tmp_path。

sidecar DB 通过 autouse fixture 隔离到每个测试的 tmp_path —— 测试之间不共享状态。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures import _build


@pytest.fixture(autouse=True)
def isolated_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """每个测试拿一个独立 sidecar DB，避免跨测试污染。"""

    sidecar_path = tmp_path / "sidecar.sqlite"
    monkeypatch.setenv("PDFANNO_SIDECAR_PATH", str(sidecar_path))
    return sidecar_path


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("pdfanno-fixtures")
    _build.build_all(base)
    return base


@pytest.fixture(scope="session")
def simple_pdf(fixtures_dir: Path) -> Path:
    return fixtures_dir / "simple.pdf"


@pytest.fixture(scope="session")
def existing_annotations_pdf(fixtures_dir: Path) -> Path:
    return fixtures_dir / "existing_annotations.pdf"


@pytest.fixture(scope="session")
def rotated_90_pdf(fixtures_dir: Path) -> Path:
    return fixtures_dir / "rotated_90.pdf"


@pytest.fixture(scope="session")
def rotated_270_pdf(fixtures_dir: Path) -> Path:
    return fixtures_dir / "rotated_270.pdf"


@pytest.fixture(scope="session")
def two_columns_pdf(fixtures_dir: Path) -> Path:
    return fixtures_dir / "two_columns.pdf"


@pytest.fixture(scope="session")
def scanned_no_text_pdf(fixtures_dir: Path) -> Path:
    return fixtures_dir / "scanned_no_text.pdf"


@pytest.fixture(scope="session")
def encrypted_pdf(fixtures_dir: Path) -> Path:
    return fixtures_dir / "encrypted.pdf"
