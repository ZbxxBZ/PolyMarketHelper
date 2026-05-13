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
        self._pending_market_sells = {}

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
        pending_key = (rule["id"], rule["token_id"])

        positions, err = pm.get_positions_with_prices()
        if err:
            db.add_log(
                rule["id"], rule["token_id"], rule["market_name"],
                rule["rule_type"], rule["threshold"], trigger_price,
                rule["sell_percent"], 0, "error", f"获取持仓失败: {err}",
            )
            return

        pos = None
        for p in positions:
            if p["token_id"] == rule["token_id"]:
                pos = p
                break

        if not pos or pos["size"] <= 0:
            pending = self._pending_market_sells.pop(pending_key, None)
            if pending:
                msg = (f"已成交 @ 市价: 卖出 {pending['amount']:.2f}，"
                       "收到待确认，剩余持仓 0.00")
                if not db.update_latest_submitted_log(
                    rule["id"], rule["token_id"], pending["amount"], "success", msg
                ):
                    db.add_log(
                        rule["id"], rule["token_id"], rule["market_name"],
                        rule["rule_type"], rule["threshold"], trigger_price,
                        rule["sell_percent"], pending["amount"], "success", msg,
                    )
                db.disable_rule(rule["id"])
                logger.info("规则 #%d %s", rule["id"], msg)
                logger.info("规则 #%d 已自动禁用", rule["id"])
                return

            db.add_log(
                rule["id"], rule["token_id"], rule["market_name"],
                rule["rule_type"], rule["threshold"], trigger_price,
                rule["sell_percent"], 0, "skipped", "持仓为空，跳过卖出",
            )
            db.disable_rule(rule["id"])
            return

        import math
        position_size = pos["size"]
        sell_amount = position_size * rule["sell_percent"] / 100
        sell_amount = math.floor(sell_amount * 100) / 100
        if sell_amount <= 0:
            sell_amount = math.floor(position_size * 100) / 100

        sell_mode = rule.get("sell_mode", "limit")
        neg_risk = pos.get("neg_risk", False)

        pending = self._pending_market_sells.get(pending_key)
        if sell_mode == "market" and pending:
            elapsed = time.time() - pending["created_at"]
            if elapsed < 120:
                logger.info(
                    "规则 #%d 市价卖单仍在待确认中，已等待 %.0f 秒，跳过重复下单",
                    rule["id"], elapsed,
                )
                return
            logger.info("规则 #%d 市价卖单待确认超时，允许重新尝试", rule["id"])
            self._pending_market_sells.pop(pending_key, None)

        logger.info(
            "准备卖出: rule=%d, 持仓=%.4f, 计划卖出=%.4f (%.1f%%), 模式=%s, neg_risk=%s",
            rule["id"], position_size, sell_amount, rule["sell_percent"], sell_mode, neg_risk,
        )

        if sell_mode == "market":
            result, err = pm.market_sell(rule["token_id"], sell_amount, neg_risk=neg_risk)
            price_desc = "市价"
        else:
            price_offset = rule.get("price_offset", 0)
            sell_price = round(trigger_price + price_offset, 2)
            if sell_price < 0.01:
                sell_price = 0.01
            if sell_price > 0.99:
                sell_price = 0.99
            result, err = pm.sell(rule["token_id"], sell_amount, sell_price, neg_risk=neg_risk)
            price_desc = f"{sell_price}"

        filled = float(result.get("filled_size", 0)) if isinstance(result, dict) else 0.0
        received = float(result.get("received", 0)) if isinstance(result, dict) else 0.0
        filled_known = bool(result.get("filled_known", False)) if isinstance(result, dict) else False
        received_known = bool(result.get("received_known", False)) if isinstance(result, dict) else False
        order_status = str(result.get("status", "")) if isinstance(result, dict) else ""
        remaining = max(position_size - filled, 0)

        if isinstance(result, dict) and result.get("success") and not filled_known and sell_mode == "market":
            # Some CLOB responses acknowledge a matched FAK order without fill amounts.
            # Re-read positions once and infer the fill from the position delta.
            time.sleep(2)
            refreshed, refresh_err = pm.get_positions_with_prices()
            if not refresh_err:
                current_size = 0.0
                for p in refreshed:
                    if p["token_id"] == rule["token_id"]:
                        current_size = float(p.get("size", 0))
                        break
                inferred = max(position_size - current_size, 0.0)
                if inferred > 0.001:
                    filled = inferred
                    filled_known = True
                    remaining = max(current_size, 0.0)
                    order_status = f"{order_status or 'unknown'}:position_delta"

        if err and filled <= 0:
            err_text = str(err)
            if sell_mode == "market" and "not enough balance" in err_text.lower():
                refreshed, refresh_err = pm.get_positions_with_prices()
                if not refresh_err:
                    current_size = 0.0
                    for p in refreshed:
                        if p["token_id"] == rule["token_id"]:
                            current_size = float(p.get("size", 0))
                            break
                    if current_size <= 0.001:
                        msg = (f"已成交 @ 市价: 卖出 {sell_amount:.2f}，"
                               "收到待确认，剩余持仓 0.00")
                        self._pending_market_sells.pop(pending_key, None)
                        if not db.update_latest_submitted_log(
                            rule["id"], rule["token_id"], sell_amount, "success", msg
                        ):
                            db.add_log(
                                rule["id"], rule["token_id"], rule["market_name"],
                                rule["rule_type"], rule["threshold"], trigger_price,
                                rule["sell_percent"], sell_amount, "success", msg,
                            )
                        db.disable_rule(rule["id"])
                        logger.info("规则 #%d %s", rule["id"], msg)
                        logger.info("规则 #%d 已自动禁用", rule["id"])
                        return

            msg = f"卖出失败: {err}。持仓 {position_size:.2f}，未成交"
            db.add_log(
                rule["id"], rule["token_id"], rule["market_name"],
                rule["rule_type"], rule["threshold"], trigger_price,
                rule["sell_percent"], sell_amount, "error", msg,
            )
            logger.warning("规则 #%d %s", rule["id"], msg)
            if sell_mode == "market":
                logger.info("规则 #%d 市价卖出未成交，保持启用，下次重试", rule["id"])
                return
        elif filled > 0 and filled < sell_amount - 0.001:
            self._pending_market_sells.pop(pending_key, None)
            received_desc = f"{received:.2f} USDC" if received_known else "待确认"
            msg = (f"部分成交 @ {price_desc}: 已卖 {filled:.2f} / 目标 {sell_amount:.2f}，"
                   f"收到 {received_desc}，剩余持仓 {remaining:.2f}")
            db.add_log(
                rule["id"], rule["token_id"], rule["market_name"],
                rule["rule_type"], rule["threshold"], trigger_price,
                rule["sell_percent"], filled, "partial", msg,
            )
            logger.info("规则 #%d %s", rule["id"], msg)
        elif filled_known and filled >= sell_amount - 0.001:
            self._pending_market_sells.pop(pending_key, None)
            received_desc = f"{received:.2f} USDC" if received_known else "待确认"
            msg = (f"已成交 @ {price_desc}: 卖出 {filled:.2f}，"
                   f"收到 {received_desc}，剩余持仓 {remaining:.2f}")
            db.add_log(
                rule["id"], rule["token_id"], rule["market_name"],
                rule["rule_type"], rule["threshold"], trigger_price,
                rule["sell_percent"], filled, "success", msg,
            )
            logger.info("规则 #%d %s", rule["id"], msg)
        else:
            msg = (f"卖单已提交 @ {price_desc}: 目标卖出 {sell_amount:.2f}，"
                   f"成交量待确认，订单状态={order_status or 'unknown'}")
            db.add_log(
                rule["id"], rule["token_id"], rule["market_name"],
                rule["rule_type"], rule["threshold"], trigger_price,
                rule["sell_percent"], 0, "submitted", msg,
            )
            logger.info("规则 #%d %s", rule["id"], msg)
            if sell_mode == "market":
                self._pending_market_sells[pending_key] = {
                    "amount": sell_amount,
                    "created_at": time.time(),
                    "order_id": result.get("order_id", "") if isinstance(result, dict) else "",
                }
                logger.info("规则 #%d 市价卖出成交量未确认，保持启用，下次复查持仓", rule["id"])
                return

        db.disable_rule(rule["id"])
        logger.info("规则 #%d 已自动禁用", rule["id"])
