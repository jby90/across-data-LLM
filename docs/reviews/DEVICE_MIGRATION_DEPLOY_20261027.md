# 新设备迁移部署记录（2026-10-27）

分支 `codex/device-development`，基于交接包提交 `b601f75`。后续本地产出的报告统一放在 `docs/reviews/`。

## 环境
- Windows；Git 2.56、Python 3.12.10、uv 0.13.6、Node 24.22.1（说明要求 22，22 安装失败，24 下 typecheck/build 正常）、pnpm 10.33.2、PostgreSQL 16 客户端、Docker Desktop。
- 数据库：Docker 容器 `cyshuhe-handoff-db`（127.0.0.1:15432），库 `cyshuhe_dev_main`，口令为本机随机生成，存于 `dev-data/.pgpass.local`（不在仓库内）。
- 原文归档：`dev-data/cyshuhe/archive`；Python 环境：`dev-envs/cyshuhe`。

## 数据恢复结果
29 表 / 35,826 行 / 81 版本 / 80 原文 / 31 项运行权限 / 11 张 FORCE RLS，迁移版本 0023；无模型调用、无线上写入。

## 验证
- `pnpm -r typecheck`：通过。
- `pnpm -r build`：通过（仅 chunk 体积警告）。
- 回归 `test_chat_country_integrity.py` + `test_s6_user_facts.py` + `review_s1_s2_contracts.py`：40 passed。

## Windows 测试补丁
`tempfile.mkdtemp()` 建的目录权限过紧，pgserver 的 initdb 报 `could not access directory ... Invalid argument`，导致测试全部在准备阶段报错。
仓库外的 pytest 插件 `dev-tools/win_mkdtemp_acl.py` 在建目录后执行 `icacls /reset`。运行方式：

```powershell
$env:PYTHONPATH='<工作目录>/dev-tools'
uv run --project backend pytest -p win_mkdtemp_acl <测试文件> -q
```

## 未做
未运行其余后端测试；未设置 `CYSH_LLM_API_KEY`，模型相关功能不可用；未开启定时采集。
