import pytest

from backend.models.models import ColumnInfo, DatabaseSchema, TableInfo
from backend.services.ai_service import AIServiceError, format_schema, parse_sql_answer


def test_parses_json_answer():
    raw = '{"sql": "SELECT 1", "explanation": "Bir döndürür."}'
    assert parse_sql_answer(raw) == {"sql": "SELECT 1", "explanation": "Bir döndürür."}


def test_parses_json_wrapped_in_text():
    raw = 'İşte sorgu:\n{"sql": "SELECT TOP 3 * FROM Orders", "explanation": "x"}\nUmarım yardımcı olur.'
    assert parse_sql_answer(raw)["sql"] == "SELECT TOP 3 * FROM Orders"


def test_parses_markdown_fence():
    raw = "```sql\nSELECT * FROM Orders\n```"
    assert parse_sql_answer(raw) == {"sql": "SELECT * FROM Orders", "explanation": ""}


def test_parses_bare_sql():
    assert parse_sql_answer("SELECT 1")["sql"] == "SELECT 1"


def test_empty_answer_raises():
    with pytest.raises(AIServiceError):
        parse_sql_answer("   ")


def test_schema_formatting_includes_types_pk_and_fk():
    orders = TableInfo(
        name="Orders",
        columns=[ColumnInfo("OrderID", "int", False), ColumnInfo("CustomerID", "nchar(5)")],
        primary_key=["OrderID"],
        foreign_keys=[("CustomerID", "Customers", "CustomerID")],
    )
    text = format_schema(DatabaseSchema(tables={"Orders": orders}))
    assert "TABLE Orders (OrderID int PK, CustomerID nchar(5))" in text
    assert "FK Orders.CustomerID -> Customers.CustomerID" in text
