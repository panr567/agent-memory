#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开工四问 + 三分类结算（2026-11-02 加，11-03 改，11-05 加第四问）

规矩是从用户那条「持续学习」链里拆出来的：
学习 → 想法 → **评估想法的成功点和失败点** → 评估方案 → 选择执行
→ **未达到目标预期** → 失败 → 复盘 → 改进 → 到预期

注意链路里「未达到目标预期」在「失败」前面——它们是两回事：
  - 达到预期      → 收到
  - 未达到但没崩  → 最危险。不报警，容易被当成「做完了」混过去
  - 崩了          → 至少看得见

四问（add）：
  --win    我猜它会在哪成
  --lose   我猜它会在哪败
  --detect 败了我怎么知道
  --accept **成了我怎么知道**   ← 11-05 加的验收点

为什么补第四问：
  前三问只管「败」，不管「成」。于是「动作做完」很容易被当成「功能成立」——
  11-05 装 dsh-memory 时，加了 dependencies 就报「好了」，而真正决定加载的
  bundles 没动，四轮才装上。回执记的是「动了 17 次工具」，账上满分。
  第四问逼我在动手前写下「成了长什么样、指给谁看」；写不出来 = 这事没法验。

用法：
  python tools/predict.py add --task j1102233544-fde --win "…" --lose "…" --detect "…" --accept "…"
  python tools/predict.py settle --task j1102233544-fde --reached --proof "13 项测试全过 / .health.json 有 predict 字段"
  python tools/predict.py settle --task j1102233544-fde --fell-short --note "能跑，但没到预期，差在…"
  python tools/predict.py settle --task j1102233544-fde --broke --note "哪一步崩的"
  python tools/predict.py stats
  python tools/predict.py list

账本：memory/autonomy/predictions.jsonl（追加，不改旧行）
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from locklib import 锁  # noqa: E402

根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# PREDICT_BOOK 是给测试用的：不许拿真账本试阈值，不然清理起来又是一笔烂账。
账本 = os.environ.get("PREDICT_BOOK") or os.path.join(根, "memory", "autonomy", "predictions.jsonl")

# 2026-11-06 抽公共模块：jsonl 读取统一走 tools/台账.py（tools/ 下曾有 15 处重复）
from 台账 import 读行

三类 = ("达到", "未达", "崩")

# 对账（11-03 加）：stats 只是打印，没人看；check 带退出码，才会真变成「明天的念头」。
# 最少样本=5：样本太少谈准确率是自欺，宁可先忍着不报。
# 及格线=0.60：这条线是我先拍的，不是从数据里算出来的。跑满 30 条之后该回头校准一次。
最少样本 = 5
及格线 = 0.60


def 读():
    """读预测账本。读写口径统一在 tools/台账.py（2026-11-06 抽公共模块）。"""
    return 读行(账本)


def 追加(事):
    os.makedirs(os.path.dirname(账本), exist_ok=True)
    with 锁("predictions", os.path.join(根, "memory")):
        with open(账本, "a", encoding="utf-8") as f:
            f.write(json.dumps(事, ensure_ascii=False) + "\n")


事实目录 = os.path.join(根, "memory", "facts")


def 切词(s):
    """中文按两字切片，跟 xi-brain 的切词一个思路：粗，但够用。"""
    out = []
    for 段 in re.split(r"[^\u4e00-\u9fa5A-Za-z0-9]+", s or ""):
        if len(段) < 2:
            continue
        for i in range(len(段) - 1):
            out.append(段[i:i + 2])
    return out


def 读经验():
    """只读 front-matter：开工提示要短，正文留给真正需要时再翻。"""
    out = []
    if not os.path.isdir(事实目录):
        return out
    for 名 in sorted(os.listdir(事实目录)):
        if not 名.endswith(".md"):
            continue
        try:
            with open(os.path.join(事实目录, 名), encoding="utf-8") as f:
                文 = f.read()
        except Exception:
            continue
        段 = 文.split("---")
        if len(段) < 3:
            continue
        项 = {}
        for 行 in 段[1].splitlines():
            if ":" in 行:
                k, v = 行.split(":", 1)
                项[k.strip()] = v.strip()
        if 项.get("type") != "feedback":
            continue
        out.append({
            "id": 项.get("id", 名[:-3]),
            "title": 项.get("title", ""),
            "trigger": 项.get("trigger", ""),
            "tags": [x.strip() for x in 项.get("tags", "").strip("[]").split(",") if x.strip()],
            "正文": 段[2].strip(),
        })
    return out


