from __future__ import annotations

from pathlib import Path

import tomli

from token_vault_service.config import STANDARDIZATION_MODULE, Settings


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_service_uses_token_vault_package_name_and_does_not_depend_on_external_runtime() -> None:
    pyproject = tomli.loads((project_root() / "pyproject.toml").read_text("utf-8"))

    dependencies = pyproject["project"]["dependencies"]

    assert pyproject["project"]["name"] == "token-vault-service"
    assert any(
        dependency.startswith("ymb-standardization-core @ file:///D:/PYTHON_WORK/ymb-skill/packages/ymb_standardization_core")
        for dependency in dependencies
    )
    assert all("privacy_filter" not in dependency.lower() for dependency in dependencies)
    assert "python-multipart>=0.0.9" in dependencies
    assert "eval-type-backport>=0.2" in dependencies
    assert "openpyxl>=3.1.5,<4" in dependencies


def test_project_is_constrained_to_python_3_11_runtime() -> None:
    pyproject = tomli.loads((project_root() / "pyproject.toml").read_text("utf-8"))

    dependencies = pyproject["project"]["dependencies"]

    assert pyproject["project"]["requires-python"] == ">=3.11,<3.12"
    assert "pandas>=2.2.3,<3" in dependencies
    assert "tomli>=2.0" in pyproject["project"]["optional-dependencies"]["test"]


def test_default_standardizer_uses_shared_core_package() -> None:
    assert Settings().standardization_module == STANDARDIZATION_MODULE
    assert STANDARDIZATION_MODULE == "ymb_standardization_core"


def test_test_extra_includes_build_tool() -> None:
    pyproject = tomli.loads((project_root() / "pyproject.toml").read_text("utf-8"))

    test_dependencies = pyproject["project"]["optional-dependencies"]["test"]

    assert "build>=1.2" in test_dependencies


def test_person_ner_extra_keeps_hanlp_optional() -> None:
    pyproject = tomli.loads((project_root() / "pyproject.toml").read_text("utf-8"))

    dependencies = pyproject["project"]["dependencies"]
    person_ner_dependencies = pyproject["project"]["optional-dependencies"]["person-ner"]

    assert all("hanlp" not in dependency.lower() for dependency in dependencies)
    assert "hanlp>=2.1,<3" in person_ner_dependencies


