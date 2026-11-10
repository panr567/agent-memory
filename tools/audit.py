#!/usr/bin/env python3
"""纠错 AI（2026-11-04）

用户原话：「纠错ai必须严厉」。

严厉不是 prompt 里写一句「你要严厉」——那是自评，会通胀（今天反复在讲的事）。
这里全部用**硬判据**，每条判定都带证据，能被反驳；不写「整体不错、建议……」这种话。

四条判据：
  1. 空话    —— 命中套话表，或者回执短于 40 字
  2. 没证据  —— 回执里没有任何文件路径 / 命令 / 数字
  3. 复述    —— 与原文摘要的三字组重合率 > 0.35（说明它只是把摘要换了个说法）
  4. 超时    —— 派单超过 --min 分钟还没交回执

不判的：内容「对不对」——那需要读原文，不是静态检查能定的。这一层只判「交没交、
交得像不像话」。真伪由人和上游负责。

用法：
  python tools/audit.py run [--min 15] [--json]
  python tools/audit.py check --id <id> --text "回执内容" [--摘要 "原文摘要"]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(ROOT, "memory", "inbox", "internet.jsonl")
OUT = os.path.join(ROOT, "memory", "audit", "report.jsonl")

套话 = [
    "可以借鉴", "值得关注", "有一定价值", "有一定启发", "整体不错",
    "建议进一步", "或许可以", "有待观察", "值得期待", "赋能", "闭环生态",
]

有证据 = [
    r"[A-Z]:\\",
    r"[/\\][\w.-]+\.(py|js|mjs|json|jsonl|md|bat|ps1|yml)",  # 带目录的路径
    # 裸文件名也算：回执里写「改 scene.py / 跑 verify.py」就是具体产物，
    # 上一版只认带斜杠的路径，把这种全判成「没证据」，误伤了真给了文件名的回执。
    r"\b[\w-]+\.(py|js|mjs|json|jsonl|md|bat|ps1|yml)\b",
    r"\bpython\s+\S+", r"\bnode\s+\S+", r"\bcurl\s+\S+", r"\bschtasks\b",  # 命令
    r"`[^`]{3,}`",                       # 反引号里的具体对象
    r"\bdsh-[a-z-]+",                    # 模块 / 插件名
    r"\d+(\.\d+)?\s*(条|个|行|字|分钟|秒|次|%|倍|×|篇|轮|MiB|MB|GB|GiB|TB|核|线程|卡)",   # 带单位的数字
    r"\b(facts|signals|adoption|usage|inbox|jobs|ledger|recall|tiers)\b",
]


# 声称「已经改了」的动词。只有这些词在场才去查路径存不存在——
# 「计划改 / 要改 / 准备改」是意图，文件不在是正常的，判挂就是误伤。
# 2026-11-05 16:3x：加「改了」——子 AI 实测「把 X 改了，还没跑」被判放行，
# 因为原表只认「已改/改完/改好」，不认「改了」。同一个意思换个词就漏。
已改词 = ["已改", "改完", "改好", "改了", "已写", "写完", "已修", "已加", "已跑通", "已新建", "已经改"]
本地前缀 = ("memory/", "tools/", "plugins/", "content/", "tmp/")
# 2026-11-05 20:3x：子 AI 验假说时挖出来的真 bug。
# 原来交替是 `py|js|mjs|json|jsonl|...`，Python 的 re 交替是**左优先**：
# `memory/facts/index.json` 会先匹配到 `.js` 就成功收工，剩下的 `on` 不管，
# 于是被截成 `memory/facts/index.js` → 明明存在的 json 全被判「文件不存在」。
# 修法两件一起：扩展名长的在前 + 加 (?![\w]) 边界（防 md5.json 之类再被吃掉）。
文件模 = re.compile(r"((?:memory|tools|plugins|content|tmp)/[\w./\-\u4e00-\u9fa5]+"
                    r"\.(?:jsonl|json|mjs|py|js|md|bat|ps1|yml))(?![\w])")
# 分句符：时态词和路径必须在**同一句**里才配对。
# 2026-11-05 16:3x 子 AI 实测出的真 bug：原来是「全文 any(时态词)」+「全文 finditer(路径)」，
# 两个作用域不配对 →「计划改 A 和 B，已改 A」会把 B 也拿去查（误伤），
# 「把 X 改了，还没跑」又因为词表漏了「改了」而放行（漏判）。5 条里错 3 条。
分句符 = re.compile(r"[。；;！!？?\n，,]")


def 查路径(文):
    """回执声称改过的本地文件，逐个查在不在。
    这是 2026-11-04 由推断环节逼出来的一条判据：
    第一条假说「audit 只测格式不测真伪」实测成立（乱写一个 memory/不存在.py 也判合格）。
    修法的关键是不误伤计划性描述——所以只在出现「已改/已写/已跑通」时才查。
    2026-11-05 再修一层：**按分句配对**，同一句里既有时态词又有路径，才查这一句里的路径。"""
    缺 = []
    for 句 in 分句符.split(文 or ""):
        if not any(w in 句 for w in 已改词):
            continue
        for m in 文件模.finditer(句):
            路 = m.group(1)
            # 占位名跳过。2026-11-06 00:x 实测误报：验「查路径」这条判据的卡里写着
            # 「我造了 memory/绝对不存在xyz.py 和 tools/fake-none.py」——那是**测试用例**，
            # 不是声称改过的文件。占位名的字符本身就是记号，直接按名字跳过。
            if any(x in 路 for x in ('不存在', 'fake', 'xyz', 'placeholder', '示例')):
                continue
            if 路 not in 缺 and not os.path.exists(os.path.join(ROOT, 路)):
                缺.append(路)
    return 缺


def 读全部():
    全 = []
    if not os.path.exists(INBOX):
        return 全
    with open(INBOX, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                全.append(json.loads(line))
            except Exception:
                pass
    return 全


def 三字组(s):
    s = re.sub(r"\s+", " ", s or "").lower()
    return {s[i:i + 3] for i in range(max(0, len(s) - 2))}


def 判(回执, 摘要, 派单时间, 分钟阈值):
    """返回 (合格?, [不合格项])，每项带证据。"""
    文 = (回执 or "").strip()
    挂 = []

    if not 文:
        if 派单时间:
            try:
                分钟 = (datetime.now() - datetime.fromisoformat(派单时间)).total_seconds() / 60
                if 分钟 >= 分钟阈值:
                    挂.append({"项": "超时", "证": "派单 %.0f 分钟还没交（阈值 %d）"
                               % (分钟, 分钟阈值)})
            except Exception:
                pass
        return (len(挂) == 0), 挂

    if len(文) < 40:
        挂.append({"项": "空话", "证": "只有 %d 字，连三行都算不上" % len(文)})
    else:
        命中 = [w for w in 套话 if w in 文]
        if 命中:
            挂.append({"项": "空话", "证": "用了套话：" + "、".join(命中[:3])})

    if 文 and not any(re.search(p, 文, re.I) for p in 有证据):
        挂.append({"项": "没证据", "证": "全文没有一处文件路径/命令/带单位的数字——无法核对"})

    缺 = 查路径(文)
    if 缺:
        挂.append({"项": "文件不存在",
                   "证": "声称改过，但这些文件没有：" + "、".join(缺[:3])})

    if 文 and 摘要:
        a, b = 三字组(文), 三字组(摘要)
        if b:
            重 = len(a & b) / max(1, len(a))
            if 重 > 0.35:
                挂.append({"项": "复述", "证": "跟摘要的三字组重合 %.0f%%，基本是换个说法" % (重 * 100)})

    return (len(挂) == 0), 挂


def cmd_check(a):
    ok, 挂 = 判(a.text, a.摘要 or "", None, 0)
    print(("合格" if ok else "不合格") + "：%s" % a.id)
    for x in 挂:
        print("  ✗ %s —— %s" % (x["项"], x["证"]))
    return 0 if ok else 1


# 账本路径跟着 predict.py 的 PREDICT_BOOK 走。
# 2026-11-06 00:4x 实测踩到：audit 这里写死了真账本，而 predict.py 认环境变量，
# 测试时卡写进临时账本、这里读的却是真账本——「审我的验收点」永远看不到新卡。
# 更坏的情况是以后真改路径，这边会静默读旧文件，不报错。
PREDICT = os.environ.get("PREDICT_BOOK") or os.path.join(
    ROOT, "memory", "autonomy", "predictions.jsonl")

# 「异体审判据」的审查人。写回时记谁审的——不然过几天分不清是子 AI 判的还是我自己补的。
ACCEPT_QUEUE = os.path.join(ROOT, "memory", "autonomy", "accept-queue.jsonl")


def _读预测():
    条 = []
    if os.path.exists(PREDICT):
        with open(PREDICT, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    条.append(json.loads(line))
                except Exception:
                    continue
    return 条


def cmd_accept_queue(a):
    """列出「还没被异体审过」的验收点。

    2026-11-06 00:4x 用户「加」。上一轮我承认过：`mine` 只能查「证据跟验收点对得上吗」，
    查不了「这条判据本身对不对」。给自己出题的人判不了自己的题——
    所以要一个不知道上下文的脑袋来看：**这条 accept，换个人拿着，能验收同一件事吗**。

    这个命令只负责「列出来」。真正的判断必须由子 AI 在会话里做（定时任务起不了 subagent），
    审完用 `accept-set` 写回。
    """
    import datetime as _dt

    条 = [x for x in _读预测() if (x.get("accept") or "").strip()]
    待 = [x for x in 条 if not x.get("accept_review")]
    # 只审最近 7 天的：老卡审了也没用，改了判据也不会回头补
    截 = (_dt.datetime.now() - _dt.timedelta(days=7)).isoformat()[:10]
    待 = [x for x in 待 if (x.get("t") or "") >= 截]

    if a.json:
        print(json.dumps([{"task": x.get("task"), "t": x.get("t"),
                           "accept": x.get("accept")} for x in 待],
                         ensure_ascii=False))
        return 0

    if not 待:
        print("没有待审的验收点（最近 7 天建的卡都审过了，或者还没建卡）")
        return 0

    print("待审验收点 %d 条（最近 7 天、还没被异体看过）" % len(待))
    print("=" * 64)
    for x in 待:
        print()
        print("task: %s   %s" % (x.get("t", "")[:16], (x.get("task") or "")[:40]))
        print("  验收点：%s" % (x.get("accept") or "")[:200])
        print("  写回：python tools/audit.py accept-set \"%s\" --verdict 过 --why \"...\""
              % (x.get("task") or ""))
    print()
    print("（判据：换一个不知道上下文的人，拿着这条能不能判断「成了没有」。")
    print("  不能就是不过——不管它写得多具体。）")
    return 0


def cmd_accept_set(a):
    """把异体对验收点的判断写回账本。

    按 task 匹配最近一条卡；找得到就判，判过再判则把旧判断挪进 accept_history。
    找不到就报错，不许默默新建——写错地方等于这条判据永远没人看。
    """
    import datetime as _dt

    if a.verdict not in ("过", "不过"):
        print("--verdict 只能是「过」或「不过」")
        return 2
    if not (a.why or "").strip():
        print("--why 必填：说不出理由的判断，跟没审一样")
        return 2

    条 = _读预测()
    # 从后往前找：同一个 task 名可能建过多次，审最近那次。
    # 2026-11-09 19:3x 改：原来只匹配「还没审过」的卡，于是**改完再验**写不进来——
    # 异体验收员改完重验只能手改账本（它真这么干了，虽然加了锁、留了备份）。
    # 手改账本就是后门。现在允许对同一张卡再判，旧判断整条挪进 accept_history，
    # 谁在哪一轮说了什么全留着，不是覆盖掉。
    目标 = None
    for x in reversed(条):
        if (x.get("task") or "") == a.task:
            目标 = x
            break
    if 目标 is None:
        print("账本里没有「%s」这条卡（task 名写错了？）" % a.task)
        return 1

    旧审 = 目标.get("accept_review")
    if 旧审:
        目标.setdefault("accept_history", []).append(旧审)
        print("这张卡上一轮的判断挪进 accept_history（%s，%s）"
              % (旧审.get("判"), 旧审.get("谁")))

    目标["accept_review"] = {
        "判": a.verdict,
        "理由": a.why.strip(),
        "谁": a.by or "子AI",
        "时间": _dt.datetime.now().isoformat(timespec="seconds"),
    }

    # 整本重写。字段一个不能丢——所以是「改内存里的那一行再全写回去」，
    # 不是追加一条新的（追加会让同一条卡出现两个版本，谁是真的说不清）。
    临时 = PREDICT + ".tmp"
    with open(临时, "w", encoding="utf-8") as f:
        for x in 条:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    os.replace(临时, PREDICT)

    print("写回：%s → %s（%s）" % (a.task, a.verdict, a.why.strip()[:60]))

    # 2026-11-07 加（用户：「你的外部反馈源不只有我还有你的异体审查员」）：
    # 以前「不过」只躺在 accept_review 字段里，signals.py 看不见 ——
    # 于是同一个理由被反复打回也不会累积，rules.py 永远长不出动作。
    # 现在判不过就落一条差值，来源记 audit（外部执行面，不是我自己）。
    if a.verdict == "不过":
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import signals
            什么 = "异体审判不过：%s" % (a.task or "")[:40]
            旧 = signals.读()
            # 2026-11-08 10:0x：排除已销账的（同 predict/hardcheck 那条规矩）。
            死的 = signals.已处理(旧)
            if not any((not x.get("处理")) and x.get("来源") == "audit"
                       and x.get("id") not in 死的
                       and str(x.get("什么") or "") == 什么 for x in 旧):
                signals.加({
                    "id": "sg-aud-" + os.urandom(4).hex(),
                    "时间": signals.现在(),
                    "来源": "audit",
                    "什么": 什么,
                    "修法": a.why.strip()[:160],
                    "处理": False,
                })
                print("（差值已记：来源=audit）")
        except Exception as e:
            print("（差值没记上：%s）" % e)

    return 0 if a.verdict == "过" else 1


def 抽锚(文):
    """从一段话里抽「能指给别人看的东西」：文件、扩展名、带单位的数字。

    为什么需要：光看「有没有证据」不够——证据可以答非所问。
    验收点说要查 A，证据却在说 B，两个都有「证据」，但没人对过账。
    """
    锚 = set()
    for m in 文件模.finditer(文 or ""):
        锚.add(os.path.basename(m.group(1)))
    for m in re.finditer(r"\b[\w-]+\.(py|js|mjs|json|jsonl|md|bat|ps1|yml)\b", 文 or ""):
        锚.add(m.group(0))
    for m in re.finditer(r"\d+\s*(条|行|个|字节|分钟|秒|%)", 文 or ""):
        锚.add(m.group(0).replace(" ", ""))
    # 「grep 输出 0」「为 0」「计数 0」都算结论型锚。
    # 2026-11-06 00:x 连修两次：
    #   第一版只认「N 条/行/个」，accept 写「为 0」抽不到；
    #   第二版加了「为/=|是|输出」，实测证据写的是「计数 0」——又漏。
    # 教训：**用词表去认语义，词表永远漏**。这里只能尽量宽，别指望它完备。
    for m in re.finditer(r"(?:为|等于|是|输出|计数|数量|共|仅有?|=)\s*(0|[1-9]\d*)", 文 or ""):
        锚.add("=" + m.group(1))
    return 锚


def cmd_mine(a):
    """审我自己的活。

    2026-11-06 00:x 加。纠错 AI 从 11-04 建起来，对象一直是「子 AI 交没交、
    交得像不像话」——我一次也没把自己放进去。结果是：子 AI 的活有异体审，
    我的活还是自己出题自己判卷。用户点的：「真反馈源就是把你干的项目交给纠错 AI」。

    三条判据（都只用人能反驳的方式说，不写「挺好的」）：
      ① 判了达到，但证据是空话或太短
      ② 验收点跟证据对不上——两个都有锚，但不是同一批
      ③ 证据里声称改过的本地文件不存在
    """
    条 = []
    if os.path.exists(PREDICT):
        with open(PREDICT, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    条.append(json.loads(line))
                except Exception:
                    continue

    # 只看判了「达到」的——那是「我说它成了」的时刻，最该被审。
    达到 = [x for x in 条 if x.get("结果") == "达到"]
    挂的 = []
    for x in 达到:
        证 = (x.get("证据") or "").strip()
        验 = (x.get("accept") or "").strip()
        项 = []

        # ① 证据是空话
        if not 证:
            项.append(("没证据", "判了达到但证据栏是空的"))
        elif len(证) < 8:
            项.append(("证据太短", "「%s」不够让人复验" % 证))
        elif any(w in 证 for w in 套话):
            项.append(("证据像套话", "「%s」" % 证[:30]))

        # ② 验收点跟证据对不上
        if 证 and 验:
            验锚 = 抽锚(验)
            证锚 = 抽锚(证)
            if 验锚 and 证锚 and not (验锚 & 证锚):
                项.append(("答非所问",
                          "验收点要的是 %s，证据却说 %s"
                          % ("、".join(sorted(验锚)[:2]), "、".join(sorted(证锚)[:2]))))

        # ③ 声称改过但文件不存在
        缺 = 查路径(证)
        if 缺:
            项.append(("文件不存在", "、".join(缺[:3])))

        if 项:
            挂的.append({"任务": (x.get("task") or "")[:40], "t": (x.get("t") or "")[:16], "挂": 项})

    if a.json:
        print(json.dumps({"审": len(达到), "不合格": len(挂的), "明细": 挂的}, ensure_ascii=False))
        return 0 if not 挂的 else 1

    print("纠错 AI · 审我自己的活  达到 %d 条｜不合格 %d 条   %s"
          % (len(达到), len(挂的), datetime.now().strftime("%H:%M")))
    print("-" * 62)
    if not 达到:
        print("还没有判过「达到」的卡——不是清白，是没样本。")
        return 0
    for r in 挂的:
        print("✗ %s  %s" % (r["t"], r["任务"]))
        for 名, 证 in r["挂"]:
            print("    %s —— %s" % (名, 证))
    if not 挂的:
        print("没有不合格的。但「没被抓到」不等于「干得好」，只说明这三条没命中。")
    else:
        # 老账标注：`--proof` 是 11-03 15:4x 才强制必填的，在那之前判「达到」
        # 不需要证据。不追溯补（补了等于造假账），但要标出来，否则天天红两条会麻木。
        老 = [r for r in 挂的 if (r.get("t") or "") < "2026-11-03T16"]
        if 老 and len(老) == len(挂的):
            print()
            print("（这 %d 条都是 2026-11-03 16 点之前的老账——那时 --proof 还没强制，"
                  "补证据等于造假账，所以留着红，不修。）" % len(老))
    return 0 if not 挂的 else 1


def cmd_run(a):
    全 = 读全部()
    待审 = [x for x in 全 if x.get("回执") or x.get("派单")]
    结果 = []
    for x in 待审:
        回 = (x.get("回执") or {}).get("内容", "")
        派 = (x.get("派单") or {}).get("时间")
        ok, 挂 = 判(回, x.get("摘要", ""), 派, a.min)
        结果.append({"id": x["id"], "标题": (x.get("标题") or "")[:50],
                     "给": (x.get("回执") or x.get("派单") or {}).get("谁") or "",
                     "合格": ok, "挂": 挂})
    if a.json:
        print(json.dumps(结果, ensure_ascii=False))
        return 0 if all(r["合格"] for r in 结果) else 1

    挂的 = [r for r in 结果 if not r["合格"]]
    print("纠错 AI  审计 %d 条｜合格 %d｜不合格 %d   %s"
          % (len(结果), len(结果) - len(挂的), len(挂的), datetime.now().strftime("%H:%M")))
    print("-" * 62)
    for r in 挂的:
        print("✗ %s｜%s" % (r["id"], r["标题"]))
        for x in r["挂"]:
            print("    %s —— %s" % (x["项"], x["证"]))
    if not 挂的 and 结果:
        print("没有不合格的。但「没被抓到」不等于「干得好」，只说明这四条判据没命中。")
    if not 结果:
        print("还没有可审的：没人派单，也没人交回执。")

    if not a.dry:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "a", encoding="utf-8") as f:
            f.write(json.dumps({"时间": datetime.now().isoformat(timespec="seconds"),
                                "审": len(结果), "不合格": len(挂的),
                                "明细": 结果}, ensure_ascii=False) + "\n")
    return 0 if not 挂的 else 1


def main():
    p = argparse.ArgumentParser(description="纠错 AI：硬判据，不写客套")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--min", type=float, default=15.0)
    r.add_argument("--json", action="store_true")
    r.add_argument("--dry", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("--id", default="manual")
    c.add_argument("--text", required=True)
    c.add_argument("--摘要", default="")
    m = sub.add_parser("mine", help="审我自己的活（predict 账本里判了达到的卡）")
    m.add_argument("--json", action="store_true")
    q = sub.add_parser("accept-queue", help="列出还没被异体审过的验收点")
    q.add_argument("--json", action="store_true")
    s = sub.add_parser("accept-set", help="把异体对验收点的判断写回账本")
    s.add_argument("task")
    s.add_argument("--verdict", required=True, choices=["过", "不过"])
    s.add_argument("--why", required=True)
    s.add_argument("--by", default="子AI")
    a = p.parse_args()
    if a.cmd == "run":
        return cmd_run(a)
    if a.cmd == "check":
        return cmd_check(a)
    if a.cmd == "mine":
        return cmd_mine(a)
    if a.cmd == "accept-queue":
        return cmd_accept_queue(a)
    if a.cmd == "accept-set":
        return cmd_accept_set(a)
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
