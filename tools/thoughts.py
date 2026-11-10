#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""念头自动产生 + 自动删除。

背景（11-03 用户）：念头原来是手写进 `memory/NEXT.md` 的 `**N. 标题**`，
我不写队列就一直是旧的（"抖音第二条"挂了三天）。所以改成从账本自动抽，
并且来源那条做完之后自动删掉——不用我记得去清。

来源（每条念头带 `来源键`，prune 时就按来源键回查它还在不在；来源不再产出，念头自动删）：
  job:<id>        memory/autonomy/jobs.json     状态 failed / pending
  ledger:<描述>    memory/self-iterate/ledger.json 状态 open
  staged:inbox    有备卡没派（真派必须过会话里的手）
  cand:<id>       memory/autonomy/candidates.json 状态 candidate
  cycle:<层>      分层压缩到期
  infer:<id>      可推的已回执已知
  collide:pending memory/collide/pairs.jsonl 还有「待看」的对，且没有在等的碰撞卡
  longterm        反复出现且没处理的差值（没有就不生成）

自动删除的判据（就是"实现了"）：
  job 变 done / 消失        → 删
  ledger 变 fixed / 消失    → 删
  candidate 变成 dropped/处理掉 / 消失 → 删

用法：
  python tools/thoughts.py scan     # 重建 memory/thoughts.json（顺带剔除过期条目）
  python tools/thoughts.py list     # 看现在有哪些念头、各自来源
  python tools/thoughts.py check    # 干跑，只报会删谁、会加谁，不写文件
  python tools/thoughts.py done <id> --note "干了什么"
  python tools/thoughts.py skip <id> --why "为什么不做"
                                    # 处理过（做或不做）就永久消失；来源状态变了才生成新的一条
