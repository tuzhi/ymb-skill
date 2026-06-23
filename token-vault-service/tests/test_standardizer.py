from __future__ import annotations

from pathlib import Path

from token_vault_service.standardization_adapter import BundledStandardizationAdapter


def test_standardization_adapter_calls_bundled_standardize_function(tmp_path: Path):
    def fake_standardize(path: str, out_dir: str):
        del path
        output_dir = Path(out_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "result__standardized.csv"
        output_path.write_text("姓名\n张三\n", encoding="utf-8-sig")
        return (
            str(output_path),
            str(output_dir / "result__mapping.json"),
            {"标准化统计": {"交易笔数": 1}},
        )

    input_path = tmp_path / "raw.csv"
    input_path.write_text("raw", encoding="utf-8")

    result = BundledStandardizationAdapter(
        standardize_func=fake_standardize,
    ).standardize(
        input_path=input_path,
        work_dir=tmp_path / "work",
    )

    assert result.ok is True
    assert result.standardized_path is not None
    assert result.standardized_path.name == "result__standardized.csv"
    assert result.summary["rows"] == 1
