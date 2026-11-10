#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""让检查从数据里长出来（2026-11-06 加）。

背景：`hardcheck.py` 原来的 8 项**硬编码在源码里**，加一条检查就得动 `.py`。
那意味着「长出新的动作」的入口永远只有我一只手——**这不是自我迭代，是自我手改**。

改法：检查变成数据（`memory/rules/checks.jsonl`，一行一条），本工具负责
**从已有账本里生出候选、验格式、写进去**。写进去就生效，不需要碰任何源码。

三步：
  1. `propose`  从 signals 差值里挑「反复 ≥3 次还没处理」的，转成候选规则
  2. 子 AI 看候选，给出能机检的那一条（有些差值根本写不成命令，得让它说「写不成」）
  3. `add`      验格式、验正则能编译，写进 checks.jsonl

**刻意不做的事**：不允许规则里带「执行代码」。那样 checks.jsonl 就成了任意代码执行入口，
一条写错的规则能把整套东西搞崩。只支持三种类型，够用且安全。

用法：
  python tools/rules.py list
  python tools/rules.py propose
  python tools/rules.py add --json '{"名":"...","类型":"文本匹配",...}'
  python tools/rules.py disable --名 "..."
"""
import argparse
import glob as _glob
import io
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
记忆 = os.path.join(根, "memory")
规则库 = os.path.join(记忆, "rules", "checks.jsonl")
候选库 = os.path.join(记忆, "rules", "candidates.jsonl")
信号库 = os.path.join(记忆, "signals.jsonl")

允许类型 = ("文件存在", "文本匹配", "目录文本匹配")


# 2026-11-06 抽公共模块：jsonl 读取统一走 tools/台账.py。
# 原来每个脚本各写一遍「读文件→按行→解析→跳过坏行」，tools/ 下有 15 处重复。
# 重复本身还不是最贵的——贵在它让「字段名写错」变成静默失败：
# 今晚我把 signals 的 `什么` 写成 `说明`，候选样本全空，程序一声不响跑完了。
from 台账 import 读行


def cmd_list(a):
    条 = 读行(规则库)
    if not 条:
        print("规则库里还没有东西：%s" % os.path.relpath(规则库, 根))
        return 0
    print("已生效的数据化检查 %d 条（hardcheck 会自动带上，不用改源码）" % len(条))
    for i, x in enumerate(条, 101):
        print()
        print("%d. %s" % (i, x.get("名") or "?"))
        print("   类型：%s  目标：%s" % (x.get("类型"), x.get("目标")))
        if x.get("正则"):
            print("   正则：%s  期望：%s" % (x.get("正则"), x.get("期望")))
        print("   加于：%s  谁：%s" % (x.get("加于", "?"), x.get("谁", "?")))
        if x.get("说明"):
            print("   为什么：%s" % x["说明"][:100])
    return 0


def cmd_propose(a):
    """从 signals 里挑反复的差值，转成候选规则卡。

    只做「挑」和「摊开」，**不做「想」**——差值能不能写成命令，得由子 AI 判，
    因为它可能是「别像交报告那样说话」这种根本没法机检的。
    """
    信号 = 读行(信号库)
    if not 信号:
        print("signals.jsonl 是空的，没有差值可用")
        return 0

    # 按簇聚合。**别写成「跟 signals.py gc 口径一致」**——gc 用的是词面 聚()，
    # 这里用的是来源+内容，两套不一样（2026-11-06 发现，注释一直写着谎话）。
    # 按来源聚的恶果（11-06 01:x 实测）：所有 user 纠正共用一个簇名 `user`，
    # 第一次判「写不成」之后，**以后每一次纠正都被永久跳过**——三条新纠正记进去了，
    # propose 却报「都判过了」。所以 user 和 check 都改成按内容聚。
    # 字段名以 signals.jsonl 为准：id / 时间 / 来源 / 什么 / 修法 / 处理 / 目标
    # 2026-11-06 00:5x 踩过：第一版读的是 `说明`，实际叫 `什么`，候选样本全是空。
    簇 = {}
    按内容 = {}   # 键 -> 来源，决定阈值
    for s in 信号:
        if s.get("处理"):
            continue
        来源 = s.get("来源") or ""
        if 来源 in ("check", "user"):
            键 = (s.get("什么") or "")[:40]
            按内容[键] = 来源
        else:
            键 = 来源 or (s.get("什么") or "")[:20]
        簇.setdefault(键, []).append(s)

    # 检查挂了、他纠正我了——这两种一次就够，不等三次。
    # 等三次的意思是多挨两次，而 user 那一源他花的是他自己的时间。
    # 其它来源保持 >=3：偶发一次的偏差不足以说明是「反复」。
    反复 = {k: v for k, v in 簇.items()
            if len(v) >= (1 if k in 按内容 else 3)}
    # 2026-11-06 01:x 修：propose 原来每次整库重写，会把 reject 记的「判过·写不成」冲掉——
    # 于是同一簇每轮都被摊出来重问一遍，等于没在学。现在把已判过的原样留着，跳过它们。
    已判 = {x.get("簇"): x for x in 读行(候选库) if x.get("判")}
    反复 = {k: v for k, v in 反复.items() if k not in 已判}
    if not 反复:
        print("反复 ≥3 次的差值都判过了（还有 %d 簇记着「写不成」）——这一轮没有新东西可长"
              % len(已判))
        return 0

    os.makedirs(os.path.dirname(候选库), exist_ok=True)
    with io.open(候选库, "w", encoding="utf-8") as f:
        # 先写回已判过的（保历史），再写新候选
        for x in 已判.values():
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
        for 键, 事们 in sorted(反复.items(), key=lambda kv: -len(kv[1])):
            卡 = {
                "簇": 键,
                "次数": len(事们),
                "最近": max((x.get("时间") or "") for x in 事们),
                "样本": [(x.get("什么") or "")[:100] for x in 事们[:3]],
                "提到过的修法": list({(x.get("修法") or "")[:80] for x in 事们 if x.get("修法")})[:3],
                "状态": "待判",
                # 性质决定子 AI 该怎么答：检查挂了 ≠ 缺规则（2026-11-06 加）
                "性质": {"check": "检查失败", "user": "他的纠正"}.get(按内容.get(键), "反复差值"),
            }
            f.write(json.dumps(卡, ensure_ascii=False) + "\n")

    print("待判差值 %d 簇，已摊进 candidates.jsonl（检查失败按 1 次算，其它来源按 3 次）"
          % len(反复))
    print()
    print("接下来该做的事（必须由子 AI 判，不能我自己判）：")
    有检查 = [k for k in 反复 if 按内容.get(k) == "check"]
    if 有检查:
        print("  ⚠ 其中 %d 簇是「已有的检查挂了」，**不是缺一条新规则**。" % len(有检查))
        print("    判的时候先答：是数据该修（修完检查就绿了），还是这条检查本身的口径错了？")
        print("    只有当「换个口径能抓到真正的毛病」时，才给新规则。")
        print()
    print("  每簇问一句：**这条能不能写成一条命令？**")
    print("    能 → 给出一条 checks.jsonl 格式的规则（类型只能在这三种里选）")
    print("    不能 → 明说「写不成」，并说清它属哪类（文风/判断/节奏）")
    print()
    print("判完用：python tools/rules.py add --json '<规则>'")
    return 0


def 验规则(条):
    """验格式。宁可拒收，也不让一条坏规则进库——它会被 hardcheck 每次跑一遍。"""
    缺 = [k for k in ("名", "类型", "目标") if not (条.get(k) or "").strip()]
    if 缺:
        return "缺字段：%s" % "、".join(缺)
    if 条["类型"] not in 允许类型:
        return "类型只能是 %s，收到「%s」" % ("/".join(允许类型), 条["类型"])
    if len(条["名"]) < 4:
        return "名字太短，看不出它查什么"
    if 条["类型"] in ("文本匹配", "目录文本匹配"):
        if not (条.get("正则") or "").strip():
            return "这个类型必须给 正则"
        try:
            re.compile(条["正则"], re.M)
        except Exception as e:
            return "正则编不过：%s" % e
        if 条["类型"] == "目录文本匹配" and not (条.get("文件匹配") or "").strip():
            return "目录文本匹配必须给 文件匹配（glob，如 *.md）"
        if (条.get("期望") or "有") not in ("有", "无"):
            return "期望只能是「有」或「无」"
        # 目标要真存在，否则这条检查永远报错——写规则的人可能把路径写错了
        目 = os.path.join(根, 条["目标"])
        if not os.path.exists(目):
            return "目标不存在：%s" % 条["目标"]
    return ''


def cmd_add(a):
    try:
        条 = json.loads(a.json)
    except Exception as e:
        print("这不是合法 JSON：%s" % e)
        return 2
    错 = 验规则(条)
    if 错:
        print("拒收：%s" % 错)
        return 2

    # 同名的不许重复加——重复加会每次跑两遍，还分不清哪条是真的
    已有 = 读行(规则库)
    if any((x.get("名") or "") == 条["名"] for x in 已有):
        print("已经有一条同名规则了：%s" % 条["名"])
        return 2

    import datetime as _dt
    条.setdefault("加于", _dt.datetime.now().isoformat(timespec="seconds"))
    条.setdefault("谁", a.by or "未知")
    os.makedirs(os.path.dirname(规则库), exist_ok=True)
    with io.open(规则库, "a", encoding="utf-8") as f:
        f.write(json.dumps(条, ensure_ascii=False) + "\n")

    print("加进去了：%s" % 条["名"])
    print("下次跑 hardcheck 会自动带上它——不用改任何源码。")
    return 0


def cmd_brief(a):
    """打印给「规则生成」子 AI 的完整 prompt（2026-11-06 加）。

    为什么只打印、不自己派：脚本/定时任务**起不了 subagent**，这是平台边界。
    所以这一环拆两半——这里出 prompt，子 AI 干完自己跑 accept/reject 落库。
    跟 `arch-listen.py` 的 pick / receipt 一个模式，不新造一套。
    """
    待判 = [x for x in 读行(候选库) if not x.get("判")]
    if not 待判:
        # 返回 0 不是 1：**没有待判的候选是正常状态**，不是失败。
        # 返回非零会被接进来的自动化当成崩（2026-11-06 子 AI 跑一遍后指出来的）。
        print("candidates.jsonl 里没有待判的候选。先跑 propose。")
        return 0
    现有 = 读行(规则库)

    print("=" * 64)
    print("把下面整段发给「规则生成」子 AI。它判完自己跑最后那条命令落库。")
    print("=" * 64)
    print()
    print("你是「规则生成」子 AI。有一批差值等着你判：**能不能变成一条能跑的检查？**")
    print()
    print("## 候选项")
    for x in 待判:
        print()
        print("### 簇：%s" % x.get("簇"))
        print("- 性质：%s" % (x.get("性质") or "反复差值"))
        print("- 出现 %s 次，最近 %s" % (x.get("次数"), x.get("最近")))
        for s in (x.get("样本") or []):
            print("- 样本：%s" % s)
        if x.get("性质") == "检查失败":
            print("- ⚠ 这一簇是**已有的检查挂了**，不是缺一条新规则。")
            print("  先答：是数据该修，还是这条检查本身的口径错了？")
            print("  只有当「换个口径能抓到真正的毛病」时，才给新规则。")
    print()
    print("## 现在的检查库（别加重复的）")
    for x in 现有:
        print("- %s（%s）" % (x.get("名"), x.get("类型")))
    print()
    print("## 你只能写这三种类型")
    print("- 文件存在：字段 `名` `说明` `类型` `目标`（相对工作区根，如 memory/facts/x.jsonl）")
    print("- 文本匹配：再加 `正则` `期望`（有/无）")
    print("- 目录文本匹配：再加 `文件匹配`（glob，如 *.md），可选 `排除匹配`（正则，命中整份跳过）")
    print()
    print("## 硬约束")
    print("- 正则必须 `re.M` 编得过；目标必须真实存在，否则会被拒收。")
    print("- **判不成就是「写不成」，不是失败**。硬凑一条假规则会天天报红，比不加更坏。")
    print("- 你的判据要能被别人复验：说清「拿什么命令、看什么输出、说明什么」。")
    print()
    print("## 判完自己跑（二选一）")
    print("能写成命令：")
    print("  python tools/rules.py accept --json '<规则JSON>' --簇 '<上面的簇名>' --by \"子AI:规则生成\"")
    print("写不成：")
    print("  python tools/rules.py reject --簇 '<上面的簇名>' --why '<具体理由>' --类 <文风/判断/节奏/其他> --by \"子AI:规则生成\"")
    print()
    print("accept 会先过 `验规则()`——格式不对、正则编不过、目标不存在，一律拒收，一个字不写。")
    return 0


def cmd_accept(a):
    """子 AI 交卷：验过规则才落库，同时把候选标成已判（2026-11-06 加）。

    跟 `add` 的区别只有一处：**accept 会顺便销掉候选**。
    不然同一簇下一轮又被 propose 摊出来问一遍——同一个问题问三次，等于没在学。

    验规则() 一个字没改：子 AI 不能靠「交卷」这个身份绕过格式检查。
    """
    try:
        条 = json.loads(a.json)
    except Exception as e:
        print("这不是合法 JSON：%s" % e)
        return 2
    错 = 验规则(条)
    if 错:
        print("拒收：%s" % 错)
        print("（一个字都没写进 checks.jsonl）")
        return 2

    已有 = 读行(规则库)
    if any((x.get("名") or "") == 条["名"] for x in 已有):
        print("已经有一条同名规则了：%s" % 条["名"])
        return 2

    import datetime as _dt
    条.setdefault("加于", _dt.datetime.now().isoformat(timespec="seconds"))
    条.setdefault("谁", a.by or "子AI")
    os.makedirs(os.path.dirname(规则库), exist_ok=True)
    with io.open(规则库, "a", encoding="utf-8") as f:
        f.write(json.dumps(条, ensure_ascii=False) + "\n")
    print("已落库：%s" % 条["名"])

    条们 = 读行(候选库)
    命中 = 0
    for x in 条们:
        if not x.get("判") and x.get("簇") == a.簇:
            x["状态"] = "判过·写规则"
            x["判"] = "能"
            x["规则名"] = 条["名"]
            x["判于"] = _dt.datetime.now().isoformat(timespec="seconds")
            x["谁"] = a.by or "子AI"
            命中 += 1
    if 命中:
        with io.open(候选库, "w", encoding="utf-8") as f:
            for x in 条们:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
        print("候选标成「判过·写规则」%d 条" % 命中)
    else:
        print("⚠ 规则落库了，但没找到簇「%s」的候选——候选没销账，下轮 propose 还会问一次" % a.簇)
    print("下次跑 hardcheck 会自动带上它——不用改任何源码。")
    return 0


def 回放(条):
    """把一条规则对着真实文件跑一遍，返回 (命中数, 样例, 错误)。

    不做的事：不判断「命中的这些是不是该命中」——那是人的活。
    这里只回答「它抓得到东西吗、抓了多少」，剩下的交给人看样例。
    """
    类 = 条.get("类型")
    目标 = os.path.join(根, 条.get("目标") or "")
    if not os.path.exists(目标):
        return 0, [], "目标不存在：%s" % 条.get("目标")
    if 类 == "文件存在":
        return 1, [os.path.relpath(目标, 根)], None
    try:
        正 = re.compile(条.get("正则") or "")
    except Exception as e:
        return 0, [], "正则编译失败：%s" % e

    if 类 == "文本匹配":
        文件们 = [目标]
    elif 类 == "目录文本匹配":
        pat = 条.get("文件匹配") or "*"
        文件们 = sorted(_glob.glob(os.path.join(目标, "**", pat), recursive=True))
    else:
        return 0, [], "未知类型：%s" % 类

    排 = 条.get("排除匹配")
    命 = []
    for f in 文件们:
        if not os.path.isfile(f):
            continue
        try:
            文 = io.open(f, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        if 排:
            try:
                if re.search(排, 文):
                    continue
            except Exception:
                pass
        for i, 行 in enumerate(文.splitlines(), 1):
            if 正.search(行):
                命.append("%s:%d: %s" % (os.path.relpath(f, 根), i, 行.strip()[:70]))
    return len(命), 命, None


def cmd_backtest(a):
    """入库前回放：这条规则抓得到东西吗？抓得太宽吗？（2026-11-09 加）

    **动机**：`accept` 只验格式。格式对不等于规则有用——一条正则可能恒不命中
    （加了等于没加，还占位置），也可能匹配半个文件天天报红（把真警报淹掉）。
    自进化最危险的地方就在这儿：**系统能自己加一条废规则，然后被自己加的红烦死**。
    所以入库前先让它对着历史跑一遍，人看样例再决定。

    三态判定（拒的时候退出码 2，跟 add/accept 的拒收一致）：
      命中 0 条            → 拒（抓不到任何东西）
      命中 > 上限          → 拒（太宽，会淹没真警报）
      样本给了但没全中     → 拒（连它想抓的都没抓到）

    样本从哪来：候选规则的「样本」字段（propose 会把差值的原文抄进去）。
    用 `--样本` 可以额外覆盖。
    """
    try:
        条 = json.loads(a.json)
    except Exception as e:
        print("这不是合法 JSON：%s" % e)
        return 2
    错 = 验规则(条)
    if 错:
        print("格式就不对，先修：%s" % 错)
        return 2

    n, 样例, 错 = 回放(条)
    if 错:
        print("拒收：%s" % 错)
        return 2

    print("回放：%s" % 条.get("名"))
    print("  类型：%s  目标：%s" % (条.get("类型"), 条.get("目标")))
    if 条.get("正则"):
        print("  正则：%s" % 条["正则"])
    print("  命中 %d 条（上限 %d）" % (n, a.limit))
    for s in 样例[:8]:
        print("    %s" % s)
    if n > 8:
        print("    …还有 %d 条" % (n - 8))

    理由 = None
    if 条.get("类型") != "文件存在":
        if n == 0:
            理由 = "命中 0 条——这条规则抓不到任何东西，加了等于没加"
        elif n > a.limit:
            理由 = "命中 %d 条，超过上限 %d——太宽，真警报会被淹掉" % (n, a.limit)

    样本 = list(a.样本 or []) + list(条.get("样本") or [])
    if 样本 and not 理由:
        未中 = [s for s in 样本 if not re.search(条.get("正则") or "", s)]
        if 未中:
            理由 = "有 %d 条本该抓到的样本没抓到，第一条：%s" % (len(未中), 未中[0][:60])

    if 理由:
        print()
        print("拒收：%s" % 理由)
        print("（没写进 checks.jsonl。要么改规则，要么用 reject 记「写不成」）")
        return 2

    print()
    print("过：抓得到，也不太宽。还要人看一眼上面那些命中是不是真该报红。")
    print("确认后落库：python tools/rules.py accept --json '<同样这段>' --簇 '<簇名>'")
    return 0


def cmd_disable(a):
    """停用一条规则：从文件里挪走一条，不删历史。

    为什么不直接删：规则也是有历史的——哪天想问「这条当初为什么加、什么时候停的」，
    删了就查不到。所以挪到 checks.retired.jsonl。
    """
    条们 = 读行(规则库)
    留, 挪 = [], []
    for x in 条们:
        (挪 if (x.get("名") or "") == a.名 else 留).append(x)
    if not 挪:
        print("规则库里没有「%s」" % a.名)
        return 1
    停 = os.path.join(记忆, "rules", "checks.retired.jsonl")
    with io.open(停, "a", encoding="utf-8") as f:
        for x in 挪:
            import datetime as _dt
            x["停于"] = _dt.datetime.now().isoformat(timespec="seconds")
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    with io.open(规则库, "w", encoding="utf-8") as f:
        for x in 留:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    print("停用 %d 条，剩 %d 条生效" % (len(挪), len(留)))
    return 0


def cmd_reject(a):
    """记录「这条差值写不成检查」，并说清为什么。

    2026-11-06 01:x 加。动机：子 AI 判完「写不成」之后，如果没有地方记，
    下次 propose 还会把同一簇摊出来再问一遍——**同一个问题问三次，等于没在学**。

    「写不成」不是失败，是结论：它说明这条偏差属于**文风/判断/节奏**，
    该靠人看或者靠时序证据，不该硬塞进静态检查。硬塞一条假的比不塞更坏——
    它会让 hardcheck 天天报一个跟真问题无关的红。
    """
    理由 = (a.why or "").strip()
    if len(理由) < 10:
        print("--why 太短：说不出为什么写不成，等于没判")
        return 2
    if a.类 not in ("文风", "判断", "节奏", "其他"):
        print("--类 只能是 文风 / 判断 / 节奏 / 其他")
        return 2

    # 把候选库里这一簇标成「已判」，下次 propose 不再摊出来
    条们 = 读行(候选库)
    命中 = False
    import datetime as _dt
    for x in 条们:
        if x.get("簇") == a.簇:
            x["状态"] = "判过·写不成"
            x["判"] = "不能"
            x["类"] = a.类
            x["理由"] = 理由
            x["判于"] = _dt.datetime.now().isoformat(timespec="seconds")
            x["谁"] = a.by or "子AI"
            命中 = True
    if not 命中:
        print("候选库里没有这一簇：%s" % a.簇)
        return 1
    with io.open(候选库, "w", encoding="utf-8") as f:
        for x in 条们:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    print("记下：%s → 写不成（%s）" % (a.簇, a.类))
    print("  理由：%s" % 理由[:100])
    print("  下次 propose 不会再问这一簇。写不成的要留在文档里，不是删掉。")
    return 0


def main():
    p = argparse.ArgumentParser(description="让检查从数据里长出来")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("propose")
    ad = sub.add_parser("add")
    ad.add_argument("--json", required=True)
    ad.add_argument("--by", default="子AI")
    d = sub.add_parser("disable")
    d.add_argument("--名", required=True)
    rj = sub.add_parser("reject", help="记「这条差值写不成检查」，下次不再问")
    rj.add_argument("--簇", required=True)
    rj.add_argument("--why", required=True)
    rj.add_argument("--类", required=True)
    rj.add_argument("--by", default="子AI")
    sub.add_parser("brief", help="打印给子 AI 的完整 prompt（脚本起不了 subagent，派那一下在会话里做）")
    ac = sub.add_parser("accept", help="子 AI 交卷：验规则后落库，并把候选标成已判")
    ac.add_argument("--json", required=True)
    ac.add_argument("--簇", required=True)
    ac.add_argument("--by", default="子AI")
    bt = sub.add_parser("backtest", help="入库前回放：抓不到 / 太宽的规则当场拒")
    bt.add_argument("--json", required=True)
    bt.add_argument("--limit", type=int, default=10, help="命中上限，超过判太宽（默认 10）")
    bt.add_argument("--样本", action="append", default=[], help="本该命中的文本，可多次给")
    a = p.parse_args()
    return {"list": cmd_list, "propose": cmd_propose,
            "add": cmd_add, "disable": cmd_disable,
            "reject": cmd_reject, "brief": cmd_brief,
            "accept": cmd_accept, "backtest": cmd_backtest}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
