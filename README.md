# 🐑 羊羊股市监测 — ETF 强弱趋势分析

> A 股 ETF 强弱趋势分析仪表盘 · 数据日期 20260703 · 197 只 ETF

## 🚀 一键部署到 Streamlit Cloud

### 前置:把代码放到 GitHub

1. 在 GitHub 上 **Create new repository**,命名 `yangyang-stock-monitor`(或你喜欢的)
   - 选 **Public**(Streamlit Cloud 免费版只支持公开 repo)
   - **不要**勾选 "Add README"(我们自己有)
2. 把这个目录的所有文件 push 上去:

```bash
cd yangyang-stock-monitor  # 这个目录
git init
git add .
git commit -m "init: 羊羊股市监测 Streamlit 版"
git branch -M main
git remote add origin https://github.com/你的用户名/yangyang-stock-monitor.git
git push -u origin main
```

### 部署

1. 打开 https://share.streamlit.io
2. **Sign in with GitHub**
3. 点 **New app**,填:
   - **Repository**: `你的用户名/yangyang-stock-monitor`
   - **Branch**: `main`
   - **Main file path**: `app.py`
4. 点 **Deploy!** ⏳ 等 2-5 分钟
5. 拿到类似 `https://你的用户名-yangyang-stock-monitor.streamlit.app` 的 URL 🎉

### 更新数据

理财助理更新 CSV 后:
```bash
python fetch_data.py
git add data/
git commit -m "data: 刷新到 YYYYMMDD"
git push
```
Streamlit Cloud 检测到 push 会自动重启,~1 分钟后页面就是新数据。

---

## 🛠️ 本地开发

```bash
pip install -r requirements.txt
python fetch_data.py     # 一次性:从 CloudBase 抓数据
streamlit run app.py     # 浏览器打开 http://localhost:8501
```

---

## 📁 文件结构

```
.
├── app.py                  # 主应用 (Streamlit 单文件)
├── fetch_data.py           # 从 CloudBase API 拉数据 → data/
├── requirements.txt        # Python 依赖
├── data/
│   ├── results.csv         # 197 只 ETF 当日强弱 (从 API 抓取)
│   └── etf_trend_history.csv # 25 天趋势演变
└── README.md
```

## 🔧 数据源

- 默认从 [CloudBase API](https://agentchat-d0gsw7sn6c36f0b00.service.tcloudbase.com/api/etf-strength) 拉
- 通过 `fetch_data.py --base <URL>` 可以换数据源
- 想完全脱离 CloudBase?把 `data/results.csv` 和 `data/etf_trend_history.csv` 替换成你自己抓的数据即可,字段名保持一致

## 📊 功能

- **筛选**:行业分类、趋势标签、Top N、排序字段/方向、关键词搜索
- **分布卡片**:6 档趋势分类 × 总规模
- **主表格**:50/20/120 日斜率、综合夏普、ADX、60 日上涨占比
- **图表**:饼图(趋势分布)、柱图(分类强度)、散点图(斜率 vs 规模)
- **趋势演变**:25 天热力图(emoji 标记)
- **缓存**:Streamlit `@st.cache_data` 5 分钟自动刷新

## ⚠️ 数据声明

- 当前数据为示例快照(2026-07-03),ETF 代码/名称/规模等仅供参考
- 投资有风险,本仪表盘**不构成任何投资建议**
- 数据源来自第三方 API(CloudBase + AmazingData),请遵循相关使用条款