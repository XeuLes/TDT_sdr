# 现场实录 IQ：检查、离线解码与 SDR 回放

用户确认这些文件来自 RM 官方赛场的实际红蓝方广播录制。文件本身没有扩展名或参数头，按内容和原录波流图判断为小端 complex64 IQ：每个采样是两个 float32，依次为 I、Q，共 8 字节。这里保留其现场来源说明；各文件包含的具体应用帧，须以解调与 CRC 结果确认。

## 1. 选择录波

Linux 实际项目目录为 `/home/user/code/RM_str`，原始录波目录为 `/home/user/code/Info`。工具默认读取该录波目录，不复制进工程、不改写。以下命令从项目根目录运行；Windows 仍默认使用源码项目同级的“信息波录制”。

| 工具中的 ID | 原文件 |
| --- | --- |
| `red` | `RX_RED` |
| `red-ganrao-1` / `2` / `3` | `RX_RED_ganrao_1` / `2` / `3` |
| `blue` | `RX_BLUE` |
| `blue-ganrao-1` / `2` / `3` | `RX_BLUE_ganrao_1` / `2` / `3` |

```bash
cd /home/user/code/RM_str
python3 tools/tx/rm_sdr_replay.py --list
python3 tools/tx/rm_sdr_replay.py --recording red
python3 tools/tx/rm_sdr_replay.py --recording blue --full-scan
```

检查命令只用 Python 标准库，Windows 也能运行。输出包括文件大小、采样数、检查覆盖范围、非有限值、幅度和削顶比例。没有指定采样率时，时长显示 `null`，不猜测播放速度。格式标记表示按 raw IQ 解释，并不单凭文件大小证明数据来源。

也可以显式指定 Linux 的录波目录或单个文件：

```bash
python3 tools/tx/rm_sdr_replay.py --recording red --recording-dir /home/user/code/Info
python3 tools/tx/rm_sdr_replay.py --file /home/user/code/Info/RX_RED
```

`--start-sample` 和 `--samples` 可选择片段，单位为复数采样点；默认从开头到 EOF。默认单次，只有加 `--repeat` 才会循环，循环接缝可能破坏跨接缝的数据帧。

## 2. 先确认录制参数

原始文件不保存采样率、接收中心频率、时间戳或丢样记录。因此下表只是**当前项目接收器的候选配置，不是这些录波已证实的参数**：

| 当前 RX 配置 | 红方 | 蓝方 |
| --- | --- | --- |
| 原始 IQ 采样率 | 2,000,000 Sa/s | 2,000,000 Sa/s |
| 宽带 RF 中心 | 432.600 MHz | 434.520 MHz |
| 信息波数字偏移 | +600 kHz | -600 kHz |
| 密钥支路 1 偏移 | -400 kHz | +400 kHz |
| 密钥支路 2 偏移 | -100 kHz | +100 kHz |
| 抽取后每符号采样数 | 47 | 47 |

可先用候选采样率查看频谱峰位置；以下命令不会发射：

```bash
python3 tools/tx/rm_sdr_replay.py --recording red --sample-rate 2000000 --spectrum
```

频谱功能需 NumPy。`offset_hz` 以所填采样率为前提，`fraction_of_sample_rate` 是不依赖实际 Hz 标定的归一化位置。最强峰不一定就是信息波，不应直接拿它当作信道中心。

确认方法是：找当时录制流图或设备配置；再对照采样率、信道偏移、SPS 和有效 CRC 帧。只看到频谱，或者只发现访问码，都不足以确认完整参数。直接改变播放采样率会改变时长、码率和频谱；工具不自动重采样。

## 3. 不连接 SDR，先离线解码

Linux 使用与已安装 `gr-robomaster` 相同的 Python 环境。先检查候选设置：

```bash
python3 tools/rx/rm_sdr_file_rx.py --recording red --sample-rate 2000000 --mode describe
python3 tools/rx/rm_sdr_file_rx.py --recording blue --sample-rate 2000000 --mode describe
```

再运行单次离线解码：

```bash
python3 tools/rx/rm_sdr_file_rx.py --recording red --sample-rate 2000000 --fast
python3 tools/rx/rm_sdr_file_rx.py --recording blue --sample-rate 2000000 --fast
```

链路为 Complex File Source → 频移滤波 / 抽取 2 → 鉴频 → 原有浮点滤波 → 项目 OOT 解调 → 应用帧 CRC。三支路参数按当前红蓝流图构造，根目录文件不被导入或修改。

输出 `event=frame` 代表通过 CRC 的应用帧，包括命令字、序号和原始 payload；已知长度的坐标、血量、密钥还会展开显示。结束输出各信道的解调字节数与 CRC 统计。有有效帧返回 0，无有效帧返回 1，参数或依赖问题返回 2。不能将候选参数下“0 帧”解释为文件无信号或并非现场录制。

若实际录制参数与当前配置不同，可以缩小到单支路调整。例如下面只是“以 +450 kHz 为待验证中心”的用法示例，并不认定实际信道在那里：

```bash
python3 tools/rx/rm_sdr_file_rx.py --recording red --sample-rate 2000000 \
  --only info --info-offset 450000 --channel-width 200000 --sps 47 --fast
```

