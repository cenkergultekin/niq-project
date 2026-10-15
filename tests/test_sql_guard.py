import pytest

from backend.core.sql_guard import validate_sql

ALLOWED = ["Orders", "Order Details", "Customers", "Sales.Invoices"]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM Orders",
    "SELECT TOP 5 * FROM dbo.Orders",
    "select o.OrderID, c.CompanyName from Orders o join Customers c on o.CustomerID = c.CustomerID",
    "SELECT * FROM [Order Details]",
    "WITH recent AS (SELECT * FROM Orders) SELECT * FROM recent",
    "SELECT CustomerID FROM Orders UNION SELECT CustomerID FROM Customers",
    "SELECT * FROM Sales.Invoices",
    "SELECT * FROM Orders;",
    "SELECT COUNT(*) AS n FROM Orders WHERE OrderDate >= '2020-01-01'",
    "SELECT * FROM Orders -- aciklama",
])
def test_allows_read_only_selects(sql):
    result = validate_sql(sql, allowed_tables=ALLOWED)
    assert result.ok, result.error


@pytest.mark.parametrize("sql", [
    "DROP TABLE Orders",
    "DELETE FROM Orders",
    "UPDATE Orders SET Freight = 0",
    "INSERT INTO Orders (OrderID) VALUES (1)",
    "SELECT * FROM Orders; DROP TABLE Orders",
    "SELECT * FROM Orders; SELECT * FROM Customers",
    "SELECT * INTO NewTable FROM Orders",
    "EXEC sp_who",
    "EXEC xp_cmdshell 'dir'",
    "SELECT * FROM OPENROWSET('SQLNCLI', 'Server=x;', 'SELECT 1')",
    "WAITFOR DELAY '0:0:30'",
    "TRUNCATE TABLE Orders",
    "SELECT * FROM master.dbo.sysdatabases",
    "SELECT * FROM sys.objects",
    "",
    "   ",
])
def test_rejects_dangerous_or_non_select(sql):
    result = validate_sql(sql, allowed_tables=ALLOWED)
    assert not result.ok


def test_rejects_unselected_tables():
    result = validate_sql("SELECT * FROM Employees", allowed_tables=ALLOWED)
    assert not result.ok
    assert "employees" in result.error.lower()


def test_rejects_join_to_unselected_table():
    result = validate_sql("SELECT * FROM Orders o JOIN Employees e ON o.EmployeeID = e.EmployeeID", allowed_tables=ALLOWED)
    assert not result.ok


def test_cte_names_do_not_count_as_tables():
    assert validate_sql("WITH x AS (SELECT 1 AS a) SELECT * FROM x", allowed_tables=ALLOWED).ok


def test_column_named_like_dangerous_word_is_fine():
    # Eski metin taraması 'SP_' / 'CREATE' içeren kolon adlarını yanlışlıkla reddediyordu
    assert validate_sql("SELECT created_at, sp_code FROM Orders", allowed_tables=["Orders"]).ok


def test_without_allowlist_still_blocks_writes():
    assert not validate_sql("DELETE FROM Orders").ok
    assert validate_sql("SELECT 1").ok


@pytest.mark.parametrize("dialect, sql", [
    ("postgresql", 'SELECT "Orders".* FROM public."Orders" LIMIT 5'),
    ("postgresql", "SELECT o.id FROM orders o WHERE o.name ILIKE '%a%'"),
    ("mysql", "SELECT `id` FROM `orders` WHERE x = 1 LIMIT 5"),
    ("mysql", "SELECT id FROM shop.orders"),
    ("sqlite", 'SELECT * FROM "orders" LIMIT 5'),
])
def test_dialect_selects_allowed(dialect, sql):
    result = validate_sql(sql, allowed_tables=["orders", "Orders"], dialect_id=dialect, database="shop")
    assert result.ok, result.error


@pytest.mark.parametrize("dialect, sql", [
    ("postgresql", "SELECT pg_sleep(30) FROM orders"),
    ("postgresql", "SELECT pg_read_file('/etc/passwd') FROM orders"),
    ("postgresql", "SELECT * FROM orders FOR UPDATE"),
    ("postgresql", "COPY orders TO '/tmp/x'"),
    ("postgresql", "SELECT * FROM pg_catalog.pg_user"),
    ("postgresql", "SELECT * FROM generate_series(1, 100000000)"),
    ("mysql", "SELECT load_file('/etc/passwd') FROM orders"),
    ("mysql", "SELECT SLEEP(10) FROM orders"),
    ("mysql", "SELECT * FROM orders INTO OUTFILE '/tmp/x'"),
    ("mysql", "SELECT * FROM otherdb.orders"),
    ("mysql", "DROP TABLE orders"),
    ("sqlite", "ATTACH DATABASE '/tmp/x.db' AS x"),
    ("sqlite", "SELECT load_extension('evil') FROM orders"),
    ("sqlite", "PRAGMA writable_schema = 1"),
])
def test_dialect_dangerous_rejected(dialect, sql):
    assert not validate_sql(sql, allowed_tables=["orders"], dialect_id=dialect, database="shop").ok
