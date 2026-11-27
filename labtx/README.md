# 双设备 LabTX

LabTX 是独立运行的发射台，默认网页地址为 `http://127.0.0.1:8780`。发射 URI 和 E200 参考地址在 [config.yaml](config.yaml) 中配置。

| 用途 | 默认地址 |
| --- | --- |
| 信息波发射 | `ip:192.168.1.101` |
| 干扰波发射 | `ip:192.168.1.102` |
| E200 接收 | `ip:192.168.1.11` |

从 RM_sdr 根目录检查配置并启动：

```bash
python3 -m labtx --describe
bash labtx/start.sh
```

启动脚本加载 ROS 2 Humble。硬件模式需要 GNU Radio 3.10.7 或更新版本、兼容的 gr-iio 和 `libiio.so.0`，同一 Python 环境使用 PyQt5 和 NumPy。设备检查使用 `iio_info`、`iio_attr`，序列号缺失时用 Linux `ip` 查询同网段邻居 MAC。LabTX 沿用环境中的 `ROS_DOMAIN_ID`，未设置时用 `78`。

在网页确认设备后，可单发或双发、编辑信息字段和密钥、设置衰减并观察欠流。更换设备或射频端口时编辑 `labtx/config.yaml`，也可用 `bash labtx/start.sh --config /path/to/config.yaml`。`python3 -m labtx --preview` 可以预览并准备发射数据。

LabTX 使用内置 SHARK 组包、连续 GFSK 和 IIO 发射链。设备占用由 LabTX 会话管理，双发启动失败时回收整组，停机结果在页面显示。发射衰减以 dB 设置，实际输出功率以 dBm 实测。在 `calibration.broadcast` 和 `calibration.interference` 分别填写 `measured_dbm`、测量时的 `attenuation_db` 和 `profile`。

接收监控见 [SDR Dashboard](../dashboard/README.md)。LabTX 代码来源和版权见 [SHARK 来源说明](vendor/shark_labtx/README.md)。
