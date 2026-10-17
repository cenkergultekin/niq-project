"""Kullanıcının deneyebileceği, yalnızca sentetik verili yerel SQLite örneği."""
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from backend.config.config import data_dir


def demo_database() -> Path:
    path = data_dir() / "ornek-satislar.sqlite"
    if path.exists():
        return path
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, city TEXT);
            CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL);
            CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id),
                                order_date TEXT, status TEXT);
            CREATE TABLE order_items(id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id),
                                     product_id INTEGER REFERENCES products(id), quantity INTEGER, unit_price REAL);
        """)
        conn.executemany("INSERT INTO customers VALUES(?,?,?)", [
            (1, "Ada Market", "İzmit"), (2, "Mavi Ofis", "İstanbul"), (3, "Yeşil Kafe", "Ankara"),
            (4, "Kuzey Kitap", "İzmir"), (5, "Güneş Atölye", "İzmit"), (6, "Pera Tasarım", "Bursa")])
        products = [(1, "Defter", "Kırtasiye", 85), (2, "Kalem seti", "Kırtasiye", 120),
                    (3, "Masa lambası", "Ofis", 650), (4, "Termos", "Yaşam", 480),
                    (5, "Klavye", "Teknoloji", 1250), (6, "Kulaklık", "Teknoloji", 950)]
        conn.executemany("INSERT INTO products VALUES(?,?,?,?)", products)
        for order_id in range(1, 61):
            day = date.today() - timedelta(days=(order_id * 7) % 90)
            status = "İade" if order_id % 11 == 0 else "Bekliyor" if order_id % 9 == 0 else "Tamamlandı"
            conn.execute("INSERT INTO orders VALUES(?,?,?,?)", (order_id, order_id % 6 + 1, day.isoformat(), status))
            for index in range(2):
                product = products[(order_id + index) % len(products)]
                conn.execute("INSERT INTO order_items VALUES(?,?,?,?,?)",
                             (order_id * 2 + index, order_id, product[0], (order_id + index) % 4 + 1, product[3]))
    return path
