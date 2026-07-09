from __future__ import annotations

import math

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from .constants import (
    CEILING_DB,
    DEFAULT_FREQUENCY_LIMIT,
    DEFAULT_FREQUENCY_MIN,
    DEFAULT_FREQUENCY_RESOLUTION,
    DEFAULT_HISTOGRAM_STATE,
    DEFAULT_DISPLAY_FPS,
    DEFAULT_HISTORY_SECONDS,
    DEFAULT_REALTIME_FOLLOW,
    DEFAULT_SYSTEM_VOLUME_MAPPING_ENABLED,
    DEFAULT_TIME_RESOLUTION,
    DISPLAY_MAX_DB,
    DISPLAY_MIN_DB,
    FLOOR_DB,
    FPS_OPTIONS,
    MAX_FREQUENCY_LIMIT,
    MAX_TIME_SPAN,
    MIN_FREQUENCY_LIMIT,
    MIN_TIME_SPAN,
    RESOLUTION_OPTIONS,
)


class SpectrogramViewBox(pg.ViewBox):
    zoom_requested = QtCore.Signal(int)

    def __init__(self) -> None:
        super().__init__(enableMenu=False)
        self.setMouseEnabled(x=False, y=False)
        self.setMouseMode(self.PanMode)

    def wheelEvent(self, event, axis=None) -> None:  # type: ignore[override]
        delta = event.delta()
        if delta:
            self.zoom_requested.emit(delta)
        event.accept()

    def mouseDragEvent(self, event, axis=None) -> None:  # type: ignore[override]
        event.accept()

    def mouseClickEvent(self, event) -> None:  # type: ignore[override]
        event.accept()


class TimeAxisItem(pg.AxisItem):
    def __init__(self, orientation: str = "bottom") -> None:
        super().__init__(orientation=orientation)
        self._realtime_follow = True
        self._elapsed_seconds = 0.0

    def set_time_mapping(self, realtime_follow: bool, elapsed_seconds: float) -> None:
        self._realtime_follow = realtime_follow
        self._elapsed_seconds = max(0.0, elapsed_seconds)
        self.picture = None
        self.update()

    def tickStrings(self, values, scale, spacing):
        labels: list[str] = []
        for value in values:
            raw_value = float(value)
            display_value = raw_value if not self._realtime_follow else raw_value
            if abs(display_value) < 0.095:
                labels.append("0")
            elif spacing >= 1.0:
                labels.append(f"{display_value:.0f}")
            else:
                labels.append(f"{display_value:.1f}")
        return labels


