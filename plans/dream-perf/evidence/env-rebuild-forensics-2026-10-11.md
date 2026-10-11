# 定案：`installs/*/environments/<hash>/` 为什么被「删掉又重建」，重建窗口内活进程为什么失联

**定案一句话**：删除者不是 `pm` 的回收流程，而是 **asset-quant-invest profile 的 agent 会话 `20261011_101759_0677af` 自己写的一个一次性回收脚本 `cache/scratch/reclaim_hermes.py`（10:48:56 写、10:48:58 跑）**：它按 `facts.json` 里记的「选中 hash」当白名单，把 `installs/b200403c2b90970e/environments/` 下另外 **19 个**目录 `shutil.rmtree` 掉了 —— 其中 `51e7b11d1f0e44bcba68d977a823d512` 正是**运行中的默认 profile 网关（pid 49012/49016，Oct 10 15:59:57 启动）**绑定的环境。**重建也是同一个 agent**：11:02:01 它用 `cp -c -R ef2099f3… 51e7b11d…`（APFS clone）把同一路径补回来，所以「同一个 hash 原地出现」——不是 Hermes 的原子切换，是人工拷回。

- 窗口：**10:51:12.995 → 11:02:03**（首次 `FileNotFoundError` → 拷回），≈ **10 分 50 秒**；日志里最后一条 `FileNotFoundError` 是 **11:02:05.972**。
- 破坏面：该网关进程里**每一次新建 httpx/openai 客户端**都失败（`httpx/_config.py:40 → ssl.create_default_context(cafile=certifi.where())` 读的是被删 venv 里的 `certifi/cacert.pem`），所以模型调用全断；已导入到内存的模块、已开着的流不受影响 ⇒ 表现为「部分子代理动、其余卡死」。
- 硬约束遵守情况：本次只读 + 只写本文件；未改任何 profile 的配置/技能/密钥/cron；未删任何文件；**没有重启共享网关**。

---

## 1 时间线（全部为实测/原始记录）

| 时刻 | 事件 | 证据 |
|---|---|---|
| 10:45:25 | 用户在 asset-quant-invest 会话里提要求：「回收一下，codegraph 应该是项目级的，不要存储在 hermes 内部」 | `profiles/asset-quant-invest/state.db` messages id=45189 |
| 10:48:56 | agent 写 `cache/scratch/reclaim_hermes.py`（`KEEP_ENV = "ef2099f3d361"  # the venv facts.json records as selected`） | 文件 birth/mtime = 10:48:56；write_file 结果 id=45234 |
| 10:48:58 | 跑 `python3 …/cache/scratch/reclaim_hermes.py` | 工具调用 id=45235（`{"name": "terminal", "arguments": {"command": "python3 …/reclaim_hermes.py"}}`） |
| 10:51:12.995 | 首条 `FileNotFoundError`（`gateway.error.log`，provider=custom base_url=https://monk.party/v1 model=monk） | `grep -a "FileNotFoundError: \[Errno 2\]" gateway.error.log`（窗口内首/末条见 §4） |
| 10:51:56 | 脚本输出回给 agent：19 行 `rm … installs/b200403c2b90970e/environments/<hash>`，`envs left: ['…/ef2099f3d36145779f2e00c200d4279a']` | state.db messages id=45275（原文见 §8） |
| 11:01:05 | 用户：「你好像把 model 配置搞坏了」 | messages id=45345 |
| 11:02:01→11:02:13 | agent 执行 `cp -c -R "$E/ef2099f3…" "$E/51e7b11d…"` → 输出 `restored 51e7b11d (APFS clone)`；随后用该 venv 跑通 openai/httpx/truststore/certifi | messages id=45369 / 45372 |
| 11:02:05.972 | 最后一条 `FileNotFoundError`；11:03 起计数 0 | `awk` 计数 = 0（messages id=45414）；本机复核见 §4 |
| 11:03:00 | `lsof` 复核：只有 pid 49012/49016 映射过被删的 env | messages id=45398 |
| 11:04:09/11:04:48 | 重写为 `scripts/reclaim-hermes.py`（默认 dry-run；env 删除前 lsof + `.leases` 双保险；`--self-test`） | 文件 birth 11:04:48 |

