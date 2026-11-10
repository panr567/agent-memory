#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把教训升级成硬检查。

背景（11-03 用户「是否实现知行合一」）：教训写在 facts 里只是「知」，
靠自觉执行就是「知行不一」——今天我自己就三次被「吞异常的 try」骗，
明知教训在库里还是犯。所以标准是：

  **一条教训能不能写成一条命令？能，就写成检查；不能的才留在文档里。**

本工具收的口径（13 条 feedback 里能机检的）：
  1. CORE 同步      ← 「核心卡由 facts 生成」
  2. 当天日记       ← 「会话收尾必须写当下」
  3. 写记忆加锁     ← 「记忆目录双宿主共写，读改写一律加锁」
  4. 日志体积       ← 「xi-brain-trigger.log 一天涨几 MB」
  5. 写死的会变数字 ← 「文档别写死会变的统计数」
  6. 语法           ← 「长脚本写完先跑一遍」

不能机检的（写在这里是为了别再假装能做到）：
  「他给方向前别抢跑」「别像交报告那样说话」「该顶就顶」
  「结论前先读产生它的脚本」「省 token 三条」——这些靠人看。

用法：
  python tools/hardcheck.py           # 全跑，有问题退出码 1
  python tools/hardcheck.py --only 3  # 只跑第几条
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
记忆 = os.path.join(根, "memory")
家目录 = r"D:\dsh\home"

结果 = []  # [(编号, 名称, ok, 说明)]

# 2026-11-06 抽公共模块：jsonl 读取统一走 tools/台账.py
from 台账 import 读行


def 记(号, 名, ok, 说明=''):
    结果.append((号, 名, ok, 说明))


def 读(p):
    try:
        return io.open(p, encoding='utf-8').read()
    except Exception:
        return ''


def 查1_core同步():
    """MEMORY.md 的 CORE 块 == min(强事实数, gen_core 的 limit)。

    22:0x 踩过的坑：gen_core 有 --limit 20 硬截断，检查原来只数 strength>=3，
    于是强事实涨到 22 条时检查天天报「20 vs 22」，而核心卡其实是对的。
    口径必须跟着生成器走：取 min(强事实, limit)。
    被 limit 挤掉的条数单独报出来——那是容量压力，不是错误。
    """
    mem = 读(os.path.join(记忆, 'MEMORY.md'))
    m = re.search(r'<!-- CORE:BEGIN[^>]*-->(.*?)<!-- CORE:END -->', mem, re.S)
    if not m:
        return 记(1, 'CORE 同步', False, 'MEMORY.md 里找不到 CORE 块')
    块内 = len(re.findall(r'^- \[', m.group(1), re.M))
    声明 = re.search(r'核心卡\*\*（(\d+) 条', mem)
    声明数 = int(声明.group(1)) if 声明 else None

    # 从 gen_core.py 读 limit，别写死——否则生成器改了这里又对不上
    limit = 20
    try:
        g = 读(os.path.join(根, 'memory', 'scripts', 'gen_core.py'))
        lm = re.search(r"'--limit',\s*type=int,\s*default=(\d+)", g)
        if lm:
            limit = int(lm.group(1))
    except Exception:
        pass

    fd = os.path.join(记忆, 'facts')
    强 = 0
    if os.path.isdir(fd):
        for n in os.listdir(fd):
            if not n.endswith('.md') or n == 'README.md':
                continue
            文 = 读(os.path.join(fd, n))
            sv = re.search(r'^strength:\s*(\d+)', 文, re.M)
            被取代 = bool(re.search(r'^superseded_by:', 文, re.M))
            if sv and int(sv.group(1)) >= 3 and not 被取代:
                强 += 1

    期望 = min(强, limit)
    问题 = []
    if 块内 != 期望:
        问题.append('CORE 里 %d 条，按 min(%d 强事实, limit %d) 应该是 %d 条' % (块内, 强, limit, 期望))
    if 声明数 is not None and 声明数 != 块内:
        问题.append('标题写 %d 条，实际 %d 条' % (声明数, 块内))
    挤掉 = 强 - limit if 强 > limit else 0
    说明 = '；'.join(问题) or '一致（%d 条%s）' % (限内 := 期望, '，另有 %d 条强事实被 limit 挤掉' % 挤掉 if 挤掉 else '')
    记(1, 'CORE 同步', not 问题, 说明)