class FrequencyAxisItem(pg.AxisItem):
    def __init__(self, orientation: str = "left") -> None:
        super().__init__(orientation=orientation)
        self._lower = float(DEFAULT_FREQUENCY_MIN)
        self._upper = float(DEFAULT_FREQUENCY_LIMIT)
        self._power = 1.0

    def set_frequency_mapping(self, lower: float, upper: float, power: float) -> None:
        self._lower = lower
        self._upper = max(upper, lower + 1.0)
        self._power = max(power, 1.0)
        self.picture = None
        self.update()

    def tickValues(self, minVal: float, maxVal: float, size: float):
        major_ticks, minor_ticks = self._smart_ticks(size)
        major_positions = [self._frequency_to_position(freq) for freq in major_ticks]
        minor_positions = [self._frequency_to_position(freq) for freq in minor_ticks]
        return [(1.0, minor_positions), (5.0, major_positions)]

    def tickStrings(self, values, scale, spacing):
        labels: list[str] = []
        for value in values:
            freq = self._position_to_frequency(value)
            if freq >= 1000:
                kilo_value = freq / 1000.0
                if abs(kilo_value - round(kilo_value)) < 0.05:
                    labels.append(f"{int(round(freq / 1000))}k")
                else:
                    labels.append(f"{kilo_value:.1f}k")
            else:
                labels.append(f"{int(round(freq))}")
        return labels

    def _smart_ticks(self, axis_size: float) -> tuple[list[float], list[float]]:
        span = max(self._upper - self._lower, 1.0)
        target_major = max(5, int(axis_size / 72.0))
        target_minor = max(target_major + 2, int(axis_size / 34.0))

        major_candidates = self._candidate_ticks(dense=False)
        minor_candidates = self._candidate_ticks(dense=True)

        major_ticks = self._filtered_ticks_by_pixels(
            major_candidates,
            axis_size=axis_size,
            target_count=target_major,
            base_spacing=60.0,
            span=span,
        )
        minor_ticks = self._filtered_ticks_by_pixels(
            minor_candidates,
            axis_size=axis_size,
            target_count=target_minor,
            base_spacing=28.0,
            span=span,
        )
        minor_ticks = [tick for tick in minor_ticks if all(abs(tick - major) > 1e-6 for major in major_ticks)]
        return major_ticks, minor_ticks

    def _candidate_ticks(self, dense: bool) -> list[float]:
        if dense:
            mantissas = (1, 1.5, 2, 3, 4, 5, 7.5, 10, 15, 20, 22)
        else:
            mantissas = (1, 2, 5, 20)

        candidates = {round(self._lower, 6), round(self._upper, 6)}
        low_exp = int(math.floor(math.log10(max(self._lower if self._lower > 0 else 1, 1e-6)))) - 1
        high_exp = int(math.ceil(math.log10(max(self._upper, 1.0)))) + 1

        for exponent in range(low_exp, high_exp + 1):
            base = 10**exponent
            for mantissa in mantissas:
                value = mantissa * base
                if self._lower <= value <= self._upper:
                    candidates.add(round(value, 6))
        return sorted(candidates)

    def _filtered_ticks_by_pixels(
        self,
        ticks: list[float],
        axis_size: float,
        target_count: int,
        base_spacing: float,
        span: float,
    ) -> list[float]:
        if len(ticks) <= 2:
            return ticks

        adaptive_spacing = max(12.0, min(base_spacing, axis_size / max(target_count, 1)))
        filtered = [ticks[0]]
        last_pixel = self._frequency_to_position(ticks[0]) * axis_size

        for tick in ticks[1:-1]:
            pixel = self._frequency_to_position(tick) * axis_size
            threshold = adaptive_spacing
            if tick < 1000:
                threshold *= 0.72
            elif tick < 5000:
                threshold *= 0.9

            if pixel - last_pixel >= threshold:
                filtered.append(tick)
                last_pixel = pixel

        end_tick = ticks[-1]
        end_pixel = self._frequency_to_position(end_tick) * axis_size
        if end_tick - filtered[-1] > 1e-6:
            if end_pixel - last_pixel < adaptive_spacing * 0.5 and len(filtered) > 1:
                filtered[-1] = end_tick
            else:
                filtered.append(end_tick)

        if span <= 1500 and not any(0 < tick < 1000 for tick in filtered):
            sub_k_candidates = [tick for tick in ticks if 0 < tick < 1000]
            if sub_k_candidates:
                filtered.insert(1, sub_k_candidates[min(len(sub_k_candidates) - 1, len(sub_k_candidates) // 2)])
                filtered = sorted(set(round(tick, 6) for tick in filtered))
        return filtered

    @staticmethod
    def _nice_step(raw: float) -> float:
        if raw <= 0:
            return 1.0
        exponent = math.floor(math.log10(raw))
        fraction = raw / (10**exponent)
        if fraction <= 1:
            nice = 1
        elif fraction <= 2:
            nice = 2
        elif fraction <= 5:
            nice = 5
        else:
            nice = 10
        return nice * (10**exponent)

    def _frequency_to_position(self, frequency: float) -> float:
        if self._upper <= self._lower:
            return 0.0
        normalized = (frequency - self._lower) / (self._upper - self._lower)
        normalized = min(max(normalized, 0.0), 1.0)
        return normalized ** (1.0 / self._power)

    def _position_to_frequency(self, position: float) -> float:
        position = min(max(position, 0.0), 1.0)
        return self._lower + (self._upper - self._lower) * (position**self._power)


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None, config: dict[str, float | int]) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.resize(380, 300)
        self._reset_requested = False

        layout = QtWidgets.QFormLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.time_resolution_combo = QtWidgets.QComboBox()
        self.frequency_resolution_combo = QtWidgets.QComboBox()
        for value in RESOLUTION_OPTIONS:
            self.time_resolution_combo.addItem(f"{value}x", value)
            self.frequency_resolution_combo.addItem(f"{value}x", value)
        self._set_combo_value(self.time_resolution_combo, int(config["time_resolution"]))
        self._set_combo_value(self.frequency_resolution_combo, int(config["frequency_resolution"]))
        layout.addRow("时间分辨率", self.time_resolution_combo)
        layout.addRow("频率分辨率", self.frequency_resolution_combo)

        self.time_span_spin = QtWidgets.QDoubleSpinBox()
        self.time_span_spin.setRange(MIN_TIME_SPAN, MAX_TIME_SPAN)
        self.time_span_spin.setSingleStep(1.0)
        self.time_span_spin.setDecimals(4)
        self.time_span_spin.setSuffix(" s")
        self.time_span_spin.setValue(float(config["time_span"]))
        layout.addRow("显示时间范围", self.time_span_spin)

        self.display_fps_combo = QtWidgets.QComboBox()
        for value in FPS_OPTIONS:
            self.display_fps_combo.addItem(f"{value} FPS", value)
        self._set_combo_value(self.display_fps_combo, int(config.get("display_fps", DEFAULT_DISPLAY_FPS)))
        layout.addRow("显示帧率", self.display_fps_combo)

        self.realtime_follow_checkbox = QtWidgets.QCheckBox("实时跟随")
        self.realtime_follow_checkbox.setChecked(bool(config["realtime_follow"]))
        layout.addRow("滚动模式", self.realtime_follow_checkbox)

        self.system_volume_mapping_checkbox = QtWidgets.QCheckBox("启用系统音量补偿到 100%")
        self.system_volume_mapping_checkbox.setChecked(
            bool(config.get("system_volume_mapping_enabled", DEFAULT_SYSTEM_VOLUME_MAPPING_ENABLED))
        )
        layout.addRow("回采音量补偿", self.system_volume_mapping_checkbox)

        freq_row = QtWidgets.QHBoxLayout()
        self.freq_min_spin = QtWidgets.QSpinBox()
        self.freq_min_spin.setRange(MIN_FREQUENCY_LIMIT, MAX_FREQUENCY_LIMIT - 1)
        self.freq_min_spin.setSingleStep(10)
        self.freq_min_spin.setSuffix(" Hz")
        self.freq_min_spin.setValue(int(config["frequency_min"]))
        freq_row.addWidget(self.freq_min_spin)

        self.freq_max_spin = QtWidgets.QSpinBox()
        self.freq_max_spin.setRange(MIN_FREQUENCY_LIMIT + 1, MAX_FREQUENCY_LIMIT)
        self.freq_max_spin.setSingleStep(1000)
        self.freq_max_spin.setSuffix(" Hz")
        self.freq_max_spin.setValue(int(config["frequency_max"]))
        freq_row.addWidget(self.freq_max_spin)
        layout.addRow("频率范围", freq_row)

        self.freq_min_spin.valueChanged.connect(self._sync_frequency_bounds)
        self.freq_max_spin.valueChanged.connect(self._sync_frequency_bounds)

        info = QtWidgets.QLabel("鼠标滚轮控制频率显示缩放；各设置会自动保存。")
        info.setWordWrap(True)
        layout.addRow(info)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.reset_button = buttons.addButton("重置设置", QtWidgets.QDialogButtonBox.ButtonRole.ResetRole)
        self.reset_button.clicked.connect(self._reset_to_defaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    @staticmethod
    def _set_combo_value(combo: QtWidgets.QComboBox, value: int) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _sync_frequency_bounds(self) -> None:
        self.freq_min_spin.setMaximum(self.freq_max_spin.value() - 1)
        self.freq_max_spin.setMinimum(self.freq_min_spin.value() + 1)

    def _reset_to_defaults(self) -> None:
        confirmed = QtWidgets.QMessageBox.question(
            self,
            "重置设置",
            "确定要将所有设置重置为代码默认值吗？",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirmed != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self._reset_requested = True
        self._set_combo_value(self.time_resolution_combo, DEFAULT_TIME_RESOLUTION)
        self._set_combo_value(self.frequency_resolution_combo, DEFAULT_FREQUENCY_RESOLUTION)
        self.time_span_spin.setValue(DEFAULT_HISTORY_SECONDS)
        self._set_combo_value(self.display_fps_combo, DEFAULT_DISPLAY_FPS)
        self.realtime_follow_checkbox.setChecked(DEFAULT_REALTIME_FOLLOW)
        self.system_volume_mapping_checkbox.setChecked(DEFAULT_SYSTEM_VOLUME_MAPPING_ENABLED)
        self.freq_min_spin.setValue(DEFAULT_FREQUENCY_MIN)
        self.freq_max_spin.setValue(DEFAULT_FREQUENCY_LIMIT)
        self._sync_frequency_bounds()

    def values(self) -> dict[str, float | int]:
        return {
            "time_resolution": int(self.time_resolution_combo.currentData() or DEFAULT_TIME_RESOLUTION),
            "frequency_resolution": int(self.frequency_resolution_combo.currentData() or DEFAULT_FREQUENCY_RESOLUTION),
            "time_span": float(self.time_span_spin.value()),
            "display_fps": int(self.display_fps_combo.currentData() or DEFAULT_DISPLAY_FPS),
            "realtime_follow": bool(self.realtime_follow_checkbox.isChecked()),
            "system_volume_mapping_enabled": bool(self.system_volume_mapping_checkbox.isChecked()),
            "frequency_min": int(self.freq_min_spin.value()),
            "frequency_max": int(self.freq_max_spin.value()),
        }

    def reset_requested(self) -> bool:
        return self._reset_requested


class InteractiveHistogramLUTItem(pg.HistogramLUTItem):
    state_changed = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.restoreState(self._normalized_state(DEFAULT_HISTOGRAM_STATE))
        self._apply_interaction_style()
        self._lock_value_axis()
        self._connect_signals()

    def _apply_interaction_style(self) -> None:
        line_pen = pg.mkPen((255, 255, 255, 220), width=2)
        hover_pen = pg.mkPen((255, 255, 255, 255), width=8)
        for index, line in enumerate(self.region.lines):
            line.setPen(line_pen)
            line.setHoverPen(hover_pen)
            line.markers.clear()
            marker = "<|" if index == 0 else "|>"
            line.addMarker(marker, position=0.5, size=11.0)
        self.gradient.setFixedWidth(26)
        self._update_region_brush()

    def _lock_value_axis(self) -> None:
        self.vb.setMouseEnabled(x=False, y=False)
        self.vb.setLimits(yMin=FLOOR_DB, yMax=CEILING_DB, minYRange=CEILING_DB - FLOOR_DB, maxYRange=CEILING_DB - FLOOR_DB)
        self.vb.setYRange(FLOOR_DB, CEILING_DB, padding=0.0)
        self.vb.wheelEvent = lambda ev, axis=None: ev.accept()  # type: ignore[assignment]

    def _connect_signals(self) -> None:
        self.sigLevelChangeFinished.connect(self._clamp_levels)
        self.sigLevelChangeFinished.connect(self.state_changed)
        self.sigLookupTableChanged.connect(self.state_changed)
        self.gradient.sigGradientChangeFinished.connect(self.state_changed)
        self.gradient.sigGradientChanged.connect(self._update_region_brush)

    def _clamp_levels(self) -> None:
        low, high = self.getLevels()
        clamped_low = min(max(low, FLOOR_DB), CEILING_DB - 0.1)
        clamped_high = max(min(high, CEILING_DB), FLOOR_DB + 0.1)
        if (clamped_low, clamped_high) != (low, high):
            self.setLevels(clamped_low, clamped_high)
        self.vb.setYRange(FLOOR_DB, CEILING_DB, padding=0.0)

    def _update_region_brush(self) -> None:
        gradient_brush = QtGui.QLinearGradient(0.0, 1.0, 0.0, 0.0)
        gradient_brush.setCoordinateMode(QtGui.QGradient.CoordinateMode.ObjectBoundingMode)
        for pos, color in self.gradient.saveState()["ticks"]:
            gradient_brush.setColorAt(1.0 - float(pos), QtGui.QColor(*color))
        self.region.setBrush(QtGui.QBrush(gradient_brush))

        hover_gradient = QtGui.QLinearGradient(0.0, 1.0, 0.0, 0.0)
        hover_gradient.setCoordinateMode(QtGui.QGradient.CoordinateMode.ObjectBoundingMode)
        for pos, color in self.gradient.saveState()["ticks"]:
            qcolor = QtGui.QColor(*color)
            qcolor.setAlpha(min(255, qcolor.alpha() + 48))
            hover_gradient.setColorAt(1.0 - float(pos), qcolor)
        self.region.setHoverBrush(QtGui.QBrush(hover_gradient))

    @staticmethod
    def _normalized_state(state: dict) -> dict:
        return {
            "mode": state.get("mode", "mono"),
            "levels": state.get("levels", [DISPLAY_MIN_DB, DISPLAY_MAX_DB]),
            "gradient": {
                "mode": state.get("gradient", {}).get("mode", "rgb"),
                "ticks": [
                    (float(pos), tuple(int(channel) for channel in color))
                    for pos, color in state.get("gradient", {}).get("ticks", DEFAULT_HISTOGRAM_STATE["gradient"]["ticks"])
                ],
            },
        }

    def restore_serialized_state(self, state: dict) -> None:
        self.restoreState(self._normalized_state(state))
        self._apply_interaction_style()
        self._clamp_levels()

    def serialized_state(self) -> dict:
        state = self.saveState()
        return {
            "mode": state["mode"],
            "levels": [float(state["levels"][0]), float(state["levels"][1])],
            "gradient": {
                "mode": state["gradient"]["mode"],
                "ticks": [[float(pos), list(map(int, color))] for pos, color in state["gradient"]["ticks"]],
            },
        }
