# RM_sdr 独立联调工具

本目录提供实录 IQ 回放、模拟发射、录波离线解码和硬件接收诊断。根目录红蓝接收流图、`gr-robomaster` 和 ROS 集成不由这些工具修改。

| 目录 / 文件 | 功能 | 需要什么 |
| --- | --- | --- |
| `tx/rm_sdr_replay.py` | 检查实录 IQ；直接送入 SDR 发射 | 检查只需 Python；发射需 GNU Radio IIO |
| `tx/RM_SDR_REPLAY.grc` | 实录 IQ 发射的 Companion 流图 | GNU Radio Companion + IIO |
| `tx/rm_sdr_tx.py` | 生成坐标、血量、密钥测试波形，软件回环或发射 | GNU Radio；回环还需项目 OOT |
| `tx/generate_radio_test_data.py` | 为模拟发射生成 packed-byte 文件 | Python 标准库 |
| `tx/RM_SDR_TX.grc` | Byte → GFSK Mod 的模拟发射流图 | GNU Radio Companion + IIO |
| `rx/rm_sdr_file_rx.py` | 实录 IQ → 项目解调块 → 应用帧 CRC；可对接现有 ROS | GNU Radio + OOT + NumPy |
| `rx/sdr_rx_probe.py` | 从硬件接收 IQ，测频谱、功率和削顶 | GNU Radio IIO + NumPy |
| `sdr_tools/`、`radio_test_frames.py` | 录波选择、采样检查、运行控制、CRC 和协议共用代码 | 按调用功能加载依赖 |
| `recordings.json` | 8 个现场录波的索引；记录参数暂留未知 | Linux 默认读取 `/home/user/code/Info` |
| `reports/` | 本机实际录波的检查结果 | 报告会注明检查覆盖范围及假设 |
| `tests/` | 文件处理、流式 CRC、协议及频谱验证 | 标准库；频谱测试需 NumPy |

## 从实录开始

Linux 实际项目目录为 `/home/user/code/RM_str`，录波目录为 `/home/user/code/Info`。在项目根目录运行：

```bash
cd /home/user/code/RM_str
python3 tools/tx/rm_sdr_replay.py --list
python3 tools/tx/rm_sdr_replay.py --recording red
python3 tools/tx/rm_sdr_replay.py --recording blue-ganrao-1
```

以上命令不打开 SDR，不改变原始文件。默认按 `cf32_le` 解释并抽查文件头、中、尾；`--full-scan` 检查选定范围的所有采样。录制目录可用 `--recording-dir` 指定，任意录波可用 `--file` 指定。

索引中的 Linux 目录使用相对路径 `../../Info`，相对于 `tools/recordings.json` 解析，启动位置不会影响它。Windows 仍默认读取源码项目同级的“信息波录制”，可将命令中的 `python3` 替换为相应的 `python`。

**现场文件的采样率、接收中心频率没有附带记录。** `recordings.json` 不会因为名字叫红方／蓝方，就自动采用当前接收器参数。发射必须显式填写 `--sample-rate`、`--frequency` 和 `--uri`。这两个录制参数确认前，先按[实录 IQ 回放与验证](docs/实录IQ回放与验证.md)完成离线核查。

## 按任务查文档

- [实录 IQ 回放与验证](docs/实录IQ回放与验证.md)：8 个录波的选择、候选参数、离线解码、SDR 发射和 GRC。
- [模拟发射与回环联调](docs/模拟发射与回环联调.md)：人工测试数据与 A3/E200 联调。
- [无线接收故障排查](docs/无线接收故障排查.md)：区分没信号和解不出数据。
- [Ubuntu 22.04 / ANTSDR E200 复现指南](docs/Ubuntu22.04_ANTSDR_E200_复现指南.md)：现有集成接收环境。

旧命令 `tools/rm_sdr_tx.py`、`tools/generate_radio_test_data.py` 和 `tools/sdr_rx_probe.py` 保留兼容入口；实现已归到 `tx/`、`rx/`。原 `tools/RM_SDR_TX.grc` 移到 `tools/tx/`。默认生成数据放在 `tools/outputs/`，不纳入 Git。

```bash
python3 -B -m unittest discover -s tools/tests -v
```

Windows 上的文件检查与单元测试不能代替 Linux GNU Radio 流图运行或 SDR 硬件验证；具体完成范围见实录指南和报告。