def 查2_当天日记():
    """今天有过活动，就必须有当天日记。"""
    今天 = __import__('datetime').date.today().strftime('%Y-%m-%d')
    日记 = os.path.join(记忆, 'summaries', 今天 + '.md')
    if not os.path.exists(日记):
        return 记(2, '当天日记', False, '缺 %s.md' % 今天)
    t = 读(日记)
    记(2, '当天日记', len(t.strip()) > 200, '有（%d 字）' % len(t.strip()) if len(t.strip()) > 200 else '只有 %d 字，太短' % len(t.strip()))


def 查3_写记忆加锁():
    """memory/ 下的整份重写必须走 locklib；抓明显的裸写。"""
    坏 = []
    for 目录 in ('tools', 'memory/scripts'):
        d = os.path.join(根, 目录)
        if not os.path.isdir(d):
            continue
        for n in os.listdir(d):
            if not n.endswith('.py'):
                continue
            p = os.path.join(d, n)
            文 = 读(p)
            # 只看真正改 memory 文件的写入
            for m in re.finditer(r"open\(([^\n]*memory[^\n]*),\s*['\"]w", 文):
                片段 = 文[max(0, m.start() - 600):m.start()]
                if '锁' not in 片段 and 'locklib' not in 片段:
                    坏.append('%s：写 memory 文件但附近没有加锁' % n)
                    break
    记(3, '写记忆加锁', not 坏, '；'.join(坏[:3]) or '未发现裸写')


def 查4_日志体积():
    """单个日志超过 2MB 就报——轮转没生效或者又被写爆了。"""
    大 = []
    for 名 in ('xi-brain-trigger.log', 'web.out.log', 'brain-daily.log'):
        p = os.path.join(家目录, 名)
        if not os.path.exists(p):
            p = os.path.join(r'D:\dsh', 名)
        if os.path.exists(p):
            mb = os.path.getsize(p) / 1048576.0
            if mb > 2:
                大.append('%s %.1fMB' % (名, mb))
    记(4, '日志体积', not 大, '；'.join(大) or '都在 2MB 以内')


def 查5_写死数字():
    """content/*.md 里写死的统计数，必须带时间点或「会变」声明。"""
    坏 = []
    cd = os.path.join(根, 'content')
    if os.path.isdir(cd):
        样式 = re.compile(r'\d+\s*条(事实|日记|教训|念想)|\d{2}%\s*（样本')
        for n in os.listdir(cd):
            if not n.endswith('.md'):
                continue
            文 = 读(os.path.join(cd, n))
            for 行 in 文.split('\n'):
                # 先剥掉引号内内容：文中引用一个数字（举例、引用报错原文）不等于断言当前值。
                # 这一条是 11-03 21:2x 被同一篇文章误报四次之后加的。
                净行 = re.sub(r'「[^」]*」', '', 行)
                净行 = re.sub(r'“[^”]*”', '', 净行)
                净行 = re.sub(r'"[^"]*"', '', 净行)
                if not 样式.search(净行):
                    continue
                if any(k in 行 for k in ('写作时', '现查', '会变', '截至', '样本', '当时')):
                    continue
                坏.append('%s：%s' % (n, 行.strip()[:40]))
                break
    记(5, '写死的会变数字', not 坏, '；'.join(坏[:3]) or '都带了口径')


def 查6_语法():
    """最近 24 小时改过的 .py / .mjs 跑一遍语法检查。"""
    p = os.path.join(根, 'tools', 'syntax-check.py')
    if not os.path.exists(p):
        return 记(6, '语法', True, '没装 syntax-check.py，跳过')
    try:
        r = subprocess.run([sys.executable, p], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=60)
        出 = ((r.stdout or '') + (r.stderr or '')).strip().splitlines()
        记(6, '语法', r.returncode == 0, (出[0] if 出 else '无输出')[:80])
    except Exception as e:
        记(6, '语法', False, '跑不动：%s' % e)


