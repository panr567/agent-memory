#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短期记忆（工作记忆）：这一轮在干什么、到哪一步、下一步从哪接。

**为什么要另开一层**：现有的记忆全是"事后"的——
`facts/` 是结论，`summaries/` 是日记，`handoff.py` 是收工才写的交接卡。
可是**干活干到一半断了**，这三样都接不上：事实还没沉淀、日记还没写、交接卡还没生成。

于是加这一层：**随时可写、每轮注入、不算账、不压缩**。
它跟 handoff 的分工要说清（不然会变成两份交接卡）：

- `handoff`：收工时写的现场摘要，写一次，下次开工读一次
- `working`：干活途中随时改的一行状态，**每轮都注入**，覆盖写

**只存一条**，不追加历史——历史在 `log.jsonl` 里。当前状态永远只有一份，
免得"三个地方各说各话"。

用法：
  python tools/working.py set --doing "..." [--where "..."] [--next "..."]
  python tools/working.py show
  python tools/working.py clear
  python tools/working.py log --n 10
"""
import argparse
import datetime as dt
import io
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
记忆 = os.path.join(根, "memory")
目录 = os.path.join(记忆, "working")
当前 = os.path.join(目录, "current.json")
流水 = os.path.join(目录, "log.jsonl")


def 读当前():
    try:
        return json.load(io.open(当前, encoding="utf-8"))
    except Exception:
        return None


def cmd_set(a):
    if not (a.doing or "").strip():
        print("--doing 不能空：说不出在干什么，这条就没用")
        return 2
    os.makedirs(目录, exist_ok=True)
    旧 = 读当前() or {}
    条 = {
        "在干": a.doing.strip(),
        "到哪": (a.where or "").strip(),
        "下一步": (a.next or "").strip(),
        "更新": dt.datetime.now().isoformat(timespec="seconds"),
        "改了几次": int(旧.get("改了几次") or 0) + 1,
    }
    from locklib import 锁
    with 锁("working", 记忆根=记忆, 超时=15):
        io.open(当前, "w", encoding="utf-8").write(json.dumps(条, ensure_ascii=False, indent=1))
        with io.open(流水, "a", encoding="utf-8") as f:
            f.write(json.dumps(条, ensure_ascii=False) + "\n")
    print("工作记忆已更新（第 %d 次）" % 条["改了几次"])
    print("  在干：%s" % 条["在干"])
    if 条["到哪"]:
        print("  到哪：%s" % 条["到哪"])
    if 条["下一步"]:
        print("  下一步：%s" % 条["下一步"])
    return 0


def cmd_show(a):
    条 = 读当前()
    if not 条:
        print("还没有工作记忆。跑一次 set --doing \"...\" 就有了。")
        return 0
    print("工作记忆（更新于 %s，改过 %s 次）" % (条.get("更新", "?"), 条.get("改了几次", "?")))
    print("  在干：%s" % 条.get("在干", ""))
    if 条.get("到哪"):
        print("  到哪：%s" % 条["到哪"])
    if 条.get("下一步"):
        print("  下一步：%s" % 条["下一步"])
    return 0


def cmd_clear(a):
    if not os.path.exists(当前):
        print("本来就是空的")
        return 0
    from locklib import 锁
    with 锁("working", 记忆根=记忆, 超时=15):
        try:
            os.remove(当前)
        except Exception:
            pass
    print("工作记忆已清空（流水还留着）")
    return 0


def cmd_log(a):
    if not os.path.exists(流水):
        print("流水是空的")
        return 0
    with io.open(流水, encoding="utf-8") as f:
        行 = [l.strip() for l in f if l.strip()]
    for l in 行[-a.n:]:
        try:
            x = json.loads(l)
        except Exception:
            continue
        print("%s  %s" % (x.get("更新", "")[:16], x.get("在干", "")[:70]))
    print("\n共 %d 条流水，显示最后 %d 条" % (len(行), min(a.n, len(行))))
    return 0


def 供注入():
    """给插件用的：返回一段可直接拼进系统提示的文本。没有就返回空串。"""
    条 = 读当前()
    if not 条:
        return ""
    行 = ["【工作记忆（会话断了也能接上）】", "- 在干：%s" % 条.get("在干", "")]
    if 条.get("到哪"):
        行.append("- 到哪：%s" % 条["到哪"])
    if 条.get("下一步"):
        行.append("- 下一步：%s" % 条["下一步"])
    行.append("- 更新于 %s（改过 %s 次）。这不是结论，是现场；收工时该写进 handoff。" % (条.get("更新", ""), 条.get("改了几次", 0)))
    return "\n".join(行)


def main():
    p = argparse.ArgumentParser(description="短期记忆（工作记忆）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("set")
    s.add_argument("--doing", required=True)
    s.add_argument("--where", default="")
    s.add_argument("--next", default="")

    sub.add_parser("show")
    sub.add_parser("clear")

    lg = sub.add_parser("log")
    lg.add_argument("--n", type=int, default=10)

    a = p.parse_args()
    return {"set": cmd_set, "show": cmd_show, "clear": cmd_clear, "log": cmd_log}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
