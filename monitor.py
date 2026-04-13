import logging
import threading
import time

import database as db
import polymarket_client as pm

logger = logging.getLogger(__name__)


class PriceMonitor:
    """后台价格监控引擎，检测规则触发并执行自动卖出"""

    def __init__(self, interval=30):
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="PriceMonitor")
        self._thread.start()
        logger.info("监控引擎已启动，间隔 %d 秒", self.interval)

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("监控引擎已停止")

    def set_interval(self, seconds):
        self.interval = max(5, seconds)
        logger.info("监控间隔已更新为 %d 秒", self.interval)

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        logger.info("监控线程开始运行")
        while not self._stop_event.is_set():
            try:
                self._check_rules()
            except Exception:
                logger.exception("监控循环异常")

            # 分段等待以便快速响应停止信号
            for _ in range(self.interval):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    def _check_rules(self):
        rules = db.get_enabled_rules()
        if not rules:
            return

        # 收集所有需要查价的 token_id
        token_ids = list({r["token_id"] for r in rules})
        prices = pm.get_prices_batch(token_ids)

        for rule in rules:
            token_id = rule["token_id"]
            cur_price = prices.get(token_id, 0.0)
            if cur_price <= 0:
                continue

            triggered = False
            if rule["rule_type"] == "stop_loss" and cur_price <= rule["threshold"]:
                triggered = True
            elif rule["rule_type"] == "take_profit" and cur_price >= rule["threshold"]:
                triggered = True

            if triggered:
                self._execute_sell(rule, cur_price)

    def _execute_sell(self, rule, trigger_price):
        """执行卖出并记录日志"""
        logger.info(
            "规则触发: id=%d, type=%s, threshold=%.4f, trigger_price=%.4f",
            rule["id"], rule["rule_type"], rule["threshold"], trigger_price,
        )

        # 获取当前持仓量
        positions, err = pm.get_positions_with_prices()
        if err:
            db.add_log(
                rule["id"], rule["token_id"], rule["market_name"],
                rule["rule_type"], rule["threshold"], trigger_price,
                rule["sell_percent"], 0, "error", f"获取持仓失败: {err}",
            )
            return

        # 找到对应持仓
        pos = None
        for p in positions:
            if p["token_id"] == rule["token_id"]:
                pos = p
                break

        if not pos or pos["size"] <= 0:
            db.add_log(
                rule["id"], rule["token_id"], rule["market_name"],
                rule["rule_type"], rule["threshold"], trigger_price,
                rule["sell_percent"], 0, "skipped", "持仓为空，跳过卖出",
            )
            db.disable_rule(rule["id"])
            return

        import math
        sell_amount = pos["size"] * rule["sell_percent"] / 100
        # 向下取整到小数点后两位，确保不会超出持仓
        sell_amount = math.floor(sell_amount * 100) / 100
        if sell_amount <= 0:
            sell_amount = math.floor(pos["size"] * 100) / 100

        # 根据 price_offset 计算卖出价格
        # 正数 = 高于市价挂单，负数 = 低于市价快速成交，0 = 按市价
        price_offset = rule.get("price_offset", 0.01)
        sell_price = round(trigger_price + price_offset, 2)
        if sell_price < 0.01:
            sell_price = 0.01
        if sell_price > 0.99:
            sell_price = 0.99

        resp, err = pm.sell(rule["token_id"], sell_amount, sell_price)

        if err:
            db.add_log(
                rule["id"], rule["token_id"], rule["market_name"],
                rule["rule_type"], rule["threshold"], trigger_price,
                rule["sell_percent"], sell_amount, "error", f"卖出失败: {err}",
            )
        else:
            db.add_log(
                rule["id"], rule["token_id"], rule["market_name"],
                rule["rule_type"], rule["threshold"], trigger_price,
                rule["sell_percent"], sell_amount, "success",
                f"卖单已提交: {sell_amount} 份 @ {sell_price}",
            )

        # 触发后自动禁用规则
        db.disable_rule(rule["id"])
        logger.info("规则 #%d 已自动禁用", rule["id"])
