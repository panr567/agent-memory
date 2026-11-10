# -*- coding: utf-8 -*-
"""碰撞：拿「今天的新东西」去撞「最不相干的老事实」，看能不能撞出一句新判断。

为什么要这个东西（2026-11-05 19:1x，用户一句话点醒的）：
他说「我们没做到让新信息和记忆产生碰撞，所以才没有新方向」。
对。现在的念头全部来自扫账本——账本写什么没做，就念什么，一条清单外的也念不出来。
`infer.py` 看着像在出新东西，其实也是从「已回执的已知」往下推，还是清单内。

碰撞跟推断的分界：
  推断 = 已知 → 已知（同类往下走）
  碰撞 = 新信息 × 不相干的老记忆（异类相撞）
相近的撞出来是「哦这跟上次一样」，只有不相干的才可能撞出清单外的东西。

**最大的风险，必须先写在这儿**：我能给任何两样东西编一个像样的连接。
今天下午编过一条不存在的事故，被子 AI 当场抓出来——那不是联想，是缝合。
缝合的特征是「听着对、没法验」。所以 add 有三道硬闸，缺一道就拒收：
  1. 共用的道理（一句话）
  2. 能推出的新检查或新判断（要具体到能跑）
  3. 什么算撞错了（可证伪）
第 2 条是命门——说不清怎么验的，一律丢。

用法：
  pick                 挑一对（新信息 + 最不相干的老事实），打印全文供判断
  pick --n 3           一次挑三对
  show <对号>          重看某一对
  add --pair <对号> --title --text    交判断（三行），进 inbox 等拆分 AI 派活
  report               撞了多少对、几条落成了新检查、几条被证伪
  list                 列挑过的对
"""
import argparse
import io
import json
import os
import re
import sys
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
事实目录 = os.path.join(根, "memory", "facts")
碰撞目录 = os.path.join(根, "memory", "collide")
对账本 = os.path.join(碰撞目录, "pairs.jsonl")
收件箱 = os.path.join(根, "memory", "inbox", "internet.jsonl")
当天 = os.path.join(根, "memory", "summaries")

停用词 = set("的了是在和与及就都而也不有这那个我你他它上下来去很还只把被让给对说要看会能可没为以如")


def 今天():
    return datetime.now().strftime("%Y-%m-%d")


def 读文本(p):
    try:
        with io.open(p, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def 拆front(文):
    """把 --- 之间的 front-matter 拆成 dict，正文单独返回。"""
    头, 体 = {}, 文
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", 文, re.S)
    if not m:
        return 头, 体
    for line in m.group(1).split("\n"):
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        头[k.strip()] = v.strip()
    return 头, m.group(2).strip()


def 读事实():
    """读 memory/facts/fact-*.md（不用 index.json，那个要重跑 stats 才更新）。"""
    出 = []
    if not os.path.isdir(事实目录):
        return 出
    for 名 in sorted(os.listdir(事实目录)):
        if not (名.startswith("fact-") and 名.endswith(".md")):
            continue
        文 = 读文本(os.path.join(事实目录, 名))
        if not 文:
            continue
        头, 体 = 拆front(文)
        出.append({
            "id": 头.get("id") or 名[:-3],
            "标题": 头.get("title") or "",
            "类型": 头.get("type") or "",
            "标签": [t.strip() for t in (头.get("tags") or "").strip("[]").split(",") if t.strip()],
            "场景": 头.get("scene") or "",
            "正文": 体,
        })
    return 出


def 二元(文):
    """2-gram 集合，去掉标点空白和停用字。够用的粗分词。"""
    净 = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]+", "", 文 or "")
    净 = "".join(c for c in 净 if c not in 停用词)
    return {净[i:i + 2] for i in range(len(净) - 1)}


def 相似(a, b):
    A, B = 二元(a), 二元(b)
    if not A or not B:
        return 0.0
    共 = len(A & B)
    return 共 / float(len(A | B))


def 新信息():
    """今天的「新东西」：今天写的 fact + 今天日记里的段落。

    刻意只取今天的——碰撞要的是「刚进来的东西」，拿旧东西撞旧东西没意义。
    """
    今 = 今天()
    新 = []
    for f in 读事实():
        if (f["场景"] or "").startswith(今):
            新.append({"来源": "事实:" + f["id"], "标题": f["标题"], "正文": f["正文"],
                       "标签": f["标签"], "id": f["id"]})
    p = os.path.join(当天, 今 + ".md")
    文 = 读文本(p)
    if 文:
        # 按二级标题切段，每段当一条「新信息」
        段 = re.split(r"^## ", 文, flags=re.M)[1:]
        for s in 段:
            行 = s.strip().split("\n")
            标 = 行[0].strip()[:40]
            体 = "\n".join(行[1:]).strip()
            if len(体) < 40:
                continue
            新.append({"来源": "日记:" + 标, "标题": 标, "正文": 体, "标签": [], "id": ""})
    return 新


