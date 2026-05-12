import logging
import threading

import requests

import config

logger = logging.getLogger(__name__)

_client = None
_lock = threading.Lock()

DATA_API_URL = "https://data-api.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"

# 缓存市场信息（tick_size, neg_risk）
_market_info_cache = {}


def _get_client():
    """懒初始化 ClobClient（线程安全）"""
    global _client
    if _client is not None:
        return _client

    with _lock:
        if _client is not None:
            return _client

        from py_clob_client.client import ClobClient

        c = ClobClient(
            config.CLOB_API_URL,
            key=config.PRIVATE_KEY,
            chain_id=config.CHAIN_ID,
            signature_type=1,
            funder=config.FUNDER_ADDRESS,
        )
        c.set_api_creds(c.create_or_derive_api_creds())
        _client = c
        logger.info("ClobClient 初始化成功")
        return _client


def reset_client():
    """重置客户端（配置更新后调用）"""
    global _client
    with _lock:
        _client = None


def _get_market_info(token_id):
    """通过 ClobClient 获取市场的 tick_size 和 neg_risk（带缓存）

    优先使用 ClobClient 内置方法（从 CLOB API 获取），
    比 Gamma API 更准确。
    """
    if token_id in _market_info_cache:
        return _market_info_cache[token_id]

    info = {"tick_size": "0.01", "neg_risk": False}
    can_cache = True

    try:
        client = _get_client()
        tick_size = client.get_tick_size(token_id)
        info["tick_size"] = str(tick_size)
    except Exception:
        logger.warning("CLOB get_tick_size 失败，使用默认值 0.01: %s", token_id)

    try:
        client = _get_client()
        neg_risk = client.get_neg_risk(token_id)
        info["neg_risk"] = bool(neg_risk)
    except Exception:
        logger.warning("CLOB get_neg_risk 失败，使用默认值 False: %s", token_id)
        can_cache = False

    if can_cache:
        _market_info_cache[token_id] = info
    return info


def get_positions_with_prices():
    """通过 Data API 获取所有持仓（含当前价格和盈亏）

    返回: (list[dict], error_msg|None)
    """
    try:
        logger.info("查询持仓: user=%s", config.FUNDER_ADDRESS)
        params = {
            "user": config.FUNDER_ADDRESS.lower(),
            "sizeThreshold": 0,
            "limit": 500,
            "offset": 0,
            "sortBy": "CURRENT",
            "sortDirection": "DESC",
        }

        all_raw = []
        while True:
            resp = requests.get(f"{DATA_API_URL}/positions", params=params, timeout=15)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            all_raw.extend(batch)
            if len(batch) < params["limit"]:
                break
            params["offset"] += params["limit"]

        positions = []
        for pos in all_raw:
            size = float(pos.get("size", 0))
            if size <= 0:
                continue

            token_id = pos.get("asset", "")
            condition_id = pos.get("conditionId", "")
            avg_price = float(pos.get("avgPrice", 0))
            cur_price = float(pos.get("curPrice", 0))
            outcome = pos.get("outcome", "")
            market_name = pos.get("title", "")
            neg_risk = bool(pos.get("negativeRisk", False))

            initial_value = float(pos.get("initialValue", 0))
            current_value = float(pos.get("currentValue", 0))
            cash_pnl = float(pos.get("cashPnl", 0))

            positions.append({
                "token_id": token_id,
                "condition_id": condition_id,
                "market_name": market_name or "未知市场",
                "outcome": outcome,
                "size": round(size, 4),
                "avg_price": round(avg_price, 4),
                "cur_price": round(cur_price, 4),
                "cost": round(initial_value, 4),
                "value": round(current_value, 4),
                "pnl": round(cash_pnl, 4),
                "neg_risk": neg_risk,
            })

        return positions, None

    except Exception as e:
        logger.exception("获取持仓失败")
        return [], str(e)


def get_price(token_id):
    """获取单个 token 的当前中间价

    返回: (price: float, error_msg|None)
    """
    try:
        client = _get_client()
        mid = client.get_midpoint(token_id)
        if isinstance(mid, dict):
            price = float(mid.get("mid", 0))
        else:
            price = float(mid) if mid else 0.0
        logger.info("查询中间价: token=%s, price=%.4f", token_id, price)
        return price, None
    except Exception as e:
        logger.exception("获取价格失败: token=%s", token_id)
        return 0.0, str(e)


def get_prices_batch(token_ids):
    """批量获取价格

    返回: dict[token_id -> float]
    """
    prices = {}
    client = _get_client()
    for tid in token_ids:
        try:
            mid = client.get_midpoint(tid)
            if isinstance(mid, dict):
                prices[tid] = float(mid.get("mid", 0))
            else:
                prices[tid] = float(mid) if mid else 0.0
        except Exception:
            logger.exception("批量查价失败: token=%s", tid)
            prices[tid] = 0.0
    return prices


