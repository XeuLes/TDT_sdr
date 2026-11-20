# RM 无线电工具

提供官方参数造波、现场 IQ 回放、软件环回和工程接收启动入口。官方造波采用 **RMUC 2026 规则 V2.2.0 + 通信协议 V2.0.0**；2027 V1.0.0 尚未公布完整电磁波参数，不将这组数值标为 2027 最终参数。

```text
tools/
  tx/official_tx.py       官方参数造波并发射
  tx/replay_tx.py         现场 IQ 文件回放发射
  rx/project_rx.py        启动工程原有接收流图
  test/loopback.py        无硬件软件环回，使用 gr-robomaster
  config/messages.json   可编辑的红蓝广播数据
  config/power.json      发射功率校准
  config/recordings.json  录波位置、实测参数、回放频率
  sdr_tools/             共用协议、造波与运行代码
  tests/                 核心自动检查
  PARAMETERS.md          官方参数、录波参数、命令选项
```

## 环境和目录

在已安装项目依赖的 Linux 系统 Python 中运行。软件环回需要 GNU Radio、NumPy 和 `gr-robomaster`；硬件发射另需 GNU Radio IIO，工程接收还使用项目原有的 Qt/IIO/ZeroMQ 环境。

```bash
cd /home/user/code/RM_str
python3 -c "from gnuradio import gr, robomaster; print('ready')"
```

原始录波放在 `/home/user/code/Info`，不需要复制到工程。Windows 的默认录波位置为工程同级的“信息波录制”。目录不同时，回放和录波环回可加 `--recording-dir /实际目录`。

发射器默认使用 A3 的 `ip:192.168.1.102`、TX1；接收器默认沿用工程的 E200 URI 和 RX 配置。TX 与 RX 是独立进程，可运行在不同电脑上。

## 1. 官方参数造波发射

先运行接收端，再在发射端选择阵营和波形。以下 `--attenuation 60` 是手动台架衰减，绝对 dBm 尚需测量；严格功率运行方式见“功率校准”。

```bash
# 红方信息波
python3 tools/tx/official_tx.py --side red --wave info --attenuation 60

# 蓝方一级干扰波
python3 tools/tx/official_tx.py --side blue --wave jam1 --attenuation 60

# 红方三级干扰波
python3 tools/tx/official_tx.py --side red --wave jam3 --attenuation 60

# 信息波 + 一档干扰波同时发射
python3 tools/tx/official_tx.py --side red --wave info jam2 --attenuation 60
```

`--wave` 可选 `info`、`jam1`、`jam2`、`jam3`。可以单发，也可以选择 `info` 加一档干扰；三档干扰是互选等级。红蓝阵营通过 `--side red|blue` 选择。

默认发射 10 秒；加 `--duration 30` 改为 30 秒，`--duration 0` 持续到 Ctrl+C。设备地址不同时加 `--uri ip:实际地址`。

信息波和干扰波组合时，IQ 保留官方定义的 50 dB 相对功率差。信息波使用 `0x0A01` 至 `0x0A05`，各 10 Hz、合计 1400 byte/s；干扰波每秒含 10 个 `0x0A06`，其余为随机填充，合计 1350 byte/s。数据以 0.3 秒为周期循环，周期内包序号递增。15 字节分片跨应用帧和发送批次连续拼接，没有额外 AA 前导码或逐应用帧补零。

### 改变发送数据

编辑 `tools/config/messages.json`，保存后重启发射器。也可以复制为自己的 JSON，再用 `--data /路径/messages.json` 指定。

`red`、`blue` 表示广播源所在阵营；按官方定义，广播内容描述对方队伍。每组字段如下：

| 字段 | 内容 |
| --- | --- |
| `positions_cm` | 六组 `[x,y]`，顺序为英雄、工程、3号步兵、4号步兵、空中、哨兵，单位 cm |
| `hp` | 六个血量，顺序为英雄、工程、3号步兵、4号步兵、保留位、哨兵；保留位填 0 |
| `ammo` | 五个允许发弹量，顺序为英雄、3号步兵、4号步兵、空中、哨兵 |
| `status` | `remaining_gold` 剩余金币、`total_gold` 累计金币、`flags` 场地状态低 16 位 |
| `buffs` | 五行，顺序为英雄、工程、3号步兵、4号步兵、哨兵；每行为 `[回血,冷却,防御,负防御,攻击]` |
| `sentry_stance` | 哨兵姿态，1～6 |
| `robot_states` | 上述五台地面机器人的主要状态，0～3 |
| `key` | 6 个 ASCII 字母或数字，例如 `RED001` |

CRC、长度、访问码和分片由程序生成。`0x0A05` 按通信协议 V2.0.0 编为 **41 字节**，含姿态和五个机器人状态字节。

### 功率校准

官方功率目标为信息波 −60 dBm、干扰波 −10 dBm。硬件衰减单位 dB 与输出功率单位 dBm 不等价。

