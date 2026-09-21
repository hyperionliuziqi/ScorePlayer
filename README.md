# ScorePlayer Desktop v0.8

目标：**多页曲谱图片 → 本地 AI 乐谱识别 → 自动校错/人工修正 → 跟随谱面播放**。最终用户只需要解压并双击 `ScorePlayer.exe`，无需另装 Python、Java、Audiveris、HOMR 或 ONNX Runtime。

## v0.8 新增：播放时自动切页并高亮当前小节

HOMR 负责把乐谱变成 MusicXML；ScorePlayer v0.8 额外对原始页面做一遍**页面几何分析**：

1. 检测五线谱间距；
2. 找出上/下两行组成的 grand staff；
3. 找出每一条钢琴系统的横向范围；
4. 检测贯穿双谱表的小节线；
5. 处理同一高度左右分开的独立系统（例如 Coda 附近）；
6. 把 MusicXML 的第 N 小节映射回原始图片区域；
7. 播放或点击音符时，自动切换到对应页面并高亮该小节。

如果图像小节线数量与 MusicXML 小节数存在少量差异，程序不会整页错位，而会保留页面/系统顺序，在对应系统内部重新对齐，并在界面标记为“对齐”。

## 用本次 5 页《Avid》实际校准

v0.8 已直接对本次提供的五页图片运行谱面跟随几何检测：

| 页 | 谱距 | 钢琴系统 | 图像小节区域 |
|---:|---:|---:|---:|
| 1 | 8 px | 5 | 15 |
| 2 | 8 px | 6 | 17 |
| 3 | 8 px | 7 | 20 |
| 4 | 8 px | 6 | 19 |
| 5 | 8 px | 6 | 19 |

合计：**30 个钢琴系统、90 个图像小节区域**。

第 3 页中间存在左右分开的两段谱面，旧的“整行就是一个系统”思路会错；v0.8 已经能够把它们拆成两个独立系统。实际框选结果在 `calibration_overlay/` 中。

## v0.7 已有的人工校正能力继续保留

识别后可以选中任意音符并：

- 降 / 升半音
- 降 / 升八度
- 输入精确 MIDI 音高
- 时值 ÷2 / ×2
- 输入精确拍数
- 删除误识别音
- 单独试听
- 撤销 / 重做
- 恢复原始 OMR 结果

修改后会立即刷新播放、异常检测和 MIDI 导出。

## 自动校错

程序会主动标记：

- 超出 88 键钢琴范围
- 同一声部异常大跳
- 异常密集和弦
- 异常超宽和弦
- 同时刻同声部重复音
- 极端异常时值

双击异常项可跳到对应音符，并同步高亮原谱所属小节。程序只提示可疑点，不会擅自改谱。

## `.scoreplayer` 工程文件

工程文件会保存：

- 当前修正后的音符时间轴
- 原始页面图片
- 原始识别 MusicXML（如果存在）
- 页面预处理报告
- 当前修正结果

因此可以校正一半保存，下次继续。

## 已实现核心模块

- 多页 JPG / PNG 导入
- 页面白边裁剪、倾斜估计、谱距检测、自动缩放
- HOMR 本地 OMR 接口
- MusicXML 音高 / 和弦 / 时值 / 高低音谱表解析
- 反复记号、第一/第二结尾、D.C.、D.S.、Fine、To Coda
- 和弦同时播放
- 自动异常检测
- 人工音高/时值/删除修正
- 撤销 / 重做
- `.scoreplayer` 工程保存 / 打开
- 离线 WAV 合成播放
- 修正后 MIDI 导出
- **页面/系统/小节几何检测**
- **播放自动切页**
- **当前小节原谱高亮**
- Windows PyInstaller 自包含构建
- 打包后的 EXE 断网 OMR 实推理发布门禁

## 最终用户形态

```text
ScorePlayer-Windows-x64-portable-v0.8.zip
└── ScorePlayer/
    ├── ScorePlayer.exe
    ├── _internal/
    │   ├── HOMR
    │   ├── ONNX Runtime
    │   ├── AI 模型缓存
    │   ├── Qt
    │   └── Python runtime
    ├── README.md
    └── THIRD_PARTY_NOTICES.md
```

用户流程：

1. 解压；
2. 双击 `ScorePlayer.exe`；
3. 添加多页曲谱；
4. 点击“开始 AI 识别”；
5. 查看自动异常；
6. 必要时修正错音；
7. 点击播放；
8. 程序自动切页并框出当前小节；
9. 保存工程或导出 MIDI。

## Windows 构建

发布构建机需要 Windows + Python 3.12 + 网络；**最终用户不需要这些**。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

构建脚本会预下载 HOMR 模型并打包。随后关闭在线模型获取，再直接执行**打包后的 `ScorePlayer.exe`**，验证：

- MusicXML
- 和弦
- 反复路线
- WAV / MIDI
- v0.8 谱面小节几何检测
- HOMR / ONNX 加载
- 一次真实离线 OMR 推理

全部通过才生成 portable ZIP。

## 开发运行

```bash
pip install -r requirements-dev.txt
python -m scoreplayer.main
```

测试：

```bash
pytest
```

当前 v0.8 自动测试：**11 passed**。

## 尚未完成的最终验证

当前开发环境不是 Windows，因此还不能把源码包冒充成已经验证完成的 Windows EXE。最终仍需在 Windows runner / 实机完成：

- `ScorePlayer.exe` GUI 启动；
- PyInstaller 动态依赖完整性；
- 完全断网的 HOMR 推理；
- 这 5 页《Avid》的最终 OMR 音高/时值准确率；
- Windows 实际音频设备播放测试。

## 许可证

HOMR 使用 AGPL-3.0。发行包含 HOMR 的便携包时必须遵守相应开源许可并提供对应源码/许可信息。参见 `THIRD_PARTY_NOTICES.md`。
