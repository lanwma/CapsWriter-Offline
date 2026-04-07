# coding: utf-8
"""
快捷键任务模块

管理单个快捷键的录音任务状态
"""

import asyncio
import time
from threading import Event, Lock
from typing import TYPE_CHECKING, Optional

from config_client import ClientConfig as Config
from util import get_logger
from . import logger
from util.tools.my_status import Status

if TYPE_CHECKING:
    from util.client.shortcut.shortcut_config import Shortcut
    from util.client.state import ClientState
    from util.client.audio.recorder import AudioRecorder


timing_logger = get_logger('client_timing')


class ShortcutTask:
    """
    单个快捷键的录音任务

    跟踪每个快捷键独立的录音状态，防止互相干扰。
    """

    def __init__(self, shortcut: 'Shortcut', state: 'ClientState', recorder_class=None):
        """
        初始化快捷键任务

        Args:
            shortcut: 快捷键配置
            state: 客户端状态实例
            recorder_class: AudioRecorder 类（可选，用于延迟导入）
        """
        self.shortcut = shortcut
        self.state = state
        self._recorder_class = recorder_class

        # 任务状态
        self.task: Optional[asyncio.Future] = None
        self.recording_start_time: float = 0.0
        self.is_recording: bool = False

        # hold_mode 状态跟踪
        self.pressed: bool = False
        self.released: bool = True
        self.event: Event = Event()
        self._transition_lock = Lock()
        self.last_toggle_time: float = 0.0
        self.toggle_debounce: float = 0.2

        # 线程池（用于 countdown）
        self.pool = None

        # 录音状态动画
        self._status = Status('开始录音', spinner='point')

    def _get_recorder(self) -> 'AudioRecorder':
        """获取 AudioRecorder 实例"""
        if self._recorder_class is None:
            from util.client.audio.recorder import AudioRecorder
            self._recorder_class = AudioRecorder
        return self._recorder_class(self.state)

    def launch(self) -> None:
        """启动录音任务"""
        launch_start = time.perf_counter()
        with self._transition_lock:
            if self.is_recording:
                logger.debug(f"[{self.shortcut.key}] 忽略重复开始录音")
                return

            if self.state.stream_manager is None:
                logger.error(f"[{self.shortcut.key}] 音频流管理器未初始化，无法开始录音")
                return

            logger.info(f"[{self.shortcut.key}] 触发：开始录音")

            ensure_start = time.perf_counter()
            if not self.state.stream_manager.ensure_open():
                ensure_ms = (time.perf_counter() - ensure_start) * 1000
                logger.warning(f"[{self.shortcut.key}] ensure_open() 失败，耗时: {ensure_ms:.2f}ms")
                logger.warning(f"[{self.shortcut.key}] 麦克风未就绪，取消本次录音")
                timing_logger.warning(f"[{self.shortcut.key}] ensure_open() 失败，耗时: {ensure_ms:.2f}ms")
                return
            ensure_ms = (time.perf_counter() - ensure_start) * 1000
            logger.info(f"[{self.shortcut.key}] ensure_open() 同步耗时: {ensure_ms:.2f}ms")
            timing_logger.info(f"[{self.shortcut.key}] ensure_open() 同步耗时: {ensure_ms:.2f}ms")

            # 记录开始时间
            self.recording_start_time = time.time()
            self.last_toggle_time = self.recording_start_time
            self.is_recording = True

            # 将开始标志放入队列
            asyncio.run_coroutine_threadsafe(
                self.state.queue_in.put({'type': 'begin', 'time': self.recording_start_time, 'data': None}),
                self.state.loop
            )

            # 更新录音状态
            self.state.start_recording(self.recording_start_time)

            # 打印动画：正在录音
            self._status.start()

            # 启动识别任务
            recorder = self._get_recorder()
            self.task = asyncio.run_coroutine_threadsafe(
                recorder.record_and_send(),
                self.state.loop,
            )

            launch_ms = (time.perf_counter() - launch_start) * 1000
            logger.info(f"[{self.shortcut.key}] launch() 主线程耗时: {launch_ms:.2f}ms")
            timing_logger.info(f"[{self.shortcut.key}] launch() 主线程耗时: {launch_ms:.2f}ms")

    def cancel(self) -> None:
        """取消录音任务（时间过短）"""
        cancel_start = time.perf_counter()
        with self._transition_lock:
            logger.debug(f"[{self.shortcut.key}] 取消录音任务（时间过短）")

            self.is_recording = False
            self.last_toggle_time = time.time()
            self.state.stop_recording()
            self._status.stop()

            if self.task is not None:
                self.task.cancel()
                self.task = None

            self._release_mic_if_needed()
            cancel_ms = (time.perf_counter() - cancel_start) * 1000
            logger.info(f"[{self.shortcut.key}] cancel() 总耗时: {cancel_ms:.2f}ms")
            timing_logger.info(f"[{self.shortcut.key}] cancel() 总耗时: {cancel_ms:.2f}ms")

    def finish(self) -> None:
        """完成录音任务"""
        finish_start = time.perf_counter()
        with self._transition_lock:
            if not self.is_recording:
                logger.debug(f"[{self.shortcut.key}] 忽略重复结束录音")
                return

            logger.info(f"[{self.shortcut.key}] 释放：完成录音")

            self.is_recording = False
            self.last_toggle_time = time.time()
            self.state.stop_recording()
            self._status.stop()

            asyncio.run_coroutine_threadsafe(
                self.state.queue_in.put({
                    'type': 'finish',
                    'time': time.time(),
                    'data': None
                }),
                self.state.loop
            )

            # 执行 restore（可恢复按键 + 非阻塞模式）
            # 阻塞模式下按键不会发送到系统，状态不会改变，不需要恢复
            if self.shortcut.is_toggle_key() and not self.shortcut.suppress:
                self._restore_key()

            self._release_mic_if_needed()
            finish_ms = (time.perf_counter() - finish_start) * 1000
            logger.info(f"[{self.shortcut.key}] finish() 总耗时: {finish_ms:.2f}ms")
            timing_logger.info(f"[{self.shortcut.key}] finish() 总耗时: {finish_ms:.2f}ms")

    def should_debounce_toggle(self) -> bool:
        """检查是否应忽略过近的重复切换事件"""
        return (time.time() - self.last_toggle_time) < self.toggle_debounce

    def _restore_key(self) -> None:
        """恢复按键状态（防自捕获逻辑由 ShortcutManager 处理）"""
        # 通知管理器执行 restore
        # 防自捕获：管理器会设置 flag 再发送按键
        manager = self._manager_ref()
        if manager:
            logger.debug(f"[{self.shortcut.key}] 自动恢复按键状态 (suppress={self.shortcut.suppress})")
            manager.schedule_restore(self.shortcut.key)
        else:
            logger.warning(f"[{self.shortcut.key}] manager 引用丢失，无法 restore")

    def _release_mic_if_needed(self) -> None:
        """按需模式下在录音结束后释放麦克风占用。"""
        if Config.mic_open_on_demand and self.state.stream_manager is not None:
            self.state.stream_manager.close()