def 查7_改代码建卡():
    """改了代码但账本里没有对应的卡，就报。

    11-03 22:2x 加。今天三次改代码都没在动手前跑三问（roll-ledger、检查口径、
    index.js 挂表），全是事后补卡。硬规矩写了「动手前先答三问」，但没人拦，
    靠自觉就等于没有。这条把它变成会响的线。

    口径：24 小时内的代码改动按 30 分钟聚成批，每一批必须落在某条 add 的
    ±30 分钟窗口内。一次 add 覆盖同时改的几个文件算合规。
    """
    import time
    import datetime

    # 2026-11-08 10:2x 上架前实测抓的：全新安装的第一次 hardcheck 会把
    # 「刚解压出来的 13 个文件」当成「改了代码没建卡」，红一条假的。
    # 账本都还没有的时候，没有「建卡」这回事——跟查8/查9 一样跳过。
    if not os.path.exists(os.path.join(根, 'memory', 'predictions.jsonl')):
        return 记(7, '改代码建卡', True, '还没有账本')

    代码目录 = ['plugins', os.path.join('tools'), os.path.join('memory', 'scripts')]
    现在 = time.time()
    改动 = []
    for d in 代码目录:
        全 = os.path.join(根, d)
        if not os.path.isdir(全):
            continue
        for 基, _, 文件们 in os.walk(全):
            if 'node_modules' in 基 or '__pycache__' in 基:
                continue
            for n in 文件们:
                if not n.endswith(('.js', '.py', '.mjs')):
                    continue
                fp = os.path.join(基, n)
                try:
                    mt = os.path.getmtime(fp)
                except Exception:
                    continue
                if 现在 - mt <= 24 * 3600:
                    改动.append(mt)
    if not 改动:
        return 记(7, '改代码建卡', True, '24 小时内没改过代码')

    改动.sort()
    # 聚成批：相邻 30 分钟算一批
    批 = []
    当前批 = [改动[0]]
    for t in 改动[1:]:
        if t - 当前批[-1] <= 30 * 60:
            当前批.append(t)
        else:
            批.append(当前批)
            当前批 = [t]
    批.append(当前批)

    # 读账本里的 add 时间
    卡时 = []
    账本 = os.path.join(记忆, 'autonomy', 'predictions.jsonl')
    if os.path.exists(账本):
        for d in 读行(账本):
            t = d.get('t') or ''
            if not t:
                continue
            try:
                秒 = datetime.datetime.fromisoformat(t).timestamp()
            except Exception:
                continue
            卡时.append(秒)

    漏 = []
    for i, b in enumerate(批, 1):
        起, 止 = min(b), max(b)
        有卡 = any(起 - 30 * 60 <= k <= 止 + 30 * 60 for k in 卡时)
        if not 有卡:
            段 = datetime.datetime.fromtimestamp(起).strftime('%H:%M')
            漏.append('第 %d 批（%s 附近 %d 个文件）没有对应的卡' % (i, 段, len(b)))

    记(7, '改代码建卡', not 漏, '；'.join(漏[:3]) or '%d 批改动都有卡' % len(批))


def 查8_验收点():
    """有卡但没写「成了怎么知道」，就报。

    11-05 23:4x 加。原来的三问只问「败了怎么知道」，不问「成了怎么知道」。
    后果实测过一次：装 dsh-memory 时加了 dependencies 就报「好了」，
    而真正决定加载的 bundles 没动，四轮才装上——回执记「动了 17 次工具」，账上满分。
    第四问 accept 逼我在动手前写下「成了长什么样」，写不出 = 这事没法验。

    口径：只看 2026-11-05T23:50 之后建的卡（第四问那一刻上线之前的老账
    没这个字段，追不了；用整天当界会天天红 29 条，检查就废了）。
    """
    账本 = os.path.join(记忆, 'autonomy', 'predictions.jsonl')
    if not os.path.exists(账本):
        return 记(8, '验收点', True, '还没有账本')
    起 = '2026-11-05T23:50'
    缺 = []
    总 = 0
    for d in 读行(账本):
        t = d.get('t') or ''
        if t < 起:
            continue
        总 += 1
        if not (d.get('accept') or '').strip():
            缺.append(d.get('task') or t)
    if not 总:
        return 记(8, '验收点', True, '新规矩之后还没建过卡')
    return 记(8, '验收点', not 缺,
             ('%d 张卡缺 accept：%s' % (len(缺), '、'.join(缺[:3]))) if 缺
             else '%d 张卡都写了验收点' % 总)


