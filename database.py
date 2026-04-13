import sqlite3
import os
import sys
import time


def _get_app_dir():
    """获取应用运行目录（兼容 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _get_data_dir():
    """获取数据目录（Docker 容器内用 /app/data，否则用应用目录）"""
    data_dir = os.path.join(_get_app_dir(), "data")
    if not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)
    return data_dir


DB_PATH = os.path.join(_get_data_dir(), "polymarket.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS auto_sell_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT NOT NULL,
            market_name TEXT NOT NULL DEFAULT '',
            outcome TEXT NOT NULL DEFAULT '',
            rule_type TEXT NOT NULL CHECK(rule_type IN ('stop_loss', 'take_profit')),
            threshold REAL NOT NULL,
            sell_percent REAL NOT NULL CHECK(sell_percent > 0 AND sell_percent <= 100),
            price_offset REAL NOT NULL DEFAULT 0,
            sell_mode TEXT NOT NULL DEFAULT 'limit' CHECK(sell_mode IN ('limit', 'market')),
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            triggered_at REAL
        );

        CREATE TABLE IF NOT EXISTS execution_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER,
            token_id TEXT NOT NULL,
            market_name TEXT NOT NULL DEFAULT '',
            rule_type TEXT NOT NULL,
            threshold REAL NOT NULL,
            trigger_price REAL NOT NULL,
            sell_percent REAL NOT NULL,
            sell_amount REAL,
            status TEXT NOT NULL DEFAULT 'pending',
            message TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY (rule_id) REFERENCES auto_sell_rules(id)
        );
    """)
    conn.commit()
    conn.close()


# --- 规则 CRUD ---

def get_all_rules():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM auto_sell_rules ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_enabled_rules():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM auto_sell_rules WHERE enabled = 1"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_rule(token_id, market_name, outcome, rule_type, threshold, sell_percent, price_offset=0, sell_mode='limit'):
    conn = get_connection()
    conn.execute(
        """INSERT INTO auto_sell_rules
           (token_id, market_name, outcome, rule_type, threshold, sell_percent, price_offset, sell_mode, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (token_id, market_name, outcome, rule_type, threshold, sell_percent, price_offset, sell_mode, time.time()),
    )
    conn.commit()
    conn.close()


def toggle_rule(rule_id, enabled):
    conn = get_connection()
    conn.execute(
        "UPDATE auto_sell_rules SET enabled = ? WHERE id = ?",
        (1 if enabled else 0, rule_id),
    )
    conn.commit()
    conn.close()


def delete_rule(rule_id):
    conn = get_connection()
    conn.execute("DELETE FROM auto_sell_rules WHERE id = ?", (rule_id,))
    conn.commit()
    conn.close()


def disable_rule(rule_id):
    """触发后自动禁用规则"""
    conn = get_connection()
    conn.execute(
        "UPDATE auto_sell_rules SET enabled = 0, triggered_at = ? WHERE id = ?",
        (time.time(), rule_id),
    )
    conn.commit()
    conn.close()


# --- 执行日志 ---

def add_log(rule_id, token_id, market_name, rule_type, threshold,
            trigger_price, sell_percent, sell_amount, status, message):
    conn = get_connection()
    conn.execute(
        """INSERT INTO execution_log
           (rule_id, token_id, market_name, rule_type, threshold,
            trigger_price, sell_percent, sell_amount, status, message, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (rule_id, token_id, market_name, rule_type, threshold,
         trigger_price, sell_percent, sell_amount, status, message, time.time()),
    )
    conn.commit()
    conn.close()


def get_logs(limit=100):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM execution_log ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
