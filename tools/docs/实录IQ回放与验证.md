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

## 3. 用 gr-robomaster 测试文件：不连接 SDR

这一步在 Linux 主机的软件中处理录波，不连接 E200 或发射设备，也不需要启动 ROS。它把原来的 SDR Source 换成 File Source，后面继续运行项目的鉴频、gr-robomaster 解调模块和应用帧 CRC。

先同步最新 `tools` 到 `/home/user/code/RM_str/tools`，使用与现有接收程序相同、已能导入 GNU Radio 和 `robomaster` 的 Python：

```bash
cd /home/user/code/RM_str
python3 tools/rx/test_recording.py --recording red
```

蓝方使用 `--recording blue`。按本次提供的 **47 μs/bit** 作为测试前提，默认只测试下面三组，三组在进入解调器时都是 **1 MSa/s、SPS=47**：

| 参数组 | 文件采样率 | 信息波偏移：红 / 蓝 | 抽取倍数 | 解调器 SPS |
| --- | --- | --- | --- | --- |
| `rm-sdr` | 2 MSa/s | +600 / -600 kHz | 2 | 47 |
| `whu-raw-47` | 3 MSa/s | +500 / -500 kHz | 3 | 47 |
| `info-centered-47` | 1 MSa/s | 0 / 0 Hz | 1 | 47 |

这三组比较的是录波保存位置，不是在猜比特时长。47 μs/bit 不能直接决定文件采样率：原始 3 MSa/s IQ 每 bit 有 141 点，抽取 3 倍后才是 47 点。

`whu-raw-47` 采用 WHU 流图的原始接收布局，并按本批信号的 47 μs 时序解码；这不表示原 WHU 脚本中的 SPS=52 就能解析新版信号。`info-centered-47` 假设已保存居中的 1 MSa/s 信息波 IQ。官方新旧版依据见[官方无线电参数说明](官方无线电参数说明.md)。

旧版 52 μs/bit 候选 `rm-sdr-52`、`whu-raw`、`info-centered` 仍可显式选择，但本批录波不再默认测试它们。

每组结束时，会看到类似下面的结果格式；数字仅为说明格式，不是实录文件的已测结果：

```text
CRC_VALID_FRAMES=8  DECODED_FRAGMENT_BYTES=360
```

- `CRC_VALID_FRAMES > 0`：这组配置通过 gr-robomaster 解出了 CRC 有效的应用帧；随后会显示最多三条数据示例。
- `DECODED_FRAGMENT_BYTES > 0`，但 CRC 为 0：物理解调已有片段输出，应用数据仍未通过校验。
- 两项都是 0：这些参数没有解出有效数据，不能据此判断录波没有信息波。
- `ERROR` 或导入失败：先解决软件环境或配置错误，不是无线接收失败。

所有候选都没通过，说明需要进一步检查采样率、信道位置、符号时序或数据格式。这个入口不会发射，不绑定 ZeroMQ 端口，只读原文件；详细日志及 JSON 汇总放在 `tools/outputs/rx-tests/`。

CRC 通过证明该组软件解码配置可用，不能仅凭它唯一恢复当时的绝对射频接收中心。完整记录可能有多种等价的采样率/滤波配置；发射频率仍须结合原录制中心或已知信道位置确定。

### 单独复测一个参数组

若汇总显示 `whu-raw-47` 通过，可以单独运行：

```bash
python3 tools/rx/rm_sdr_file_rx.py --recording red --profile whu-raw-47 --only info --fast
```

链路为：Complex File Source → 频移滤波 / 按所选倍数抽取 → 鉴频 → 浮点滤波 → gr-robomaster → 应用帧 CRC。`--fast` 尽快处理文件；不加它就按录制采样率限速。输出的 `event=frame` 是 CRC 有效应用帧，`event=summary` 是结束统计。

`--mode describe` 只打印这组配置，不读取波形进行解码，也不是必须先执行的步骤：

```bash
python3 tools/rx/rm_sdr_file_rx.py --recording red --profile whu-raw-47 --mode describe
```

需要手动调参时，可以覆盖 `--sample-rate`、`--info-offset`、`--channel-width`、`--transition-width`、`--decimation` 和 `--sps`；SPS 指所选抽取之后、到达解调器的每符号采样数。工具检查滤波带宽，避免因抽取过多而混叠。未指定 `--profile` 的旧命令仍按 RM_sdr 的抽取 2 倍、SPS=47 解释。

### 已经解码成功后接入现有 ROS

只在已通过测试的参数组上接入。先停掉占用相同 ZeroMQ 端口的根目录接收流图，启动原 ROS 节点，再执行，例如：

```bash
python3 tools/rx/rm_sdr_file_rx.py --recording red --profile whu-raw-47 --only info --publish --repeat
```

`--publish` 按实时速度把 OOT 原始 15 字节片段送往现有本机端口；此时去掉 `--fast`。信息波端口为 5555，密钥支路为 6666、6667。密钥可通过 `--only key1` / `key2` 及相应偏移参数单独测试；第三份 `ganrao_3` 文件不能仅凭名字就认定对应当前某个固定解调支路。

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

2026-11-19 在 Windows Python 3.12.10 下完成 27 项测试，包含 Linux/Windows 默认目录与显式目录覆盖、文件边界、采样值、CRC 流式重组、六组候选配置、默认 47 μs 时序及抽取后的带宽检查、混合控制台日志中的 CRC 汇总提取、已知频率信号的频谱和功率检查。8 份录波已完成全部采样扫描，未发现 NaN、Inf 或 I/Q 分量达到 0.98 的削顶；结果见 [recording_inspection.json](../reports/recording_inspection.json)。两份发射 GRC 已完成 YAML/JSON 解析、连接引用和输入类型检查。

当前机器的 MSYS Python 3.14 在长循环扫描中出现过异常，本次使用签名有效的 Python 官方便携版及校验哈希的 NumPy/PyYAML 测试包复核。临时环境仅位于 `tools/outputs/`，未修改系统安装或 PATH，也不纳入 Git。此 Windows 工作区可用以下命令检查录波：

```powershell
.\tools\outputs\python-runtime\python.exe tools\tx\rm_sdr_replay.py --recording red
```

开发环境当前没有 GNU Radio，Linux 文件解调、GRC 生成运行、SDR 发射及官方录波的实际 CRC 通过情况尚未验证。文件完整扫描不能确认原采样率或原中心频率，报告不将候选参数标成已确认。

API 对照：GNU Radio 3.10.1.1 的 [File Source](https://github.com/gnuradio/gnuradio/blob/v3.10.1.1/gr-blocks/grc/blocks_file_source.block.yml)、[分段循环实现](https://github.com/gnuradio/gnuradio/blob/v3.10.1.1/gr-blocks/lib/file_source_impl.cc)、[IIO Sink](https://github.com/gnuradio/gnuradio/blob/v3.10.1.1/gr-iio/grc/iio_fmcomms2_sink.block.yml)。
