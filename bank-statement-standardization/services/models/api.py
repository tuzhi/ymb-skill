"""流水标准化 Service 对外请求与结果 DTO。"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date, datetime
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
import zipfile


@dataclass(frozen=True)
class ServiceError:
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InputFile:
    """标准化服务可读取的单个输入文件。"""

    file_name: str
    file_path: str
    file_md5: str = ""


@dataclass(frozen=True)
class StandardizationRequest:
    """同步标准化请求；Run ID 仍由 Python Runner 生成。"""

    client_name: str | None
    files: tuple[InputFile, ...]
    parent_run_id: str | None = None
    remove_file_ids: tuple[str, ...] = ()
    file_passwords: Mapping[str, str] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class StandardizationResult:
    """同步标准化的唯一公开结果；dataset 在进程内可直接持有 DataFrame。"""

    run_id: str
    status: str
    next_action: str
    message: str
    client: Mapping[str, Any]
    rule_snapshot: Mapping[str, Any]
    summary: Mapping[str, Any]
    file_results: list[dict[str, Any]]
    stages: Mapping[str, Any]
    qc_client: Mapping[str, Any]
    business_summary: Mapping[str, Any]
    dataset: Mapping[str, Any]
    deliverable: Mapping[str, Any]

    def to_summary_dict(self) -> dict[str, Any]:
        """返回不含 dataset 的轻量结果，供状态返回、日志和数据库主表使用。"""
        return {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if item.name != "dataset"
        }

    @staticmethod
    def _json_scalar(value: Any) -> Any:
        """把单个 DataFrame 单元转换为标准 JSON 标量，不复制整张表。"""
        if value is None:
            return None
        try:
            import pandas as pd

            missing = pd.isna(value)
            if bool(missing):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, os.PathLike):
            return os.fspath(value)
        if hasattr(value, "item"):
            try:
                return value.item()
            except (TypeError, ValueError):
                pass
        return value

    @classmethod
    def _json_default(cls, value: Any) -> Any:
        normalized = cls._json_scalar(value)
        if normalized is value:
            raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
        return normalized

    @staticmethod
    def _write_chunks(stream: io.BufferedWriter, chunks: Any) -> None:
        for chunk in chunks:
            stream.write(chunk.encode("utf-8"))

    @classmethod
    def _write_dataframe(cls, stream: io.BufferedWriter, frame: Any, encoder: json.JSONEncoder) -> None:
        columns = [str(column) for column in frame.columns]
        stream.write(b"[")
        first = True
        for values in frame.itertuples(index=False, name=None):
            if first:
                first = False
            else:
                stream.write(b",")
            row = {
                column: cls._json_scalar(value)
                for column, value in zip(columns, values)
            }
            cls._write_chunks(stream, encoder.iterencode(row))
        stream.write(b"]")

    def write_zip(
        self,
        path: str | os.PathLike[str],
        *,
        member_name: str = "standardization_result.json",
    ) -> str:
        """把完整 DTO 逐行编码并直接压缩到 ZIP，不创建完整 dict 或 JSON 字符串。"""
        if not member_name or Path(member_name).name != member_name:
            raise ValueError("member_name 必须是 ZIP 根目录下的单个文件名")
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        os.close(descriptor)
        encoder = json.JSONEncoder(
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            default=self._json_default,
        )
        try:
            with zipfile.ZipFile(
                temporary,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                allowZip64=True,
            ) as archive:
                with archive.open(member_name, mode="w", force_zip64=True) as raw:
                    with io.BufferedWriter(raw, buffer_size=1024 * 1024) as stream:
                        stream.write(b"{")
                        first_field = True
                        for item in fields(self):
                            if first_field:
                                first_field = False
                            else:
                                stream.write(b",")
                            self._write_chunks(stream, encoder.iterencode(item.name))
                            stream.write(b":")
                            value = getattr(self, item.name)
                            if item.name != "dataset":
                                self._write_chunks(stream, encoder.iterencode(value))
                                continue
                            stream.write(b"{")
                            first_dataset = True
                            for key, frame in value.items():
                                if first_dataset:
                                    first_dataset = False
                                else:
                                    stream.write(b",")
                                self._write_chunks(stream, encoder.iterencode(str(key)))
                                stream.write(b":")
                                if hasattr(frame, "columns") and hasattr(frame, "itertuples"):
                                    self._write_dataframe(stream, frame, encoder)
                                else:
                                    self._write_chunks(stream, encoder.iterencode(frame))
                            stream.write(b"}")
                        stream.write(b"}")
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return str(destination)
