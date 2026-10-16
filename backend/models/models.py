"""
Uygulama veri modelleri.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ColumnInfo:
    name: str
    data_type: str = ""
    nullable: bool = True

    def describe(self) -> str:
        suffix = "" if self.nullable else " NOT NULL"
        return f"{self.name} {self.data_type}{suffix}".strip()


@dataclass
class TableInfo:
    """Bir tablonun AI'a verilecek şema bilgisi."""
    name: str
    columns: List[ColumnInfo]
    primary_key: List[str] = field(default_factory=list)
    # (kolon, hedef_tablo, hedef_kolon)
    foreign_keys: List[tuple] = field(default_factory=list)

    @property
    def column_names(self) -> List[str]:
        return [c.name for c in self.columns]


@dataclass
class DatabaseSchema:
    tables: Dict[str, TableInfo]


@dataclass
class QueryRequest:
    question: str
    tables: List[str]

    def __post_init__(self):
        if not self.question.strip():
            raise ValueError("Question cannot be empty")
        if not self.tables:
            raise ValueError("At least one table must be specified")


@dataclass
class QueryResponse:
    """Salt-okunur sorgu çalıştırma sonucu."""
    sql_query: str
    results: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None
    truncated: bool = False

    @property
    def is_successful(self) -> bool:
        return self.error is None


@dataclass
class SavedQuery:
    id: Optional[int] = None
    question: str = ""
    sql_query: str = ""
    tables_used: List[str] = field(default_factory=list)
    created_at: Optional[datetime] = None
    is_successful: bool = True
    error_message: Optional[str] = None
    query_results: Optional[List[Dict[str, Any]]] = None
    result_message: Optional[str] = None
    verified: bool = False

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "sql_query": self.sql_query,
            "tables_used": self.tables_used,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "is_successful": self.is_successful,
            "error_message": self.error_message,
            "query_results": self.query_results,
            "result_message": self.result_message,
            "verified": self.verified,
            "is_scheduled": False,
        }