def 查9_未审结算():
    """最近 3 天判了「达到」的卡，必须都过异体审。

    2026-11-06 02:4x 加。用户「对，就这么补」。
    原来的闸有个洞：`settle --reached` 拦得住「没审就结算」，**拦不住我根本不送审**。
    今晚前半段是活证据——8 张卡判了「达到」，accept_review 字段全是空的，
    没有任何东西拦我，因为压根没往异体审那边送。

    所以得有双不依赖我自觉的眼睛：每轮 hardcheck 把「最近判了达到却没审过」的点名。
    红了就得处理，跟 101 一个路数。

    口径：只追 **2026-11-06T00:00（闸上线那天）之后**判「达到」的卡。
    为什么不用「最近 N 天」这种滚动窗口——我第一版写的就是最近 3 天，一跑报了
    「97 张达到，95 张没过审」：这本账总共才 8 天，3 天窗口几乎等于全部历史，
    **窗口太宽等于没有窗口**。跟 查8 用「第四问上线时刻」当分界是同一个道理：
    新规矩只追它上线之后的事，老账不追溯（硬追会天天红，检查就废了）。
    第二版把分界写成 02:20，又错了一次——那会儿实际才 01:09，**分界设在未来**，
    今晚的卡全被排除、检查假绿。**别凭感觉写时刻，先 Get-Date。**
    """
    import datetime as _dt
    账本 = os.environ.get('PREDICT_BOOK') or os.path.join(记忆, 'autonomy', 'predictions.jsonl')
    if not os.path.exists(账本):
        return 记(9, '未审结算', True, '还没有账本')
    早 = '2026-11-06T00:00'    # 闸上线那天起
    坏 = []
    总 = 0
    for d in 读行(账本):
        if d.get('结果') != '达到':
            continue
        收 = str(d.get('收工') or '')
        if not 收 or 收 < 早:      # 老账不追
            continue
        总 += 1
        审 = d.get('accept_review') or {}
        判 = str(审.get('判') or '') if isinstance(审, dict) else ''
        if 判 != '过':
            坏.append('%s（%s）' % (str(d.get('task') or '?')[:14], 判 or '没审'))
    if not 总:
        return 记(9, '未审结算', True, '闸上线之后还没判过达到的卡')
    return 记(9, '未审结算', not 坏,
              ('闸上线后 %d 张达到，%d 张没过审：%s'
               % (总, len(坏), '、'.join(坏[:3]))) if 坏
              else '闸上线后 %d 张达到，全都过审了' % 总)


def 阈值(名, 缺省值):
    """读外部阈值文件。没文件、没值、没出处 → 返回 (缺省值, 问题说明)。

    2026-11-07 加（用户「先把这三个问题修补」的第三条）：
    阈值原来是写在我代码里的数字——把自己放水只需要改一个数，
    检查就永远绿。把值和**出处**一起挪到 memory/system/thresholds.json，
    出处为空或写着「临时/待定」就报红：数字由谁定，必须留得下名字。
    """
    p = os.environ.get('THRESHOLDS_BOOK') or os.path.join(记忆, 'system', 'thresholds.json')
    try:
        # utf-8-sig：这台机器上 Set-Content/ConvertTo-Json 会带 BOM，
        # 带 BOM 的 json 用 utf-8 读会抛「Unexpected UTF-8 BOM」，检查就变成假红。
        全 = json.load(io.open(p, encoding='utf-8-sig'))
    except FileNotFoundError:
        return 缺省值, '没有 memory/system/thresholds.json，阈值无处可查'
    except Exception as e:
        return 缺省值, '阈值文件读不了：%s' % e
    项 = 全.get(名) or {}
    try:
        值 = int(项.get('值', 缺省值))
    except Exception:
        return 缺省值, '阈值「%s」的值不是整数' % 名
    来 = str(项.get('来源') or '').strip()
    if not 来:
        return 值, '阈值「%s」没有出处' % 名
    if '临时' in 来 or '待定' in 来 or '未定' in 来:
        return 值, '阈值「%s」出处是临时的：%s' % (名, 来)
    # 2026-11-08 10:0x 异体审抓的：只拒三个词太松——写一句「随便」也能过。
    # 出处必须同时有**日期**和**人名**，否则等于没署名。
    import re as _re
    if not _re.search(r'\d{4}-\d{2}-\d{2}', 来):
        return 值, '阈值「%s」出处没写日期：%s' % (名, 来)
    if not ('用户' in 来):
        # 2026-11-08 10:2x 异体审抓的：原来只要出现「汐」也算署名，
        # 于是我自己写一条「汐 2026-11-08 定」就能过——而这条检查的失败文案
        # 写的是「阈值不许由我自己拍」。闸和文案对不上就是假闸。
        # 现在只认「用户」：他签的数才算数，我签的要他补一句。
        return 值, '阈值「%s」不是用户定的（出处：%s）' % (名, 来)
    # 10:3x 再收紧一次：出处里"提到用户"不等于"用户定的"。
    # 我原来的写法是「汐 2026-11-08 定，依据用户原话」——提到他就过了，等于没闸。
    # 现在要求出现「用户…定」这种署名句式，或者整条出处以用户开头。
    if not (_re.search(r'用户[^，。；]{0,8}定', 来) or 来.startswith('用户')):
        return 值, '阈值「%s」出处里没有用户署名（只是提到了他）：%s' % (名, 来)
    return 值, ''