"""
import argparse
import hashlib
import io
import json
import os
import sys
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from locklib import 锁

根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
记忆 = os.path.join(根, "memory")
账本 = os.environ.get("THOUGHTS_BOOK") or os.path.join(记忆, "thoughts.json")
# 回执账本：插件掷中后写「念头 + 这一轮干了什么」。环境变量给测试用。
回执账本 = os.environ.get("ROLL_LEDGER") or os.path.join(记忆, "autonomy", "roll-ledger.jsonl")

# 一条念头最多挂多久：超过就丢，防止某个来源坏了导致旧念头永远挂着
最长天数 = 30


def 读回执():
    """把回执账本按念头文本归集。返回 (桶, 坏行数, 总行数)。

    2026-11-03 加（自我迭代对账）：原来回执只记「这一轮动了没有」，念头那边完全看不到。
    结果是同一条念头可以无限次被念出来、念了也没人记，我今天就撞到同一句「web.out.log 轮转」
    被念了好几轮——它不会被降权，也不会被标「念了没动」。
    **坏行不再静默**（同一天第二次改）：之前 `except: continue` 一行脏数据就悄悄消失，
    而 roll-ledger 真出过「多条粘成一行」的 bug（7 条 0 换行）——那种情况下整本表都会解析失败，
    统计全是 0，看上去像「从来没念过」。所以坏行要计数、要报出来。
    """
    桶 = {}
    坏 = 0
    总 = 0
    原始 = []
    try:
        with io.open(回执账本, encoding="utf-8-sig") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                总 += 1
                try:
                    e = json.loads(raw)
                except json.JSONDecodeError:
                    坏 += 1       # 记账，不吞掉
                    continue
                原始.append(e)
    except OSError:
        return {}, 0, 0

    # 先按「掷中」时刻归并：一次掷中现在会落两行（pending + 结果），
    # 不归并就会把一次算成两次。旧行没有 pending，只有结果行，归并同样成立。
    合并 = {}
    for e in 原始:
        键 = str(e.get("掷中") or e.get("时刻") or "")
        话 = 短(e.get("念头") or "", 60)
        if not 键 or not 话:
            continue
        项 = 合并.setdefault(键, {"念头": 话, "结果": "", "上次": ""})
        项["念头"] = 话
        if e.get("结果") in ("动了", "只说话"):
            项["结果"] = e["结果"]
        t = str(e.get("时刻") or "")
        if t > 项["上次"]:
            项["上次"] = t

    for 项 in 合并.values():
        b = 桶.setdefault(项["念头"], {"次": 0, "动": 0, "只说": 0, "上次": ""})
        b["次"] += 1
        if 项["结果"] == "动了":
            b["动"] += 1
        elif 项["结果"] == "只说话":
            b["只说"] += 1
        if 项["上次"] > b["上次"]:
            b["上次"] = 项["上次"]
    return 桶, 坏, 总


def 回执警告(坏, 总):
    """坏行有就明说，没有就一个字都不打（避免每天刷一句没用的）。"""
    if not 坏:
        return ""
    return "回执账本 %d 行里 %d 行解析不了（统计可能偏少）" % (总, 坏)


# 长期任务冷却（2026-11-03 20:3x 加）：不设冷却时，「自我迭代」这条兜底活会把队列
# 永远填满——没别的活就叫它，它每次又都能找个小洞修掉，于是 20:24、20:26、20:31
# 连着被叫了三次、每次间隔两三分钟。那不是勤奋，是循环。
# 规则：过去 N 小时内因为同一条念头被掷中 ≥ 上限次，就先不加它。
长期冷却小时 = 6
长期冷却上限 = 2


def 近小时次数(文本, 小时):
    """过去 N 小时内这条念头被掷中过几次。

    回执里的「时刻」是 UTC ISO（带 Z），这里按同格式的 UTC 差值算，
    不碰本地时区——跨时区换算只会多一个出错的地方。
    """
    界 = datetime.now(timezone.utc).replace(tzinfo=None)
    from datetime import timedelta
    界 = 界 - timedelta(hours=小时)
    键 = 短(文本, 60)
    次 = 0
    try:
        with io.open(回执账本, encoding="utf-8-sig") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    e = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if 短(e.get("念头") or "", 60) != 键:
                    continue
                t = str(e.get("时刻") or "")
                try:
                    时 = datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    continue
                if 时 >= 界:
                    次 += 1
    except OSError:
        return 0
    return 次


def 标念叨(条, 回执):
    """给每一条念头补上「念过几次」。文本按前 60 字对齐，跟写入侧同一个口径。"""
    for x in 条:
        b = 回执.get(短(x.get("文本") or "", 60))
        if not b:
            x["念叨"] = {"次": 0, "动": 0, "只说": 0, "上次": ""}
            continue
        x["念叨"] = dict(b)
        # 念过 ≥3 次且一次没动 —— 不是没时间，是这条活根本没被执行
        x["催"] = b["次"] >= 3 and b["动"] == 0
    return 条


def 标念叨(条, 回执):
    """给每一条念头补上「念过几次」。文本按前 60 字对齐，跟写入侧同一个口径。"""
    for x in 条:
        b = 回执.get(短(x.get("文本") or "", 60))
        if not b:
            x["念叨"] = {"次": 0, "动": 0, "只说": 0, "上次": ""}
            continue
        x["念叨"] = dict(b)
        # 念过 ≥3 次且一次没动 —— 不是没时间，是这条活根本没被执行
        x["催"] = b["次"] >= 3 and b["动"] == 0
    return 条


def 念叨说明(x):
    b = x.get("念叨") or {}
    if not b.get("次"):
        return ""
    s = "念过 %d 次（动 %d / 只说话 %d）" % (b["次"], b.get("动", 0), b.get("只说", 0))
    if x.get("催"):
        s += " ★念了没动"
    return s


def 读jsonl(路径):
    """逐行读 jsonl。坏行跳过，不让一条脏数据把整本账读废。"""
    出 = []
    if not os.path.exists(路径):
        return 出
    try:
        for 行 in io.open(路径, encoding="utf-8", errors="replace"):
            行 = 行.strip()
            if not 行:
                continue
            try:
                出.append(json.loads(行))
            except Exception:
                continue
    except Exception:
        pass
    return 出


def 读json(路径, 默认):
    try:
        with io.open(路径, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return 默认


def 现在():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def 短(文本, 上限=60):
    t = " ".join(str(文本 or "").split())
    return t[:上限]


def 生成():
    """从三本账抽念头。返回 [{id, 文本, 来源, 来源键, 生成}]"""
    项 = []

    # 1) 自主作业队列
    队列 = 读json(os.path.join(记忆, "autonomy", "jobs.json"), {})
    for x in 队列.get("任务", []) or []:
        状态 = x.get("状态")
        if 状态 not in ("failed", "pending"):
            continue
        标题 = 短(x.get("标题") or x.get("目标") or "", 40)
        if not 标题:
            continue
        前缀 = "没做成：" if 状态 == "failed" else "排着队："
        项.append({
            "id": "job:%s" % x.get("id"),
            "目标": 短(x.get("目标") or "", 200),
            "判据": 短(x.get("判据") or "", 200),
            "文本": 前缀 + 标题,
            "来源": "job",
            "来源键": str(x.get("id")),
            "生成": 现在(),
        })

    # 2) 自迭代账本里还没改的毛病
    ld = 读json(os.path.join(记忆, "self-iterate", "ledger.json"), {})
    for x in (ld.get("条目") or []):
        if x.get("状态") != "open":
            continue
        描述 = 短(x.get("描述") or "", 40)
        if not 描述:
            continue
        项.append({
            "id": "ledger:%s" % hashlib.md5(描述.encode("utf-8")).hexdigest()[:10],
            "目标": "把这条毛病改掉，改完能说出改了什么、怎么验的：" + 描述,
            "判据": 短(x.get("判据") or "", 200),
            "文本": "还没改：" + 描述,
            "来源": "ledger",
            "来源键": 描述,
            "生成": 现在(),
        })

    # 3) 有备卡还没派——「自己调动自己」的最后一步
    # 定时任务能备卡（python 做得了），派单必须过会话里的手（平台边界）。
    # 以前念头队列不看备卡，于是掷骰叫醒我时队列是空的，叫醒了也没事干。
    # 加这条之后，任务卡直接就是「派这 N 张」，不用我每次自己去 staged 查。
    卡 = [x for x in 读jsonl(os.path.join(记忆, "inbox", "internet.jsonl"))
          if x.get("备卡") and not x.get("派单") and not x.get("回执")]
    if 卡:
        名们 = "、".join(短(x.get("标题") or x["id"], 16) for x in 卡[:3])
        项.append({
            "id": "staged:inbox",
            "目标": "把备好的 %d 张卡真派出去（dispatch → sent → 起 subagent）：%s"
                    % (len(卡), 名们),
            "判据": "跑 python tools/arch-listen.py staged 看剩余张数变少；"
                    "status 里「在办」涨、「已备卡」降",
            "文本": "有 %d 张备卡没派：%s" % (len(卡), 名们),
            "来源": "staged",
            "来源键": "staged:inbox",
            "生成": 现在(),
        })

    # 4) 观察候选里还没处理的
    cs = 读json(os.path.join(记忆, "autonomy", "candidates.json"), {})
    for x in (cs.get("候选") or []):
        if x.get("状态") != "candidate":
            continue
        # 「工作区」候选只是文件被改了，不是毛病——拿它当念头会天天念同一个文件
        if x.get("来源") == "工作区":
            continue
        标题 = 短(x.get("标题") or "", 40)
        if not 标题:
            continue
        项.append({
            "id": "cand:%s" % x.get("id"),
            "目标": 短(x.get("目标") or "", 200),
            "判据": 短(x.get("判据") or "", 200),
            "文本": "候选：" + 标题,
            "来源": "cand",
            "来源键": str(x.get("id")),
            "生成": 现在(),
        })

    # 4) 分层压缩到期（11-03 用户：「每一个1天要压缩，每一个3天要压缩，以此类推」）
    # 到期就派活，不靠人记得去看 status。判据写死：该层 md 存在且非空、且已经 mark 过。
    try:
        import subprocess
        p = subprocess.run([sys.executable, os.path.join(根, "memory", "scripts", "cycle.py"), "status", "--json"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        层们 = json.loads((p.stdout or "{}")).get("层级") or []
    except Exception:
        层们 = []
    for x in 层们:
        if not x.get("到期"):
            continue
        名 = x.get("层")
        if 名 == "daily":
            continue  # daily 是写日记，不是压缩，单独管
        项.append({
            "id": "cycle:%s" % 名,
            "文本": "该压 %s 了（%d 天一层，%s）" % (名, x.get("天") or 0, x.get("说明") or "到期"),
            "来源": "cycle",
            "来源键": 名,
            "目标": "把 %s 这一层压出来：从 summaries/ 和相邻层收，只留偏好/决策/未决/硬约束/路径版本" % 名,
            "判据": "memory/%s 存在、非空、字数不超过该层上限，然后 `python memory/scripts/cycle.py mark %s`" % (x.get("目标"), 名),
            "生成": 现在(),
        })

    # 5) 推断（11-04 用户「推理也要全自动」）。
    # 已回执的已知里还有没推过假说的，就生成一条念头——xi-brain 掷骰时会把它当活派给我，
    # 我醒来跑 `infer.py brief` 出任务卡、交给推断 AI、`add` 收回。
    # 这样推断不用人盯着，跟别的活一样由定时器叫醒。可推的清零后念头自己消失。
    try:
        _收 = []
        _inbox = os.path.join(根, "memory", "inbox", "internet.jsonl")
        if os.path.exists(_inbox):
            with open(_inbox, encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _收.append(json.loads(_line))
                    except Exception:
                        pass
        _推过 = set()
        for _x in _收:
            for _s in (_x.get("推断自") or []):
                _推过.add(_s)
        _可推 = [_x for _x in _收
                 if _x.get("回执") and (_x.get("深度") or 0) < 2 and _x["id"] not in _推过]
        if _可推:
            _x = _可推[0]
            项.append({
                "id": "infer:%s" % _x["id"],
                "文本": "从 %s 推一条假说（还积压 %d 条可推）" % (_x["id"], len(_可推)),
                "来源": "infer",
                "来源键": _x["id"],
                "目标": "python tools/infer.py brief -n 1 出任务卡；把卡交给推断 AI（或自己推）；"
                        "收回来的假说用 infer.py add 写进 inbox",
                "判据": "inbox 里多出一条 来源=推断 的记录（infer.py list 看得到），且它写了「什么算假」",
                "生成": 现在(),
            })
    except Exception:
        pass

    # 6) 碰撞队列：**已删**（11-05 23:5x）
    # 原来这里按「待看数」生成 collide:pending:N，和下面的 collide:batch 管同一件事。
    # 麻烦在于待看数永远是几百（sweep 每天补新的对），这条念头就永远成立、每半小时念一次，
    # 而一次 pick 只消化 3 对——念的频率远超消化速度，纯粹是噪音 + 烧钱。
    # 碰撞的节流交给 collide.py auto --hours 12（一批 5 对），念头只认批次文件。

    # 7) 碰撞批次：auto 已经备好一批，等我逐对判断
    # 11-05 20:0x 用户「12 小时碰一次，要全自动」。auto 负责到点挑好、写批次文件；
    # 这里负责让叫醒我的时候手上有「读哪份、判完怎么销」的具体动作，不是空喊「去碰一下」。
    try:
        _批 = []
        _碰 = os.path.join(记忆, "collide")
        if os.path.isdir(_碰):
            _批 = sorted(x for x in os.listdir(_碰) if x.startswith("batch-") and x.endswith(".md"))
        if _批:
            项.append({
                "id": "collide:batch:%s" % _批[0],
                "文本": "碰撞批次 %s 还没逐对判断" % _批[0],
                "来源": "collide",
                "来源键": "collide:batch",
                "目标": "读 memory/collide/%s：有话说 add --pair <对号> --title … --text …，"
                        "没话说 skip --pair <对号>；全部处理完 batch-done %s" % (_批[0], _批[0]),
                "判据": "memory/collide/%s 消失（batch-done 销掉），账本里这批对的状态不再是「待看」" % _批[0],
                "生成": 现在(),
            })
    except Exception as _e:
        print("碰撞批次读取失败：%s" % _e, file=sys.stderr)

    # 8) 方向候选：碰撞判断里有个概念反复出现
    # 11-05 20:2x 用户：「方向由你分辨，值不值得追也由你」。
    # 这里只把「反复」摆上来；算不算方向、追不追，由我被叫醒后自己判，判完 direct-add 留凭据。
    try:
        import subprocess
        _r = subprocess.run(
            [sys.executable, os.path.join(根, "tools", "collide.py"), "themes", "--json", "--min", "3"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        _够 = (json.loads((_r.stdout or "{}")).get("够格") or [])
        if _够:
            _z = _够[0]
            项.append({
                "id": "direction:%s" % _z["词"],
                "文本": "方向候选：「%s」在 %d 条判断里反复出现" % (_z["词"], _z["条数"]),
                "来源": "direction",
                "来源键": _z["词"],
                "目标": "跑 python tools/collide.py themes 看是哪几条判断；判断它们是不是真指向同一个洞。"
                        "是就 direct-add --title … --why … --wrong …，不是就 skip 掉这条念头说明理由",
                "判据": "要么 memory/collide/directions.jsonl 多一条写了「凭什么」和「判错标志」的方向，"
                        "要么 skip 掉并说清这几个反复为什么不算一个方向",
                "生成": 现在(),
            })
    except Exception as _e:
        print("方向候选读取失败：%s" % _e, file=sys.stderr)

    # 5) 长期任务：自我迭代（11-03 用户）。
    # 22:4x 改成「有差值才派」——原来靠「闲着就派活」驱动，而目标写的是
    # 「按五条标尺对一遍，哪条空补哪条」，那是一张永远能找到缺口的清单，不是差值。
    # 所以每轮都能修个小洞、每轮都"有产出"，永远停不下来。
    # 冷却只是限速，治不了自转；真正的启动条件必须是「某个差值反复出现且没处理」。
    #
    # 差值从 tools/signals.py gc 取（预测未达/检查失败/用户纠正/只说话）。
    # 没有够格的差值就**不生成这条念头**——队列空着就空着，不自转。
    # 22:4x 观察：冷却和差值驱动叠加会互相抵消——今天有真差值（三问那 4 条），
    # 却被「自我迭代 6 小时内已叫 2 次」挡住，一条都没派。冷却是给「闲着找活」
    # 准备的，差值驱动下它只会让真问题排队。现在按差值本身节流：同一簇 2 小时一次。
    差值们 = 取反复差值() or []
    # 挑哪一簇先处理（2026-11-06 01:x 改）：**一次仍只生成一条念头**，不刷屏；
    # 但挑的规则从「次数最多」改成「user 源优先 → 时间最近优先」。
    # 为什么：次数多的往往是旧账（11-03 那条 3 次），而他自己刚花时间指出来的
    # 才是此刻最该改的。按次数排等于让旧账永远压住新账。
    #
    # 2026-11-08 09:5x 用户「给它接上」：原来这一条念头只带一个笼统目标，
    # 来源键固定成 self-iterate —— 差值销了念头也不消，判据也不可执行，
    # 于是「检查失败 → 真的去改」这一环一直是装饰。现在：
    #   ① 每簇一张卡，最多两张（多了会刷屏）；
    #   ② 来源键 = signal:<簇键>，整簇销账后念头按来源键自动消失；
    #   ③ 判据写成一条能跑的命令（signals close + hardcheck --only 12）。
    用户们 = [d for d in 差值们 if 'user' in (d.get('来源') or [])]
    其他们 = [d for d in 差值们 if 'user' not in (d.get('来源') or [])]
    # 2026-11-08 10:0x 异体审抓的：原来按「最早」倒排（最近的在前）再取前两条，
    # 结果三簇超期时，最老那簇反而没出卡——红在输出里，手上没活，最该先改的被挤掉。
    # 10:2x 第二轮异体审又抓一次：只把两组各自排序、再用户组先占两个名额，
    # 于是「两个新鲜的 user 簇」永远挡住「五天前的 predict 簇」，老 bug 只是收窄了。
    # 现在合成一组按「最老」排，user 只用来并列时优先。
    用户们.sort(key=lambda d: (str(d.get('最早') or ''), d.get('来源') != ['user']))
    其他们.sort(key=lambda d: str(d.get('最早') or ''))
    差值们 = sorted(用户们 + 其他们,
                    key=lambda d: str(d.get('最早') or ''))[:2]
    for 差值 in 差值们:
        键 = str(差值.get('键') or '')
        if not 键:
            continue
        if 近小时次数(str(差值.get('代表') or '')[:20], 2) >= 1:
            continue
        项.append({
            "id": "signal:" + 键,
            "文本": "差值：%s" % str(差值.get("代表") or "")[:30],
            "来源": "signal",
            "来源键": "signal:" + 键,
            "目标": "这个差值已经出现 %d 次、最早 %s 天前，还没处理：%s。"
                    "先读它当时留下的产物或日志，找到它是在哪一步发生的，改掉能改的那部分"
                    % (差值.get("次数", 0), 差值.get("年龄天", 0), 差值.get("代表") or ""),
            # 2026-11-08 10:0x 异体审抓的：原来判据②写「hardcheck --only 12 转绿」，
            # 但那条闸是**全局**的——多簇超期时，销掉自己这簇它也还是红，
            # 于是这张卡的判据永远不可能满足。改成只看自己这个键。
            "判据": "①跑 `python tools/signals.py close %s --how \"改了什么\"` 把整簇销账；"
                    "②再跑 `python tools/signals.py gc --json`，输出里不能再有这个键（%s）。"
                    "两条都做到才算这个差值处理掉了；只说「我记住了」不算" % (键, 键),
            "生成": 现在(),
        })

    # 9) 主动性：我自己立的、没人要求做的事（11-10 加）
    # 其他来源全是「欠账」——没干完的活、检查挂了、他纠正了我。这一条是「想做」。
    # 分开计是因为节奏不一样：欠账要按时还，主动性要有空档才发芽。
    # 这里不用再设节流：账本本身四样必填、在世上限 3 条，堆不起来。
    for x in 读jsonl(os.path.join(记忆, "initiatives.jsonl")):
        if x.get("状态") != "在世":
            continue
        号 = x.get("id") or ""
        if not 号:
            continue
        项.append({
            "id": "want:" + 号,
            "文本": "想做的：%s" % 短(x.get("想要") or "", 30),
            "来源": "want",
            "来源键": "want:" + 号,
            "目标": "这是我自己立的，没人要求：%s。为什么：%s。怎么做：%s"
                    % (x.get("想要") or "", 短(x.get("为什么") or "", 80),
                       短(x.get("怎么做") or "", 80)),
            "判据": "照「算完成」做：%s。做成了跑 "
                    "`python tools/initiative.py done --id %s --做成了 \"...\"`；"
                    "不做了就跑 drop 并说清为什么"
                    % (x.get("算完成") or "", 号),
            "生成": 现在(),
        })

    # 去重：同一句话只留一条（不同来源可能撞标题）
    见过 = set()
    出 = []
    for x in 项:
        # 按前 16 字去重：完整文本常被截断成不同长度，同一件事会漏判
        键 = x["文本"][:16]
        if 键 in 见过:
            continue
        见过.add(键)
        出.append(x)

    # 处理过的永久不出现（做了/选择不做都算处理过）。
    完成 = 读完成()
    出 = [x for x in 出 if not 处理过(x, 完成)]
    return 出


def 取反复差值(最少=3):
    """调 signals.py gc --json，拿最近一簇够格的差值。

    11-03 22:4x：自我迭代的启动条件从「闲着」换成「有反复差值」。
    这一层是进程边界的唯一耦合点——thoughts 不去读 signals.jsonl 的结构，
    只认 gc 的输出，将来差值来源换掉也不用改这里。
    """
    import subprocess
    脚本 = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'signals.py')
    if not os.path.exists(脚本):
        return None
    try:
        r = subprocess.run([sys.executable, 脚本, 'gc', '--json', '--min', str(最少)],
                           capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=15)
        出 = (r.stdout or '').strip()
        if not 出:
            return None
        组 = json.loads(出)
        if not 组:
            return None
        return 组          # 全部簇，不再只取第一个——
        # 2026-11-06 01:x 修的：原来 `return 组[0]`，而 gc 按「次数」倒序排，
        # 于是 11-03 那条 3 次的旧账永远排第一、永远被消费，
        # 今晚每一条 1 次的真实纠正都轮不到——出口只开了一条缝。
    except Exception:
        return None


完成账本 = os.path.join(记忆, "thoughts-done.jsonl")


def 读完成():
    """处理过哪些念头（做了或明确不做）。返回 {id: 最近一次时间戳}。"""
    出 = {}
    try:
        with io.open(完成账本, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    x = json.loads(raw)
                except Exception:
                    continue
                i = x.get("id")
                if not i:
                    continue
                try:
                    t = float(x.get("ts") or 0)
                except Exception:
                    t = 0.0
                出[i] = max(出.get(i, 0.0), t)
    except FileNotFoundError:
        pass
    return 出


def 处理过(条目, 完成):
    """这条念头已经被处理过——做了，或者明确选择不做。

    11-05 用户：「念头是可执行可不执行的，执行完或者选择不执行后念头消失」。
    所以不是压一段时间再冒出来，是处理完就永久不出现。
    来源状态要是变了（比如碰撞待看数从 441 变成 436），那是**新的一条待决事项**，
    id 会跟着变，自然生成新念头——旧的那条不复活。
    """
    return 条目.get("id") in 完成


def 算urge(条数):
    """和插件里的口径一致：0.2×条数 + 0.25，封顶 1。"""
    return round(min(1.0, 0.2 * 条数 + (0.25 if 条数 else 0.0)), 3)


def 过期(条目):
    """条目自己太久没被来源刷新（来源还在但没变）——按生成时间兜底。"""
    线 = datetime.now().timestamp() - 最长天数 * 86400
    try:
        t = datetime.strptime(条目.get("生成") or "", "%Y-%m-%dT%H:%M:%S").timestamp()
    except Exception:
        return False
    return t < 线


def cmd_scan(参):
    新 = 生成()
    旧 = 读json(账本, {})
    旧条 = {x.get("id"): x for x in (旧.get("条目") or [])}
    新ids = set(x["id"] for x in 新)

    # 保留生成时间：来源没变就别每次都刷新「生成」，否则 30 天兜底永远不触发
    for x in 新:
        o = 旧条.get(x["id"])
        if o and o.get("文本") == x["文本"] and o.get("生成"):
            x["生成"] = o["生成"]

    删 = [i for i in 旧条 if i not in 新ids]
    加 = [i for i in 新ids if i not in 旧条]
    留 = [x for x in 新 if not 过期(x)]
    回执, 坏行, 总行 = 读回执()
    标念叨(留, 回执)

    数据 = {"更新时间": 现在(), "urge": 算urge(len(留)), "条目": 留}
    tmp = 账本 + ".tmp"
    with 锁("thoughts", 记忆):
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(数据, f, ensure_ascii=False, indent=1)
        os.replace(tmp, 账本)

    print("念头 %d 条（urge %.2f）" % (len(留), 数据["urge"]))
    for x in 留:
        说 = 念叨说明(x)
        print("  [%s] %s%s" % (x["id"], x["文本"], ("  ← " + 说) if 说 else ""))
    if 坏行:
        print(回执警告(坏行, 总行))
    if 加:
        print("新增：%s" % "、".join(加))
    if 删:
        print("自动删除（来源已完成或消失）：%s" % "、".join(删))
    return 0


def cmd_list(参):
    数据 = 读json(账本, {})
    回执, 坏行, 总行 = 读回执()
    条 = 标念叨(数据.get("条目") or [], 回执)
    if not 条:
        print("念头是空的：%s" % 账本)
        return 0
    print("更新时间 %s  urge %s  共 %d 条" % (数据.get("更新时间"), 数据.get("urge"), len(条)))
    for x in 条:
        说 = 念叨说明(x)
        print("  [%-22s] %s%s" % (x.get("id"), x.get("文本"), ("  ← " + 说) if 说 else ""))
    if 坏行:
        print(回执警告(坏行, 总行))
    return 0


def _记处理(参, 做了):
    """done 和 skip 共用：一条念头被处理了，永久不再出现。"""
    数据 = 读json(账本, {})
    条 = {x.get("id"): x for x in (数据.get("条目") or [])}
    if 参.id not in 条:
        print("队列里没有 %s——可能已经被处理过，或者来源已经把它删了" % 参.id)
        return 1
    记 = {
        "id": 参.id,
        "ts": datetime.now().timestamp(),
        "时间": 现在(),
        "处理": "做" if 做了 else "不做",
        "说明": (参.note if 做了 else 参.why) or "",
    }
    with 锁("thoughts-done", 记忆):
        with io.open(完成账本, "a", encoding="utf-8") as f:
            f.write(json.dumps(记, ensure_ascii=False) + "\n")
    print("已记%s：%s  %s" % ("完成" if 做了 else "放弃", 参.id, 记["说明"] or "（没写说明）"))
    print("这条从此不再出现。来源状态变了会生成新的一条。")
    return 0


def cmd_done(参):
    return _记处理(参, True)


def cmd_skip(参):
    return _记处理(参, False)


def cmd_check(参):
    新 = 生成()
    旧 = 读json(账本, {})
    旧ids = set(x.get("id") for x in (旧.get("条目") or []))
    新ids = set(x["id"] for x in 新)
    print("会新增：%s" % ("、".join(sorted(新ids - 旧ids)) or "无"))
    print("会删除：%s" % ("、".join(sorted(旧ids - 新ids)) or "无"))
    print("保持  ：%s" % ("、".join(sorted(旧ids & 新ids)) or "无"))
    return 0


def main():
    p = argparse.ArgumentParser(description="念头自动产生 + 自动删除")
    s = p.add_subparsers(dest="cmd")
    s.add_parser("scan", help="重建念头队列")
    s.add_parser("list", help="看现在的念头")
    s.add_parser("check", help="干跑，只报差异")
    _d = s.add_parser("done", help="这条念头做完了：记一笔，永久不再出现")
    _d.add_argument("id")
    _d.add_argument("--note", default="", help="干了什么，一句话")
    _k = s.add_parser("skip", help="这条念头决定不做：同样记一笔，永久不再出现")
    _k.add_argument("id")
    _k.add_argument("--why", default="", help="为什么不做，一句话")
    参 = p.parse_args()
    if 参.cmd == "scan":
        return cmd_scan(参)
    if 参.cmd == "list":
        return cmd_list(参)
    if 参.cmd == "check":
        return cmd_check(参)
    if 参.cmd == "done":
        return cmd_done(参)
    if 参.cmd == "skip":
        return cmd_skip(参)
    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
