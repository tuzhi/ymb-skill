"""标准化完整数据集 DTO；内部继续持有原 DataFrame 引用。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generic, Self, TypeVar


Money = int | float | Decimal | None
DateValue = str | date | datetime | None


@dataclass(frozen=True)
class DatasetRowDTO:
    """中文 DataFrame 行到英文 SDK 字段的只读映射基类。"""

    @classmethod
    def from_mapping(cls, source: Mapping[str, Any]) -> Self:
        values = {}
        for item in fields(cls):
            source_name = str(item.metadata.get("source") or item.name)
            values[item.name] = source.get(source_name, source.get(item.name))
        return cls(**values)


@dataclass(frozen=True)
class TransactionDTO(DatasetRowDTO):
    transaction_id: str | None = field(default=None, metadata={"source": "交易唯一编号"})
    client_name: str | None = field(default=None, metadata={"source": "客户名称"})
    account_type: str | None = field(default=None, metadata={"source": "账户类型"})
    owner_name: str | None = field(default=None, metadata={"source": "本方名称"})
    owner_account: str | None = field(default=None, metadata={"source": "本方账户"})
    bank_name: str | None = field(default=None, metadata={"source": "开户行"})
    transaction_time: DateValue = field(default=None, metadata={"source": "交易时间"})
    counterparty_name: str | None = field(default=None, metadata={"source": "对手名称"})
    counterparty_account: str | None = field(default=None, metadata={"source": "对手账户"})
    income_amount: Money = field(default=None, metadata={"source": "收入金额"})
    expense_amount: Money = field(default=None, metadata={"source": "支出金额"})
    transaction_amount: Money = field(default=None, metadata={"source": "交易金额"})
    analysis_income_amount: Money = field(default=None, metadata={"source": "分析收入金额"})
    analysis_expense_amount: Money = field(default=None, metadata={"source": "分析支出金额"})
    analysis_transaction_amount: Money = field(default=None, metadata={"source": "分析交易金额"})
    balance: Money = field(default=None, metadata={"source": "账户余额"})
    virtual_balance: Money = field(default=None, metadata={"source": "虚拟账户余额"})
    bank_memo: str | None = field(default=None, metadata={"source": "银行备注"})
    account_memo: str | None = field(default=None, metadata={"source": "账户方附言"})
    transaction_status: str | None = field(default=None, metadata={"source": "交易状态"})
    reversal_transaction_id: str | None = field(default=None, metadata={"source": "关联冲正交易编号"})
    direction: str | None = field(default=None, metadata={"source": "收支方向"})
    level_1_tag: str | None = field(default=None, metadata={"source": "一级标签"})
    level_2_tag: str | None = field(default=None, metadata={"source": "二级标签"})
    level_3_tag: str | None = field(default=None, metadata={"source": "三级标签"})
    tag_source: str | None = field(default=None, metadata={"source": "标签来源"})
    tag_confidence: float | None = field(default=None, metadata={"source": "标签置信度"})
    matched_keyword: str | None = field(default=None, metadata={"source": "命中关键词"})
    transaction_channel: str | None = field(default=None, metadata={"source": "交易渠道"})
    source_file_name: str | None = field(default=None, metadata={"source": "来源文件名"})
    source_row_number: int | str | None = field(default=None, metadata={"source": "来源行号"})


@dataclass(frozen=True)
class AccountBalanceDTO:
    """某日某个账户的日末余额。"""

    account: str
    balance: Money


@dataclass(frozen=True)
class DailyBalanceDTO(DatasetRowDTO):
    date: DateValue = field(default=None, metadata={"source": "日期"})
    accounts: tuple[AccountBalanceDTO, ...] = ()
    total_balance: Money = field(default=None, metadata={"source": "合计余额"})

    @classmethod
    def from_mapping(cls, source: Mapping[str, Any]) -> Self:
        configured = source.get("accounts", source.get("account_balances"))
        if isinstance(configured, Mapping):
            accounts = tuple(
                AccountBalanceDTO(account=str(account), balance=balance)
                for account, balance in configured.items()
            )
        elif configured is not None:
            accounts = tuple(
                item
                if isinstance(item, AccountBalanceDTO)
                else AccountBalanceDTO(
                    account=str(item.get("account") or ""),
                    balance=item.get("balance"),
                )
                for item in configured
                if isinstance(item, (AccountBalanceDTO, Mapping))
            )
        else:
            accounts = tuple(
                AccountBalanceDTO(account=str(key), balance=value)
                for key, value in source.items()
                if key not in {"日期", "合计余额", "date", "total_balance"}
            )
        return cls(
            date=source.get("日期", source.get("date")),
            accounts=accounts,
            total_balance=source.get("合计余额", source.get("total_balance")),
        )


@dataclass(frozen=True)
class AccountDTO(DatasetRowDTO):
    owner_name: str | None = field(default=None, metadata={"source": "本方名称"})
    account_type: str | None = field(default=None, metadata={"source": "账户类型"})
    bank_name: str | None = field(default=None, metadata={"source": "开户行"})
    owner_account: str | None = field(default=None, metadata={"source": "本方账户"})
    transaction_count: int | None = field(default=None, metadata={"source": "交易笔数"})
    total_inflow: Money = field(default=None, metadata={"source": "流入合计"})
    total_outflow: Money = field(default=None, metadata={"source": "流出合计"})
    opening_date: DateValue = field(default=None, metadata={"source": "期初日期"})
    closing_date: DateValue = field(default=None, metadata={"source": "期末日期"})
    source_files: str | None = field(default=None, metadata={"source": "来源文件"})


@dataclass(frozen=True)
class BalanceCheckDTO(DatasetRowDTO):
    account: str | None = field(default=None, metadata={"source": "账户"})
    transaction_count: int | None = field(default=None, metadata={"source": "交易数"})
    status: str | None = field(default=None, metadata={"source": "校验状态"})
    balance_breakpoint_count: int | None = field(default=None, metadata={"source": "余额断点"})
    breakpoint_rate: float | None = field(default=None, metadata={"source": "断点率"})
    suspected_incomplete_parse: bool | None = field(default=None, metadata={"source": "疑似解析不全"})
    breakpoint_examples: list[str] | None = field(default=None, metadata={"source": "断点交易示例"})
    closing_balance: Money = field(default=None, metadata={"source": "期末余额"})


@dataclass(frozen=True)
class TagSummaryDTO(DatasetRowDTO):
    direction: str | None = field(default=None, metadata={"source": "收支方向"})
    level_1_tag: str | None = field(default=None, metadata={"source": "一级标签"})
    level_2_tag: str | None = field(default=None, metadata={"source": "二级标签"})
    level_3_tag: str | None = field(default=None, metadata={"source": "三级标签"})
    transaction_count: int | None = field(default=None, metadata={"source": "笔数"})
    total_inflow: Money = field(default=None, metadata={"source": "收入合计"})
    total_outflow: Money = field(default=None, metadata={"source": "支出合计"})


@dataclass(frozen=True)
class ReviewItemDTO(DatasetRowDTO):
    item_type: str | None = field(default=None, metadata={"source": "事项类型"})
    reason: str | None = field(default=None, metadata={"source": "复核原因"})
    evidence_transaction_id: str | None = field(default=None, metadata={"source": "证据交易编号"})
    recommended_action: str | None = field(default=None, metadata={"source": "建议动作"})


RowDTO = TypeVar("RowDTO", bound=DatasetRowDTO)


@dataclass(frozen=True)
class DatasetTableDTO(Generic[RowDTO]):
    """持有原表引用，按需逐行产生 DTO，不常驻复制明细。"""

    frame: Any
    row_type: type[RowDTO]

    def __len__(self) -> int:
        try:
            return len(self.frame)
        except TypeError:
            return 0

    def __iter__(self) -> Iterator[RowDTO]:
        if hasattr(self.frame, "columns") and hasattr(self.frame, "itertuples"):
            columns = [str(column) for column in self.frame.columns]
            for values in self.frame.itertuples(index=False, name=None):
                yield self.row_type.from_mapping(dict(zip(columns, values)))
            return
        for value in self.frame or ():
            if isinstance(value, self.row_type):
                yield value
            elif isinstance(value, Mapping):
                yield self.row_type.from_mapping(value)


@dataclass(frozen=True)
class StandardizationDatasetDTO(Mapping[str, Any]):
    """完整数据集 DTO；Mapping 访问继续返回原表以兼容现有 BI。"""

    transactions: DatasetTableDTO[TransactionDTO]
    daily_balances: DatasetTableDTO[DailyBalanceDTO]
    accounts: DatasetTableDTO[AccountDTO]
    balance_checks: DatasetTableDTO[BalanceCheckDTO]
    tag_summaries: DatasetTableDTO[TagSummaryDTO]
    review_items: DatasetTableDTO[ReviewItemDTO]

    _ROW_TYPES = {
        "transactions": TransactionDTO,
        "daily_balances": DailyBalanceDTO,
        "accounts": AccountDTO,
        "balance_checks": BalanceCheckDTO,
        "tag_summaries": TagSummaryDTO,
        "review_items": ReviewItemDTO,
    }

    @classmethod
    def from_mapping(cls, source: Mapping[str, Any] | None) -> Self:
        source = source or {}
        return cls(**{
            name: (
                source.get(name)
                if isinstance(source.get(name), DatasetTableDTO)
                else DatasetTableDTO(source.get(name, ()), row_type)
            )
            for name, row_type in cls._ROW_TYPES.items()
        })

    def __getitem__(self, key: str) -> Any:
        if key not in self._ROW_TYPES:
            raise KeyError(key)
        return getattr(self, key).frame

    def __iter__(self) -> Iterator[str]:
        return iter(self._ROW_TYPES)

    def __len__(self) -> int:
        return len(self._ROW_TYPES)

    def __bool__(self) -> bool:
        return any(len(getattr(self, key)) for key in self._ROW_TYPES)

    def table(self, key: str) -> DatasetTableDTO[Any]:
        """返回带行 DTO 类型的表包装；普通 Mapping 访问仍返回原 DataFrame。"""
        if key not in self._ROW_TYPES:
            raise KeyError(key)
        return getattr(self, key)