def 查10_实时层当日():
    """今天有活干，live.jsonl 当天就得有一条。

    2026-11-07 加。跨窗口记忆上线当天，账上只有 1 条，而且是它自己的说明书——
    机制通了但没人往里写。写不写全看自觉，而靠自觉的环一定会空。

    口径（免得天天红）：
      - 只有当天日记已经写了实质内容（> 2000 字节）才要求 live 有条目；
        凌晨脚本生成的空壳日记不算「有活动」。
      - 不过 6 点看前一天：brain-daily 04:10 跑的时候，「今天」还没发生什么。
    """
    import datetime as _dt

    门槛, 出处问题 = 阈值('查10_日记字节', 800)
    if 出处问题:
        return 记(10, '实时层当日', False, 出处问题 + '（阈值不许由我自己拍）')
    账 = os.environ.get('LIVE_BOOK') or os.path.join(记忆, 'live.jsonl')
    现在 = _dt.datetime.now()
    日 = 现在.strftime('%Y-%m-%d')
    if 现在.hour < 6:
        日 = (现在 - _dt.timedelta(days=1)).strftime('%Y-%m-%d')

    日记 = os.path.join(记忆, 'summaries', 日 + '.md')
    大 = os.path.getsize(日记) if os.path.exists(日记) else 0
    # 不够门槛只说明「日记写得少」，不等于当天没活动：如果 live 里已经有当天条目，
    # 仍然要按内容判，否则「日记空着 + 随手写一条凑数」就能整关跳过（异体审 ef5f72aa 指出）。
    活动日 = 大 > 门槛

    有 = 0
    自动 = 0
    if os.path.exists(账):
        for d in 读行(账):
            if not str(d.get('ts') or '').startswith(日):
                continue
            # 机制自己写的条目不算数（2026-11-07 异体审抓出来的）：
            # live-watch 每 10 分钟自动写「结构变更」，如果它也满足这条检查，
            # 就是机制在给自己发合格证——我整天不写，检查照样绿。
            if 'live-watch' in str(d.get('谁') or ''):
                自动 += 1
                continue
            有 += 1
    少字, 出处2 = 阈值('查10_条目最少字数', 24)
    if 出处2:
        return 记(10, '实时层当日', False, 出处2 + '（阈值不许由我自己拍）')

    像话 = 0
    凑数 = []
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from audit import 套话 as _套话
    except Exception:
        _套话 = []
    if os.path.exists(账):
        for d in 读行(账):
            if not str(d.get('ts') or '').startswith(日):
                continue
            if 'live-watch' in str(d.get('谁') or ''):
                continue
            文 = str(d.get('原文') or '').strip()
            依据 = str(d.get('依据') or '').strip()
            if len(文) < 少字:
                凑数.append('太短:%s' % 文[:12])
            # 2026-11-07 异体审 6868cbc1 提的洞：原来是「命中套话 **且** 没依据」才挂，
            # 于是「值得关注」+ 随手填个依据就过。套话一律挂——带依据也不行，
            # 因为依据填「x」「开会」这种压根不校验内容。
            elif any(c in 文 for c in _套话):
                凑数.append('套话:%s' % 文[:12])
            elif not 依据:
                凑数.append('没依据:%s' % 文[:12])
            else:
                像话 += 1

    说明 = '%s 日记 %d 字节，live 当天 %d 条（自动 %d 条不算），像话 %d 条' % (日, 大, 有, 自动, 像话)
    if 有 and not 像话:
        return 记(10, '实时层当日', False, 说明 + '；都像凑数：' + '、'.join(凑数[:3]))
    if not 有:
        return 记(10, '实时层当日', 活动日 is False, 说明 + ('；不算活动日，也没条目' if not 活动日 else ''))
    return 记(10, '实时层当日', True, 说明)


