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

    _market_info_cache[token_id] = info
    return info


def get_positions_with_prices():
    """通过 Data API 获取所有持仓（含当前价格和盈亏）

    返回: (list[dict], error_msg|None)
    """
    try:
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
            return float(mid.get("mid", 0)), None
        return float(mid) if mid else 0.0, None
    except Exception as e:
        logger.exception("获取价格失败: %s", token_id)
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
            prices[tid] = 0.0
    return prices


def sell(token_id, size, price):
    """提交 GTC 限价卖单

    返回: (response_dict, error_msg|None)
    """
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
        from py_clob_client.order_builder.constants import SELL

        client = _get_client()

        # 从 Gamma API 获取市场参数
        market_info = _get_market_info(token_id)
        tick_size = market_info["tick_size"]
        neg_risk = market_info["neg_risk"]

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=SELL,
        )
        options = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)
        signed = client.create_order(order_args, options=options)
        resp = client.post_order(signed, OrderType.GTC)
        logger.info("卖单提交成功: token=%s, size=%s, price=%s, resp=%s",
                     token_id, size, price, resp)
        return resp, None

    except Exception as e:
        logger.exception("卖单提交失败: token=%s", token_id)
        return None, str(e)
