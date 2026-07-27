# A 股市场观察

一个纯静态的 A 股市场、行业与风格看板。访问网页时只读取生成好的 JSON；SQLite 仅在更新时作为临时计算缓存，不会被发布到网页或提交到仓库。

## 本地运行

```bash
uv sync
uv run python -m http.server 8787 --directory dashboard
```

打开 <http://127.0.0.1:8787/web/>。

## 更新数据

首次或无本地缓存时，完整构建：

```bash
uv run python dashboard/scripts/update_data.py --full
```

已有本地 SQLite 缓存时，日常增量更新：

```bash
uv run python dashboard/scripts/update_data.py
```

生成给网页使用的数据文件是 `dashboard/data/dashboard.json`。

## 免费发布

仓库已附带 GitHub Pages 工作流：每个工作日 A 股收市后构建并发布一次，也可在 GitHub Actions 页面手动触发。

1. 在 GitHub 新建空仓库并推送本项目的 `main` 分支。
2. 打开仓库的 **Settings → Pages**，将 Source 设为 **GitHub Actions**。
3. 首次工作流完成后，GitHub 会给出公开访问地址。

工作流每次在临时运行器中全量拉取官方数据、生成静态网页后发布；数据库不会上云，也不需要一直运行的服务器。

## 数据边界

- 涨幅均为价格指数收益，不含分红再投资。
- 行业的 PE、PB 与股息率来自申万口径；中证公开历史接口只展示可核实的滚动 PE。
- 因子页的相对表现是研究比较，不是新编制的可投资指数。
- 成分股和权重为官方接口提供的最新快照，行业归属使用申万当前一级行业。
