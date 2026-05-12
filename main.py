import logging
import logging.handlers
import os
import sys
import time
from collections import deque
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session

import config
import database as db
from monitor import PriceMonitor
import polymarket_client as pm


system_logs = deque(maxlen=500)


class MemoryLogHandler(logging.Handler):
    def emit(self, record):
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + logging.Formatter().formatException(record.exc_info)
        system_logs.append({
            'time': datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S'),
            'level': record.levelname.lower(),
            'message': message,
        })


def resource_path(relative_path):
    """获取资源文件路径（兼容 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def _get_log_dir():
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base, "data", "logs")
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


_log_format = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
_log_formatter = logging.Formatter(_log_format)
logging.basicConfig(level=logging.INFO, format=_log_format)

LOG_ROTATE_CHOICES = {
    "H": ("每小时", "%Y-%m-%d_%H"),
    "6H": ("每 6 小时", "%Y-%m-%d_%H"),
    "12H": ("每 12 小时", "%Y-%m-%d_%H"),
    "midnight": ("每天（午夜）", "%Y-%m-%d"),
    "W0": ("每周（周一）", "%Y-%m-%d"),
}

_file_handler = None


def _build_file_handler(when, backup_count):
    interval = 1
    suffix = "%Y-%m-%d"
    w = when.upper()
    if w == "6H":
        base_when, interval = "H", 6
    elif w == "12H":
        base_when, interval = "H", 12
    elif w == "H":
        base_when, interval, suffix = "H", 1, "%Y-%m-%d_%H"
    elif w == "W0":
        base_when = "W0"
    else:
        base_when = "midnight"

    if base_when == "H":
        suffix = "%Y-%m-%d_%H"

    handler = logging.handlers.TimedRotatingFileHandler(
        os.path.join(_get_log_dir(), "app.log"),
        when=base_when,
        interval=interval,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(_log_formatter)
    handler.suffix = suffix
    return handler


def reconfigure_file_handler(when=None, backup_count=None):
    global _file_handler
    when = when or config.LOG_ROTATE_WHEN
    backup_count = backup_count if backup_count is not None else config.LOG_BACKUP_COUNT
    root = logging.getLogger()
    if _file_handler is not None:
        root.removeHandler(_file_handler)
        _file_handler.close()
    _file_handler = _build_file_handler(when, backup_count)
    root.addHandler(_file_handler)


reconfigure_file_handler()

memory_handler = MemoryLogHandler()
memory_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(memory_handler)

app = Flask(
    __name__,
    template_folder=resource_path('templates'),
    static_folder=resource_path('static'),
)
app.secret_key = "polymarket-auto-trade-local-only"

# 全局监控引擎
monitor = PriceMonitor(interval=config.MONITOR_INTERVAL)


def require_auth():
    """检查是否需要密码验证"""
    if not config.WEB_PASSWORD:
        return True
    return session.get("authenticated") == True


@app.before_request
def check_auth():
    """所有请求前检查认证"""
    if not config.WEB_PASSWORD:
        return

    # 登录页面和静态资源不需要验证
    if request.endpoint in ['login', 'static']:
        return

    if not session.get("authenticated"):
        return redirect(url_for('login'))


# Jinja2 过滤器：时间戳 → 可读时间
@app.template_filter("datetimeformat")
def datetimeformat(value):
    if not value:
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))
    except Exception:
        return str(value)


@app.context_processor
def inject_globals():
    """注入全局模板变量"""
    return {
        "monitor_running": monitor.running,
        "monitor_interval": config.MONITOR_INTERVAL,
        "config": config,
    }


# --- 路由 ---

@app.route("/login", methods=["GET", "POST"])
def login():
    if not config.WEB_PASSWORD:
        return redirect(url_for("index"))

    if request.method == "POST":
        password = request.form.get("password", "")
        if password == config.WEB_PASSWORD:
            session["authenticated"] = True
            return redirect(url_for("index"))
        else:
            flash("密码错误", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("login"))


@app.route("/")
def index():
    if not config.is_configured():
        return render_template("setup.html")
    return redirect(url_for("positions_page"))


@app.route("/positions")
def positions_page():
    if not config.is_configured():
        return redirect(url_for("index"))
    return render_template("positions.html")


@app.route("/api/positions")
def api_positions():
    if not config.is_configured():
        return jsonify({"error": "未配置凭证", "positions": []})
    positions, error = pm.get_positions_with_prices()
    if error:
        return jsonify({"error": error, "positions": []})
    return jsonify({"positions": positions, "error": None})


@app.route("/api/orderbook/<token_id>")
def api_orderbook(token_id):
    if not config.is_configured():
        return jsonify({"error": "未配置凭证"})
    token_id = token_id.strip()
    if not token_id:
        return jsonify({"error": "缺少 token_id"})
    summary = pm.get_orderbook_summary(token_id)
    if summary is None:
        return jsonify({"error": "查询盘口失败"})
    return jsonify(summary)


@app.route("/rules")
def rules_page():
    if not config.is_configured():
        return redirect(url_for("index"))
    rules = db.get_all_rules()
    return render_template("rules.html", rules=rules)


@app.route("/rules/add", methods=["POST"])
def add_rule():
    token_id = request.form.get("token_id", "").strip()
    market_name = request.form.get("market_name", "").strip()
    outcome = request.form.get("outcome", "").strip()
    rule_type = request.form.get("rule_type", "")
    threshold = request.form.get("threshold", "")
    sell_percent = request.form.get("sell_percent", "")
    price_offset = request.form.get("price_offset", "0")
    sell_mode = request.form.get("sell_mode", "limit")

    if not token_id or not rule_type or not threshold or not sell_percent:
        flash("请填写所有必填字段", "error")
        return redirect(url_for("rules_page"))

    try:
        threshold = float(threshold)
        sell_percent = float(sell_percent)
        price_offset = float(price_offset)
    except ValueError:
        flash("阈值、卖出比例和价格偏移必须是数字", "error")
        return redirect(url_for("rules_page"))

    if threshold < 0.001 or threshold > 0.999:
        flash("阈值必须在 0.001 到 0.999 之间", "error")
        return redirect(url_for("rules_page"))

    if sell_percent < 1 or sell_percent > 100:
        flash("卖出比例必须在 1% 到 100% 之间", "error")
        return redirect(url_for("rules_page"))

    if price_offset < -0.10 or price_offset > 0.10:
        flash("价格偏移必须在 -0.10 到 +0.10 之间", "error")
        return redirect(url_for("rules_page"))

    db.add_rule(token_id, market_name, outcome, rule_type, threshold, sell_percent, price_offset, sell_mode)
    mode_text = "市价" if sell_mode == "market" else f"限价(偏移 {price_offset:+.2f})"
    flash(f"规则已添加: {'止损' if rule_type == 'stop_loss' else '止盈'} @ {threshold}，{mode_text}", "success")
    return redirect(url_for("rules_page"))


@app.route("/rules/<int:rule_id>/toggle", methods=["POST"])
def toggle_rule(rule_id):
    enabled = request.form.get("enabled", "1") == "1"
    db.toggle_rule(rule_id, enabled)
    flash(f"规则 #{rule_id} 已{'启用' if enabled else '禁用'}", "info")
    return redirect(url_for("rules_page"))


@app.route("/rules/<int:rule_id>/delete", methods=["POST"])
def delete_rule(rule_id):
    db.delete_rule(rule_id)
    flash(f"规则 #{rule_id} 已删除", "info")
    return redirect(url_for("rules_page"))


@app.route("/log")
def log_page():
    if not config.is_configured():
        return redirect(url_for("index"))
    logs = db.get_logs()
    return render_template("log.html", logs=logs)


def _update_env_file(updates):
    """更新 .env 文件中的多个键（dict: key -> value）"""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    lines = []
    seen = set()
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                matched = False
                for k, v in updates.items():
                    if line.startswith(f"{k}="):
                        lines.append(f"{k}={v}\n")
                        seen.add(k)
                        matched = True
                        break
                if not matched:
                    lines.append(line)
    for k, v in updates.items():
        if k not in seen:
            lines.append(f"{k}={v}\n")
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        updates = {}

        new_interval = int(request.form.get("monitor_interval", 30))
        if new_interval < 1:
            flash("监控间隔必须大于 0 秒", "error")
        else:
            updates["MONITOR_INTERVAL"] = new_interval
            monitor.interval = new_interval
            config.MONITOR_INTERVAL = new_interval
            flash(f"监控间隔已更新为 {new_interval} 秒", "success")

        new_password = request.form.get("web_password", "").strip()
        updates["WEB_PASSWORD"] = new_password
        config.WEB_PASSWORD = new_password

        new_when = request.form.get("log_rotate_when", "midnight").strip()
        if new_when not in LOG_ROTATE_CHOICES:
            new_when = "midnight"
        try:
            new_backup = int(request.form.get("log_backup_count", "30"))
            new_backup = max(1, min(new_backup, 365))
        except ValueError:
            new_backup = 30

        if new_when != config.LOG_ROTATE_WHEN or new_backup != config.LOG_BACKUP_COUNT:
            config.LOG_ROTATE_WHEN = new_when
            config.LOG_BACKUP_COUNT = new_backup
            updates["LOG_ROTATE_WHEN"] = new_when
            updates["LOG_BACKUP_COUNT"] = new_backup
            reconfigure_file_handler(new_when, new_backup)
            flash(f"日志轮转已更新: {LOG_ROTATE_CHOICES[new_when][0]}，保留 {new_backup} 份", "success")

        _update_env_file(updates)
        return redirect(url_for("settings_page"))

    return render_template("settings.html",
                         current_interval=config.MONITOR_INTERVAL,
                         current_password=config.WEB_PASSWORD,
                         current_log_when=config.LOG_ROTATE_WHEN,
                         current_log_backup=config.LOG_BACKUP_COUNT,
                         log_rotate_choices=LOG_ROTATE_CHOICES)


@app.route("/system-log")
def system_log_page():
    return render_template("system_log.html")


@app.route("/api/system-logs")
def api_system_logs():
    return jsonify({"logs": list(system_logs)})


@app.route("/api/system-logs/clear", methods=["POST"])
def api_clear_system_logs():
    system_logs.clear()
    logging.info("系统日志已清空")
    return jsonify({"success": True})


@app.route("/api/log-files")
def api_log_files():
    log_dir = _get_log_dir()
    files = []
    try:
        for name in os.listdir(log_dir):
            path = os.path.join(log_dir, name)
            if not os.path.isfile(path):
                continue
            if not name.startswith("app.log"):
                continue
            stat = os.stat(path)
            files.append({
                "name": name,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
    except OSError as e:
        return jsonify({"error": str(e), "files": []})
    files.sort(key=lambda x: x["mtime"], reverse=True)
    return jsonify({"files": files})


@app.route("/api/log-files/<path:filename>")
def api_log_download(filename):
    from flask import send_from_directory, abort
    if "/" in filename or "\\" in filename or filename.startswith(".") or ".." in filename:
        abort(400)
    if not filename.startswith("app.log"):
        abort(400)
    log_dir = _get_log_dir()
    full = os.path.join(log_dir, filename)
    if not os.path.isfile(full):
        abort(404)
    return send_from_directory(log_dir, filename, as_attachment=True)


if __name__ == "__main__":
    db.init_db()

    if config.is_configured():
        monitor.start()
        logging.info("监控引擎已随应用启动")
    else:
        logging.warning("未配置凭证，监控引擎未启动。请配置 .env 后重启。")

    # 生产环境使用 waitress
    try:
        from waitress import serve
        logging.info("使用 Waitress 启动服务器，监听 0.0.0.0:5000")
        serve(app, host="0.0.0.0", port=5000, threads=4)
    except ImportError:
        logging.warning("Waitress 未安装，使用 Flask 开发服务器")
        app.run(host="0.0.0.0", port=5000, debug=False)
