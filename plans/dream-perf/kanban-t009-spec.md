# 定案：Hermes 运行环境为什么被「删掉又重建」，以及重建期间活进程为什么会失联

**交付物（唯一验收标准）**：新建
`/Users/maxim/Repositories/Repo/Dream/plans/dream-perf/evidence/env-rebuild-forensics-2026-10-11.md`，
写清「谁、在什么条件下、删掉并重建 `~/.hermes/installs/*/environments/<hash>/`」，并给出可执行的规避或修法。

## 地面真值（实测，别再猜；逐条自己核一遍，不要照抄）

| 事实 | 取证方式 |
|---|---|
| 2026-10-11 **09:50** 该环境目录还在且可用（`51e7b11d1f0e44bcba68d977a823d512/venv/bin/python3` 能 import ruamel/croniter） | 当时实测 |
| **10:51:33** `~/.hermes/installs/b200403c2b90970e/environments/` 被改动（该目录 mtime = 10:51:33），`51e7b11d…` 目录一度不存在，只剩 `ef2099f3d36145779f2e00c200d4279a` | `ls -ldT` + 目录列举 |
| **10:51:13 起** `~/.hermes/logs/gateway.error.log` 出现 `FileNotFoundError`：`httpx/_config.py create_ssl_context` → `truststore/_api.py load_verify_locations`，`provider=custom base_url=https://monk.party/v1 model=monk`；10:51 一分钟 498 行、10:52 又 362 行 | `grep -a -c`/`tail` 该日志 |
| 该错误**只在新建 httpx/openai 客户端时**触发（内存里已导入的模块照常工作）⇒ 表现为「部分子代理动、其余卡死」（8/10 idle > 10 分钟，最长 33.9 分钟） | 会话 `cron_52fe6cf3a3dc_20261011_090717` 的 messages 逐条时间戳 |
| **11:02:03** 同一个 hash 的目录**重新出现且可用**（能 import httpx / truststore / discord）；11:02:05 之后再无 FileNotFoundError（尾部只剩 3 条 APITimeoutError），子代理 11:03:27 恢复动作 ⇒ **卡死由「路径不存在的窗口」造成，与网络无关** | 目录 mtime + 日志 + 会话时间戳 |
| 网络与证书本身正常：`curl` 直连与走代理打 `https://monk.party/v1/models` 都返回 401、耗时 1.25s；`hermes egress status` 显示 iron-proxy 未启用；`config.yaml` 里没有任何证书变量；`~/.hermes/disk-cleanup/cleanup.log` 只记 `cache/scratch`，**不含 `installs`** | 命令原文 |
| 背景压力：App Store 在装 Xcode（`/Applications/Xcode.app` mtime 10:59:02、`install.log` 里 appstoreagent 10:59:35 注册 bundle）、load average 16.7/18.0/15.7、空闲内存一度仅 136MB、`mds_stores` 占 43–119% CPU；`/usr/bin/python3` 与 `/usr/bin/git` 一度退 69（许可未同意，用户 11:03 已修） | `uptime` / `vm_stat` / `ps` / `ls -lT` |

## 要回答的问题（分叉，别混着答）

1. 删除+重建的**触发者**是谁：Hermes 自己的环境供给/修复流程（新 hash 01:49 建好、旧 hash 10:51 被处理）、
   还是 `hermes update` / 插件安装 / 某次 `hermes doctor`、还是磁盘压力下的清理、还是 App Store 安装期间的
   系统级清理？**给出定案依据**：能复现的复现步骤，或能指到具体 `file:line` + 触发条件。
2. 重建窗口有多长（本例 ≈10.5 分钟），期间对**活进程**的破坏面是什么（本例：新建 TLS 客户端全部失败）。
3. 规避/修法（**官方机制优先**，不要自造手拷）：例如在新 hash 目录建好后再原子切换、重建前检测是否被
   活进程引用、或至少把窗口写进日志并让 `perf-probe.py` 能提前报警。

## 硬约束

- **只读 + 只写那一份交付报告**。不改任何 profile 的配置、技能、密钥、cron；不删任何文件。
- **不要重启共享网关**（`~/.hermes/logs/` 那个进程同时服务 16 个 profile，重启会打断别人的会话）。
- 仓库里别人的在制品不要 `git add` / `commit` / 还原；只按 pathspec 提交你自己那份报告（若你提交）。
- 判据一律用实测原文，不要「应该是」；引用别人的数字要自己核一遍。

## 歧义出口

先把能定的做完。若发现另一层含义（例如删除者不止一个、或窗口期间还有别的破坏面），
`kanban_comment` 说清缺什么 + 需要什么才能继续，再 `kanban_block` —— 不要因为一部分不全就整份报告不做，
也不要自己扩大工作面（例如顺手去修 Hermes 源码）。