def 挑对(新, 老, 排除):
    """给一条新信息，找一条「最不相干」的老事实。

    不相干 = 2-gram Jaccard 最低。另外要求标签不重叠（同一类的不算撞）。
    完全零相似的要排除——那多半是「一个数字配一个成语」，无话可说。
    所以要「有一点相似、但最少」：相似度落在 (0, 0.05] 区间，取最小的。
    """
    候选 = []
    for f in 老:
        if f["id"] == 新.get("id"):
            continue
        if set(f["标签"]) & set(新.get("标签") or []):
            continue
        键 = (新["来源"], f["id"])
        if 键 in 排除:
            continue
        # 只撞「教训 × 教训」。2026-11-05 19:2x 实测：第一版不筛类型，
        # 3 对里 2 对撞的是 QQ 端口配置、dsh 升级步骤——字面几乎零重合，
        # 但根本无话可说，因为「配置参数」这种东西没有可抽象性。
        # 能撞出道理的前提是两边都能被抽象成一句话，不是字面不重合就行。
        if f["类型"] != "feedback":
            continue
        s = 相似(新["正文"], f["正文"] + " " + f["标题"])
        if s <= 0.0 or s > 0.06:
            continue
        候选.append((s, f))
    if not 候选:
        return None
    候选.sort(key=lambda x: x[0])
    return 候选[0]


