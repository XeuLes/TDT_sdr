RM_sdr：Ubuntu 22.04 + ANTSDR E200 复现指南
==========================================

适用设备：优先使用 ANTSDR E200，运行 Pluto/libiio 兼容固件。AIDOSDR A3 可以后续替换接收设备，前提是其当前固件暴露兼容的 AD936x IIO 接口，并根据实际设备调整 URI 和接收通道。一台 SDR 即可供当前流图的三个接收支路使用。

本文按本地提交 `0b8aa69` 的源码及上游接口核对整理，尚未在你的 Ubuntu 和 SDR 上执行编译或射频实测。建议环境为 Ubuntu 22.04、ROS 2 Humble、系统 Python 3.10 和发行版提供的 GNU Radio 3.10.1.1。首次复现使用系统环境，在 Bash 中分别运行接收程序和 ROS 节点。

接收链路是：SDR 的 IQ 采样 → GNU Radio 信道化和 GFSK 解调 → ZeroMQ 字节流 → ROS 2 重组、CRC 校验和协议解析 → ROS 2 话题。

**1. 安装依赖**

尚未配置 ROS 软件源时，先按 [ROS 2 Humble 的 Ubuntu 安装文档](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debians.html) 配置软件源。下面的 `ros-humble-*` 安装命令以软件源已配置为前提。

```bash
sudo apt update
sudo apt install -y \
  git build-essential cmake pkg-config \
  gnuradio gnuradio-dev \
  libiio-dev libiio-utils libad9361-dev libzmq3-dev \
  python3-dev python3-numpy python3-scipy python3-pyqt5 \
  python3-zmq pybind11-dev

sudo apt install -y \
  ros-humble-ros-base ros-humble-rclcpp-components \
  ros-humble-ament-cmake-auto ros-humble-rosidl-default-generators \
  python3-colcon-common-extensions python3-rosdep
```

检查实际环境：

```bash
command -v python3
/usr/bin/python3 --version
gnuradio-config-info --version
/usr/bin/python3 -c "from gnuradio import gr, iio, zeromq, qtgui; print(gr.version())"
```

如果你的 GNU Radio 已经通过其他来源升级，按实际版本判断后面的 ZeroMQ 参数修改是否需要执行。Ubuntu 22.04 默认包版本是 3.10.1.1，仓库的 `.py` 则由 3.10.9.2 生成。

**2. 先确认 E200 能通过 IIO 连接**

E200 的 Pluto 固件出厂默认地址通常是 `192.168.1.10`。厂商文档说明部分出厂设备的 QSPI 中已有 Pluto 固件，启动方式需以你设备实际状态为准。当前工程使用 IIO Source；若设备运行 UHD 固件，需要先选择已有的 Pluto 兼容固件，或者另行适配 UHD 接收源。

通过网线连接 E200，将连接 SDR 的电脑网卡设为 `192.168.1.100/24`；直连时该网卡的网关和 DNS 可留空。设备地址修改过时，使用实际地址和相应网段。天线先接 RX1。

```bash
ping -c 3 192.168.1.10
iio_info -u ip:192.168.1.10
```

第二条命令应能列出 AD936x 射频控制设备及接收采样设备，例如 `ad9361-phy`、`cf-ad9361-lpc`。仅能 ping 通还不足以证明 IIO 采样可用。这一步失败时，先检查供电、固件、启动方式和网络。

**3. 放置源码并编译 GNU Radio 自定义模块**

以下假设当前这份工程放在 `~/RM_sdr`。建议直接在 Linux 上用 Git 拉取，避免复制 Windows 工作目录时带入 CRLF 换行，造成 Python 绑定头文件的哈希校验变化。

```bash
cd ~/RM_sdr
cmake -S gr-robomaster -B gr-robomaster/build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_STANDARD=17 \
  -DENABLE_PYTHON=ON \
  -DPYTHON_EXECUTABLE=/usr/bin/python3
cmake --build gr-robomaster/build --parallel 4
sudo cmake --install gr-robomaster/build
sudo ldconfig

/usr/bin/python3 -c "from gnuradio import robomaster; print(robomaster.gfsk_receiver, robomaster.gfsk_receiver_noise)"
```

最后一条命令应能打印两个接收块。出现 `ImportError` 时，先检查安装输出中的 Python 路径，以及 GNU Radio、OOT 模块和运行程序是否使用同一个 Python 环境。

**4. 处理 Ubuntu 22.04 的 cppzmq 构建兼容问题**

当前 `src/radio_recive/CMakeLists.txt` 使用 `find_package(cppzmq REQUIRED)`。Ubuntu 22.04 的 `libzmq3-dev` 已提供 `zmq.hpp`，但它的文件清单中没有 `cppzmqConfig.cmake`；仅安装该系统包会留下 CMake 查找问题。

对于上述系统依赖组合，将该文件中的：

```cmake
find_package(cppzmq REQUIRED)
```

替换为：

