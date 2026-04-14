# Polymarket Auto Trade

一个用于 Polymarket 的自动止损/止盈交易工具，支持 Web 界面管理和 Docker 部署。

## 功能特性

- 持仓总览：实时查看所有持仓、盈亏情况
- 自动止损/止盈：设置价格触发规则，自动执行卖出
- 执行日志：记录所有交易操作和触发历史
- 系统设置：可调整监控频率、访问密码
- 系统日志：实时查看运行状态和错误信息
- Docker 支持：一键部署，支持多账户运行

## 快速开始

### 方式 1：Docker 部署（推荐）

1. **克隆项目**
```bash
git clone https://github.com/ZbxxBZ/PolyMarket.git
cd PolyMarket
```

2. **配置环境变量**
```bash
cp .env.example .env
# 编辑 .env 文件，填入你的私钥和 Funder 地址
```

3. **启动容器**
```bash
docker compose up -d
```

4. **访问 Web 界面**
```
http://localhost:5000
```

### 方式 2：本地运行

1. **安装依赖**
```bash
pip install -r requirements.txt
```

2. **配置环境变量**
```bash
cp .env.example .env
# 编辑 .env 文件
```

3. **运行程序**
```bash
python main.py
```

## 配置说明

### 获取私钥

1. 打开 MetaMask → 账户详情 → 导出私钥
2. 输入密码确认
3. 复制以 `0x` 开头的私钥字符串

### 获取 Funder 地址

1. 访问 [polymarket.com](https://polymarket.com) 并登录
2. 进入设置页面或充值页面
3. 复制显示的存款地址（Proxy Wallet 地址）

### 环境变量

在 `.env` 文件中配置：

```env
PRIVATE_KEY=0x你的私钥
FUNDER_ADDRESS=0x你的Funder地址
MONITOR_INTERVAL=30
WEB_PASSWORD=你的访问密码
```

## 使用指南

### 添加止损规则

1. 进入"持仓总览"页面
2. 点击持仓右侧的"添加规则"按钮
3. 设置触发价格、卖出比例、价格偏移
4. 保存后自动开始监控

### 规则说明

- **止损规则**：当价格 ≤ 触发价格时卖出
- **止盈规则**：当价格 ≥ 触发价格时卖出
- **卖出比例**：触发时卖出持仓的百分比（1-100%）
- **价格偏移**：
  - 正数：高于触发价挂单（慢速成交，价格更好）
  - 负数：低于触发价快速成交
  - 0：按触发价成交

### 多账户部署

如需同时运行多个账户，复制项目目录并修改端口：

```bash
# 账户 1
cd PolyMarket-account1
# 修改 docker-compose.yml 中的端口为 5000:5000
docker compose up -d

# 账户 2
cd PolyMarket-account2
# 修改 docker-compose.yml 中的端口为 5001:5000
docker compose up -d
```

## 安全提示

重要安全建议：

1. 私钥安全：私钥仅存储在本地 .env 文件中，不会上传到任何服务器
2. 访问控制：建议设置 WEB_PASSWORD 防止未授权访问
3. 网络安全：如果部署在公网服务器，建议使用反向代理（Nginx）并配置 HTTPS
4. 备份数据：定期备份 data/ 目录下的数据库文件

## 技术栈

- **后端**：Python 3.11 + Flask
- **前端**：原生 JavaScript + CSS
- **数据库**：SQLite
- **部署**：Docker + Waitress

## 项目结构

```
PolyMarket/
├── main.py                 # 主程序入口
├── config.py              # 配置管理
├── database.py            # 数据库操作
├── monitor.py             # 价格监控引擎
├── polymarket_client.py   # Polymarket API 客户端
├── templates/             # HTML 模板
├── static/                # 静态资源
├── Dockerfile             # Docker 镜像
├── docker-compose.yml     # Docker 编排
├── requirements.txt       # Python 依赖
└── .env.example          # 环境变量示例
```

## 常见问题

### 1. 容器启动后立即退出

检查日志：
```bash
docker logs polymarket-auto-trade
```

常见原因：
- `.env` 文件配置错误
- 私钥或 Funder 地址格式不正确

### 2. 无法访问 Web 界面

- 检查防火墙是否开放端口
- 确认容器正在运行：`docker ps`
- 查看容器日志排查错误

### 3. 止损规则未触发

- 检查"系统日志"页面是否有错误
- 确认监控间隔设置合理（建议 10-30 秒）
- 验证规则是否已启用

## 开发计划

- [ ] WebSocket 实时价格推送
- [ ] 移动端适配
- [ ] Telegram 通知
- [ ] 更多交易策略

## 许可证

MIT License

## 免责声明

本工具仅供学习和研究使用。使用本工具进行交易的所有风险由用户自行承担。作者不对任何交易损失负责。

## 贡献

欢迎提交 Issue 和 Pull Request！

## 联系方式

如有问题或建议，请提交 [Issue](https://github.com/你的用户名/PolyMarket/issues)。
