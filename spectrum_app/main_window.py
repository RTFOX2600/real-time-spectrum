from __future__ import annotations

import json
import queue
import time

import numpy as np
import pyaudiowpatch as pyaudio
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from .audio import AudioWorker, LoopbackVolumeMapper
from .constants import (
    APP_NAME,
    APP_ORG,
    BASE_FFT_SIZE,
    CAPTURE_BLOCK_SIZE,
    CEILING_DB,
    DEFAULT_DISPLAY_FPS,
    DEFAULT_FREQUENCY_LIMIT,
    DEFAULT_FREQUENCY_MIN,
    DEFAULT_FREQUENCY_RESOLUTION,
    DEFAULT_FREQUENCY_ZOOM_POWER,
    DEFAULT_HISTORY_SECONDS,
    DEFAULT_REALTIME_FOLLOW,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_SYSTEM_VOLUME_MAPPING_ENABLED,
    DEFAULT_TIME_RESOLUTION,
    DEFAULT_HISTOGRAM_STATE,
    DISPLAY_MAX_DB,
    DISPLAY_MIN_DB,
    FLOOR_DB,
    FPS_OPTIONS,
    MAX_FREQUENCY_LIMIT,
    MAX_FREQUENCY_ZOOM_POWER,
    MAX_TIME_SPAN,
    LEGACY_APP_ORG,
    MIN_FREQUENCY_LIMIT,
    MIN_TIME_SPAN,
    RESOLUTION_OPTIONS,
)
from .models import DeviceOption
from .widgets import FrequencyAxisItem, InteractiveHistogramLUTItem, SettingsDialog, SpectrogramViewBox
from .widgets import TimeAxisItem


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("实时频谱图")
        self.resize(1400, 860)
        self.setMinimumWidth(760)

        self.settings = QtCore.QSettings(APP_ORG, APP_NAME)
        self._loading_settings = True
        self._migrate_legacy_settings()

        self.audio_queue: queue.SimpleQueue[np.ndarray] = queue.SimpleQueue()
        self.devices: list[DeviceOption] = []
        self.audio_worker = AudioWorker()
        self.loopback_volume_mapper = LoopbackVolumeMapper()

        self.sample_rate = DEFAULT_SAMPLE_RATE
        self.max_frequency = self.sample_rate / 2
        self.time_span = DEFAULT_HISTORY_SECONDS
        self.display_fps = DEFAULT_DISPLAY_FPS
        self.time_resolution = DEFAULT_TIME_RESOLUTION
        self.frequency_resolution = DEFAULT_FREQUENCY_RESOLUTION
        self.capture_block_size = CAPTURE_BLOCK_SIZE
        self.hop_size = BASE_FFT_SIZE
        self.fft_size = BASE_FFT_SIZE
        self.frequency_min = DEFAULT_FREQUENCY_MIN
        self.frequency_max = DEFAULT_FREQUENCY_LIMIT
        self.visible_frequency_min = DEFAULT_FREQUENCY_MIN
        self.visible_frequency_max = DEFAULT_FREQUENCY_LIMIT
        self.frequency_zoom_power = DEFAULT_FREQUENCY_ZOOM_POWER
        self.realtime_follow = DEFAULT_REALTIME_FOLLOW
        self.system_volume_mapping_enabled = DEFAULT_SYSTEM_VOLUME_MAPPING_ENABLED
        self.saved_device_label = ""

        self.freq_bins = self.fft_size // 2 + 1
        self.history_columns = max(120, int(self.time_span * self.sample_rate / self.hop_size))
        self.window = np.hanning(self.fft_size).astype(np.float32)
        self.window_gain = max(float(np.sum(self.window) * 0.5), 1.0)
        self.spectrogram_ring = np.full((self.freq_bins, self.history_columns), FLOOR_DB, dtype=np.float32)
        self.display_spectrogram = np.full((self.freq_bins, self.history_columns), FLOOR_DB, dtype=np.float32)
        self.display_rgba = np.zeros((self.freq_bins, self.history_columns, 4), dtype=np.uint8)
        self.ordered_display_rgba = np.zeros((self.freq_bins, self.history_columns, 4), dtype=np.uint8)
        self.pending_samples = np.zeros(self.fft_size * 2, dtype=np.float32)
        self.pending_sample_count = 0
        self.write_column = 0
        self.image_dirty = False
        self.page_fill_count = 0
        self.row_lower_index = np.zeros(1, dtype=np.int32)
        self.row_upper_index = np.zeros(1, dtype=np.int32)
        self.row_interp_weight = np.zeros(1, dtype=np.float32)
        self.column_mapped_low = np.full(1, FLOOR_DB, dtype=np.float32)
        self.column_mapped_high = np.full(1, FLOOR_DB, dtype=np.float32)
        self.column_display = np.full(1, FLOOR_DB, dtype=np.float32)
        self.column_color_float = np.zeros(1, dtype=np.float32)
        self.column_color_index = np.zeros(1, dtype=np.uint8)
        self.lut_rgba = np.zeros((256, 4), dtype=np.uint8)
        self.color_level_low = DISPLAY_MIN_DB
        self.color_level_high = DISPLAY_MAX_DB
        self.last_frame_time: float | None = None
        self.actual_fps = 0.0
        self._last_render_row_count = 0
        self.volume_gain_db = 0.0

        self._build_ui()
        self._connect_signals()
        self._load_settings()
        self._refresh_devices()
        self._reset_spectrogram()

        self.update_timer = QtCore.QTimer(self)
        self.update_timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self.update_timer.timeout.connect(self._drain_audio_queue)
        self._apply_display_fps()
        self.update_timer.start()

    def _migrate_legacy_settings(self) -> None:
        current_settings = self.settings
        if current_settings.allKeys():
            return

        legacy_settings = QtCore.QSettings(LEGACY_APP_ORG, APP_NAME)
        legacy_keys = legacy_settings.allKeys()
        if not legacy_keys:
            return

        for key in legacy_keys:
            current_settings.setValue(key, legacy_settings.value(key))
        current_settings.sync()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(8)
        root.addLayout(controls)

        self.device_combo = QtWidgets.QComboBox()
        self.device_combo.setMinimumWidth(220)
        controls.addWidget(QtWidgets.QLabel("声卡"))
        controls.addWidget(self.device_combo, 1)

        self.settings_button = QtWidgets.QPushButton("设置")
        controls.addWidget(self.settings_button)

        self.refresh_button = QtWidgets.QPushButton("刷新设备")
        controls.addWidget(self.refresh_button)

        self.start_button = QtWidgets.QPushButton("开始")
        controls.addWidget(self.start_button)

        self.stop_button = QtWidgets.QPushButton("停止")
        controls.addWidget(self.stop_button)

        self.status_label = QtWidgets.QLabel("未启动")
        controls.addWidget(self.status_label)
        controls.addStretch(1)

        self.freq_zoom_label = QtWidgets.QLabel("频率缩放: 1.00x")
        self.freq_zoom_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Maximum, QtWidgets.QSizePolicy.Policy.Preferred)
        controls.addWidget(self.freq_zoom_label)

        self.actual_fps_label = QtWidgets.QLabel("FPS 000.0")
        fps_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        fps_font.setPointSize(max(fps_font.pointSize(), 10))
        self.actual_fps_label.setFont(fps_font)
        self.actual_fps_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        fps_width = self.actual_fps_label.fontMetrics().horizontalAdvance("FPS 000.0")
        self.actual_fps_label.setFixedWidth(fps_width + 8)
        controls.addWidget(self.actual_fps_label)

        self.info_label = QtWidgets.QLabel()
        self.info_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        controls.addWidget(self.info_label)

        plot_row = QtWidgets.QHBoxLayout()
        plot_row.setSpacing(10)
        root.addLayout(plot_row, 1)

        self.frequency_axis = FrequencyAxisItem("left")
        self.view_box = SpectrogramViewBox()
        self.time_axis = TimeAxisItem("bottom")
        self.plot_widget = pg.PlotWidget(
            viewBox=self.view_box,
            axisItems={"left": self.frequency_axis, "bottom": self.time_axis},
        )
        self.plot_widget.setBackground("#0b1020")
        self.plot_widget.setViewportUpdateMode(QtWidgets.QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self.plot_widget.setOptimizationFlag(QtWidgets.QGraphicsView.OptimizationFlag.DontSavePainterState, True)
        self.plot_widget.setOptimizationFlag(QtWidgets.QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self.plot_widget.setLabel("bottom", "时间", units="s")
        self.plot_widget.setLabel("left", "频率", units="Hz")
        self.plot_widget.getPlotItem().setMenuEnabled(False)
        plot_row.addWidget(self.plot_widget, 1)

        self.plot_item = self.plot_widget.getPlotItem()
        self.plot_item.setLimits(xMin=0, yMin=0, yMax=1, minYRange=1, maxYRange=1)
        self.plot_item.setDownsampling(auto=False)
        self.plot_item.setClipToView(False)

        self.image_item = pg.ImageItem(axisOrder="row-major")
        self.image_item.setAutoDownsample(False)
        self.image_item.setImage(np.zeros((1, 1, 4), dtype=np.uint8), autoLevels=False)
        self.plot_item.addItem(self.image_item)

        self.histogram = InteractiveHistogramLUTItem()
        self.histogram_proxy_item = pg.ImageItem(axisOrder="row-major")
        self.histogram.setImageItem(self.histogram_proxy_item)
        graphics_layout = pg.GraphicsLayoutWidget()
        graphics_layout.addItem(self.histogram)
        graphics_layout.setMinimumWidth(124)
        graphics_layout.setMaximumWidth(124)
        plot_row.addWidget(graphics_layout)

    def _connect_signals(self) -> None:
        self.audio_worker.chunk_ready.connect(self.audio_queue.put_nowait)
        self.audio_worker.state_changed.connect(self._set_status)
        self.audio_worker.error.connect(self._show_status_message)
        self.view_box.zoom_requested.connect(self._on_wheel_zoom_requested)
        self.settings_button.clicked.connect(self._open_settings_dialog)
        self.refresh_button.clicked.connect(self._refresh_devices)
        self.start_button.clicked.connect(self._start_capture)
        self.stop_button.clicked.connect(self._stop_capture)
        self.device_combo.currentIndexChanged.connect(self._save_settings)
        self.histogram.state_changed.connect(self._save_settings)
        self.histogram.state_changed.connect(self._update_info)
        self.histogram.state_changed.connect(self._sync_image_visuals)
        self.plot_widget.installEventFilter(self)

    def _refresh_devices(self) -> None:
        self.devices.clear()
        self.device_combo.clear()
        try:
            pa = pyaudio.PyAudio()
        except Exception as exc:
            self._show_status_message(f"读取声卡列表失败: {exc}")
            return

        try:
            hostapi_names = {
                index: pa.get_host_api_info_by_index(index).get("name", "Unknown")
                for index in range(pa.get_host_api_count())
            }
            discovered: list[DeviceOption] = []
            for index in range(pa.get_device_count()):
                device = pa.get_device_info_by_index(index)
                input_channels = int(device.get("maxInputChannels", 0))
                is_loopback = bool(device.get("isLoopbackDevice", False))
                if input_channels > 0 or is_loopback:
                    hostapi_name = hostapi_names.get(int(device.get("hostApi", -1)), "Unknown")
                    device_name = str(device.get("name", f"Device {index}"))
                    default_samplerate = int(device.get("defaultSampleRate", DEFAULT_SAMPLE_RATE) or DEFAULT_SAMPLE_RATE)
                    prefix = "回采" if is_loopback else "输入"
                    discovered.append(
                        DeviceOption(
                            label=f"{prefix} | {device_name} | {hostapi_name}",
                            device_name=device_name,
                            device_index=index,
                            sample_rate=default_samplerate,
                            channels=max(1, input_channels),
                            hostapi_name=hostapi_name,
                            is_loopback=is_loopback,
                        )
                    )
        finally:
            pa.terminate()

        preferred_devices = [d for d in discovered if d.is_loopback or "WASAPI" in d.hostapi_name.upper()]
        self.devices = preferred_devices or discovered
        for option in self.devices:
            self.device_combo.addItem(option.label)

        if not self.devices:
            self.device_combo.addItem("没有找到可用声卡")
            self.device_combo.setEnabled(False)
            self.start_button.setEnabled(False)
            return

        self.device_combo.setEnabled(True)
        self.start_button.setEnabled(True)
        preferred_index = next(
            (i for i, option in enumerate(self.devices) if option.label == self.saved_device_label),
            next((i for i, option in enumerate(self.devices) if option.is_loopback), 0),
        )
        self.device_combo.setCurrentIndex(preferred_index)
        self._show_status_message(f"已加载 {len(self.devices)} 个可采集设备（含回采）。")
        self._save_settings()

    def _open_settings_dialog(self) -> None:
        dialog = SettingsDialog(
            self,
            {
                "time_resolution": self.time_resolution,
                "frequency_resolution": self.frequency_resolution,
                "time_span": self.time_span,
                "display_fps": self.display_fps,
                "realtime_follow": self.realtime_follow,
                "system_volume_mapping_enabled": self.system_volume_mapping_enabled,
                "frequency_min": self.frequency_min,
                "frequency_max": self.frequency_max,
            },
        )
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        values = dialog.values()
        self.time_resolution = int(values["time_resolution"])
        self.frequency_resolution = int(values["frequency_resolution"])
        self.time_span = float(values["time_span"])
        self.display_fps = int(values["display_fps"])
        self.realtime_follow = bool(values["realtime_follow"])
        self.system_volume_mapping_enabled = bool(values["system_volume_mapping_enabled"])
        self.frequency_min = int(values["frequency_min"])
        self.frequency_max = int(values["frequency_max"])
        if dialog.reset_requested():
            self._reset_hidden_settings_to_defaults()
        self._apply_display_fps()
        self._apply_display_settings()
        self._save_settings()

    def _reset_hidden_settings_to_defaults(self) -> None:
        self._loading_settings = True
        try:
            self.frequency_zoom_power = DEFAULT_FREQUENCY_ZOOM_POWER
            self.system_volume_mapping_enabled = DEFAULT_SYSTEM_VOLUME_MAPPING_ENABLED
            self.saved_device_label = ""
            self.histogram.restore_serialized_state(DEFAULT_HISTOGRAM_STATE)
            self._sync_image_visuals()
            if self.devices:
                preferred_index = next((i for i, option in enumerate(self.devices) if option.is_loopback), 0)
                self.device_combo.setCurrentIndex(preferred_index)
        finally:
            self._loading_settings = False

    def _apply_display_settings(self) -> None:
        self._recompute_resolution_settings()
        self._reset_spectrogram()

    def _recompute_resolution_settings(self) -> None:
        self.hop_size = max(CAPTURE_BLOCK_SIZE, BASE_FFT_SIZE // self.time_resolution)
        self.fft_size = BASE_FFT_SIZE * self.frequency_resolution

    def _reset_spectrogram(self) -> None:
        self.max_frequency = self.sample_rate / 2
        self.freq_bins = self.fft_size // 2 + 1
        self.history_columns = max(120, int(self.time_span * self.sample_rate / self.hop_size))
        self.window = np.hanning(self.fft_size).astype(np.float32)
        self.window_gain = max(float(np.sum(self.window) * 0.5), 1.0)
        self.spectrogram_ring = np.full((self.freq_bins, self.history_columns), FLOOR_DB, dtype=np.float32)
        self.pending_samples = np.zeros(self.fft_size * 2, dtype=np.float32)
        self.pending_sample_count = 0
        self.write_column = 0
        self.image_dirty = False
        self.page_fill_count = 0
        self._rebuild_frequency_mapping()
        self.plot_item.setLimits(
            xMin=-self.time_span if self.realtime_follow else 0.0,
            xMax=0.0 if self.realtime_follow else self.time_span,
            yMin=0.0,
            yMax=1.0,
            minYRange=1.0,
            maxYRange=1.0,
        )
        self.plot_item.setYRange(0.0, 1.0, padding=0.0)
        self._update_time_axis_view()
        self._update_frequency_axis()
        self._refresh_image()
        self._present_image(force=True)
        self._update_info()

    def _on_wheel_zoom_requested(self, delta: int) -> None:
        step = 0.18 if delta > 0 else -0.18
        new_power = float(np.clip(self.frequency_zoom_power + step, 1.0, MAX_FREQUENCY_ZOOM_POWER))
        if abs(new_power - self.frequency_zoom_power) < 1e-6:
            return
        self.frequency_zoom_power = new_power
        self._rebuild_frequency_mapping()
        self.image_dirty = True
        self._refresh_image()
        self._present_image(force=True)
        self._update_frequency_axis()
        self._update_info()
        self._save_settings()

    def _rebuild_frequency_mapping(self) -> None:
        nyquist = min(int(self.max_frequency), MAX_FREQUENCY_LIMIT)
        self.visible_frequency_min = int(np.clip(self.frequency_min, MIN_FREQUENCY_LIMIT, max(nyquist - 1, 0)))
        self.visible_frequency_max = int(np.clip(self.frequency_max, self.visible_frequency_min + 1, nyquist))
        span = max(self.visible_frequency_max - self.visible_frequency_min, 1)
        natural_row_count = max(256, int(span / max(self.sample_rate, 1) * self.fft_size * 2))
        render_row_cap = self._render_row_cap()
        row_count = min(self.freq_bins, natural_row_count, render_row_cap)
        self.display_spectrogram = np.full((row_count, self.history_columns), FLOOR_DB, dtype=np.float32)
        self.display_rgba = np.zeros((row_count, self.history_columns, 4), dtype=np.uint8)
        self.ordered_display_rgba = np.zeros((row_count, self.history_columns, 4), dtype=np.uint8)
        self.column_mapped_low = np.full(row_count, FLOOR_DB, dtype=np.float32)
        self.column_mapped_high = np.full(row_count, FLOOR_DB, dtype=np.float32)
        self.column_display = np.full(row_count, FLOOR_DB, dtype=np.float32)
        self.column_color_float = np.zeros(row_count, dtype=np.float32)
        self.column_color_index = np.zeros(row_count, dtype=np.uint8)
        self._last_render_row_count = row_count

        normalized = np.linspace(0.0, 1.0, row_count, dtype=np.float32)
        effective_power = self._effective_frequency_power()
        mapped_frequency = self.visible_frequency_min + span * np.power(normalized, effective_power, dtype=np.float32)
        mapped_bins = mapped_frequency / max(self.max_frequency, 1.0) * (self.freq_bins - 1)
        self.row_lower_index = np.floor(mapped_bins).astype(np.int32)
        self.row_upper_index = np.clip(self.row_lower_index + 1, 0, self.freq_bins - 1)
        self.row_interp_weight = (mapped_bins - self.row_lower_index).astype(np.float32)
        self.freq_zoom_label.setText(f"频率缩放: {self.frequency_zoom_power:.2f}x")

    def _update_frequency_axis(self) -> None:
        self.frequency_axis.set_frequency_mapping(
            self.visible_frequency_min,
            self.visible_frequency_max,
            self._effective_frequency_power(),
        )

    def _update_time_axis_view(self) -> None:
        if self.realtime_follow:
            self.image_item.setRect(QtCore.QRectF(-self.time_span, 0.0, self.time_span, 1.0))
            self.plot_item.setXRange(-self.time_span, 0.0, padding=0.0)
            self.time_axis.set_time_mapping(True, self.time_span)
            return

        self.image_item.setRect(QtCore.QRectF(0.0, 0.0, self.time_span, 1.0))
        self.plot_item.setXRange(0.0, self.time_span, padding=0.0)
        self.time_axis.set_time_mapping(False, self.time_span)

    def _effective_frequency_power(self) -> float:
        # Compress the upper zoom range so low frequencies are not over-expanded.
        return 1.0 + (max(self.frequency_zoom_power, 1.0) - 1.0) * 0.5

    def _apply_display_fps(self) -> None:
        fps = self.display_fps if self.display_fps in FPS_OPTIONS else DEFAULT_DISPLAY_FPS
        interval_ms = max(1, round(1000 / fps))
        self.update_timer.setInterval(interval_ms)

    def _render_row_cap(self) -> int:
        viewport_height = max(0, self.plot_widget.viewport().height()) if hasattr(self, "plot_widget") else 0
        if viewport_height <= 0:
            return self.freq_bins
        # Cap internal render rows near the visible pixel height to avoid
        # spending CPU on rows the screen cannot show.
        return max(256, int(viewport_height * 1.35))

    def eventFilter(self, watched, event):  # type: ignore[override]
        if watched is getattr(self, "plot_widget", None) and event.type() == QtCore.QEvent.Type.Resize:
            new_cap = self._render_row_cap()
            if abs(new_cap - self._last_render_row_count) > max(32, int(self._last_render_row_count * 0.12)):
                self._rebuild_frequency_mapping()
                self.image_dirty = True
                self._refresh_image()
                self._present_image(force=True)
                self._update_frequency_axis()
        return super().eventFilter(watched, event)

    def _update_actual_fps(self, drew_frame: bool) -> None:
        now = time.perf_counter()
        if self.last_frame_time is None:
            self.last_frame_time = now
            self.actual_fps = 0.0
        else:
            delta = now - self.last_frame_time
            self.last_frame_time = now
            if delta > 0:
                instantaneous_fps = 1.0 / delta
                if self.actual_fps <= 0.0:
                    self.actual_fps = instantaneous_fps
                else:
                    self.actual_fps = self.actual_fps * 0.65 + instantaneous_fps * 0.35
        marker = "*" if drew_frame else " "
        self.actual_fps_label.setText(f"FPS {self.actual_fps:05.1f}{marker}")

    def _start_capture(self) -> None:
        current_index = self.device_combo.currentIndex()
        if current_index < 0 or current_index >= len(self.devices):
            self._show_status_message("没有可用设备可启动。")
            return
        option = self.devices[current_index]
        self.sample_rate = option.sample_rate
        self.loopback_volume_mapper.configure(option)
        self.volume_gain_db = self.loopback_volume_mapper.gain_db() if self._use_system_volume_mapping() else 0.0
        self._recompute_resolution_settings()
        self._reset_spectrogram()
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
        self.audio_worker.configure(option, self.capture_block_size)
        self.audio_worker.start()
        self._save_settings()

    def _stop_capture(self) -> None:
        self.audio_worker.stop()

    def _drain_audio_queue(self) -> None:
        drew_frame = False
        while True:
            try:
                chunk = self.audio_queue.get_nowait()
            except queue.Empty:
                break
            self._append_chunk(chunk)

        if self.image_dirty:
            self._present_image()
            self.image_dirty = False
            drew_frame = True

        self._update_actual_fps(drew_frame)

    def _append_chunk(self, chunk: np.ndarray) -> None:
        if chunk.size == 0:
            return
        self._append_pending_samples(chunk)
        while self.pending_sample_count >= self.fft_size:
            frame = self.pending_samples[: self.fft_size]
            fft_frame = np.fft.rfft(frame * self.window)
            magnitude = np.abs(fft_frame) / self.window_gain
            db = 20.0 * np.log10(np.maximum(magnitude, 1e-7))
            previous_gain_db = self.volume_gain_db
            if self._use_system_volume_mapping():
                self.volume_gain_db = self.loopback_volume_mapper.gain_db()
                db += self.volume_gain_db
            else:
                self.volume_gain_db = 0.0
            if abs(self.volume_gain_db - previous_gain_db) >= 0.05:
                self._update_info()
            db = np.clip(db, FLOOR_DB, CEILING_DB).astype(np.float32)

            if self.realtime_follow:
                target_index = self.write_column
                self.spectrogram_ring[:, target_index] = db
                self._update_display_column(db, target_index)
                self.write_column = (self.write_column + 1) % self.history_columns
            else:
                if self.page_fill_count >= self.history_columns:
                    self.spectrogram_ring.fill(FLOOR_DB)
                    self.display_spectrogram.fill(FLOOR_DB)
                    self.display_rgba.fill(0)
                    self.page_fill_count = 0
                target_index = self.page_fill_count
                self.spectrogram_ring[:, target_index] = db
                self._update_display_column(db, target_index)
                self.page_fill_count += 1

            self.image_dirty = True
            if not self.realtime_follow:
                self._update_time_axis_view()
            self._consume_pending_samples(self.hop_size)

    def _append_pending_samples(self, chunk: np.ndarray) -> None:
        required = self.pending_sample_count + chunk.size
        if required > self.pending_samples.size:
            new_size = max(required, self.pending_samples.size * 2)
            new_buffer = np.zeros(new_size, dtype=np.float32)
            new_buffer[: self.pending_sample_count] = self.pending_samples[: self.pending_sample_count]
            self.pending_samples = new_buffer
        self.pending_samples[self.pending_sample_count : required] = chunk
        self.pending_sample_count = required

    def _consume_pending_samples(self, count: int) -> None:
        remaining = self.pending_sample_count - count
        if remaining > 0:
            self.pending_samples[:remaining] = self.pending_samples[count : self.pending_sample_count]
        self.pending_sample_count = max(0, remaining)

    def _refresh_image(self) -> None:
        np.take(self.spectrogram_ring, self.row_lower_index, axis=0, out=self.display_spectrogram)
        high_rows = self.spectrogram_ring[self.row_upper_index, :]
        np.subtract(high_rows, self.display_spectrogram, out=high_rows)
        np.multiply(high_rows, self.row_interp_weight[:, None], out=high_rows)
        np.add(self.display_spectrogram, high_rows, out=self.display_spectrogram)
        self._recolor_full_display()

    def _update_display_column(self, db_column: np.ndarray, target_index: int) -> None:
        np.take(db_column, self.row_lower_index, axis=0, out=self.column_mapped_low)
        np.take(db_column, self.row_upper_index, axis=0, out=self.column_mapped_high)
        np.subtract(self.column_mapped_high, self.column_mapped_low, out=self.column_display)
        np.multiply(self.column_display, self.row_interp_weight, out=self.column_display)
        np.add(self.column_mapped_low, self.column_display, out=self.column_display)
        self.display_spectrogram[:, target_index] = self.column_display
        self._colorize_column(target_index)

    def _colorize_column(self, target_index: int) -> None:
        scale = 255.0 / max(self.color_level_high - self.color_level_low, 1e-6)
        np.subtract(self.column_display, self.color_level_low, out=self.column_color_float)
        np.multiply(self.column_color_float, scale, out=self.column_color_float)
        np.clip(self.column_color_float, 0.0, 255.0, out=self.column_color_float)
        self.column_color_index[:] = self.column_color_float
        self.display_rgba[:, target_index, :] = self.lut_rgba[self.column_color_index]

    def _recolor_full_display(self) -> None:
        normalized = (self.display_spectrogram - self.color_level_low) / max(
            self.color_level_high - self.color_level_low,
            1e-6,
        )
        indices = np.clip(normalized * 255.0, 0.0, 255.0).astype(np.uint8)
        self.display_rgba[...] = self.lut_rgba[indices]

    def _present_image(self, force: bool = False) -> None:
        if self.realtime_follow:
            self._present_realtime_follow(force=force)
            return

        self.image_item.show()
        self.image_item.setRect(QtCore.QRectF(0.0, 0.0, self.time_span, 1.0))
        self.image_item.setImage(self.display_rgba, autoLevels=False)

    def _present_realtime_follow(self, force: bool = False) -> None:
        self.image_item.show()
        self.image_item.setRect(QtCore.QRectF(-self.time_span, 0.0, self.time_span, 1.0))
        if self.write_column == 0:
            self.image_item.setImage(self.display_rgba, autoLevels=False)
            return

        tail_columns = self.history_columns - self.write_column
        self.ordered_display_rgba[:, :tail_columns, :] = self.display_rgba[:, self.write_column :, :]
        self.ordered_display_rgba[:, tail_columns:, :] = self.display_rgba[:, : self.write_column, :]
        self.image_item.setImage(self.ordered_display_rgba, autoLevels=False)

    def _sync_image_visuals(self) -> None:
        levels = self.histogram.getLevels()
        low = float(levels[0] if levels else DISPLAY_MIN_DB)
        high = float(levels[1] if levels else DISPLAY_MAX_DB)
        self.color_level_low = min(max(low, FLOOR_DB), CEILING_DB - 0.1)
        self.color_level_high = max(min(high, CEILING_DB), self.color_level_low + 0.1)
        self.lut_rgba = self.histogram.gradient.getLookupTable(256, alpha=True)
        self._recolor_full_display()
        self.image_dirty = True

    def _load_settings(self) -> None:
        self.saved_device_label = self.settings.value("device_label", "", str)
        self.time_resolution = int(self.settings.value("time_resolution", DEFAULT_TIME_RESOLUTION))
        self.frequency_resolution = int(self.settings.value("frequency_resolution", DEFAULT_FREQUENCY_RESOLUTION))
        self.time_span = float(self.settings.value("time_span", DEFAULT_HISTORY_SECONDS))
        self.display_fps = int(self.settings.value("display_fps", DEFAULT_DISPLAY_FPS))
        self.realtime_follow = self.settings.value("realtime_follow", DEFAULT_REALTIME_FOLLOW, bool)
        self.frequency_min = int(self.settings.value("frequency_min", DEFAULT_FREQUENCY_MIN))
        self.frequency_max = int(self.settings.value("frequency_max", DEFAULT_FREQUENCY_LIMIT))
        self.frequency_zoom_power = float(self.settings.value("frequency_zoom_power", DEFAULT_FREQUENCY_ZOOM_POWER))
        self.system_volume_mapping_enabled = self.settings.value(
            "system_volume_mapping_enabled", DEFAULT_SYSTEM_VOLUME_MAPPING_ENABLED, bool
        )

        self.time_resolution = (
            self.time_resolution if self.time_resolution in RESOLUTION_OPTIONS else DEFAULT_TIME_RESOLUTION
        )
        self.frequency_resolution = (
            self.frequency_resolution
            if self.frequency_resolution in RESOLUTION_OPTIONS
            else DEFAULT_FREQUENCY_RESOLUTION
        )
        self.time_span = float(np.clip(self.time_span, MIN_TIME_SPAN, MAX_TIME_SPAN))
        self.display_fps = self.display_fps if self.display_fps in FPS_OPTIONS else DEFAULT_DISPLAY_FPS
        self.frequency_min = int(np.clip(self.frequency_min, MIN_FREQUENCY_LIMIT, MAX_FREQUENCY_LIMIT - 1))
        self.frequency_max = int(np.clip(self.frequency_max, self.frequency_min + 1, MAX_FREQUENCY_LIMIT))
        self.frequency_zoom_power = float(np.clip(self.frequency_zoom_power, 1.0, MAX_FREQUENCY_ZOOM_POWER))

        histogram_state_raw = self.settings.value("histogram_state", "")
        if histogram_state_raw:
            try:
                self.histogram.restore_serialized_state(json.loads(histogram_state_raw))
            except Exception:
                self.histogram.restore_serialized_state(DEFAULT_HISTOGRAM_STATE)
        else:
            self.histogram.restore_serialized_state(DEFAULT_HISTOGRAM_STATE)
        self._sync_image_visuals()
        self._loading_settings = False

    def _save_settings(self) -> None:
        if self._loading_settings:
            return
        self.settings.setValue("time_resolution", self.time_resolution)
        self.settings.setValue("frequency_resolution", self.frequency_resolution)
        self.settings.setValue("time_span", self.time_span)
        self.settings.setValue("display_fps", self.display_fps)
        self.settings.setValue("realtime_follow", self.realtime_follow)
        self.settings.setValue("system_volume_mapping_enabled", self.system_volume_mapping_enabled)
        self.settings.setValue("frequency_min", self.frequency_min)
        self.settings.setValue("frequency_max", self.frequency_max)
        self.settings.setValue("frequency_zoom_power", self.frequency_zoom_power)
        if 0 <= self.device_combo.currentIndex() < len(self.devices):
            self.settings.setValue("device_label", self.devices[self.device_combo.currentIndex()].label)
        self.settings.setValue("histogram_state", json.dumps(self.histogram.serialized_state(), ensure_ascii=True))
        self.settings.sync()

    def _update_info(self) -> None:
        hop_seconds = self.hop_size / self.sample_rate
        fft_seconds = self.fft_size / self.sample_rate
        levels = self.histogram.getLevels()
        volume_text = "音量映射 关闭"
        if self._use_system_volume_mapping():
            volume_text = f"音量映射 +{self.volume_gain_db:.1f} dB"
        elif self.loopback_volume_mapper.enabled:
            volume_text = "音量映射 已禁用"
        self.info_label.setText(
            f"{self.sample_rate} Hz | 时间 {self.time_resolution}x | 频率 {self.frequency_resolution}x | "
            f"FFT {self.fft_size} ({fft_seconds:.3f} s) | 每列 {hop_seconds:.3f} s | 历史 {self.time_span:.1f} s | "
            f"{self.display_fps} FPS | dB {levels[0]:.0f}..{levels[1]:.0f} | {self.visible_frequency_min}..{self.visible_frequency_max} Hz | "
            f"{volume_text} | {'实时跟随' if self.realtime_follow else '整页更新'}"
        )

    def _use_system_volume_mapping(self) -> bool:
        return self.system_volume_mapping_enabled and self.loopback_volume_mapper.enabled

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _show_status_message(self, text: str) -> None:
        self.statusBar().showMessage(text, 5000)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save_settings()
        self._stop_capture()
        super().closeEvent(event)