**先决条件（为什么 10:51 那一刻 `51e7b11d` 看起来像"孤儿"）**：Oct 11 **01:49:20–01:49:57** 有一波按 profile 触发的 PM sync（每个 profile 各自写 receipt），其中至少两次真的重建了生成物：`venv_rebuild.ok=true`。这批 sync 把 `facts.json` 改写成指向新生成物 `ef2099f3…`（`facts.json` mtime = **01:49:56**，其 `environment` 字段 = `…/ef2099f3…/venv`；`ef2099f3…` 目录 birth = 01:49:48）。
证据：`profiles/baby/logs/update_receipts/pm_20261010T174938Z-sync-34490-…json`（started 17:49:28Z / finished 17:49:38Z，`venv_rebuild.ok=true`）、`profiles/daily-job/logs/update_receipts/pm_20261010T174956Z-sync-34576-…json`（17:49:47→17:49:56Z，`ok=true`）、`profiles/asset-quant-invest/logs/update_receipts/pm_20261010T174921Z-sync-34449-…json`（`ok=false / "already in sync"`）。

---

## 2 Q1：触发者是谁 —— 定案依据

**是 agent 写的一次性回收脚本，不是 Hermes 的 GC/供给流程，也不是 `hermes update`、插件安装、`hermes doctor`、磁盘压力下的系统清理、也不是 App Store 装机期的系统级清理。**

### 2.1 触发者的原文（v1 脚本，10:48:56）

`~/.hermes/profiles/asset-quant-invest/cache/scratch/reclaim_hermes.py`：

```python
KEEP_ENV = "ef2099f3d361"  # the venv facts.json records as selected
...
live = subprocess.run(["ps", "-Ao", "pid,command"], capture_output=True, text=True).stdout
hits = [l for l in live.splitlines() if "installs/" in l and "grep" not in l]
assert not hits, hits
print("safety: no live process under installs/ OK")
...
# 4. orphan uv venvs: keep only the one facts.json records
for d in sorted(glob.glob(H + "/installs/*/environments/*")):
    if os.path.basename(d).startswith(KEEP_ENV):
        continue
    killed += sz(d)
    rm(d)
```

两个致命点，都是**判据错**而非手滑：

1. `KEEP_ENV` 取的是 `facts.json` 的「最近选中」；但 `pm/environments.py:448-475`（`activate_dependencies`）的契约是 **"Already-running processes are never switched after a dependency install"**：进程 boot 时 `lease_generation()` 锁住它启动时的那个 generation，之后一直从它 import。所以 **`facts.json` 的 selected ≠ 活进程手里那个**。
2. 那个 `assert` 检查的是 **`ps` 的命令行里是否出现 `installs/`**。而这个网关的命令行是 store python（`/Users/maxim/.hermes/tools/python-3.14.7…/bin/python3 -I -c …`，见 §8 `ps -E`），路径只出现在 **PYTHONPATH/PATH 环境变量和打开的文件描述符**里 —— `ps` 永远看不到。真正能看到的两个官方信号 (`lsof` 映射、`.leases/*`) 都被漏掉了。

### 2.2 实际删除清单（脚本自己的输出，19 个）

会话 id=45275 的输出（节选末段，原文 19 行）：