_token缓存 = {'值': '', '时间': 0}


def 取token():
    """从 web.out.log 抓最新的 ?token=。抓不到就返回空，走降级。"""
    现在 = __import__('time').time()
    if _token缓存['值'] and 现在 - _token缓存['时间'] < 300:
        return _token缓存['值']
    最新 = ''
    for 路 in (r'D:\dsh\web.out.log',):
        try:
            with open(路, encoding='utf-8', errors='replace') as f:
                for 行 in f:
                    m = re.search(r'token=([A-Za-z0-9_-]{10,})', 行)
                    if m:
                        最新 = m.group(1)
        except Exception:
            continue
    if 最新:
        _token缓存['值'] = 最新
        _token缓存['时间'] = 现在
    return 最新


def 远程查经验(文本, 上限=5):
    """走插件的 /dsh-xi-brain/recall：和大脑注入用同一套打分。

    11-03 用户「后者」——两套打分并存是分裂：同一段话，插件和 predict.py
    给出的排序不一样。现在统一由插件算，predict.py 只消费结果。
    取不到（web 没起/超时）返回 None，调用方降级并在输出里明说。
    """
    tk = 取token()
    if not tk:
        return None
    try:
        地址 = 'http://127.0.0.1:3080/dsh-xi-brain/recall?n=%d&q=%s&token=%s' % (
            max(上限, 8), urllib.parse.quote(文本), tk)
        with urllib.request.urlopen(地址, timeout=3) as r:
            数据 = json.loads(r.read().decode('utf-8'))
    except Exception:
        return None
    出 = []
    for h in (数据.get('hits') or []):
        出.append({
            'id': h.get('id', ''),
            'title': h.get('title', ''),
            'type': h.get('type', ''),
            'trigger': '',
            'tags': h.get('tags') or [],
            '正文': h.get('body') or '',
        })
    return 出


def 查经验(文本, 上限=5):
    """按词面给经验排序，2-gram 命中越多越靠前。

    这不是"智能匹配"。它只把字面沾边的顶上来，真正的价值是
    「让开工人看见库里有哪些经验」，不是「替他判断哪条有用」。
    """
    远程 = 远程查经验(文本, 上限)
    if 远程 is not None:
        return 远程[:上限]
    print('  （web 不在线，经验匹配降级成本地算法——排序可能和大脑注入不一致）')
    词 = set(切词(文本))
    if not 词:
        return []
    分 = []
    for f in 读经验():
        # 11-03 用户：匹配串不能只看标题/trigger/tags——28 条事实里只有 2 条写了 trigger，
        # 正文里最具体的教训永远命不中。现在把正文也放进来，但减半计权：
        # 标题/trigger/tags 命中算 2，正文命中算 1，保证标题沾边的仍然排在前面，
        # 又不至于因为正文里的通用词（文件、配置、脚本）把噪音顶上来。
        头 = (f["title"] + " " + f["trigger"] + " " + " ".join(f["tags"])).lower()
        身 = (f.get("正文") or "")[:400].lower()
        n = 0
        身中 = 0
        for w in 词:
            if w in 头:
                n += 2
            elif w in 身:
                身中 += 1
        # 正文命中要求至少 2 个不同词才计分：单个词太容易撞
        #（实测「子智能体拆分任务」会靠「任务」两字捞到「会话收尾必须写当下」，纯噪音）
        if 身中 >= 2:
            n += 身中
        if n:
            分.append((n, f))
    分.sort(key=lambda x: -x[0])
    return [f for _, f in 分[:上限]]


def 查改法(文本, 上限=3):
    """从历史账本里捞「判了未达、写了改法」的条目，按词面匹。

    这是账本的回流：改法写在账本里如果开工时读不到，等于没写。
    只认 未达/崩 且有改法的——判「达到」的不该来教下一次。
    """
    词 = set(切词(文本))
    if not 词:
        return []
    分 = []
    for x in 读():
        if x.get("结果") not in ("未达", "崩"):
            continue
        改法 = (x.get("改法") or "").strip()
        if not 改法:
            continue
        串 = (x.get("task", "") + " " + x.get("说明", "") + " " + 改法).lower()
        n = sum(1 for w in 词 if w in 串)
        if n:
            分.append((n, x))
    分.sort(key=lambda x: -x[0])
    return [(x.get("task", ""), (x.get("改法") or "").strip()) for _, x in 分[:上限]]