def 查11_常驻余量():
    """AGENTS.md 离 65536 上限还有多少；少于 2KB 或尾部自我库没了就红。

    2026-11-07 加。实测 65084/65536，只剩 452 字节。
    宿主超限是从尾部切的，而自我库插在最后——切掉它不会有任何报错，
    历史上已经发生过一次（那次靠我事后数行才发现）。
    """
    上限 = 65536
    保底, 出处问题 = 阈值('查11_余量字节', 2048)
    if 出处问题:
        return 记(11, '常驻余量', False, 出处问题 + '（阈值不许由我自己拍）')
    p = os.environ.get('AGENTS_BOOK') or os.path.join(根, 'AGENTS.md')
    if not os.path.exists(p):
        return 记(11, '常驻余量', False, 'AGENTS.md 不存在：%s' % p)
    大 = os.path.getsize(p)
    余 = 上限 - 大
    文 = 读(p)
    # 通用安装出来的 AGENTS.md 没有「自我库」这一块（那是本机自己的东西），
    # 所以只在自我库存在时才判尾部；否则这条永远红，等于把用户挡在门外。异体审 eb3415d7 抓的。
    自我库 = os.path.join(记忆, 'self')
    有自我库 = os.path.isdir(自我库) and bool(os.listdir(自我库))
    尾好 = ('我的兴趣' in 文) if 有自我库 else True
    说明 = '%d/%d 字节，余 %d' % (大, 上限, 余)
    if not 有自我库:
        说明 += '；没装自我库，不判尾部'
    elif not 尾好:
        说明 += '；尾部自我库区块不见了（被截断的征兆）'
    elif 余 < 保底:
        说明 += '；余量低于 %d 字节，该瘦身了' % 保底
    return 记(11, '常驻余量', 余 >= 保底 and 尾好, 说明)


def 查12_差值闭环():
    """反复出现的差值挂太久还没销账 → 红。

    2026-11-08 09:5x 用户「给它接上」。
    这条补的是整条链最后一环：检查失败能落成差值、差值能变成念头，
    但**没有任何东西会让「一直不改」变成红的**——账越记越厚，行为不变。
    阈值：差值最早一条挂了多少天算太久（memory/system/thresholds.json）。
    销账命令写在说明里：python tools/signals.py close <键> --how "改了什么"。
    """
    天, 出处问题 = 阈值('查12_差值闭环天数', 3)
    if 出处问题:
        return 记(12, '差值闭环', False, 出处问题 + '（阈值不许由我自己拍）')
    脚本 = os.path.join(根, 'tools', 'signals.py')
    if not os.path.exists(脚本):
        return 记(12, '差值闭环', True, '没有 signals.py，跳过')
    import subprocess
    import sys as _sys
    try:
        r = subprocess.run([_sys.executable, 脚本, 'gc', '--json'],
                           capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=15)
        簇们 = json.loads((r.stdout or '').strip() or '[]')
    except Exception as e:
        return 记(12, '差值闭环', False, '读差值账失败：%s' % e)
    超期 = [c for c in 簇们 if float(c.get('年龄天') or 0) >= 天]
    if not 簇们:
        return 记(12, '差值闭环', True, '没有够格的差值簇')
    if not 超期:
        最久 = max(float(c.get('年龄天') or 0) for c in 簇们)
        return 记(12, '差值闭环', True, '%d 簇未销，最久的 %.1f 天（阈值 %s 天）'
                  % (len(簇们), 最久, 天))
    名 = '；'.join('%s（%.1f 天，%d 次，键 %s）'
                  % (str(c.get('代表') or '')[:22], float(c.get('年龄天') or 0), int(c.get('次数') or 0), c.get('键'))
                  for c in 超期[:3])
    return 记(12, '差值闭环', False,
              '%d 簇超过 %s 天没销账：%s。销账：python tools/signals.py close <键> --how "改了什么"'
              % (len(超期), 天, 名))