```
4. orphan environments
   rm 0.00 GB installs/b200403c2b90970e/environments/028894cafbc645aeb75ac2d10b194b9c
   rm 0.00 GB installs/b200403c2b90970e/environments/05032129c9c74c25a9eaf55e124c6b44
   …
   rm 0.00 GB installs/b200403c2b90970e/environments/51e7b11d1f0e44bcba68d977a823d512   ← 活的
   …
   rm 0.00 GB installs/b200403c2b90970e/environments/fa3e3623e6374a3abe04ed4f262de11b
reclaimed ~ 50.78 GB
envs left: ['/Users/maxim/.hermes/installs/b200403c2b90970e/environments/ef2099f3d36145779f2e00c200d4279a']
```

我复核：从该会话 dump 里 `grep -o "rm .*environments/[0-9a-f]*" | sort -u | wc -l` = **19**（脚本里 `rm()` 先删后 `du`，所以每行都打成 0.00 GB，别把它当体积）。整个 `~/.hermes` 之后确为 25G（我实测 `du -sh ~/.hermes` = `25G`，`df -h /` 可用 224Gi），与 agent 自述的「76.9 GB → 25 GB」一致；**回收诉求本身是用户提的**（10:45:25），误伤的是活环境这一条判据。

### 2.3 为什么 Hermes 自己的回收流程不会犯这个错（对照）

- `hermes pm gc`（`pm/cli.py:511-529`）走 `collect_generations()`（`hermes_cli/runtime_state.py:184-208`），三重跳过：**is symlink/非目录 → 跳过；`resolve()==selected` → 跳过；`.lease-managed` 不存在或 mtime 小于 `min_age_seconds=86400` → 跳过；`leases_held(generation)` 为真 → 跳过**。本例里 `51e7b11d/.lease-managed` mtime = 01:49:56，10:51 时才 9 小时 → `pm gc` 根本不会碰它；即便过了 24h，活网关持有的 lease 也会让它留下。
- `collect_superseded_generations()`（`hermes_cli/venv_sync.py:130-151`，sync 之后自动跑）用的是同一个 `collect_generations`。
- `collect_orphan_install_states()`（`pm/install_states.py:80-104`）删的是**整个 install state 目录**（checkout 已消失那种），且先 `_held()`（`*.leases` 有活锁就跳过）。
⇒ **结论：删环境这件事有官方入口，且官方入口在本例里是安全的；绕过它才出事。**

---

## 3 Q2：重建是怎么发生的、窗口多长、破坏面是什么

### 3.1 「同一 hash 重新出现」= agent 自己 `cp -c -R` 拷回（不是原子切换）

原命令与输出（state.db messages id=45369 / 45372）：

```
cp -c -R "$E/ef2099f3d36145779f2e00c200d4279a" "$E/51e7b11d1f0e44bcba68d977a823d512"
echo "restored 51e7b11d (APFS clone)"
→ restored 51e7b11d (APFS clone)
→ openai 2.24.0 | httpx 0.28.1 | truststore ok | certifi …/51e7b11d…/venv/lib/python3.14/site-packages/certifi/cacert.pem
→ SSL context built OK
```

**我独立复核了这条命令（不看它的自述）**，用 birth time 取证：

```
birth=2026-10-11 11:02:03  …/environments/51e7b11d1f0e44bcba68d977a823d512            ← 目录本身 11:02:03 才诞生
birth=2026-10-11 11:02:03  …/51e7b11d…/venv                                         ← 目录
birth=2026-10-11 11:02:03  …/51e7b11d…/venv/bin/python3（symlink）                  ← 符号链接也是新的
birth=2026-10-11 01:49:48  …/51e7b11d…/venv/pyvenv.cfg（普通文件）                   ← 文件出生时间被保留
birth=2026-10-11 01:49:49  …/51e7b11d…/workspace/uv.lock（普通文件）
```
+ `51e7b11d/venv/lib/python3.14/site-packages` 下 **43930 个文件与 `ef2099f3` 完全同集合**（`diff` 为空），窗口内**改动 4009 个条目全是目录、0 个文件**（`find -type f` = 0）—— 这正是「按文件克隆、目录与符号链接新建」的指纹。
+ 决定性旁证：`51e7b11d/.leases/4af5b36fdfce405db17867e3e6061593` 与 `ef2099f3/.leases/` 下**同名、同 birth（11:00:42）**，但 inode 不同（478089452 vs 478026901）。一个 `uuid4()` 租约名不可能在两个目录里各自独立生成 —— 只能是 11:02 整目录拷贝时被一起搬过来（拷贝时刻必须晚于 11:00:42、早于 ef2099f3 下一份租约 11:05:05）。
+ 工具指纹实验（scratch 内自测，源文件取 `ef2099f3/venv/pyvenv.cfg`，birth=01:49:48）：`cp -c` → birth 保留 01:49:48；`cp -p` → 保留；`ditto` → 保留；裸 `cp` → birth 变 11:35:42。**观测到的「文件保留 birth、目录/symlink 取新」与 `cp -c -R` 完全吻合。**

