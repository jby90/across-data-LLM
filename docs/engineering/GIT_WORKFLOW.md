# Git 与远端协作约定

> [!NOTE]
> **版本基线说明（v2026.11.28 结项同步）**：当前主工作区为 `C:\dev\CrossBorderPlatform`，主干与发布分支为 `main` 及 `release/v2026.11.19`（数据库迁移链 `0001..0078`）。下文提及的早期阶段性分支名与目录路径仅作研发演进留痕归档参考，现行全量门禁以根仓库 `README.md` 与 `AGENTS.md` 为准。

## 仓库与分支演进留痕

- 当前主工程目录：`C:\dev\CrossBorderPlatform`（早期研发阶段曾使用 `E:\汽研项目\CrossBorderPlatform`）。
- GitHub：<https://github.com/jby90/across-data>。
- 当前主干与发布分支：`main`、`release/v2026.11.19`（早期阶段性特性分支 `codex/user-experience-legal-terms` 已合入主干）。
- `E:\AutoDataCross` 是另外一个旧项目，关联 `jby90/AutoDataCross`；其未提交修改不并入本仓库。

2026-10-26 同步前，当前分支比远端 `main` 多 97 个提交，包含此前的修复与近期用户体验改进。本次保留分段历史，通过独立分支和草稿 PR 集成，不直接更新 `main`，不改写已存在的提交。

## 日常开发

在当前开发分支工作，先检查工作区，再按功能提交：

```powershell
git status --short --branch
git fetch origin --prune
git pull --ff-only
# 修改完成，运行相应回归后，明确选择要提交的文件
git add <文件路径>
git commit -m "说明本阶段完成的内容"
git push
```

工作区有修改或分支出现分叉时先检查差异，不使用 `reset --hard` 或强推清理。合并前保留原有测试、来源保护和人工复核门槛。

## 同事开发第二版本

同事使用单独目录克隆，再建立自己的分支：

```powershell
git clone --branch codex/user-experience-legal-terms https://github.com/jby90/across-data.git colleague-v2
cd colleague-v2
git switch -c codex/colleague-v2
# 完成并验证改动后
git push -u origin codex/colleague-v2
```

同事的数据库、原文归档目录、运行端口和环境变量也需独立。Git 分支只隔离代码，不隔离数据库或服务器；不给同事的开发环境直接连接生产数据库。不把真实 `.env`、数据库备份、账号口令、SSH 私钥或模型密钥加入仓库。需要分享业务数据时，通过另行受控的备份交接。

## 阶段发布留痕与现行门禁

- 早期阶段性发布记录（2026-10-26 归档）：`a2853d1`、`b95754f`、`a64d000`（当时阶段性验证为后端 591 项、前端 42 项）。
- **现行结项门禁基线（v2026.11.28）**：发布分支 `main` 与 `release/v2026.11.19` 双轨同步，数据库迁移链 `0001..0078`；后端全量 159 个可执行测试模块（1,664 项）与前端 10 套件（54 项）自动化回归全量通过，四端字节级同步（Git 仓库、GitHub 远端、正式交付包 `01_源代码与部署配置`、生产云主机 `Swap: 0 B`）。

后续发布使用新标签固定实际部署的源码提交，不覆盖旧标签。代码回退与数据恢复分开操作；回退代码不自动删除或覆盖原文、证据、报告版本和审批记录。

部署详情见 [聊天反馈与导出验证](../reviews/CHAT_FEEDBACK_EXPORT_20261026.md)。
