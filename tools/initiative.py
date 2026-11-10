#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主动性账本：我自己立的「想做的事」（2026-11-10 加）。

**为什么另开一本账**：`signals.jsonl` 里的全是**欠账**——没干完的活、检查挂了、
他纠正了我。那些东西是"被推着走"。这个账本记的是**没人要求、我自己想做的**。
两本账分开，是因为它们该用不同的节奏：欠账要按时还，主动性要有空档才发芽。

**四条硬约束，防它变成许愿池**（这是这个工具全部的价值）：

1. 四样必填：想做啥 / 为什么 / 怎么做 / 怎么算做完了。缺一样拒收。
   写不出"为什么"就是想跟风；写不出"怎么算完"就是空话。
2. **同时在世最多 3 条**。满了不许再立——想做的事攒到第 4 条，说明前 3 条
   一条都没动。这跟"反复出事三次才动"是同一个思路：约束比热情有用。
3. `done` 必须写「做成了什么」，写不出就是没做成，不许空销。
4. 立的时候记账，销的时候也记账。账是给人看的，不是给我表白的。

用法：
  python tools/initiative.py add --想要 "..." --为什么 "..." --怎么做 "..." --算完成 "..."
  python tools/initiative.py list
  python tools/initiative.py done --id <id> --做成了 "..."
  python tools/initiative.py drop --id <id> --为什么 "..."
"""
import argparse
import datetime as _dt
import io
import json
import os
import random
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
记忆 = os.path.join(根, "memory")
账 = os.path.join(记忆, "initiatives.jsonl")

在世上限 = 3
必填 = ["想要", "为什么", "怎么做", "算完成"]


def 读():
    if not os.path.exists(账):
        return []
    出 = []
    for l in io.open(账, encoding="utf-8"):
        l = l.strip()
        if not l:
            continue
        try:
            出.append(json.loads(l))
        except Exception:
            continue
    return 出


def 写(条们):
    os.makedirs(os.path.dirname(账), exist_ok=True)
    with io.open(账, "w", encoding="utf-8") as f:
        for x in 条们:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")


def 在世(条们):
    return [x for x in 条们 if x.get("状态") == "在世"]


def cmd_add(a):
    缺 = [k for k in 必填 if not (getattr(a, k, "") or "").strip()]
    if 缺:
        print("拒收：缺 %s" % " / ".join(缺))
        print("  想做啥、为什么、怎么做、怎么算做完——四样缺一样就不是计划，是念头。")
        return 2

    from locklib import 锁
    with 锁("initiative", 记忆根=记忆, 超时=15):
        条们 = 读()
        活 = 在世(条们)
        if len(活) >= 在世上限:
            print("拒收：已经在世 %d 条（上限 %d），先做完或者明确放弃一条" % (len(活), 在世上限))
            for x in 活:
                print("  - %s %s" % (x.get("id"), x.get("想要")[:40]))
            print("  立一条新的却一条不动 = 许愿池。")
            return 2

        now = _dt.datetime.now()
        x = {
            "id": "ini-%s-%s" % (now.strftime("%m%d%H%M%S"), "%04x" % random.randint(0, 0xFFFF)),
            "立": now.isoformat(timespec="seconds"),
            "想要": a.想要.strip(),
            "为什么": a.为什么.strip(),
            "怎么做": a.怎么做.strip(),
            "算完成": a.算完成.strip(),
            "状态": "在世",
        }
        条们.append(x)
        写(条们)
    print("立了：%s" % x["id"])
    print("  想要：%s" % x["想要"])
    print("  算完成：%s" % x["算完成"])
    print("  （在世 %d/%d。做成了跑 done，不做了跑 drop，别挂着）" % (len(活) + 1, 在世上限))
    return 0


def cmd_list(a):
    条们 = 读()
    活 = 在世(条们)
    死 = [x for x in 条们 if x.get("状态") != "在世"]
    if not 条们:
        print("主动性账本是空的。还没立过自己想做的事。")
        return 0
    print("在世 %d 条（上限 %d）：" % (len(活), 在世上限))
    for x in 活:
        print()
        print("  %s  立于 %s" % (x.get("id"), x.get("立")))
        print("    想要：%s" % x.get("想要"))
        print("    为什么：%s" % x.get("为什么"))
        print("    怎么做：%s" % x.get("怎么做"))
        print("    算完成：%s" % x.get("算完成"))
    if 死:
        print()
        print("已了结 %d 条：" % len(死))
        for x in 死:
            print("  %s [%s] %s" % (x.get("id"), x.get("状态"), x.get("想要")[:40]))
    return 0


def _了结(a, 状态, 字段, 值):
    from locklib import 锁
    if len((值 or "").strip()) < 5:
        print("太短：写不出做成了什么（或为什么不做），等于没结。")
        return 2
    with 锁("initiative", 记忆根=记忆, 超时=15):
        条们 = 读()
        命中 = None
        for x in 条们:
            if x.get("id") == a.id:
                命中 = x
                break
        if 命中 is None:
            print("账本里没有 %s" % a.id)
            return 1
        if 命中.get("状态") != "在世":
            print("这条已经 %s 了" % 命中.get("状态"))
            return 2
        命中["状态"] = 状态
        命中[字段] = 值.strip()
        命中["结于"] = _dt.datetime.now().isoformat(timespec="seconds")
        写(条们)
    print("%s：%s" % (状态, a.id))
    print("  %s：%s" % (字段, 值[:100]))
    return 0


def cmd_done(a):
    return _了结(a, "做成了", "做成了什么", a.做成了)


def cmd_drop(a):
    return _了结(a, "放弃了", "为什么不做", a.为什么)


def main():
    p = argparse.ArgumentParser(description="主动性账本（我自己想做的事）")
    sub = p.add_subparsers(dest="cmd", required=True)

    ad = sub.add_parser("add", help="立一条（四样必填，在世上限 3）")
    ad.add_argument("--想要", required=True)
    ad.add_argument("--为什么", required=True)
    ad.add_argument("--怎么做", required=True)
    ad.add_argument("--算完成", required=True)

    sub.add_parser("list")

    dn = sub.add_parser("done")
    dn.add_argument("--id", required=True)
    dn.add_argument("--做成了", required=True)

    dr = sub.add_parser("drop")
    dr.add_argument("--id", required=True)
    dr.add_argument("--为什么", required=True)

    a = p.parse_args()
    return {"add": cmd_add, "list": cmd_list,
            "done": cmd_done, "drop": cmd_drop}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
