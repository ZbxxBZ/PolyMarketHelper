import logging
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


# 内存日志队列（最多保留 500 条）
system_logs = deque(maxlen=500)


class MemoryLogHandler(logging.Handler):
    """自定义日志处理器，将日志保存到内存"""
    def emit(self, record):
        log_entry = {
            'time': datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S'),
            'level': record.levelname.lower(),
            'message': record.getMessage()
        }
        system_logs.append(log_entry)


def resource_path(relative_path):
    """获取资源文件路径（兼容 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# 添加内存日志处理器
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


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        # 更新监控间隔
        new_interval = int(request.form.get("monitor_interval", 30))
        if new_interval < 1:
            flash("监控间隔必须大于 0 秒", "error")
        else:
            # 更新 .env 文件
            env_path = os.path.join(os.path.dirname(__file__), ".env")
            lines = []
            updated = False

            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("MONITOR_INTERVAL="):
                            lines.append(f"MONITOR_INTERVAL={new_interval}\n")
                            updated = True
                        else:
                            lines.append(line)

            if not updated:
                lines.append(f"MONITOR_INTERVAL={new_interval}\n")

            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(lines)

            # 更新运行中的监控间隔
            monitor.interval = new_interval
            config.MONITOR_INTERVAL = new_interval

            flash(f"监控间隔已更新为 {new_interval} 秒", "success")

        # 更新密码
        new_password = request.form.get("web_password", "").strip()
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        lines = []
        updated = False

        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("WEB_PASSWORD="):
                        lines.append(f"WEB_PASSWORD={new_password}\n")
                        updated = True
                    else:
                        lines.append(line)

        if not updated:
            lines.append(f"WEB_PASSWORD={new_password}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        config.WEB_PASSWORD = new_password
        flash("密码已更新", "success")

        return redirect(url_for("settings_page"))

    return render_template("settings.html",
                         current_interval=config.MONITOR_INTERVAL,
                         current_password=config.WEB_PASSWORD)


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
