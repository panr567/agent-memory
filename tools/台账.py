#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读 / 写 jsonl 账本的公共函数。

2026-11-06 加。用户「先优化代码再加进去」。
抽出来的直接原因：`json.loads(行)` 在 `tools/` 下出现过 **15 次**，
每个函数都自己写一遍「读文件 → 按行 → 解析 → 跳过坏行」。

但重复本身还不是最贵的。**贵在它让「字段名写错」变成静默失败**：
今晚我把 signals 的 `什么` 写成 `说明`，候选样本全空，
程序一声不响跑完了，还报了「没有新东西可长」。四轮之后才发现。

**只做一件事：把 jsonl 读成 dict 列表。**

原来这里还写了 `写行` / `追加` 两个函数，但没有任何调用方
（四个文件只 import 了 `读行`）——**零调用方的代码不是资产，是不需要读的负担**，
2026-11-06 子 AI 挑出来后删掉了。等真有第二个写场景再回来加。

两个约定：
1. **加锁不是这里的活**。`locklib.锁()` 由调用方在「读→改→整写」外面套。
2. **解析坏行就跳过，但要能被看见**：`读行(..., 报错=True)` 会把坏行数打出来。
   静默跳过是今晚三次「看着像功能没做」的共同原因。
"""
import io
import json
import os

try:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except Exception:
    pass


def 读行(路径, 报错=False):
    """读一个 jsonl 文件，每行一个 dict。坏行跳过。

    返回 list；文件不存在返回空 list（不抛）。
    """
    出 = []
    坏 = 0
    if not os.path.exists(路径):
        return 出
    with io.open(路径, encoding='utf-8', errors='replace') as f:
        for 行 in f:
            行 = 行.strip()
            if not 行:
                continue
            try:
                x = json.loads(行)
            except Exception:
                坏 += 1
                continue
            if isinstance(x, dict):
                出.append(x)
            else:
                坏 += 1
    if 报错 and 坏:
        print('（%s 里有 %d 行解析不了，已跳过）' % (os.path.basename(路径), 坏))
    return 出