def cmd_add(a):
    # 2026-11-05 加第四问 --accept（验收点）。
    # 为什么补：原来三问只问「败了怎么知道」，没问「成了怎么知道」。
    # 结果就是——我把「动作做完」当成「功能成立」：
    # 装插件时加了 dependencies 就报「好了」，而真正决定加载的 bundles 没动，
    # 四轮才装上。回执上记的是「动了 17 次工具」，没人能发现方向错了。
    # 第四问逼我在动手前就写下「成了长什么样」，写不出来 = 这事根本没法验。
    缺 = [名 for 名, 值 in (("--win", a.win), ("--lose", a.lose),
                             ("--detect", a.detect), ("--accept", a.accept))
          if not (值 or "").strip()]
    if 缺:
        print("答不全：%s。" % "、".join(缺))
        if "--accept" in 缺:
            print("**第四问答不出就别开工**——说不清「成了长什么样」，等于没有验收点，"
                  "做的每一下都只能算「动了」，不能算「对了」。")
        else:
            print("**第三问答不出就别开工**——那说明这个改动没法验。")
        return 2
    追加({
        "t": datetime.datetime.now().isoformat(timespec="seconds"),
        "task": a.task or "",
        "win": a.win.strip(),
        "lose": a.lose.strip(),
        "detect": a.detect.strip(),
        # 验收点：成了的那一刻，我能指给别人看的东西是什么。
        "accept": a.accept.strip(),
        # 「我打算怎么做」——这是**方案**这个对象本身。
        # 没有它，复盘只能修个案；有了它，才能问「上次那个做法错在哪」
        "how": (a.how or "").strip(),
        "结果": "",
        # 2026-11-06 00:4x 加：异体审判据的占位。
        # 我自己写的 accept 自己判不了——`mine` 只能查「证据跟验收点对得上吗」，
        # 查不了「这条判据本身能不能验收这件事」。所以留个空位，
        # 等一个不知道上下文的子 AI 看完写回：{判: "过"/"不过", 理由: "...", 时间: "..."}
        "accept_review": None,
    })
    print("记下四问：", a.task or "(没写任务 id)", "｜方案：" + ((a.how or "").strip()[:40] or "（没写！补上）"))

    # 2026-11-05 23:5x：accept 是「我自己出的题」——自己出题自己判卷，等于没有验收点。
    # 这里做一次粗筛：accept 里得有一个可验证的锚（路径 / 命令 / 数字带单位 / 时间点）。
    # 一个都没有就警告。不是判它错，是说它没法被别人拿去复验。
    验收 = a.accept.strip()
    # 锚要「别人拿着能去查」的东西。太宽的字不算——「跑」「看到」这种词
    # 在「应该能跑通」里也出现，等于没筛。所以只留路径/命令/数字/时间点。
    锚 = ('/', '\\', '.json', '.jsonl', '.md', '.py', '.js', '.log', '.html', '.txt',
         'bytes', '字节', 'KB', 'MB', '行数', '条数', '退出码', '端口',
         '分钟', '小时', '秒', '截图', '进程')
    命中 = [x for x in 锚 if x in 验收]
    if not 命中:
        print("⚠ 这条 accept 没有可验证的锚——「%s」这种话，别人拿什么复验？" % 验收[:30])
        print("   补一个能指给人看的东西：哪个文件 / 哪条命令 / 什么数字 / 哪个时间点。")
    elif len(验收) < 12:
        print("⚠ accept 只有 %d 字符——确认它真能让第三人判断成没成。" % len(验收))
    # 2026-11-03：开工前把沾边的经验顶出来。
    # 之前经验库只靠 xi-recall 按"这一轮的话"检索，词面不重合就命不中——
    # 这轮我在改代码，最该出现的「改东西前先搜依赖」一次都没出现。
    提示 = 查经验(" ".join([a.task or "", a.win or "", a.lose or "", a.detect or "", a.how or ""]))
    if 提示:
        print("【开工前过一眼】%d 条经验沾边：" % len(提示))
        for f in 提示:
            print("  - %s｜%s" % (f["title"], f["trigger"] or f["正文"][:40].replace("\n", " ")))
    # 2026-11-03：账本回流。判了「未达」写的改法，开工时读不到就等于没写。
    旧账 = 查改法(" ".join([a.task or "", a.win or "", a.lose or "", a.detect or "", a.how or ""]))
    if 旧账:
        print("【上次栽过】%d 条改法可能用得上：" % len(旧账))
        for 任务, 改法 in 旧账:
            print("  - %s｜%s" % (任务 or "(无任务名)", 改法[:90]))
    return 0