⇒ 那段「路径不存在的窗口」是真实存在的：**agent 自己的 `11:01:47` 检查就打印了 `GONE 51e7b11d1f0e44bcba68d977a823d512`**（messages id=45361），11:02:03 之后同一路径又回来。

### 3.2 破坏面：为什么是「新建 TLS 客户端全挂」

- 该 venv 里 `httpx/_config.py` 第 36-40 行：`verify=True` 且无 `SSL_CERT_FILE` 时走 `ctx = ssl.create_default_context(cafile=certifi.where())` ⇒ **每次新建 client 都要读 `<venv>/lib/python3.14/site-packages/certifi/cacert.pem`**。目录没了 → `ssl.py:729 → truststore/_api.py:160 → FileNotFoundError: [Errno 2] No such file or directory`（C 层报错不带文件名，所以日志里看不到是哪个文件；这一点会让排查误判成网络/证书问题 —— 卡片里第 6 条「网络与证书本身正常」正是这样被排除的）。
- Hermes 的 client 是**按请求**新建的：`agent/client_lifecycle.py:461 → agent/chat_completion_helpers.py:3011/3117 → agent/relay_llm.py:337`（traceback 原文见 §8）。⇒ 窗口内**所有**模型调用失败；已经导入到内存的模块、已建立的流照常工作 ⇒ 不是整进程死，而是「新调用全失败、在途调用还能跑一会儿」，于是 8/10 子代理 idle、最长 33.9 分钟。
- 次生面（同一进程里被写死的另两处）：`ps -E -p 49016` 显示该网关
  `PATH=…:…/environments/51e7b11d1f0e44bcba68d977a823d512/venv/bin:…`、
  `PYTHONPATH=/Users/maxim/.hermes/hermes-agent:…/environments/51e7b11d…/venv/lib/python3.14/site-packages`。
  窗口内这条 PATH 项指向不存在的目录（子进程里 venv 提供的控制台脚本会解析失败），PYTHONPATH 项同理。这是「活进程失联」的第二层，容易被忽略。
- 破坏范围（我逐行核过）：窗口 `10:51:00–11:03:00` 内 `gateway.error.log` 里出现的 `environments/<hash>` **只有 `51e7b11d…` 一个**（1710 次），窗口内首条 `FileNotFoundError: [Errno 2]` = **10:51:13**、末条 = **11:02:05**。其余 18 个被删环境在 lsof 里确实无人映射（`11:03:00` 复核：只有 pid 49012/49016 映射 51e7b11d）。⇒ 误伤面 = 1 个活环境（外加 17G 需重建的环境、13.6G curator 快照、10.2G `.codegraph`、10.7G 手工备份 —— 后三类是用户要的回收）。
- **另一个必须记住的坑**：`lsof` 对已 unlink 的文件**仍会打印旧路径**。网关当前 fd 上的租约是 `51e7b11d/.leases/fbbe15cf…`(inode 476293579) 与 `f7ffa061…`(476293584)，而目录里**根本没有这两个文件**（`ls -i` 只有 478089452）—— 是删除时被 unlink 的 inode。所以「lsof 说是活的」在删除之后是**读法正确的巧合**，在复制回来之后又会变成**假证据**（§5.2 会用到这一点）。

