from __future__ import annotations

import time
import warnings

import numpy as np
import pyaudiowpatch as pyaudio
from PySide6 import QtCore

from .constants import CAPTURE_BLOCK_SIZE, DEFAULT_SAMPLE_RATE
from .models import DeviceOption

try:
    from pycaw.pycaw import AudioUtilities
except ImportError:  # pragma: no cover
    AudioUtilities = None


class AudioWorker(QtCore.QObject):
    chunk_ready = QtCore.Signal(np.ndarray)
    state_changed = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._pa: pyaudio.PyAudio | None = None
        self._stream = None
        self._sample_rate = DEFAULT_SAMPLE_RATE
        self._block_size = CAPTURE_BLOCK_SIZE
        self._channels = 1
        self._device_index: int | None = None

    def configure(self, option: DeviceOption, block_size: int) -> None:
        self.stop()
        self._device_index = option.device_index
        self._sample_rate = option.sample_rate
        self._block_size = block_size
        self._channels = max(1, min(option.channels, 2))

    @QtCore.Slot()
    def start(self) -> None:
        if self._device_index is None:
            self.error.emit("请先选择一个可用声卡。")
            return

        try:
            self._pa = pyaudio.PyAudio()
            self._stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=self._channels,
                rate=self._sample_rate,
                input=True,
                input_device_index=self._device_index,
                frames_per_buffer=self._block_size,
                stream_callback=self._audio_callback,
                start=True,
            )
            self.state_changed.emit("采集中")
        except Exception as exc:  # pragma: no cover
            self._stream = None
            if self._pa is not None:
                self._pa.terminate()
                self._pa = None
            self.error.emit(f"打开音频流失败: {exc}")
            self.state_changed.emit("未启动")

    @QtCore.Slot()
    def stop(self) -> None:
        if not self._stream and not self._pa:
            self.state_changed.emit("未启动")
            return

        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        except Exception as exc:  # pragma: no cover
            self.error.emit(f"关闭音频流失败: {exc}")
        finally:
            self._stream = None
            if self._pa is not None:
                self._pa.terminate()
                self._pa = None
            self.state_changed.emit("已停止")

    def _audio_callback(self, in_data, frame_count, time_info, status):  # pragma: no cover
        if status:
            self.error.emit(f"音频状态: {status}")

        samples = np.frombuffer(in_data, dtype=np.float32)
        if self._channels > 1:
            try:
                samples = samples.reshape(-1, self._channels).mean(axis=1, dtype=np.float32)
            except ValueError:
                return (None, pyaudio.paContinue)
        self.chunk_ready.emit(samples.copy())
        return (None, pyaudio.paContinue)


class LoopbackVolumeMapper:
    def __init__(self) -> None:
        self._endpoint_volume = None
        self._endpoint_name = ""
        self._enabled = False
        self._cached_gain_db = 0.0
        self._last_refresh = 0.0
        self._refresh_interval = 0.25

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def endpoint_name(self) -> str:
        return self._endpoint_name

    def configure(self, option: DeviceOption) -> None:
        self._endpoint_volume = None
        self._endpoint_name = ""
        self._enabled = False
        self._cached_gain_db = 0.0
        self._last_refresh = 0.0

        if AudioUtilities is None or not option.is_loopback:
            return

        endpoint = self._match_render_endpoint(option.device_name)
        if endpoint is None or getattr(endpoint, "EndpointVolume", None) is None:
            return

        self._endpoint_volume = endpoint.EndpointVolume
        self._endpoint_name = getattr(endpoint, "FriendlyName", "") or option.device_name
        self._enabled = True
        self._refresh_gain_db(force=True)

    def gain_db(self) -> float:
        if not self._enabled or self._endpoint_volume is None:
            return 0.0
        self._refresh_gain_db(force=False)
        return self._cached_gain_db

    def _refresh_gain_db(self, force: bool) -> None:
        now = time.perf_counter()
        if not force and (now - self._last_refresh) < self._refresh_interval:
            return
        self._last_refresh = now
        try:
            scalar = float(self._endpoint_volume.GetMasterVolumeLevelScalar())
        except Exception:
            self._cached_gain_db = 0.0
            self._enabled = False
            return
        scalar = min(max(scalar, 1e-4), 1.0)
        self._cached_gain_db = -20.0 * float(np.log10(scalar))

    def _match_render_endpoint(self, device_name: str):
        target = self._normalize_device_name(device_name)
        if not target:
            return None

        exact_match = None
        fuzzy_match = None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            endpoints = list(AudioUtilities.GetAllDevices())
        for endpoint in endpoints:
            friendly_name = getattr(endpoint, "FriendlyName", None)
            if not friendly_name:
                continue
            candidate = self._normalize_device_name(friendly_name)
            if candidate == target:
                exact_match = endpoint
                break
            if candidate in target or target in candidate:
                fuzzy_match = fuzzy_match or endpoint
        return exact_match or fuzzy_match

    @staticmethod
    def _normalize_device_name(name: str) -> str:
        normalized = name.replace("[Loopback]", "").replace("（", "(").replace("）", ")").strip().lower()
        return " ".join(normalized.split())