def _记差值(a, 结果):
    """未达/崩就是差值，自动记进 signals.jsonl（2026-11-06 加）。

    「四源」以前只写在 signals.py 的注释里，这条线根本没接——
    于是「反复没做对」这件事永远进不了候选规则，只能靠我自己想起来。
    去重按 task：同一个任务反复未达是同一个问题，聚成一簇才有意义。
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import signals
        旧 = signals.读()
        # 2026-11-08 10:0x：查重必须排除已销账的，否则「未达→修好→又未达」第二次不进账。
        死的 = signals.已处理(旧)
        已记 = {str(x.get("目标") or "") for x in 旧
                if not x.get("处理") and x.get("来源") == "predict"
                and x.get("id") not in 死的}
        if a.task in 已记:
            return
        signals.加({
            "id": "sg-pdt-" + os.urandom(4).hex(),
            "时间": signals.现在(),
            "来源": "predict",
            "什么": "预测未达：%s" % a.task,
            "修法": (a.fix or "").strip()[:120],
            "处理": False,
            "目标": a.task,
        })
        print("（已记进 signals.jsonl，来源=predict）")
    except Exception as e:
        # 不吞：写不进去要看得见
        print("（signals 没写进去：%s）" % e)


def cmd_settle(a):
    候选 = [("达到", a.reached), ("未达", a.fell_short), ("崩", a.broke)]
    结果 = [名 for 名, 开 in 候选 if 开][0]
    # 未达的时候，不许只说「没到」——必须说清方案错在哪、下次换成什么。
    # 这是「改进执行方案」这一环唯一的落点，缺了它，复盘永远修的是个案。
    if 结果 == "未达" and not (a.fix or "").strip():
        print("未达必须带 --fix：这次选的方案错在哪、下次换成什么。不写就别结算。")
        return 2
    # 11-03 补：达到这一侧原来什么都不用交，恰好是最容易刷的一侧。
    # 跟「未达必须 --fix」对称——判好的也要能被人反驳，不然账本只是自证清白。
    if 结果 == "达到" and not (a.proof or "").strip():
        print("达到必须带 --proof：凭什么算达到（测试数/日志行/谁确认的）。空口的不算。")
        return 2
    # 2026-11-06 00:5x 用户「判不过就拦」。
    # 没有这道闸的话，异体审判据只是记账——卡照建、活照干、照样能判达到，等于审了白审。
    # 口径：
    #   判「不过」 → 直接拒收，先改判据再审过（这是唯一硬拦的一种）
    #   没审过     → 警告，不拦（老卡没有 accept_review 字段，硬拦会把历史全卡死）
    #   判「过」   → 放行
    if 结果 == "达到":
        with 锁("predictions", os.path.join(根, "memory")):
            全部 = 读()
        这卡 = None
        for 事 in reversed(全部):
            if 事.get("task") == a.task and not 事.get("结果"):
                这卡 = 事
                break
        if 这卡 is not None:
            审 = 这卡.get("accept_review")
            判 = (审 or {}).get("判") if isinstance(审, dict) else None
            if 判 == "不过":
                print("验收点没过异体审，不许判达到。")
                print("  子 AI 说：%s" % ((审.get("理由") or "")[:120]))
                print("  先改 --accept（或用 accept-set 重审），改到能过再结算。")
                return 2
            if not 审:
                # 2026-11-06 01:5x 用户「那为什么还不修」。
                # 原来这里只打印一行 ⚠ 然后继续放行——于是今晚 6 张卡一张都没过审，
                # 照样判达到，96% 一分没掉。**警告不是闸**，警告只是让我心里好过一点。
                # 当初不硬拦的理由是「老卡没这个字段会把历史卡死」，但那不成立：
                # 历史卡都已经结算过、不会再走 settle，拦只拦得住新卡。
                print("验收点没过异体审，不许判达到。")
                print("  这道闸存在的全部理由就是「拦住」——不拦等于没审。")
                print("  先送审：python tools/audit.py accept-set \"%s\" --verdict 过|不过 --why \"...\""
                      % a.task)
                return 2
    with 锁("predictions", os.path.join(根, "memory")):
        条 = 读()
        找到 = False
        for 事 in reversed(条):
            if 事.get("task") == a.task and not 事.get("结果"):
                事["结果"] = 结果
                事["收工"] = datetime.datetime.now().isoformat(timespec="seconds")
                事["说明"] = a.note or ""
                事["改法"] = (a.fix or "").strip()
                事["证据"] = (a.proof or "").strip()
                找到 = True
                break
        if 找到:
            tmp = 账本 + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for 事 in 条:
                    f.write(json.dumps(事, ensure_ascii=False) + "\n")
            os.replace(tmp, 账本)
    if not 找到:
        print("没找到这个任务还没结算的预判：", a.task)
        return 1
    print("结算：%s → %s" % (a.task, 结果))
    if 结果 == "未达":
        print("方案错在：%s" % a.fix.strip()[:80])
    # 只有没做成的才进差值账——判「达到」的不该来教下一次
    if 结果 in ("未达", "崩"):
        _记差值(a, 结果)
    return 0


def cmd_stats(a):
    条 = 读()
    已 = [x for x in 条 if x.get("结果")]
    if not 已:
        print("还没有结算过的（共 %d 条待结算）" % len(条))
        return 0
    计数 = {k: len([x for x in 已 if x["结果"] == k]) for k in 三类}

    # 2026-11-06 02:x 换口径。原来的主指标是「达到率」，实测它对判据真假完全不敏感：
    # 撤回 5 张判「达到」的卡，达到率纹丝不动（分子分母同步减 5，96% → 96%）。
    # 主指标改成三个：过审率（判据有没有经过别人）、撤回数（系统抓到多少假达到）、
    # 判不过原因分布（我下次写判据该防什么）。达到率降级成脚注。
    过审 = [x for x in 已 if str((x.get("accept_review") or {}).get("判")) == "过"]
    没过审 = [x for x in 已 if not x.get("accept_review")]
    撤回 = [x for x in 条 if x.get("撤回于")]
    print("判据健康度（这才是主指标，达到率在下面脚注）")
    print("  过审率        %d/%d = %.0f%%"
          % (len(过审), len(已), 100.0 * len(过审) / len(已)))
    print("  从没审过      %d 条" % len(没过审))
    print("  判不过被撤回  %d 张" % len(撤回))
    if 撤回:
        类 = {"未定值/没列名单": 0, "无基线": 0, "只测单向": 0, "自相矛盾": 0, "其他": 0}
        for x in 撤回:
            理 = str((x.get("accept_review") or {}).get("理由")
                     or x.get("原审理由") or "")
            if any(w in 理 for w in ("定不出", "没定值", "定不了", "没说", "哪三种")):
                类["未定值/没列名单"] += 1
            elif any(w in 理 for w in ("基线", "没比对")):
                类["无基线"] += 1
            elif any(w in 理 for w in ("只测", "单向", "没测")):
                类["只测单向"] += 1
            elif any(w in 理 for w in ("矛盾", "相反", "lose")):
                类["自相矛盾"] += 1
            else:
                类["其他"] += 1
        print("  判不过原因    " + " ／ ".join("%s %d" % (k, v) for k, v in 类.items() if v))
    print("")
    print("脚注：已结算 %d 条，达到 %d ／ 未达 %d ／ 崩 %d，达到率 %.0f%%"
          % (len(已), 计数["达到"], 计数["未达"], 计数["崩"],
             100.0 * 计数["达到"] / len(已)))
    print("      ——达到率对「判据真假」不敏感（撤回 5 张它没动过），只拿它看会被骗。")
    未达 = [x for x in 已 if x["结果"] == "未达"]
    if 未达:
        print("最近三条「未达」——这些最该复盘方案：")
        for 事 in 未达[-3:]:
            print("  - %s｜我猜会败在：%s｜实际：%s" % (
                (事.get("task") or "")[:20], (事.get("lose") or "")[:36], (事.get("说明") or "")[:40]))
    return 0


def 对账(条):
    """算准确率并给级别。stats 和 check 共用，别各算一套。"""
    已 = [x for x in 条 if x.get("结果")]
    待 = len(条) - len(已)
    计数 = {k: len([x for x in 已 if x["结果"] == k]) for k in 三类}
    共 = len(已)
    率 = (计数["达到"] / 共) if 共 else 0.0
    if 共 < 最少样本:
        级别 = "ok"
        原因 = "样本不足（%d/%d），先不判" % (共, 最少样本)
    elif 率 < 及格线:
        级别 = "warn"
        原因 = "达到率 %.0f%% < %.0f%%" % (率 * 100, 及格线 * 100)
    else:
        级别 = "ok"
        原因 = "达到率 %.0f%%（n=%d）" % (率 * 100, 共)
    未达 = [x for x in 已 if x["结果"] in ("未达", "崩")]
    改法 = [x.get("改法") for x in 未达 if (x.get("改法") or "").strip()]
    return {"level": 级别, "rate": round(率, 3), "settled": 共, "pending": 待,
            "counts": 计数, "reason": 原因, "fixes": 改法[-3:]}


def cmd_check(a):
    """对账：准确率够样本且低于线就报 warn。

    这是「持续学习」那条链上唯一一个会自己响的环节——
    stats 只是给人看的，check 的退出码才能接进 brain-daily 变成明天的念头。
    """
    账 = 对账(读())
    人话 = "预测对账：%s（已结算 %d，待结算 %d）" % (账["reason"], 账["settled"], 账["pending"])
    if 账["level"] == "warn" and 账["fixes"]:
        人话 += "｜最近该改的：" + "；".join((c or "")[:24] for c in 账["fixes"])
    if a.json:
        print(json.dumps(账, ensure_ascii=False))
    else:
        print(人话)
    return 1 if 账["level"] == "warn" else 0


def cmd_fix_audit(a):
    """复验：写了 fix 之后，同类活是不是又栽了。

    2026-11-05 23:5x 加。动机：`settle --fell-short --fix` 写完就「结案」了，
    但没人验这个 fix 管不管用——下次同一个坑还栽，账本上却已经干净。
    「改法写了但没生效」是最容易自欺的一种，因为它看起来像已经处理过了。

    口径：把每条「未达/崩 + 有改法」的卡按 task 词面聚成簇；同一簇里如果有
    **两次以上**未达，说明第一条的改法没管用（或者根本没执行）。
    """
    import re as _re

    条 = 读()
    修过 = [x for x in 条 if x.get("结果") in ("未达", "崩") and (x.get("改法") or "").strip()]
    if not 修过:
        print("还没有「判了未达/崩且写了改法」的记录")
        return 0

    # 按 task 的**前 4 个汉字**聚簇。
    # 第一版用「所有中文词排序拼接」当键，两版任务名（「备份脚本路径写死」vs
    # 「备份脚本路径又写死」）多一个字就分到两簇，等于永远抓不到重复。
    # 前 4 字是更粗但更稳的口径：同一件事的两次尝试，开头通常是同一句话。
    簇 = {}
    for x in 修过:
        词们 = _re.findall(r"[\u4e00-\u9fa5]{2,}", x.get("task") or "")
        键 = "".join(词们)[:4] or (x.get("task") or "")[:12]
        簇.setdefault(键, []).append(x)

    重复 = {k: v for k, v in 簇.items() if len(v) >= 2}

    print("判了未达/崩并写了改法：%d 条，聚成 %d 簇" % (len(修过), len(簇)))
    if not 重复:
        print("没有「同一簇栽两次」的记录——fix 暂时没有失效证据。")
        print("（注意：样本少的时候这条判据是瞎的，不是清白。）")
        return 0

    print()
    print("⚠ %d 簇栽了两次以上，说明先写的改法没管用：" % len(重复))
    for 键, 事们 in sorted(重复.items(), key=lambda kv: -len(kv[1])):
        print()
        print("  【%s】%d 次" % (键[:24], len(事们)))
        for i, x in enumerate(事们, 1):
            改 = (x.get("改法") or "").strip().replace("\n", " ")
            print("    %d. %s  %s" % (i, (x.get("t") or "")[:16], (x.get("task") or "")[:30]))
            print("       改法：%s" % 改[:100])
        print("   → 问一句：这条改法是真的没执行，还是执行了但方向错？")

    print()
    print("（这条判据只看词面重合，同义词/换说法会漏——当提示用，不当结论。）")
    return 1


def cmd_list(a):
    条 = 读()
    待 = [x for x in 条 if not x.get("结果")]
    print("共 %d 条，待结算 %d 条" % (len(条), len(待)))
    for 事 in 条[-8:]:
        print(" %s %s [%s]" % (事.get("t", "")[:16], 事.get("task", "")[:24], 事.get("结果") or "待"))
    return 0


def cmd_audit(a):
    """给人挑刺用的：把每条三问原文摊开，并把「像敷衍」的标出来。

    为什么要有这个：这套尺子是我自己刻的，三问我可能敷衍、结算可能挑好看的。
    防不了「我诚实」这件事，但能防「我随手填」——太短、空格、像套话的，这里会标出来。
    """
    条 = 读()
    if not 条:
        print("账本是空的")
        return 0
    套话 = ("更好", "优化", "改进", "提升", "更完善", "应该没问题", "应该可以", "没啥问题")
    可疑 = []
    for 事 in 条:
        for 字段, 名 in (("detect", "第三问"), ("how", "方案"), ("lose", "失败点")):
            值 = (事.get(字段) or "").strip()
            if not 值:
                可疑.append((事, 名 + " 空着"))
            elif len(值) < 8:
                可疑.append((事, 名 + " 太短：" + 值))
            elif 字段 == "detect" and any(w in 值 for w in 套话):
                可疑.append((事, 名 + " 像套话：" + 值))
        if 事.get("结果") == "未达" and not (事.get("改法") or "").strip():
            可疑.append((事, "判了未达却没写改法"))
        if 事.get("结果") == "达到" and not (事.get("证据") or "").strip():
            可疑.append((事, "判了达到却没写证据"))
    待 = [x for x in 条 if not x.get("结果")]
    print("共 %d 条，待结算 %d 条，可疑 %d 处" % (len(条), len(待), len(可疑)))
    print("-" * 50)
    for 事 in 条:
        print("[%s] %s → %s" % (事.get("t", "")[:16], 事.get("task", "(无 id)"), 事.get("结果") or "待"))
        print("   会成：", 事.get("win", ""))
        print("   会败：", 事.get("lose", ""))
        print("   怎么知道败：", 事.get("detect", ""))
        print("   打算怎么做：", 事.get("how", "") or "（空）")
        if 事.get("改法"):
            print("   下次改法：", 事.get("改法"))
        if 事.get("证据"):
            print("   达到的证据：", 事.get("证据"))
    if 可疑:
        print("-" * 50)
        print("像敷衍的地方：")
        for 事, 原因 in 可疑:
            print("  - %s：%s" % (事.get("task", "")[:20], 原因))
    return 0


def main():
    ap = argparse.ArgumentParser(description="开工三问 + 三分类结算")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add")
    p.add_argument("--task", default="")
    p.add_argument("--win", default="")
    p.add_argument("--lose", default="")
    p.add_argument("--detect", default="")
    p.add_argument("--accept", default="", help="成了我怎么知道（验收点）：那一刻能指给别人看的东西")
    p.add_argument("--how", default="", help="我打算怎么做（方案）。没写方案的复盘只能修个案")

    p = sub.add_parser("settle")
    p.add_argument("--task", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--reached", action="store_true", help="达到预期")
    g.add_argument("--fell-short", action="store_true", help="没达到预期，但没崩（必须带 --fix）")
    g.add_argument("--broke", action="store_true", help="崩了")
    p.add_argument("--note", default="")
    p.add_argument("--fix", default="", help="方案错在哪、下次换成什么（未达时必填）")
    p.add_argument("--proof", default="", help="凭什么算达到（测试数/日志行/谁确认的）（达到时必填）")

    sub.add_parser("stats")
    sub.add_parser("list")
    sub.add_parser("audit")
    sub.add_parser("fix-audit",
                   help="复验：写了 fix 之后，同类活是不是又栽了（同词面聚合）")
    p = sub.add_parser("check", help="对账：准确率低于线报 warn，退出码 1（给 brain-daily 用）")
    p.add_argument("--json", action="store_true", help="输出机器可读的 JSON")

    a = ap.parse_args()
    return {"add": cmd_add, "settle": cmd_settle, "stats": cmd_stats,
            "list": cmd_list, "audit": cmd_audit, "fix-audit": cmd_fix_audit,
            "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