`--sps` 是**抽取 2 倍以后**每符号的采样数。密钥支路可选 `--only key1` / `key2` 并调整 `--key1-offset` / `--key2-offset`。第三份 `ganrao_3` 也是一段录波，不等同于现有第三个解调端口；可以选用任一密钥解调支路并设置它的实际偏移。

需要接入现有 ROS 时，先停掉占用相同端口的根目录接收流图，再启动现有 ROS 节点，并执行：

```bash
python3 tools/rx/rm_sdr_file_rx.py --recording red --sample-rate 2000000 --publish --repeat
```

`--publish` 按录制速度运行，将 OOT 原始 15 字节片段送到现有本机 5555、6666、6667 端口，ROS 集成无需改动。此时不能加 `--fast`。不加 `--publish` 就不绑定任何 ZeroMQ 端口。

## 4. 用 SDR 发射录波

确认录制采样率与宽带中心后，将它们作为 `--sample-rate` 和 `--frequency`。**下面的数字仍是当前 RX 候选值，只用于展示命令形式**：

```bash
python3 tools/tx/rm_sdr_replay.py --mode tx --recording red \
  --sample-rate 2000000 --frequency 432600000 \
  --uri ip:192.168.1.102 --attenuation 60
```

蓝方候选形式：

```bash
python3 tools/tx/rm_sdr_replay.py --mode tx --recording blue \
  --sample-rate 2000000 --frequency 434520000 \
  --uri ip:192.168.1.102 --attenuation 60
```

设备需提供 GNU Radio IIO 兼容的 AD936x 发射接口；上面的 URI 是项目已有 A3 地址，接收 E200 的地址仍由其接收程序配置。天线或带衰减同轴的连接方式见[模拟发射指南](模拟发射与回环联调.md)。

链路是 **Complex File Source → 幅度缩放 → IIO Sink**，没有 GFSK Mod，也没有 Throttle。波形已经包含调制，IIO Sink 提供硬件时钟。默认 TX1、60 dB 内部衰减、IQ 乘 1，不自动归一化；单次读完停止。重复 30 秒可加 `--repeat --duration 30`，Ctrl+C 可提前停止。`--tx-channel 2` 可换 TX2。

回放保留文件中的噪声、频偏与其他同时录到的信号；不会生成新的坐标或密钥。每次选择一个文件，工具不假设 8 份录波同步，也不自动把它们叠加。

发射前会执行抽样幅度检查；需要检查整个片段时加 `--full-scan`。`--amplitude` 调整 IQ 幅度，程序拒绝检查范围内缩放后超过归一化 DAC 分量范围的设置。内部衰减与外部射频测试通路按现有硬件联调方式配置。

宽带录波的 TX LO 对应录制 RX 中心。例如原录制中心若为 432.600 MHz、信息波在 +600 kHz，那么发射中心仍为 432.600 MHz，信息波自然落到 433.200 MHz；将 TX LO 填成 433.200 MHz 会使整个录波再向上搬移 600 kHz。

## 5. GNU Radio Companion

```bash
gnuradio-companion tools/tx/RM_SDR_REPLAY.grc
```

File Source 默认指向 `/home/user/code/Info/RX_RED`；播放蓝方时改为该目录下的 `RX_BLUE`，其他录波同理。核实 Complex 类型、Repeat、原采样率、原中心频率、URI 和衰减后运行；显示的红方采样率和频点仍仅为候选值。GRC 不读取索引、不做 Python 命令的采样检查，建议先用命令行检查录波。

人工造帧应使用 `tools/tx/RM_SDR_TX.grc`，其 File Source 是 Byte，后面接 GFSK Mod；实录 IQ 不应放进那个 Byte 输入。

## 验证范围

2026-11-19 在 Windows Python 3.12.10 下完成 23 项测试，包含 Linux/Windows 默认目录与显式目录覆盖、文件边界、采样值、CRC 流式重组、候选支路参数、已知频率信号的频谱和功率检查。8 份录波已完成全部采样扫描，未发现 NaN、Inf 或 I/Q 分量达到 0.98 的削顶；结果见 [recording_inspection.json](../reports/recording_inspection.json)。两份发射 GRC 已完成 YAML/JSON 解析、连接引用和输入类型检查。

当前机器的 MSYS Python 3.14 在长循环扫描中出现过异常，本次使用签名有效的 Python 官方便携版及校验哈希的 NumPy/PyYAML 测试包复核。临时环境仅位于 `tools/outputs/`，未修改系统安装或 PATH，也不纳入 Git。此 Windows 工作区可用以下命令检查录波：

```powershell
.\tools\outputs\python-runtime\python.exe tools\tx\rm_sdr_replay.py --recording red
```

开发环境当前没有 GNU Radio，Linux 文件解调、GRC 生成运行、SDR 发射及官方录波的实际 CRC 通过情况尚未验证。文件完整扫描不能确认原采样率或原中心频率，报告不将候选参数标成已确认。

API 对照：GNU Radio 3.10.1.1 的 [File Source](https://github.com/gnuradio/gnuradio/blob/v3.10.1.1/gr-blocks/grc/blocks_file_source.block.yml)、[分段循环实现](https://github.com/gnuradio/gnuradio/blob/v3.10.1.1/gr-blocks/lib/file_source_impl.cc)、[IIO Sink](https://github.com/gnuradio/gnuradio/blob/v3.10.1.1/gr-iio/grc/iio_fmcomms2_sink.block.yml)。