```cmake
find_package(PkgConfig REQUIRED)
pkg_check_modules(ZMQ REQUIRED IMPORTED_TARGET libzmq)
```

然后将 `target_link_libraries(radio_recive_component ...)` 中的 `cppzmq` 替换为 `PkgConfig::ZMQ`，其他链接项保留。C++ 仍通过系统安装的 `zmq.hpp` 使用 cppzmq 接口，链接到 `libzmq`。

如果你已单独安装了提供 CMake 配置的 cppzmq，并且 `find_package` 正常，则可以保留原写法。

**5. 编译 ROS 2 包**

```bash
cd ~/RM_sdr
source /opt/ros/humble/setup.bash

# rosdep 尚未初始化时执行初始化。
if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then
  sudo rosdep init
fi
rosdep update
rosdep install --from-paths src --ignore-src --rosdistro humble -y

colcon build --base-paths src --symlink-install \
  --cmake-args -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_STANDARD=17
source install/setup.bash
ros2 pkg executables radio_recive
```

应能看到 `radio_recive radio_recive_node`。`--base-paths src` 限定只构建 ROS 包；GNU Radio OOT 已在上一步单独安装。仓库中的包名确实拼作 `radio_recive`。

**6. 适配接收程序的设备和 GNU Radio 版本**

首次可以直接编辑要运行的 `.py`：红方为 `GFSK_RX_RED.py`，蓝方为 `GFSK2_RX_BLUE.py`。以后通过 GRC 重新生成程序时，需要把相同修改同步到 `.grc`，否则手工修改会被覆盖。

两个程序当前均使用 `ip:192.168.2.1`，并仅启用 RX2。按上面的 E200 默认地址和 RX1 接法，将 IIO 接收源改为：

```python
self.iio_fmcomms2_source_0 = iio.fmcomms2_source_fc32(
    'ip:192.168.1.10', [True, True, False, False], 65536)
```

`[True, True, False, False]` 启用 RX1 的 I/Q。把紧接着的两个增益配置 `if` 分支替换成以下调用，其他频率、采样率及校正设置保留：

```python
self.iio_fmcomms2_source_0.set_gain_mode(0, 'slow_attack')
```

这样 AGC 配置也作用于 RX1。若使用手动增益，需要另外把模式切为 `manual` 后再设增益；当前 `slow_attack` 下不应把源码中的 `64` 当作实际固定增益。

对 GNU Radio **3.10.1.1**，每个程序的三处 `zeromq.pub_sink(...)` 需要删去末尾的两个 `True`，例如主数据端口改为：

```python
self.zeromq_pub_sink_0 = zeromq.pub_sink(
    gr.sizeof_char, 1, 'tcp://127.0.0.1:5555',
    100, False, -1, '')
```

`6666`、`6667` 两处同样处理。上游 3.10.1.1 的构造接口有七个参数，仓库生成程序传入九个参数，会出现参数不匹配的 `TypeError`。对于其他版本，先查看实际接口再决定是否修改：

```bash
/usr/bin/python3 -c "from gnuradio import zeromq; print(zeromq.pub_sink.__init__.__doc__)"
```

红方程序另有启用的录波 File Sink，路径固定为 `/home/tdt/radio_data/8.4.bin`。首次复现建议删除或注释以下三处语句：

```python
self.blocks_file_sink_0 = blocks.file_sink(...)
self.blocks_file_sink_0.set_unbuffered(False)
self.connect((self.iio_fmcomms2_source_0, 0), (self.blocks_file_sink_0, 0))
```

这里的 `...` 表示定位原语句，不是可替换进去的运行代码。如果需要录波，保留原语句并改成实际可写路径，提前创建父目录。当前 complex float32 IQ、2 MSa/s 的原始文件增长速度约为 **16 MB/s，960 MB/分钟**。

**7. 确认频率和信号条件**

下面是当前代码默认值，需按实际信号源核对，不能当作所有比赛年度和场地的固定配置。

| 参数 | 红方程序 | 蓝方程序 |
| --- | --- | --- |
| SDR 射频中心 | 432.600 MHz | 434.520 MHz |
| 信息波数字信道偏移 | +600 kHz | −600 kHz |
| 信息波中心，由配置推算 | 433.200 MHz | 433.920 MHz |
| 两条干扰支路偏移 | −400 / −100 kHz | +400 / +100 kHz |
| SDR 实际采样率 | 2 MSa/s | 2 MSa/s |
| 信道抽取倍数 | 2 | 2 |
| 解调输入采样率 | 1 MSa/s | 1 MSa/s |
| 每符号采样数 SPS | 47 | 47 |
| 主 / 干扰检测门限 | 1100 / 900 | 1100 / 900 |

首次先保留采样率、抽取率、SPS 和门限，确认硬件正常采样、目标频点正确后再调参。接收块的 SPS 目前直接写成构造参数 `47`；仅修改顶部 `self.sps` 不会同步修改接收块。