def 动态检查们():
    """读 memory/rules/checks.jsonl，把数据化的检查变成函数。

    2026-11-06 00:4x 用户「必须实现自己长出新的动作」。
    之前 8 项检查**硬编码在这个文件里**，加一条就要动源码——那我永远是「长出动作」的唯一入口。
    改成数据化之后：追加一行 = 多一条检查，不用碰 .py。

    支持的三种类型（刻意少，够用且不会变成写代码）：
      文件存在       目标 = 路径
      文本匹配       目标 = 文件，正则 + 期望(有/无)
      目录文本匹配   目标 = 目录，文件匹配 = glob，每个文件都要满足 正则 + 期望

    故意不支持「执行任意代码」——那样检查文件就变成代码执行入口，
    一个写错的规则能把整套东西搞崩。
    """
    import glob as _glob

    p = os.path.join(记忆, 'rules', 'checks.jsonl')
    if not os.path.exists(p):
        return []
    规则们 = []
    for 条 in 读行(p):
        if not 条.get('名'):
            continue
        规则们.append(条)

    def 造(条, 序号):
        def _查():
            名 = 条.get('名', '?')
            类 = 条.get('类型', '')
            目标 = os.path.join(根, 条.get('目标') or '')
            正则 = 条.get('正则') or ''
            期望 = 条.get('期望') or '有'

            if 类 == '文件存在':
                ok = os.path.exists(目标)
                return 记(序号, 名[:14], ok, '' if ok else '不存在：%s' % 条.get('目标'))

            if 类 == '文本匹配':
                文 = 读(目标)
                if not 文:
                    return 记(序号, 名[:14], False, '读不到 %s' % 条.get('目标'))
                m = re.search(正则, 文, re.M)      # re.M 是必须的：front-matter 不在行首
                ok = (m is not None) if 期望 == '有' else (m is None)
                if ok:
                    return 记(序号, 名[:14], True, '')
                return 记(序号, 名[:14], False,
                          ('该有但没匹配到 %s' % 正则) if 期望 == '有'
                          else ('不该有但匹配到了：%s' % (m.group(0)[:40] if m else '')))

            if 类 == '目录文本匹配':
                文件们 = sorted(_glob.glob(os.path.join(目标, 条.get('文件匹配') or '*')))
                # 可选字段 排除匹配（2026-11-06 加）：命中的文件整份跳过。
                # 为什么要它：101 会把 superseded 的死文件也当活跃文件报，每天 2 条假红——
                # **检查一直红等于检查失效**，红久了就学会无视它。
                # 正则必须锚定 front-matter 字段（^superseded_by:）。只写 superseded 这个词会误伤：
                # 正文里提到这个词的活跃文件会被一起跳过，变成假绿——假绿比红更糟。
                排除 = 条.get('排除匹配') or ''
                坏 = []
                跳过 = 0
                for f in 文件们:
                    文 = 读(f)
                    if 排除 and re.search(排除, 文, re.M):
                        跳过 += 1
                        continue
                    m = re.search(正则, 文, re.M)
                    过 = (m is not None) if 期望 == '有' else (m is None)
                    if not 过:
                        坏.append(os.path.basename(f))
                ok = not 坏
                尾巴 = ('（按排除匹配跳过 %d 个）' % 跳过) if 跳过 else ''
                if 坏:
                    说 = '%d 个文件不满足：%s%s' % (len(坏), '、'.join(坏[:3]), 尾巴)
                else:
                    说 = '%d 个文件都满足%s' % (len(文件们) - 跳过, 尾巴)
                return 记(序号, 名[:14], ok, 说)

            return 记(序号, 名[:14], False, '不认识的类型：%s' % 类)
        _查.__doc__ = 条.get('名', '')
        return _查

    return [(条, 造(条, 101 + k)) for k, 条 in enumerate(规则们)]


