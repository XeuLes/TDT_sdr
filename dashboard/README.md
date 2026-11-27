# SDR 接收 Dashboard

## 启动接收台

在连接 E200 的 Linux 主机上，使用原来能运行 GFSK 流图的 Python 环境，从 RM_sdr 根目录运行：

```bash
python3 -m dashboard
```

启动时 Dashboard 运行 `ros2 run radio_recive radio_recive_node`。仓库中已有 `install/setup.bash` 时，接收节点会加载该工作区。若接收包安装在其他位置，从已加载相应 ROS 2 工作区的终端启动。接收节点的进程状态显示在页头。

打开 `http://127.0.0.1:8765`。页面初始等待选择阵营，点击「红方」运行 [GFSK_RX_RED.py](../GFSK_RX_RED.py)，点击「蓝方」运行 [GFSK2_RX_BLUE.py](../GFSK2_RX_BLUE.py)。Dashboard 通过自身的 `flowgraph.py` 调用原流图的 `main()`，并挂接监控观测点。URI、SPS、增益、LO 和滤波参数来自原流图。较旧的 GNU Radio 若拒绝新版 PUB 参数，启动层用相同的默认行为调用旧版接口。

切换时先退出当前受管流图，新进程送来宽带 IQ 后确认切换。新流图启动失败时会尝试恢复上一阵营。用 Ctrl+C 退出 Dashboard 服务时，回收本次启动的流图和接收节点。

Dashboard 使用 Python 和 PyZMQ。运行流图的 Python 还需要 GNU Radio、gr-iio、PyQt5、`gr-robomaster`、NumPy 和 PyZMQ。默认沿用启动 Dashboard 的 Python，也可用 `--rx-python /path/to/python` 或 `PYTHON_BIN` 指定已有的流图环境。

## 频谱和解码数据

接收端从 E200 宽带复数 IQ 和各软件滤波支路取样，做 1024 点 Hann 窗 FFT，再经过 `tcp://127.0.0.1:5560` 发送频谱。频率轴按流图 LO、采样率和分支偏移计算，电平单位是 dBFS。`iio_attr` 回读 E200 的 RX1 硬件增益和模式，「检查 SDR」显示设备 URI、LO、采样率、IQ 到达时间和增益回读。宽带 IQ 削顶比例依据 I 或 Q 分量达到数字满量程 99% 的样本估算。

| 地址 | 用途 |
| --- | --- |
| `192.168.1.50/24` | 台架 Linux 主机的有线网卡 |
| `ip:192.168.1.11` | E200 接收端默认地址 |
| `127.0.0.1:5555 / 6666 / 6667` | 信息波、一级干扰、二级干扰的 ZeroMQ 分片 |
| `127.0.0.1:5560` | 接收遥测和 FFT |
| `http://127.0.0.1:8765` | Dashboard 网页和本机 HTTP 接口 |

无线电页显示五类信息帧、干扰密钥、最近到达、近一秒有效帧率及 CRC8、CRC16 和长度诊断。`radio_recive_node` 并行订阅原有分片，提供 ROS 2 业务话题。从同一局域网查看页面时可使用 `--host 192.168.1.50`。阵营切换和 HTTP 数据提交由 Dashboard 主机处理，也可通过 SSH 转发访问。

```bash
ssh -N -L 8765:127.0.0.1:8765 user@192.168.1.50
```

## 视觉、融合和裁判接入

地图有无线电坐标、视觉定位、融合决策三个可勾选图层。无线电坐标来自有效的 `0x0A01`。顶部同时显示信息波、干扰波、相机采集和视觉处理的帧率及累计帧数。

同机程序向 `http://127.0.0.1:8765` 的路径 POST `application/json`。收到 `200` 表示接收成功，可从 `/api/state` 或 `/api/events` 读取更新。已有对应的 ROS 2 发布者时，在相同的 `ROS_DOMAIN_ID` 和可导入 `rclpy` 的环境中运行 `python3 -m dashboard --ros-inputs`。话题类型为 `std_msgs/msg/String`，`data` 是同样的 JSON。