### 3.3 窗口长度

**10:51:12.995 → 11:02:03.0 ≈ 10 分 50 秒**（首条 `FileNotFoundError` → 克隆落地）；到日志彻底安静是 11:02:05.972，即 **≈ 10 分 53 秒**。

---

## 4 Q3：规避 / 修法（官方机制优先）

### 4.1 该怎么做（按优先级）

1. **删环境只走官方入口**：`hermes pm gc`。它 = store GC + `collect_generations`（看 selected / 24h 年龄 / `.leases` 活锁）+ `collect_runtime_generations` + `collect_orphan_install_states`（`pm/cli.py:511-529`）。**任何**手写 `rmtree` 都不许碰 `installs/*/environments/`。
2. **要立刻腾空间时**：正确顺序是「先让持有者不再用，再回收」，即 *重启持有该 generation 的进程*（本例 = 默认 profile 的网关 pid 49012/49016；重启后它 boot 时按 `facts.json` 选 `ef2099f3…` 并 lease 它），然后 `hermes pm gc`。Hermes 为此有现成机制：`pm/environments_adopt.py:98-117`（`restart_needed()` / `adopt_selected()`）——进程跑在旧 generation 上且有新发布时会给出「依赖生成物 X 在本进程加载 Y 之后发布」的重启理由，可据此安排重启窗口，而不是在跑着的时候抽掉目录。
3. **如果非要自己写回收脚本**（本仓库确实需要一份）——必须用**两个官方信号**做闸：
   - `.leases/` 非空（`hermes_cli/runtime_state.py:149-181` 的 `lease_directory` 就是 Hermes 自己写的「有人在用」标记；记得用 `flock(LOCK_NB)` 判活锁，而不是「文件存在」）；
   - 对每个活 `hermes` 进程取 `lsof` 的 `environments/<hash>` 做映射检查（脚本 v2 已经这么做了）。
   **而且要先跑 dry-run。** 这仍是补丁，1 才是正路。
4. **让探针提前报警**（`plans/dream-perf/tools/perf-probe.py` 加一条，也符合 T-007 的「同一把尺子」）：
   - 差集告警：`{每个活 hermes 进程 lsof 出的 env hash} - {实际存在的目录}` 非空 ⇒ 立即报「活进程引用了不存在的 env」（本脚本 v2 的 `live_env_hashes()` 可直接借）；
   - 计数告警：`gateway.error.log` 里 `FileNotFoundError: [Errno 2]` 的每分钟计数 > 0 且伴随 `provider=` ⇒ 报「运行期地基被抽」，并把窗口长度写进日志。

### 4.2 现存风险（本次必须说清：修复是**临时的**，会复发）

`cp -c -R` 把**文件**补回来了，但**没有**把租约补回来：

- `51e7b11d` 现在**没有任何活锁**：目录里唯一的租约文件 `4af5b36…`(inode 478089452) 是 11:02 从 `ef2099f3` 拷来的 **新 inode**，flock 可独占（我实测：对 `51e7b11d/.leases/*` 取 `LOCK_EX|LOCK_NB` 成功；对 `ef2099f3/.leases/*` 三个文件全部 `EWOULDBLOCK` ⇒ 别人正持有）。网关手上那两个租约 inode 是**已 unlink 的**，`leases_held()` 扫目录时看不见它们。
- `51e7b11d` 又是 **unselected**（`facts.json` → `ef2099f3`），`.lease-managed` mtime = **01:49:56**。
- ⇒ 从 **2026-10-12 01:49:56**（marker mtime + `min_age_seconds=86400`）起，**任意一次 sync 尾巴（`venv_sync.py:123/581`）或 `hermes pm gc` 都会再次 `rmtree` 掉它**，而网关仍然跑在它上面 —— **同一个故障会自动重演**。