def get_orderbook_summary(token_id):
    """查询盘口买方深度（便于判断市价单能否成交）

    返回: dict(best_bid, bid_total_size, bid_total_value) 或 None
    """
    try:
        client = _get_client()
        book = client.get_order_book(token_id)
        bids = getattr(book, "bids", None) or []
        if not bids:
            logger.info("盘口无买单: token=%s", token_id)
            return {"best_bid": 0.0, "bid_total_size": 0.0, "bid_total_value": 0.0}

        total_size = 0.0
        total_value = 0.0
        best_bid = 0.0
        for lvl in bids:
            price = float(getattr(lvl, "price", 0))
            size = float(getattr(lvl, "size", 0))
            total_size += size
            total_value += price * size
            if price > best_bid:
                best_bid = price
        logger.info(
            "盘口快照: token=%s, best_bid=%.4f, 总买量=%.2f, 总买额=%.2f USDC",
            token_id, best_bid, total_size, total_value,
        )
        return {
            "best_bid": best_bid,
            "bid_total_size": total_size,
            "bid_total_value": total_value,
        }
    except Exception:
        logger.exception("查询盘口失败: token=%s", token_id)
        return None


def _parse_fill(resp):
    """从下单响应中解析成交量、收款金额、订单状态"""
    if not isinstance(resp, dict):
        return {"success": False, "filled_size": 0.0, "received": 0.0, "status": "", "order_id": "", "raw": resp}

    filled = 0.0
    received = 0.0
    # py-clob-client 响应字段：makingAmount(卖出份数), takingAmount(获得 USDC)
    for key in ("makingAmount", "making_amount", "filled_size", "size_matched"):
        v = resp.get(key)
        if v is not None:
            try:
                filled = float(v)
                break
            except (TypeError, ValueError):
                pass
    for key in ("takingAmount", "taking_amount"):
        v = resp.get(key)
        if v is not None:
            try:
                received = float(v)
                break
            except (TypeError, ValueError):
                pass

    return {
        "success": bool(resp.get("success", False)),
        "filled_size": filled,
        "received": received,
        "status": resp.get("status", ""),
        "order_id": resp.get("orderID", "") or resp.get("orderId", ""),
        "error": resp.get("errorMsg", "") or resp.get("error", ""),
        "raw": resp,
    }


def sell(token_id, size, price, neg_risk=None):
    """提交 GTC 限价卖单

    返回: (result_dict, error_msg|None)
    """
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
        from py_clob_client.order_builder.constants import SELL

        client = _get_client()
        market_info = _get_market_info(token_id)
        tick_size = market_info["tick_size"]
        if neg_risk is None:
            neg_risk = market_info["neg_risk"]

        logger.info(
            "提交限价卖单: token=%s, size=%s, price=%s, tick_size=%s, neg_risk=%s",
            token_id, size, price, tick_size, neg_risk,
        )

        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=SELL)
        options = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)
        signed = client.create_order(order_args, options=options)
        resp = client.post_order(signed, OrderType.GTC)
        logger.info("限价卖单响应: token=%s, resp=%s", token_id, resp)

        result = _parse_fill(resp)
        if not result["success"] and result.get("error"):
            return result, result["error"]
        return result, None

    except Exception as e:
        logger.exception("限价卖单提交失败: token=%s", token_id)
        return None, str(e)


def market_sell(token_id, size, neg_risk=None):
    """提交 FAK 市价卖单（能成交多少成交多少，剩余撤单）

    返回: (result_dict, error_msg|None)
        result_dict: success, filled_size, received, status, order_id, raw
    """
    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType, PartialCreateOrderOptions
        from py_clob_client.order_builder.constants import SELL

        client = _get_client()
        market_info = _get_market_info(token_id)
        tick_size = market_info["tick_size"]
        if neg_risk is None:
            neg_risk = market_info["neg_risk"]

        book = get_orderbook_summary(token_id)
        if book is not None and book["bid_total_size"] <= 0:
            msg = "盘口无买单，市价单无法成交"
            logger.warning("%s: token=%s", msg, token_id)
            return {"success": False, "filled_size": 0.0, "received": 0.0,
                    "status": "no_liquidity", "order_id": "", "raw": None}, msg

        logger.info(
            "提交市价卖单(FAK): token=%s, size=%s, tick_size=%s, neg_risk=%s",
            token_id, size, tick_size, neg_risk,
        )

        order_args = MarketOrderArgs(token_id=token_id, amount=size, side=SELL)
        options = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)
        signed = client.create_market_order(order_args, options=options)
        resp = client.post_order(signed, OrderType.FAK)
        logger.info("市价卖单响应: token=%s, resp=%s", token_id, resp)

        result = _parse_fill(resp)
        if not result["success"] and result.get("error"):
            return result, result["error"]
        return result, None

    except Exception as e:
        logger.exception("市价卖单提交失败: token=%s", token_id)
        return None, str(e)
