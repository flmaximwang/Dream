---
name: parse-csv-with-pandas
description: Use when 要用 pandas 读一份 CSV、按列分组统计。触发：「pandas 读 csv」「groupby 统计」
---

# 用 pandas 读 CSV 并分组统计

## When to Use

任何要把一份 CSV 读进 DataFrame、按一个键分组、对若干列求和或计数的场合。

## 步骤

1. `df = pandas.read_csv("data.csv")`
2. 列名带空格或重复时先规整：`df.columns = [c.strip() for c in df.columns]`
3. 分组统计：`df.groupby("site", dropna=False)["count"].sum()`
4. 日期列统一：`pandas.to_datetime(df["date"], errors="coerce")`

## 坑

- `read_csv` 默认把空串读成 NaN；要保留空串加 `keep_default_na=False`。
- 大文件用 `chunksize=` 分块处理，别一次读进内存。
- `groupby` 默认丢掉全 NaN 的键；要保留加 `dropna=False`。

这套做法对任何装了 pandas 的环境都成立，与在哪台机器上跑无关。
