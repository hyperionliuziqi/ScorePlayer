# Build status — v0.8

## 当前环境已验证

- Python 源码编译通过
- MusicXML 解析
- 高低音谱表事件
- 和弦同一时间点
- 升降音
- 反复路线展开
- 自动异常检测
- 人工 transpose / duration / delete
- undo / redo
- `.scoreplayer` 工程保存与恢复
- MIDI 文件生成
- 离线 WAV 生成
- 曲谱图片预处理
- 页面/钢琴系统/小节区域检测
- 第 3 页左右分离系统检测
- 用户提供 5 页实际几何回归
- `pytest`: **11 passed**

## 5 页实际几何回归

- Page 1: 5 systems / 15 measure regions
- Page 2: 6 systems / 17 measure regions
- Page 3: 7 systems / 20 measure regions
- Page 4: 6 systems / 19 measure regions
- Page 5: 6 systems / 19 measure regions
- Total: **30 systems / 90 measure regions**

## Windows 发布门禁

PyInstaller 完成后，构建脚本会禁止在线模型下载并直接运行：

```text
ScorePlayer.exe --self-test --require-engine --omr-smoke <test image>
```

v0.8 的打包后 self-test 现在还会额外验证谱面小节几何检测。

只有 EXE 本体能够：

1. 解析 MusicXML；
2. 处理和弦/路线；
3. 生成 WAV/MIDI；
4. 检测页面小节区域；
5. 加载 HOMR / ONNX / 内置模型；
6. 完成一次真实断网 OMR；

才允许生成最终 portable ZIP。

## 当前环境无法替代的 Windows 实机验证

- `ScorePlayer.exe` GUI 实机启动
- PyInstaller 最终动态依赖收集
- 真正断网机器上的 HOMR 推理
- 5 页复杂《Avid》的最终 OMR 音符准确率
- Windows 音频设备播放

因此当前交付仍是**源码 + 完整 Windows 构建工程**，不是伪装成最终成品的未验证 EXE。