1. 选择阵营/波形，用 `--attenuation 30` 发射，测量所选载波在 RF 开启期间的输出功率。组合波测量最强的干扰载波，参考面与实际使用的射频通路保持一致。
2. 在 `tools/config/power.json` 填写对应 `measured_burst_dbm`，例如 `red:info` 或 `red:info+jam1`。同时确认 URI、TX1、参考衰减 30 dB 与测量条件一致。
3. 发射时去掉 `--attenuation`，程序按校准值计算硬件衰减。若没有校准值，会在打开硬件前报错。

```bash
python3 tools/tx/official_tx.py --side red --wave info
```

手动指定 `--attenuation` 用于台架联调，此时程序会标记绝对功率未校准。校准计算仍需用仪表确认最终载波功率及组合波功率差。

## 2. 现场录波回放发射

```bash
python3 tools/tx/replay_tx.py --list

# 单次播放 RX_RED
python3 tools/tx/replay_tx.py --recording red --attenuation 60

# 循环播放蓝方二级干扰 30 秒
python3 tools/tx/replay_tx.py --recording blue-ganrao-2 --repeat --duration 30 --attenuation 60
```

可直接使用的录波为 `red`、`blue`、`blue-ganrao-1`、`blue-ganrao-2`、`blue-ganrao-3`。采样率和发射本振从预设读取，不需要手填 SPS、频偏或 LO。

回放链路为 **CF32 文件 → IIO TX**，不再次 GFSK 调制、不重采样。文件中的数据、噪声和其他频谱分量一起回放；需要改变业务数据时使用官方造波入口。程序检查文件长度和 SHA-256，避免将已确认参数用于不同文件。

录波采用成功解码的 **2 MSa/s、104 个文件采样点/bit** 解释；接收端抽取 2 倍后应选择 **SPS=52**。完整参数和每个文件的发射频率见 [PARAMETERS.md](PARAMETERS.md)。红方三份干扰录波尚无可用预设，保留在索引和参数表中，不作为自动发射选择。

## 3. 软件环回：不连接 SDR

```bash
# 官方红方信息波 → gr-robomaster
python3 tools/test/loopback.py --side red --wave info

# 官方蓝方三级干扰 → gr-robomaster
python3 tools/test/loopback.py --side blue --wave jam3

# 信息波与干扰波的组合接收测试
python3 tools/test/loopback.py --side red --wave info jam1

# 现场录波 → gr-robomaster
python3 tools/test/loopback.py --recording red
python3 tools/test/loopback.py --recording blue-ganrao-3
```

生成数据时也可加 `--data /路径/messages.json`。接收滤波参数读取工程红蓝接收流图，物理解码直接调用项目的 `robomaster.gfsk_receiver` 或 `gfsk_receiver_noise`。录波时自动采用各文件的信道偏移和 SPS=52。

软件环回不启动 IIO、Qt、ROS 或 ZeroMQ。输出每个选定通道的 CRC 有效帧数和最多三条数据示例；官方造波还逐帧核对解出的内容是否与 JSON 一致。`passed=true` 表示所有选定通道均满足检查；较强组合干扰可能使信息波通道失败，结果按实际接收能力报告。

## 4. 无线接收：复用工程流图

```bash
# 官方参数信号，SPS 默认沿用工程的 47
python3 tools/rx/project_rx.py --side red
python3 tools/rx/project_rx.py --side blue

# 现场录波回放信号
python3 tools/rx/project_rx.py --side red --sps 52

# 第三档干扰：将原第二档干扰支路改接第三档
python3 tools/rx/project_rx.py --side blue --sps 52 --jam3
```

红蓝接收入口二选一；它们使用相同 ZeroMQ 端口。接收设备地址可用 `--uri ip:实际地址` 覆盖。

该入口加载工程的 `GFSK_RX_RED.py` / `GFSK2_RX_BLUE.py`。射频接收、频移滤波、鉴频、`gr-robomaster` 和 ZeroMQ 发布均使用工程原有链路。SPS、URI 和第三档干扰通过内存适配，工程文件不改写。

默认同时接收信息波、一级干扰、二级干扰；`--jam3` 将二级支路换成三级，发布端口仍为 6667。信息波、一级干扰分别保留 5555、6666。现有 ROS 后处理按工程方式独立启动。

## 参数检查和自动检查

四个入口都支持 `--describe`，只打印配置，不连接硬件；命令参数及固定值见 [PARAMETERS.md](PARAMETERS.md)。

```bash
python3 tools/tx/official_tx.py --side red --wave info jam1 --describe
python3 tools/tx/replay_tx.py --recording red --describe
python3 -B -m unittest discover -s tools/tests -v
```

自动检查中的真实 GNU Radio 环回需要已安装 `gr-robomaster`；未安装时该项显示跳过。真实射频收发和绝对功率需在设备上验收。
