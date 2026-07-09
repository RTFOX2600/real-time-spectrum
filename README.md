# Real-Time Spectrum

一个基于 `Python 3.12` 的显示实时频谱图的小工具，支持 windows 系统。

下载发布版：

- Release 页面: [v0.1.0](https://github.com/RTFOX2600/real-time-spectrum/releases/tag/v0.1.0)
- Windows 压缩包: [RealTimeSpectrum-win64.zip](https://github.com/RTFOX2600/real-time-spectrum/releases/download/v0.1.0/RealTimeSpectrum-win64.zip)

如果你在本地源码目录中运行，也可以直接使用 `dist/RealTimeSpectrum-win64.zip`，解压后运行 `RealTimeSpectrum.exe`。

项目代码采用宽松的 `MIT` 许可证发布；打包产物中包含的第三方依赖仍分别遵循各自许可证。

## 功能

- 启动后先选择声卡
- 支持普通输入设备采集
- 支持 Windows `WASAPI Loopback` 系统输出回采（获取声音输出）
- 横轴为时间，纵轴为频率，颜色表示音量大小
- 支持独立选择时间/频率分辨率
  - 时间: `1x / 2x / 4x / 6x / 8x`
  - 频率: `1x / 2x / 4x / 6x / 8x`
- 打开设置后，可调整 `时间分辨率`、`频率分辨率`、`显示时间范围`、`显示帧率`、`实时跟随`、`回采音量补偿`、`频率上下限`
- 设置窗口支持 `重置设置`，可一键恢复为默认值
- 鼠标滚轮用于频率显示缩放，缩放后低频会占据更多画面高度
- 设置会自动保存到本地，并在下次启动时自动恢复
- 右侧 `分贝 - 颜色映射` 的范围和渐变曲线也会自动保存并恢复
- 显示帧率支持 `30 / 60 / 90 / 120 FPS`
- 显示时间范围支持手动输入到小数点后 `4` 位，可通过计算 BPM 进行切屏卡点

## 开发环境运行

```powershell
.venv\Scripts\Activate.ps1
python app.py
```

## 依赖

- `numpy`
- `PyAudioWPatch`
- `PySide6`
- `pyqtgraph`
- `pycaw`

安装依赖：

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 说明

- 默认使用 `2048` 点 FFT。
- 默认显示最近 `10` 秒历史。
- 默认时间分辨率为 `4x`。
- 默认频率分辨率为 `2x`。
- 默认频率范围为 `50 ~ 21000 Hz`。
- 默认显示帧率为 `60 FPS`。
- 默认开启 `实时跟随`。

- 首次运行如果没有数据，先确认 Windows 麦克风权限或设备是否被其他程序占用。
- 设备名前带 `回采` 的就是系统输出回采设备，适合抓 `扬声器 / 耳机 / 输出声卡` 正在播放的声音。
- 设备名前带 `输入` 的是 `普通麦克风 / 输入声卡`。
- 回采设备默认会按当前 Windows 设备音量自动补偿到 `100%` 参考后再做 dB 颜色映射，这样系统音量不是满格时颜色范围也更稳定。
- 某些带 `独立音量旋钮` 的声卡，Windows 音量可能并不能代表真实播放增益；遇到这种情况，可以在设置中关闭 `回采音量补偿`。
- 如果当前采样率的奈奎斯特频率低于你设置的上限，程序会自动按可显示的最高频率进行限制。

- 时间分辨率越高，每列时间越短，画面横向更新更密。
- 频率分辨率越高，FFT 窗口越长，频率细节更清楚，但时间响应会慢一点点。

- 项目主入口是 `app.py`，项目代码约 3000 行，依赖的库在 `requirements.txt` 里。
- 此软件的设置保存在注册表 `HKEY_CURRENT_USER\Software\RealTimeSpectrum\RealTimeSpectrum`
- 若你之前使用的是旧版本，程序会自动尝试从旧路径 `HKEY_CURRENT_USER\Software\OpenAI\RealTimeSpectrum` 迁移已有设置
- 打包命令 (PowerShell) `.\.venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --name RealTimeSpectrum --collect-submodules pyqtgraph --collect-all pyaudiowpatch app.py`
- 压缩命令 (PowerShell) `Compress-Archive -Path dist\RealTimeSpectrum -DestinationPath dist\RealTimeSpectrum-win64.zip -Force`