```
$ python3 -B check_leases.py        # 我写的一次性只读检查
== 51e7b11d1f0e44bcba68d977a823d512 marker: True
   lease 4af5b36fdfce inode 478089452 locked_by_someone: <可独占 ⇒ 无人持有>
== ef2099f3d36145779f2e00c200d4279a marker: True
   lease 27e4fa0bb3bf inode 478186236 locked_by_someone: EWOULDBLOCK
   lease 4af5b36fdfce inode 478026901 locked_by_someone: EWOULDBLOCK
   lease f117a08c422b inode 478139949 locked_by_someone: EWOULDBLOCK
$ ls -i …/51e7b11d…/.leases/    # 只有拷贝来的那个；网关 lsof 报的 476293579/476293584 不在其中（已 unlink）
```

**建议动作（都属官方路径，本会话按约束没有执行）**：方便时重启默认 profile 的网关 → 它绑回 `ef2099f3…` 并持有 lease → 之后 `51e7b11d` 这个克隆副本可安全删除（`hermes pm gc` 会处理，或手工删都行）。**重启之前，不要再跑任何 env 删除**（含 v2 脚本 `--apply`）。

---

## 5 最小复现步骤（可照做、每步可观察）

1. 准备 A：一个长驻进程从 env A 启动 ⇒ 它在 `A/.leases/` 里留一个 flock 住的租约，`lsof` 可见 `environments/A`。
2. 让一次 sync 发布 env B（`pm/packages.py:412` 新建 `environments/<uuid>`，`facts.json` 改写指向 B）。
   - 断言：**A 上仍有活租约**；`hermes pm gc` 此时**不会**删 A（`collect_generations` 的 24h + lease 双闸）。
3. 手删 A（`shutil.rmtree(environments/A)`），然后从**同一个长驻进程**里新建一个 httpx/openai 客户端。
   - 断言：`FileNotFoundError: [Errno 2]`，traceback 为 `httpx/_config.py:40 → ssl.create_default_context(cafile=certifi.where())`；`gateway.error.log` 里 `provider=…` 的 `API call failed` 风暴开始；进程本身仍活着。
4. `cp -c -R environments/B environments/A`。
   - 断言：新建客户端恢复；但 `A/.leases/*` 里**没有**活锁（`flock` 可独占）⇒ 24h 后 `hermes pm gc` 会再删 A，回到第 3 步。
   - 回归闸（Hermes 自带的现成测试，可直接跑）：`tests/pm/test_runtime_boot_gc.py`（leased/selected generation 不被回收）、`tests/pm/test_lease_reselection.py`（re-read after leasing）。

---

## 6 与卡片「地面真值」逐条核对（差异都写出来，不照抄）