| HTTP `POST` 路径 | ROS 2 话题 | 提供字段 |
| --- | --- | --- |
| `/api/input/vision` | `/rm_radar_algorithm/telemetry` | `schema = shark.radar.telemetry.v1`，`vision` 指标、`robots` 定位快照 |
| `/api/input/fusion` | `/rm_radar_integration/status` | `schema = shark.radar.fusion.v1`，`robots.<ID>.final` 最终坐标、`send_allowed`、`send_block_reason`、`tx_0305`。可含 SHARK 的 `strategy` 和 `referee_serial` |
| `/api/input/referee` | `/rm_referee_serial_node/status` | `serial_open`、`port`、`effective_radio_side`、`detected_robot_id`、`rx_count`、`tx_count`、`last_rx`、`last_tx`、`last_error`、密钥验证冷却和 `last_algorithm_request` / `last_algorithm_ack` |
| `/api/input/referee-bridge` | `/rm_referee_serial_node/referee_bridge` | `type` 和 `payload`。支持 `GameStatus`、`DartStatus`、`RadarMarkProgress`、`RadarDecisionSync` 和 `RadarCommand0121` |

机器人 ID 使用 `R1/R2/R3/R4/R6/R7` 或对应的 `B` 系列。`frame_id` 可填写 `rm_field`。红方在下的底图上，右下为原点，`x` 沿长边向上，`y` 沿短边向左。坐标可用 SHARK 的 `position_cm` 或米制 `position_m`。可用 `valid: false` 或空 `robots` 清除无效点。视觉和融合各次提交为完整快照，超过 3 秒未更新时隐藏对应图层的点。

`vision.camera_fps` 和 `vision.processing_fps` 为相机采集和视觉处理帧率，处理帧率也接受 SHARK 的 `vision.fps`。绝对帧数由视觉进程提供 `vision.camera_frame_count` 和 `vision.processed_frame_count`。可随定位快照附上 Unix 秒 `source_time` 和目标 `observed_at`。页面的数据年龄以接收时间为准。

```bash
curl http://127.0.0.1:8765/api/input/vision -H 'Content-Type: application/json' -d '{
  "schema": "shark.radar.telemetry.v1", "side": "red", "frame_id": "rm_field",
  "vision": {"camera_fps": 29.8, "processing_fps": 27.4, "camera_frame_count": 3321, "processed_frame_count": 3120},
  "robots": {"B1": {"position_m": {"x": 12.7, "y": 7.2}, "valid": true, "state": "measured"}}
}'
```

融合坐标示例为 `"robots": {"B1": {"final": {"position_cm": {"x": 1280, "y": 730}, "valid": true, "source": "vision"}}}`。发送统计填入 `tx_0305.actual_rate_hz`、`tx_0305.success_count`、`tx_0305.request_count`，以串口写入结果统计成功数。

裁判数据按 RoboMaster 2026 通信协议 V2.0.0 和 SHARK 字段展示，涉及 `0x0001` 比赛阶段、`0x0105` 飞镖、`0x020C` 标记、`0x020E` 雷达状态、`0x0301/0x0121` 密钥验证和 `0x0305` 坐标发送。`0x020C` 的空中机器人字段显示特殊标识，其他对方机器人显示易伤门槛。`0x0A02` 的保留位从血量表省略。协议文件见本机的「裁判系统」资料目录。

## 验证

```bash
python3 -m dashboard.flowgraph red --describe
python3 -m dashboard --preview
python3 -B -m unittest discover -s dashboard/tests -v
```

`--describe` 读取对应原流图的配置。`--preview` 用于界面和四类外部输入联调，网页保留阵营按钮的预览状态。

接收面板布局、绘图和 2026 年红蓝赛场底图沿用 SHARK。发射控制见 [LabTX](../labtx/README.md)，版本和版权见 [SHARK 来源说明](../labtx/vendor/shark_labtx/README.md)。