需要附近有与该协议匹配的 RoboMaster 无线电广播，或已有匹配的 IQ 录波，才能验收解码。普通广播、任意 433 MHz 信号以及仅接上两台 SDR 都不能提供所需协议数据。该仓库没有附带 IQ 样本，也没有 README 所列的 `test.py`、`GFSK2_RX_TEST.grc/.py`。

无信号源时，可以按 [模拟发射与回环联调](模拟发射与回环联调.md) 使用新增的软件回环和 SDR 发射工具。当前 `bin.py` 生成的 `0x0A05` 数据段为 41 字节，而附带的官方 2026 V1.3.0 文档规定 36 字节，现有 ROS 接收器也要求 41 字节；因此新增工具先采用坐标、血量和测试密钥帧。

该工程面向 T-DT 2026 的广播协议。用于 2027 时，应对照当年协议核对频点、调制参数、分片/组帧方式及 `0x0A01`～`0x0A06` 字段。

**8. 分终端启动并验收**

终端 A，启动 ROS 接收节点：

```bash
cd ~/RM_sdr
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 run radio_recive radio_recive_node
```

终端 B，在 Linux 桌面图形会话中启动一套 GNU Radio 接收程序：

```bash
cd ~/RM_sdr
/usr/bin/python3 GFSK_RX_RED.py
# 若要使用蓝方，停止红方后改运行：
# /usr/bin/python3 GFSK2_RX_BLUE.py
```

红蓝程序占用同一台设备，并绑定相同端口，一次运行一套。当前生成程序的图形窗口可能没有频谱控件；窗口出现只说明 GUI 启动，不代表已经解码。

终端 C，加载相同 ROS 环境后检查：

```bash
cd ~/RM_sdr
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 topic list
ros2 topic echo /radio --once
ros2 topic echo /robot_position --once
ros2 topic hz /robot_position
```

后面几条命令分别运行；没有相应数据时会等待。验收含义如下：

| 观察结果 | 已验证的部分 |
| --- | --- |
| `iio_info` 能列出设备 | IIO 控制连接正常，仍需验证持续采样 |
| 接收流图正常运行，添加频谱显示后能观察目标信号 | SDR 采样及射频频点具备调试基础 |
| `/radio` 有十六进制字节输出 | 解调后的字节已通过 ZeroMQ 到达 ROS，尚不能证明 CRC 通过 |
| `/robot_position` 等业务话题有有效数据 | 对应帧已完成 CRC 校验及协议解析 |

可观察的话题还有 `/radio_hp`、`/radio_fire`、`/radio_state`、`/radio_buff` 和 `/key_usart_sender`，是否有消息取决于实际收到的命令帧。`key_usart_sender` 在本仓库中是发布密钥的 ROS 话题，源码未实现实际串口发送。

当前 GNU Radio 程序发布 `5555`、`6666`、`6667`；ROS 节点额外订阅 `6668`，但默认流图没有启用对应输出。只收到前面三个端口的数据不代表少启动了一个进程。

首次成功后再使用 `red.sh` / `blue.sh` 整合启动。这两个脚本使用 Zsh，并会在发现 Miniconda 时自动激活 `sdr` 环境，需要保证其选择的环境就是前面编译安装的环境。

**9. 按现象定位故障**

| 现象 | 优先排查 |
| --- | --- |
| `iio_info` 无法打开设备 | 设备实际 IP、网卡网段、供电、固件是否支持 IIO |
| 设备可连接，但采样通道报错 | 当前固件开放的 RX 通道与布尔通道掩码是否一致 |
| `No module named robomaster` / `cannot import name robomaster` | OOT 安装位置与 Python/GNU Radio 环境是否一致 |
| 找不到 `cppzmqConfig.cmake` | 第 4 步的 pkg-config 替换，或已安装 cppzmq 的查找路径 |
| `pub_sink` 构造参数 `TypeError` | 第 6 步的七参数兼容修改 |
| 红方报文件打不开 | 固定录波路径和父目录，或禁用 File Sink |
| 流图运行但 `/radio` 无消息 | 目标信号是否存在、RX 天线接口、频点、采样率和同步参数 |
| `/radio` 有数据但业务话题没消息 | CRC、帧重组、协议版本、命令字及数据长度 |
| `could not connect to display` | 使用桌面图形会话运行 Qt 程序，或另行适配无 GUI 接收入口 |

依据：[E200 厂商开箱与固件说明](https://antsdr-docs.microphase.cn/en/latest/cn/device_and_usage_manual/ANTSDR_E_Series_Module/ANTSDR_E200_Reference_Manual/AntsdrE200_Unpacking_examination_cn.html)、[Ubuntu 22.04 的 GNU Radio 包](https://packages.ubuntu.com/jammy/gnuradio)、[Ubuntu 22.04 的 libzmq3-dev 文件清单](https://packages.ubuntu.com/jammy/amd64/libzmq3-dev/filelist)、[GNU Radio 3.10.1.1 的 pub_sink 接口](https://github.com/gnuradio/gnuradio/blob/v3.10.1.1/gr-zeromq/include/gnuradio/zeromq/pub_sink.h)。