def 记差值():
    """把挂了的检查写进差值账（2026-11-06 加）。

    以前四条来源（user/predict/check/roll）只写在 signals.py 的注释里：
    唯一入口是手动 `signals.py add`，hardcheck 跑完什么也不留——
    所以 rules.py propose 永远报「没有新东西可长」。不是没差值，是没米下锅。

    去重按「检查名」：同一个检查挂着就只留一条，不再重复灌。
    挂了本身就是差值，不等它挂三次——等三次就是白挨三次。
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import signals
        旧 = signals.读()
        # 2026-11-08 10:0x 异体审抓的：查重原来连**已销账**的行也算在内，
        # 于是「挂了→修好销账→又挂」第二次不再进账，同一件事一辈子只红一次。
        # 现在只把还开着的算在账上：销过账的 id 一律排除。
        死的 = signals.已处理(旧)
        在账 = {str(x.get('什么') or '') for x in 旧
                if not x.get('处理') and x.get('来源') == 'check'
                and x.get('id') not in 死的}
        新增 = 0
        for n, 名, ok, 说 in 结果:
            if ok:
                continue
            # 2026-11-08 09:5x 加：第 12 条（差值闭环）挂了，**不往同一本账里写差值**。
            # 它说的就是「账上有簇没销」这件事本身；写进去等于账本自己给自己生新账，
            # 销掉真问题之后 gc 还是一条非空，闭环永远合不上。
            # 同一条原则已经踩过：机制不能给自己的自动条目发合格证。
            if n == 12:
                continue
            什么 = '硬检查挂了：%s %s' % (n, 名)
            if 什么 in 在账:
                continue
            signals.加({
                'id': 'sg-chk-' + os.urandom(4).hex(),
                '时间': signals.现在(),
                '来源': 'check',
                '什么': 什么,
                '修法': str(说)[:100],
                '处理': False,
            })
            新增 += 1
        if 新增:
            print('（%d 条新差值进了 signals.jsonl，来源=check）' % 新增)

        # 对称的一半（2026-11-06 加）：检查转绿就把对应的未处理差值销掉。
        # 不销就是僵尸差值——账上挂着「101 挂了」，实际早绿了，
        # 然后每天提醒一个不存在的问题。提醒久了就没人信了，跟一直红着一样坏。
        已销 = {y.get('目标') for y in 旧 if y.get('来源') == '处理'}
        绿的 = {'硬检查挂了：%s %s' % (n, 名) for n, 名, ok, 说 in 结果 if ok}
        销了 = 0
        for x in 旧:
            if x.get('处理') or x.get('来源') != 'check':
                continue
            if x.get('id') in 已销:
                continue
            if str(x.get('什么') or '') not in 绿的:
                continue
            signals.加({
                'id': str(x.get('id')) + '-done',
                '时间': signals.现在(),
                '来源': '处理',
                '什么': '处理：这条硬检查已经转绿',
                '处理': False,
                '目标': x.get('id'),
            })
            销了 += 1
        if 销了:
            print('（%d 条差值已销账：对应的检查转绿了）' % 销了)
    except Exception as e:
        # 不吞：写不进去要看得见，否则又是「没输出当成没发生」
        print('（差值账没写进去：%s）' % e)


def main():
    ap = argparse.ArgumentParser(description='把教训升级成硬检查')
    ap.add_argument('--only', type=int, default=0, help='只跑第几条')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    检查 = [查1_core同步, 查2_当天日记, 查3_写记忆加锁, 查4_日志体积, 查5_写死数字, 查6_语法, 查7_改代码建卡, 查8_验收点, 查9_未审结算, 查10_实时层当日, 查11_常驻余量, 查12_差值闭环]
    for i, f in enumerate(检查, 1):
        if a.only and a.only != i:
            continue
        try:
            f()
        except Exception as e:
            记(i, f.__doc__.split('\n')[0].strip()[:12] if f.__doc__ else str(i), False, '检查本身出错：%s' % e)

    # 数据化检查（2026-11-06 加）：memory/rules/checks.jsonl 里每行一条。
    # 编号从 101 起，跟硬编码的 1-8 分开，看得出它是「长出来的」不是「写死的」。
    for k, (条, f) in enumerate(动态检查们(), 101):
        if a.only and a.only != k:
            continue
        try:
            f()
        except Exception as e:
            记(k, (条.get('名') or '?')[:14], False, '检查本身出错：%s' % e)

    if a.json:
        print(json.dumps([{'号': n, '名': 名, 'ok': ok, '说明': 说} for n, 名, ok, 说 in 结果],
                         ensure_ascii=False))
    else:
        for n, 名, ok, 说 in 结果:
            # %s 不是 %d：动态检查的编号是 '规则' 这类字符串（2026-11-06 加数据化检查后）
            print('%s %s. %-12s %s' % ('OK  ' if ok else 'FAIL', n, 名, 说))
        坏 = [x for x in 结果 if not x[2]]
        print('---')
        print('%d 项，%d 项有问题' % (len(结果), len(坏)))
        if 坏:
            print('不能机检的（靠人看）：他给方向前别抢跑 / 别像交报告 / 该顶就顶 / 结论前先读产生它的脚本 / 省 token')
    记差值()
    return 1 if any(not ok for _, _, ok, _ in 结果) else 0


if __name__ == '__main__':
    sys.exit(main())
