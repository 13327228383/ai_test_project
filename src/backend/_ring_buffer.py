# -*- coding: utf-8 -*-
"""
RingBuffer — 线程安全的环形缓冲区队列（从 app.py 拆分）。

队列满时自动丢弃最旧消息，永不阻塞。纯 Python 实现，无 Flask 依赖。
"""

import queue
import threading
from collections import deque


# ── 自定义环形队列（RingBuffer）：队列满时自动丢弃最旧消息，永不阻塞 ──
class RingBuffer:
    """
    线程安全的环形缓冲区队列。
    - put / put_nowait：写入。如果队列已满，自动丢弃最旧元素，再写入新元素（永不阻塞）。
    - get / get_nowait：读取，支持 timeout。
    - task_done / join：保持 Queue 兼容的空方法。
    """

    def __init__(self, maxsize: int = 1000):
        self._maxsize = int(maxsize)
        self._buffer: deque = deque(maxlen=self._maxsize)
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)

    def qsize(self) -> int:
        with self._lock:
            return len(self._buffer)

    def empty(self) -> bool:
        with self._lock:
            return len(self._buffer) == 0

    def full(self) -> bool:
        with self._lock:
            return len(self._buffer) >= self._maxsize

    def put(self, item, block=True, timeout=None):
        # RingBuffer 永远不阻塞：直接追加（deque 自动丢弃最旧）
        with self._lock:
            self._buffer.append(item)
            self._not_empty.notify()

    def put_nowait(self, item):
        self.put(item)

    def get(self, block=True, timeout=None):
        with self._lock:
            if not self._buffer:
                if not block:
                    raise queue.Empty
                if not self._not_empty.wait(timeout=timeout):
                    raise queue.Empty
            return self._buffer.popleft()

    def get_nowait(self, item=None):  # 参数兼容
        return self.get(block=False)

    def task_done(self):
        pass

    def join(self):
        pass