| 卡片原文 | 核对结果 |
|---|---|
| 09:50 该环境目录还在且可用 | 一致（同一会话另有 01:30/01:56 两条 `process-results` 记录用 `51e7b11d/venv/bin/hindsight-embed` 成功运行） |
| 10:51:33 `environments/` 被改动（mtime=10:51:33），`51e7b11d` 一度不存在，只剩 `ef2099f3` | 方向正确，**精确说明**：现在该目录 mtime 已是 11:02:03（克隆时又改过），10:51:33 无法再复测；删除区间由「脚本 10:48:58 起跑、10:51:56 回输出、首条错误 10:51:12.995」夹住。agent 自己 11:01:47 的检查打印了 `GONE 51e7b11d…` + `GONE` 其余 7 个日志引用的 hash |
| 10:51:13 起 `FileNotFoundError` …10:51 一分钟 498 行、10:52 又 362 行 | 我用窗口扫描复核：首条 `FileNotFoundError: [Errno 2]` = **10:51:13**（10:51:12.995 那条是 `Streaming failed before delivery`），每分钟计数 10:51=14→…→11:01=60（**卡片那两个数字我按「含所有 WARNING 行」的宽口径没复现出来，改成实测的每分钟 14–60 条**） |
| 该错误只在新建 httpx/openai 客户端时触发 | 一致，并已定位到具体行：`httpx/_config.py:40 → ssl.create_default_context(cafile=certifi.where())`（+ `agent/client_lifecycle.py:461` 按请求新建） |
| 11:02:03 同一 hash 目录重新出现且可用；11:02:05 后无 FileNotFoundError；子代理 11:03:27 恢复 | 一致（末条 11:02:05.972；11:03 起计数 0；11:03:33 起只剩 `APITimeoutError`） |
| 网络/证书正常、无证书变量、cleanup.log 不含 installs | 一致（可另证：`SSL_CERT_FILE` 未设，httpx 走的是 certifi 默认分支，所以断的是**文件**不是网络） |
| 背景压力：App Store 装 Xcode、load 16.7、空闲内存 136MB、`/usr/bin/python3` 退 69 | 与本次定案**无因果关系**：删除发生在 `~/.hermes/installs/`（系统清理与 App Store 不碰这里），且磁盘当时 `/` 还有 178Gi 可用（脚本自己的 `df` 输出）。压力只是「为什么会有人要回收」的动机，不是触发者 |
| 「新 hash 01:49 建好、旧 hash 10:51 被处理」 | 一致：01:49:48–01:49:57 一波 profile sync（receipt `venv_rebuild.ok=true`）发布 `ef2099f3`；随后 10:51 的回收脚本把它当孤儿删掉 |
| 删除者只有「一个」？（歧义出口） | **只有一个动作源**：`cache/scratch/reclaim_hermes.py`（10:48:58 那次运行）。没有第二个删除者。但**多一层含义**：重建是临时补丁、租约丢失 ⇒ 会在 2026-10-12 01:49:56 后复发（§4.2），已写进本报告，不改工作面、不顺手改 Hermes 源码 |

---

## 7 附录：关键原始输出

**A. 活进程与它的绑定**（本机实测，只读）

```
$ ps -o pid,lstart,command -p 49012,49016
  PID STARTED                      COMMAND
49012 Sat Oct 10 15:59:57 2026     /Users/maxim/.hermes/tools/python-3.14.7+…/bin/python3 -I -c import os, re, sys…
49016 Sat Oct 10 15:59:58 2026     /Users/maxim/.hermes/tools/python-3.14.7+…/bin/python3 -I -c import os, re, sys…

$ lsof -p 49012 -p 49016 | grep -oE "environments/[0-9a-f]{32}" | sort -u
environments/51e7b11d1f0e44bcba68d977a823d512

$ ps -E -p 49016 | tr ' ' '\n' | grep -E "^PATH=|^PYTHONPATH="
PATH=/Users/maxim/.hermes/hermes-agent/.hermes/bin:/Users/maxim/.hermes/installs/b200403c2b90970e/environments/51e7b11d1f0e44bcba68d977a823d512/venv/bin:…
PYTHONPATH=/Users/maxim/.hermes/hermes-agent:/Users/maxim/.hermes/installs/b200403c2b90970e/environments/51e7b11d1f0e44bcba68d977a823d512/venv/lib/python3.14/site-packages
```

**B. 出错链路原文**（`~/.hermes/logs/gateway.error.log`，11:02:05 一条）

