# -*- coding: utf-8 -*-
"""跨进程锁（2026-11-01 加）

背景：`memory/` 是**两个宿主进程**（web 3080 + 桌面版）和若干
计划任务脚本共用的目录。原来所有「读 → 改 → 整文件写回」都没加锁，两边同时结算
情绪、同时刷新当天日记、同时扣自主作业预算时，后写的那个把先写的整个盖掉（丢更新）。

协议（JS 侧 `plugins/dsh-xi-brain/lock.mjs` 必须完全一致，否则互不排斥）：

    锁目录  <记忆根>/.locks/<名字>.lock
    加锁    mkdir 成功 = 拿到；EEXIST = 别人拿着
    陈旧    锁目录 mtime 超过 容忍秒数 → 认为是死锁，删掉重抢

    mkdir 在 Windows/NTFS 上是原子的，所以不需要额外的锁文件内容。

拿不到锁时**不抛异常**：等满超时就直接往下跑。宁可偶尔丢一次更新，
也不能把插件回合或定时任务卡死。
"""
import json
import os
import shutil
import time

# tools/ 的上一级就是工作区根
默认记忆根 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'memory')

等待间隔 = 0.02          # 每次重试间隔
默认超时 = 5.0           # 等多久放弃
默认容忍 = 30.0          # 锁目录 mtime 超过这个秒数 = 死锁


def 锁目录(记忆根=None):
    return os.path.join(记忆根 or 默认记忆根, '.locks')


class 锁超时(Exception):
    pass


def 抢(名字, 记忆根=None, 超时=默认超时, 容忍=默认容忍):
    """返回 (是否拿到, 锁路径)。拿不到也要由调用方释放，所以两者都返回。"""
    路径 = os.path.join(锁目录(记忆根), 名字 + '.lock')
    截止 = time.time() + 超时
    while True:
        try:
            os.makedirs(os.path.dirname(路径), exist_ok=True)
        except Exception:
            pass
        try:
            os.mkdir(路径)                      # 原子
            return True, 路径
        except FileExistsError:
            pass
        except Exception:
            return False, 路径                  # 磁盘/权限问题：降级，不卡住
        # 陈旧锁：持有者崩了没删
        try:
            if time.time() - os.path.getmtime(路径) > 容忍:
                shutil.rmtree(路径, ignore_errors=True)
                continue
        except Exception:
            pass
        if time.time() >= 截止:
            return False, 路径
        time.sleep(等待间隔)


def 放(路径):
    shutil.rmtree(路径, ignore_errors=True)


class 锁(object):
    """with 锁('emotion'): ..."""

    def __init__(self, 名字, 记忆根=None, 超时=默认超时, 容忍=默认容忍):
        self.名字 = 名字
        self.记忆根 = 记忆根
        self.超时 = 超时
        self.容忍 = 容忍
        self.路径 = None
        self.拿到 = False

    def __enter__(self):
        self.拿到, self.路径 = 抢(self.名字, self.记忆根, self.超时, self.容忍)
        return self.拿到

    def __exit__(self, *a):
        放(self.路径)
        return False


def 读改(路径, 改, 默认=None, 锁名=None, 记忆根=None):
    """加锁读-改-写 JSON。改(数据) 的返回值就是要写回的内容；返回 None 表示不写。"""
    with 锁(锁名 or os.path.basename(路径).split('.')[0], 记忆根):
        try:
            with open(路径, encoding='utf-8') as f:
                数据 = json.load(f)
        except Exception:
            数据 = json.loads(json.dumps(默认)) if 默认 is not None else {}
        新 = 改(数据)
        if 新 is None:
            return 数据
        os.makedirs(os.path.dirname(路径) or '.', exist_ok=True)
        tmp = 路径 + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(新, f, ensure_ascii=False, indent=1)
        os.replace(tmp, 路径)
        return 新