def 读对账本():
    出 = []
    if not os.path.exists(对账本):
        return 出
    with io.open(对账本, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                出.append(json.loads(line))
            except Exception:
                continue
    return 出


def 写对账本(全):
    os.makedirs(碰撞目录, exist_ok=True)
    tmp = 对账本 + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for x in 全:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    os.replace(tmp, 对账本)


def 编号():
    return "cmp-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def cmd_pick(a):
    新 = 新信息()
    if not 新:
        print("今天还没有新东西——先干活，干完再来撞。")
        return 1
    老 = 读事实()
    已挑 = {(x["新"], x["老"]) for x in 读对账本()}
    出 = []
    用过的段 = set()
    for 条 in 新:
        if len(出) >= a.n:
            break
        if 条["来源"] in 用过的段:
            continue
        中 = 挑对(条, 老, 已挑)
        if not 中:
            continue
        用过的段.add(条["来源"])
        s, f = 中
        出.append({"号": 编号() + "-" + str(len(出) + 1), "撞击时间": datetime.now().isoformat(timespec="seconds"),
                  "新": 条["来源"], "老": f["id"], "相似度": round(s, 4),
                  "新标题": 条["标题"], "老标题": f["标题"],
                  "新正文": 条["正文"][:1500], "老正文": f["正文"][:1500]})
    if not 出:
        print("没挑出合适的一对（可能今天的段都撞过了，或者相似度都不在 0~0.06 之间）。")
        return 1
    全 = 读对账本() + 出
    写对账本(全)
    for x in 出:
        print("=" * 66)
        print("对号：%s   相似度 %.4f（越低越不像同一件事）" % (x["号"], x["相似度"]))
        print("---- 新的这一头 ----")
        print("来源：%s" % x["新"])
        print(x["新正文"][:700])
        print("---- 老的那一头 ----")
        print("来源：%s  《%s》" % (x["老"], x["老标题"]))
        print(x["老正文"][:700])
    print("=" * 66)
    print("现在问自己一句：这两件事，有没有一个共同的道理？")
    print("有的 → 写三行进收件箱：")
    print('  python tools/collide.py add --pair <对号> --title "<一句话>" --text "<三行>"')
    print("三行 = ① 共用的道理　② 能推出的新检查/新判断（要具体到能跑）　③ 什么算撞错了。缺一行拒收。")
    return 0


def cmd_show(a):
    全 = 读对账本()
    for x in 全:
        if x["号"] != a.pair:
            continue
        print("对号：%s   相似度 %.4f" % (x["号"], x["相似度"]))
        print("---- 新的 ----\n%s\n%s" % (x["新"], x["新正文"]))
        print("---- 老的 ----\n%s《%s》\n%s" % (x["老"], x["老标题"], x["老正文"]))
        if x.get("判断"):
            print("---- 已交的判断 ----\n%s" % x["判断"])
        return 0
    print("没有这个对号：%s" % a.pair)
    return 2


必写槽 = ["道理", "新检查", "撞错"]


def 拆三行(文):
    """把三行拆成三个槽。认三种写法：
    ① 1. 道理：… ② 2. 新检查：… ③ 3. 撞错：…
    或者三行纯文本按顺序填。
    """
    槽 = {}
    for 行 in (文 or "").split("\n"):
        行 = 行.strip()
        if not 行:
            continue
        for 名, 词 in (("道理", ("道理", "共用")), ("新检查", ("检查", "判断", "推出")), ("撞错", ("撞错", "证伪", "算错"))):
            if any(w in 行[:8] for w in 词):
                槽[名] = 行
                break
    if len(槽) < 3:
        # 退回按顺序填
        行 = [l.strip() for l in (文 or "").split("\n") if l.strip()]
        if len(行) >= 3:
            槽 = {"道理": 行[0], "新检查": 行[1], "撞错": 行[2]}
    return 槽


def cmd_add(a):
    对 = None
    全 = 读对账本()
    for x in 全:
        if x["号"] == a.pair:
            对 = x
            break
    if not 对:
        print("没有这个对号：%s（先 pick）" % a.pair)
        return 2
    if 对.get("判断"):
        print("这一对已经交过了：%s" % 对["判断"][:60])
        return 2
    槽 = 拆三行(a.text)
    缺 = [k for k in 必写槽 if not 槽.get(k)]
    if 缺:
        print("拒收：缺 %s。三行缺一行都不行——" % "、".join(缺))
        print("  ① 这两件事共用一个什么道理")
        print("  ② 能推出什么新检查或新判断（要具体到能跑，不能写「要更注意」）")
        print("  ③ 出现什么结果就算这次撞错了")
        return 1
    # 第 2 条再核一遍：有没有可执行的东西
    检查行 = 槽["新检查"]
    if not re.search(r"(检查|判据|命令|跑|查|python|grep|测试|阈值|如果|就)", 检查行):
        print("拒收：第 2 条看不出能跑的东西。碰撞的产物必须能落成一个动作，"
              "否则就是缝合——听着对、没法验。")
        return 1
    对["判断"] = a.text.strip()
    对["判断标题"] = a.title
    对["判断时间"] = datetime.now().isoformat(timespec="seconds")
    # 2026-11-10 20:1x 修：这里原来只写判断、不改状态，
    # 于是 `status` 里同一对会**同时被算进「待看」和「已交判断」**（两个桶按不同字段数）。
    # skip 那边一直是对的（写了 状态="看过无话"），add 这边漏了同一步。
    对["状态"] = "已交判断"
    写对账本(全)

    # 进收件箱，来源写「碰撞」，让它跟推断卡一样走拆分 AI 那条链
    新卡 = {
        "id": "cmp-" + a.pair.replace("cmp-", ""),
        "来源": "碰撞",
        "分类": "其他",
        "标题": a.title,
        "摘要": a.text.strip()[:600],
        "碰撞自": [对["新"], 对["老"]],
        "相似度": 对["相似度"],
        "时间": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        import locklib
        with locklib.锁("inbox", 记忆根=os.path.join(根, "memory")):
            行 = []
            if os.path.exists(收件箱):
                with io.open(收件箱, encoding="utf-8") as f:
                    行 = [l for l in f if l.strip()]
            行.append(json.dumps(新卡, ensure_ascii=False) + "\n")
            with io.open(收件箱, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(行)
    except Exception as e:
        print("写收件箱失败：%s（对账本已记下判断）" % e)
        return 1
    print("已收：%s → 收件箱（来源=碰撞）" % 新卡["id"])
    print("下一步：拆分 AI 会把它当普通卡备走，派给子 AI 去验第 2 条那个检查。")
    return 0


def cmd_sweep(a):
    """全量撞：今天所有新信息 × 所有教训类事实，一对都不漏，全部落账。

    2026-11-05 19:3x 用户说「碰撞当然要全部撞」。
    他是对的，而且戳中了第一版的病：`pick` 只挑「最远那一对」，
    等于让一个粗糙的相似度函数替我决定「哪对值得看」——
    可这个决定我本来就没资格做，因为「远」跟「有话说」没关系。

    改成：全组合算一遍、全部记下来（状态=待看），消化能力交给 next 分批。
    这样「撞了几对、看过几对、说过话几对」全是真数，命中率骗不了人。
    """
    新 = 新信息()
    老 = [f for f in 读事实() if f["类型"] == "feedback"]
    已有 = 读对账本()
    已键 = {(x["新"], x["老"]) for x in 已有}
    新增 = []
    for 条 in 新:
        for f in 老:
            if f["id"] == 条.get("id"):
                continue
            if (条["来源"], f["id"]) in 已键:
                continue
            新增.append({
                "号": "cmp-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(len(新增) + 1),
                "撞击时间": datetime.now().isoformat(timespec="seconds"),
                "新": 条["来源"], "老": f["id"],
                "相似度": round(相似(条["正文"], f["正文"] + " " + f["标题"]), 4),
                "新标题": 条["标题"], "老标题": f["标题"],
                "新正文": 条["正文"][:1500], "老正文": f["正文"][:1500],
                "状态": "待看",
            })
    if 新增:
        写对账本(已有 + 新增)
    全 = 读对账本()
    桶 = {"0（零重合）": 0, "0~0.005（极远）": 0, "0.005~0.02（中远）": 0,
          "0.02~0.05（中近）": 0, ">0.05（偏近）": 0}
    for x in 全:
        s = x["相似度"]
        if s <= 0:
            桶["0（零重合）"] += 1
        elif s < 0.005:
            桶["0~0.005（极远）"] += 1
        elif s < 0.02:
            桶["0.005~0.02（中远）"] += 1
        elif s <= 0.05:
            桶["0.02~0.05（中近）"] += 1
        else:
            桶[">0.05（偏近）"] += 1
    待 = sum(1 for x in 全 if x.get("状态") == "待看")
    看过 = sum(1 for x in 全 if x.get("状态") == "看过无话")
    交了 = sum(1 for x in 全 if x.get("判断"))
    print("这一轮新撞 %d 对（新信息 %d 条 × 教训 %d 条，标签重叠的跳过）。" %
          (len(新增), len(新), len(老)))
    print("账本里一共 %d 对：待看 %d ／ 看过无话 %d ／ 已交判断 %d。" % (len(全), 待, 看过, 交了))
    print("相似度分布：")
    for k, v in 桶.items():
        print("  %-18s %d" % (k, v))
    print()
    print("**零重合的那一桶基本是废对**（两个东西毫无共同词，通常也无共同道理）。")
    print("要消化就跑：python tools/collide.py next --n 10（中间带优先，不是最远优先）。")
    return 0


def cmd_next(a):
    """按批消化：中间带优先。

    不取「最远」，取「不像同一件事、但也不是毫无交集」的那一段——
    完全零重合的往往是「一个数字配一句话」，无话可说；
    太近的是「同一类的事」，撞出来只会是「哦这跟上次一样」。
    """
    待 = [x for x in 读对账本() if x.get("状态") == "待看"]

    def 分(x):
        s = x["相似度"]
        if s <= 0:
            return 9
        if s < 0.005:
            return 3
        if s <= 0.05:
            return 1
        return 2

    待.sort(key=lambda x: (分(x), x["相似度"]))
    取 = 待[:a.n]
    if not 取:
        print("没有待看的了。跑 sweep 补一批，或者全部消化完了。")
        return 0
    全 = 读对账本()
    号集 = {x["号"] for x in 取}
    for x in 全:
        if x["号"] in 号集:
            x["状态"] = "看过"
    写对账本(全)
    for x in 取:
        print("=" * 66)
        print("对号：%s   相似度 %.4f" % (x["号"], x["相似度"]))
        print("-- 新：%s" % x["新"])
        print(x["新正文"][:600])
        print("-- 老：%s《%s》" % (x["老"], x["老标题"]))
        print(x["老正文"][:600])
    print("=" * 66)
    print("这一批 %d 对已标「看过」。有话说：add --pair <对号>；" % len(取))
    print("没话说：skip --pair <对号>（一定要标，不然命中率就是假的）。")
    return 0


def cmd_skip(a):
    """标「看过、无话可说」。

    这一步比 add 还重要：不标 skip，账本里就只有「说过话」的记录，
    命中率会永远显示 100%——那是我今天已经在别处犯过的错（predict 的选择偏差）。
    """
    全 = 读对账本()
    for x in 全:
        if x["号"] == a.pair:
            if x.get("判断"):
                print("这一对已经交过判断了，不能跳过。")
                return 2
            x["状态"] = "看过无话"
            x["跳过时间"] = datetime.now().isoformat(timespec="seconds")
            写对账本(全)
            print("已标：%s → 看过无话" % a.pair)
            return 0
    print("没有这个对号：%s" % a.pair)
    return 2


def _碰分(x):
    """中间带优先：完全零重合的往往是「一个数字配一句话」，太近的只会撞出「跟上一次一样」。"""
    s = x.get("相似度") or 0
    if s <= 0:
        return 9
    if s < 0.005:
        return 3
    if s <= 0.05:
        return 1
    return 2


def cmd_auto(a):
    """每 N 小时自动出一批，摆到桌上，交给会话里的我逐对判断。

    2026-11-05 20:0x 用户：「12 小时碰一次，要全自动」。
    能全自动的是：不靠人记得，自己到点挑好、写好批次文件、生成任务卡叫醒我。
    最后「看出联系」那一下必须过模型的脑子——这一步由会话里的我或派出去的子 AI 做，
    平台边界，定时任务里起不了模型。所以这里只负责「把活的原料备好 + 到点提醒」。
    """
    已有 = []
    if os.path.isdir(碰撞目录):
        已有 = sorted(x for x in os.listdir(碰撞目录)
                     if x.startswith("batch-") and x.endswith(".md"))
    if 已有:
        print("上一批还没处理：memory/collide/%s（挂着 %d 个批次文件）" % (已有[0], len(已有)))
        return 0

    戳 = os.path.join(碰撞目录, ".last-auto")
    已 = 0.0
    if os.path.exists(戳):
        try:
            已 = float(读文本(戳).strip() or 0)
        except Exception:
            已 = 0.0
    距 = (time.time() - 已) / 3600.0 if 已 else 999.0
    if 距 < a.hours:
        print("距上次自动碰 %.1f 小时，还不到 %.0f 小时。" % (距, a.hours))
        return 0

    待 = [x for x in 读对账本() if x.get("状态") == "待看"]
    if not 待:
        print("没有待看的对了。跑 sweep 补一批。")
        return 0
    待.sort(key=lambda x: (_碰分(x), x.get("相似度") or 0))
    取 = 待[:a.n]

    批名 = "batch-%s.md" % datetime.now().strftime("%Y%m%d-%H%M%S")
    批 = os.path.join(碰撞目录, 批名)
    行 = ["# 碰撞批次 %s（%d 对）" % (datetime.now().strftime("%Y-%m-%d %H:%M"), len(取)), ""]
    for x in 取:
        行 += [
            "## 对号 %s（相似度 %.4f）" % (x["号"], x.get("相似度") or 0),
            "**新**：%s｜%s" % (x.get("新"), x.get("新标题") or ""),
            (x.get("新正文") or "")[:800],
            "",
            "**老**：%s｜%s" % (x.get("老"), x.get("老标题") or ""),
            (x.get("老正文") or "")[:800],
            "",
        ]
    行 += ["---", "逐对判断：add --pair <对号> --title … --text …；没话说：skip --pair <对号>；",
           "全部处理完：python tools/collide.py batch-done %s" % 批名, ""]
    io.open(批, "w", encoding="utf-8").write("\n".join(行))
    io.open(戳, "w", encoding="utf-8").write(str(time.time()))
    print("已出批次：memory/collide/%s（%d 对）" % (批名, len(取)))
    print("处理完跑：python tools/collide.py batch-done %s" % 批名)
    return 0


def cmd_batch_done(a):
    """一批处理完了：把批次文件销掉，念头随之消失。"""
    名 = a.file if os.path.isabs(a.file) else os.path.join(碰撞目录, a.file)
    if not os.path.exists(名):
        print("没有这个批次文件：%s" % 名)
        return 2
    os.remove(名)
    待 = sum(1 for x in 读对账本() if x.get("状态") == "待看")
    print("批次已销：%s  剩 %d 对待看" % (os.path.basename(名), 待))
    return 0


def cmd_stage(a):
    """把一批待看的对打包成一张卡，塞进收件箱，走 arch 那条链去派子 AI。

    2026-11-05 19:4x 用户「消化接口接知行合一监听口」。
    对：456 对待看不该由我一个人读——那要读完 27 万字，且我一定越读越糊。
    接到 arch 链上就变成：一张卡装几对 → 拆单 AI 备卡 → 我派给子 AI →
    子 AI 逐对答「有话说 / 无话可说」→ 回执回来 → sync 落回账本。

    **刻意不预生成几百张卡**：那样会当场把 inbox 和备卡上限（10 张）撑爆，
    变成另一种形式的堵。按需生成，一次一张。
    """
    n = max(1, a.n)
    待 = [x for x in 读对账本() if x.get("状态") == "待看"]

    def 分(x):
        s = x["相似度"]
        if s <= 0:
            return 9
        if s < 0.005:
            return 3
        if s <= 0.05:
            return 1
        return 2

    待.sort(key=lambda x: (分(x), x["相似度"]))
    取 = 待[:n]
    if not 取:
        print("没有待看的了，不用生成卡。")
        return 0
    批号 = "cmp-batch-" + datetime.now().strftime("%Y%m%d-%H%M%S")

    段 = []
    段.append("共 %d 对。**每一对都要单独答一行**，不许只答第一对就收工。" % len(取))
    段.append("")
    段.append("有话说就写：`N 撞到了。道理：… / 新检查：… / 撞错：…`")
    段.append("没话说就写：`N 无话可说。`")
    段.append("（N 是对号里的序号，从 1 开始）")
    段.append("")
    段.append("**判什么算撞到了**：这两件事之间有一个共同的道理，而且这个道理能推出一个"
              "能跑的新检查或新判断。说不出怎么验的，一律算无话可说——"
              "编一句听着对的话是最容易的，不要编。")
    段.append("")
    for i, x in enumerate(取, 1):
        段.append("【对 %d】%s  相似度 %.4f" % (i, x["号"], x["相似度"]))
        段.append("-- 新：%s" % x["新"])
        段.append(x["新正文"][:420].replace("\n", " "))
        段.append("-- 老：%s《%s》" % (x["老"], x["老标题"]))
        段.append(x["老正文"][:420].replace("\n", " "))
        段.append("")

    卡 = {
        "id": 批号,
        "来源": "碰撞",
        "分类": "其他",
        "标题": "碰撞消化 %d 对（逐对答）" % len(取),
        "摘要": "\n".join(段)[:3000],
        "对数": len(取),
        "对号": [x["号"] for x in 取],
        "时间": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        import locklib
        with locklib.锁("inbox", 记忆根=os.path.join(根, "memory")):
            行 = []
            if os.path.exists(收件箱):
                with io.open(收件箱, encoding="utf-8") as f:
                    行 = [l for l in f if l.strip()]
            行.append(json.dumps(卡, ensure_ascii=False) + "\n")
            with io.open(收件箱, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(行)
    except Exception as e:
        print("写收件箱失败：%s" % e)
        return 1
    # 这 5 对标「已打包」，别再被 other stage 挑走
    全 = 读对账本()
    号集 = set(卡["对号"])
    for x in 全:
        if x["号"] in 号集:
            x["状态"] = "已打包"
            x["批次"] = 批号
    写对账本(全)
    print("已生成卡：%s（%d 对已标『已打包』）" % (批号, len(取)))
    print("下一步走 arch 链：")
    print("  python tools/arch-listen.py stage      # 拆单 AI 备卡")
    print("  python tools/arch-listen.py staged     # 看备了什么")
    print("  python tools/arch-listen.py dispatch %s --to \"子AI:碰撞\"" % 批号)
    print("子 AI 交完回执后：python tools/collide.py sync")
    return 0


def cmd_sync(a):
    """读 inbox 里的碰撞卡回执，把结果落回 pairs 账本。

    回执格式约定（stage 里写死在卡上）：
      `N 撞到了。道理：… / 新检查：… / 撞错：…`
      `N 无话可说。`
    sync 只认这两种。答得不清楚的，状态留「已打包」等人工看——不猜。
    """
    if not os.path.exists(收件箱):
        print("收件箱不存在。")
        return 1
    全 = 读对账本()
    按号 = {x["号"]: x for x in 全}
    改动 = 0
    跳过 = 0
    with io.open(收件箱, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                卡 = json.loads(line)
            except Exception:
                continue
            if 卡.get("来源") != "碰撞" or "对号" not in 卡:
                continue
            回 = 卡.get("回执") or {}
            # 字段名踩过坑：receipt 存的是「内容」，我第一版读「文本」，
            # 结果 sync 永远落回 0 条，而乱码输出里「内容」和「文本」看着一样长，
            # 害我以为是正则的问题。两个都认，别猜。
            文 = 回.get("文本") or 回.get("内容") or ""
            if not 文:
                continue
            号列 = 卡["对号"]
            for i, 对号 in enumerate(号列, 1):
                x = 按号.get(对号)
                if not x or x.get("判断") or x.get("状态") == "看过无话":
                    continue
                命 = re.search(r"(?:^|\n)\s*%d\s*[\.、:：]?\s*([^\n]+(?:\n(?![0-9]+\s*[\.、:：]?\s*(?:撞|无))[^\n]+)*)" % i, 文)
                片 = 命.group(1).strip() if 命 else ""
                if not 片:
                    跳过 += 1
                    continue
                if "无话可说" in 片 or "没话说" in 片:
                    x["状态"] = "看过无话"
                    x["跳过原因"] = "子AI:无话可说"
                    x["回执谁"] = 回.get("谁") or ""
                    改动 += 1
                elif "撞到了" in 片 or "道理：" in 片:
                    x["判断"] = 片
                    x["判断标题"] = (片.split("道理：")[1][:60] if "道理：" in 片 else 片[:60])
                    x["判断来自"] = "子AI:" + (回.get("谁") or "?")
                    x["状态"] = "已交判断"
                    x["判断时间"] = datetime.now().isoformat(timespec="seconds")
                    改动 += 1
                else:
                    跳过 += 1
    写对账本(全)
    待 = sum(1 for x in 全 if x.get("状态") == "已打包")
    print("sync 完成：落回 %d 条，看不懂留着的 %d 条，还挂在『已打包』等回执的 %d 条。" %
          (改动, 跳过, 待))
    if 跳过:
        print("看不懂的那些不猜——它们的回执答案不在约定格式里，得人看一眼。")
    return 0


主题黑名单 = set("""
判断 问题 东西 一样 时候 地方 自己 不能 就是 所以 这个 那个 我们 可以 需要 因为 但是 不是 没有
已经 一次 每次 一个 什么 怎么 为什么 或者 如果 那么 这样 那样 这些 那些 他们 你们 现在 之前 之后
正确 错误 必须 应该 可能 一定 其实 真正 具体 直接 继续 开始 结果 情况 非常 很多 只有 就是 还是
道理 检查 新检 新检查 撞错 碰错 说明 一条 列出 到了 这是 那要 不是 都得 才算 得了 起来 上去 下来
""".split())

方向账本 = os.path.join(碰撞目录, "directions.jsonl")


def 抽概念(文本):
    """从一段判断里抽可重复的概念（英文词 + 中文二字组），去掉噪音。"""
    出 = set()
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9_\-]{2,}", 文本 or ""):
        w = m.group(0).lower()
        if w not in 主题黑名单:
            出.add(w)
    for m in re.finditer(r"[\u4e00-\u9fa5]{2,6}", 文本 or ""):
        w = m.group(0)
        for i in range(len(w) - 1):
            g = w[i:i + 2]
            if g not in 主题黑名单:
                出.add(g)
    return 出


def cmd_themes(a):
    """看「反复撞到同一处」——方向只能从这里长出来。

    2026-11-05 20:2x 用户：「方向由你分辨，值不值得追也由你」。
    机器能做的是把反复出现的概念摆出来；「这算不算一个方向、值不值得追」必须我判，
    因为那要过模型的脑子。所以这个命令只出候选，不定方向。
    """
    判断行 = [x for x in 读对账本() if x.get("判断")]
    if len(判断行) < a.min:
        if a.json:
            print(json.dumps({"够格": [], "说明": "只有 %d 条判断，少于 %d 条，看不出反复" % (len(判断行), a.min)},
                             ensure_ascii=False))
        else:
            print("交过判断的只有 %d 条，至少 %d 条才看得出「反复」。先让碰撞跑一阵。" % (len(判断行), a.min))
        return 0
    计 = {}
    for x in 判断行:
        # 只从「判断标题」抽，不从整段判断抽：
        # 判断正文是三行模板（道理 / 新检查 / 什么算碰错了），抽出来全是「道理」「检查」这种格式词，
        # 方向候选会系统性偏成同一句话。标题才是一句话结论。
        文本 = x.get("判断标题") or ""
        for w in 抽概念(文本):
            计.setdefault(w, []).append(x["号"])
    够格 = sorted(
        ({"词": w, "条数": len(v), "对号": v} for w, v in 计.items() if len(v) >= a.min),
        key=lambda z: (-z["条数"], z["词"]))
    if a.json:
        print(json.dumps({"够格": 够格}, ensure_ascii=False))
        return 0
    if not 够格:
        print("没有哪个概念在 %d 条以上判断里反复出现——现在还没有方向。" % a.min)
        return 0
    print("反复出现的概念（同一概念出现在多少条不同判断里）：")
    for z in 够格:
        print("  %-8s  %d 条  %s" % (z["词"], z["条数"], "、".join(z["对号"])))
    print("\n这只是一堆反复。算不算方向、值不值得追，要看这些判断是不是真指向同一个洞。")
    return 0


def cmd_direct_add(a):
    """立一个方向：写清楚凭什么值得、什么算我判错。没有这三样不许立。"""
    if not (a.title and a.why and a.wrong):
        print("三样都要：--title 方向 / --why 凭什么值得 / --wrong 出现什么就说明我判错了")
        return 2
    记 = {
        "时间": datetime.now().isoformat(timespec="seconds"),
        "方向": a.title,
        "凭什么": a.why,
        "判断错的标志": a.wrong,
        "证据": a.basis or "",
    }
    with io.open(方向账本, "a", encoding="utf-8") as f:
        f.write(json.dumps(记, ensure_ascii=False) + "\n")
    print("方向已记：%s" % a.title)
    print("判错的标志：%s" % a.wrong)
    return 0


def cmd_direct_list(a):
    if not os.path.exists(方向账本):
        print("还没立过方向。")
        return 0
    行 = [json.loads(l) for l in io.open(方向账本, encoding="utf-8") if l.strip()]
    if not 行:
        print("还没立过方向。")
        return 0
    for x in 行:
        print("%s  %s" % (x.get("时间"), x.get("方向")))
        print("   凭什么：%s" % x.get("凭什么"))
        print("   判错标志：%s" % x.get("判断错的标志"))
    return 0


def cmd_report(a):
    全 = 读对账本()
    if not 全:
        print("还没撞过。跑 pick。")
        return 0
    交了 = [x for x in 全 if x.get("判断")]
    待看 = sum(1 for x in 全 if x.get("状态") == "待看")
    已打包 = sum(1 for x in 全 if x.get("状态") == "已打包")
    无话 = sum(1 for x in 全 if x.get("状态") == "看过无话" and not x.get("判断"))
    print("撞过 %d 对：待看 %d ／ 已打包等回执 %d ／ 看过无话 %d ／ 已交判断 %d。"
          % (len(全), 待看, 已打包, 无话, len(交了)))
    # 2026-11-05 19:5x 修口径：原来把「没看过」也算成「落空」，
    # 523 对里 450 对还没看，却报「落空 518」——那是统计在撒谎。
    print("**看过无话 ≠ 落空，没看过的更不该算落空。** 真命中率只能拿"
          "（已交判断 ÷ 已消化）算，现在已消化 %d 对。" % (无话 + len(交了)))
    if not 交了:
        print("一条都还没交——撞出来无话可说，本身就是个信号：")
        print("要么这些对确实没关系，要么我的『道理』只认得同一类的事。")
        return 0
    print()
    for x in 交了[-10:]:
        print("- %s  %s" % (x["号"], x.get("判断标题") or ""))
        print("  撞的是：%s × %s" % (x["新"], x["老"]))
    命中率 = len(交了) / float(len(全))
    print()
    print("撞出东西的比例：%.0f%%" % (命中率 * 100))
    print("**这个数低是正常的**，甚至是好的：能撞出东西的比例太高，说明挑的对根本不相干不够，"
          "只是在同类里配对。")
    print("真正要盯的是：交回的判断里，有几条推出了能在三天内跑起来的新检查。这个账还没记。")
    return 0


def cmd_list(a):
    全 = 读对账本()
    if not 全:
        print("还没撞过。")
        return 0
    for x in 全[-20:]:
        态 = "已交判断" if x.get("判断") else "没交（大概是撞不出话）"
        print("%s  %.4f  %s × %s  [%s]" % (x["号"], x["相似度"], x["新"][:24], x["老"], 态))
    return 0


def main():
    ap = argparse.ArgumentParser(description="碰撞：新信息 × 最不相干的老记忆")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pick")
    p.add_argument("--n", type=int, default=1)
    p.set_defaults(func=cmd_pick)

    p = sub.add_parser("show")
    p.add_argument("--pair", required=True)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("add")
    p.add_argument("--pair", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("sweep")
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("next")
    p.add_argument("--n", type=int, default=10)
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("skip")
    p.add_argument("--pair", required=True)
    p.set_defaults(func=cmd_skip)

    p = sub.add_parser("stage")
    p.add_argument("--n", type=int, default=5)
    p.set_defaults(func=cmd_stage)

    p = sub.add_parser("auto")
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--hours", type=float, default=12.0)
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("batch-done")
    p.add_argument("file")
    p.set_defaults(func=cmd_batch_done)

    p = sub.add_parser("sync")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("report")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("themes")
    p.add_argument("--min", type=int, default=3, help="同一概念要出现在多少条判断里才算反复")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_themes)

    p = sub.add_parser("direct-add")
    p.add_argument("--title", required=True)
    p.add_argument("--why", default="", help="凭什么觉得值得追")
    p.add_argument("--wrong", default="", help="出现什么就说明我判错了")
    p.add_argument("--basis", default="", help="证据：哪几条判断指过来")
    p.set_defaults(func=cmd_direct_add)

    p = sub.add_parser("direct-list")
    p.set_defaults(func=cmd_direct_list)

    p = sub.add_parser("list")
    p.set_defaults(func=cmd_list)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