```
  File "/Users/maxim/.hermes/hermes-agent/agent/chat_completion_helpers.py", line 3011, in _open_chat_stream
    self.agent._create_request_openai_client(reason="chat_completion_stream_request", …)
  File ".../installs/b200403c2b90970e/environments/51e7b11d1f0e44bcba68d977a823d512/venv/lib/python3.14/site-packages/openai/_base_client.py", line 817, in __init__
  File ".../httpx/_transports/default.py", line 153, in __init__
  File ".../httpx/_config.py", line 40, in create_ssl_context        ← ssl.create_default_context(cafile=certifi.where())
  File "/Users/maxim/.hermes/tools/python-3.14.7+…/lib/python3.14/ssl.py", line 729, in create_default_context
    context.load_verify_locations(cafile, capath, cadata)
  File ".../truststore/_api.py", line 160, in load_verify_locations
    return self._ctx.load_verify_locations(cafile=cafile, capath=capath, cadata=cadata)
FileNotFoundError: [Errno 2] No such file or directory
WARNING agent.conversation_loop: API call failed (attempt 1/3) error_type=FileNotFoundError … provider=custom base_url=https://monk.party/v1 model=monk
```

**C. 删除脚本的会话记录**（`~/.hermes/profiles/asset-quant-invest/state.db`，session `20261011_101759_0677af`，messages 表）

```sql
select id, role, datetime(timestamp,'unixepoch','localtime'), tool_name, substr(content,1,200)
from messages where session_id='20261011_101759_0677af'
  and timestamp > strftime('%s','2026-10-11 10:44:00')-8*3600
  and timestamp < strftime('%s','2026-10-11 11:06:30')-8*3600 order by timestamp;
-- id=45189 user 10:45:25 「回收一下，codegraph 应该是项目级的…」
-- id=45233 assistant 10:48:56 write_file cache/scratch/reclaim_hermes.py
-- id=45235 assistant 10:48:58 terminal python3 …/reclaim_hermes.py
-- id=45275 tool 10:51:56 「BEFORE: 76.89 GB … rm 0.00 GB installs/…/environments/51e7b11d… … envs left: [ef2099f3…]」
-- id=45345 user 11:01:05 「你好像把 model 配置搞坏了」
-- id=45369 assistant 11:02:01 terminal cp -c -R ef2099f3… 51e7b11d…
-- id=45372 tool 11:02:13 「restored 51e7b11d (APFS clone) … SSL context built OK」
-- id=45393 tool 11:02:50 日志里 FileNotFoundError 的 traceback（含 51e7b11d 的 httpx/httpcore 路径）
-- id=45398 tool 11:03:00 「pid 49012 -> 51e7b11d…」「pid 49016 -> 51e7b11d…」
-- id=45414 tool 11:03:37 「any FileNotFoundError since 11:02:10? 0」
```

**D. 我这次取证用的命令**（都在 scratch，只读；不落在任何 profile/installs 目录）

```
# 目录/文件出生时间（判「目录被重建、文件被保留」）
stat -f "birth=%SB mtime=%Sm %N" -t "%F %T" <每个关键路径>
# 窗口内改动条目：文件 0 个、目录 4009 个
find …/51e7b11d…/venv/lib -type f -newermt "2026-10-11 10:45" ! -newermt "2026-10-11 11:10" | wc -l
# 两个 generation 文件集合比较
diff <(ls A/venv/lib/python3.14/site-packages) <(ls B/venv/lib/python3.14/site-packages)
# 拷贝工具是否保留 birth（scratch 自测）
cp -c / cp -p / cp / ditto  ← 前三个保留 birth，裸 cp 不保留
# 租约是否被活进程持有（flock LOCK_NB）
python3 -B ~/.hermes/cache/scratch/check_leases.py   # 一次性脚本，在 scratch（闲置 24h 会被清）
# 窗口内 traceback 出现的 env hash 只有 51e7b11d（1710 次）
```

---

**结论**：这是**人为误删 + 人为补回**，不是 Hermes 的回收/更新流程。定案可复现（§5），修法有官方入口（`hermes pm gc` / 先重启持有者再 gc），并且**当前状态仍会复发**（克隆副本无租约，2026-10-12 01:49:56 后任意 gc/sync 会再次删它）—— 需要在方便时重启默认 profile 网关收尾。
