# Third-party notices

## HOMR

ScorePlayer 的 OMR 引擎适配目标是 HOMR：

- Project: `liebharc/homr`
- Purpose: Optical Music Recognition, score image/PDF → MusicXML
- License: AGPL-3.0

ScorePlayer 的 Windows 构建脚本会通过 Python 包管理器安装 HOMR CPU 版本，并将其与最终便携版一起分发。任何实际分发应保留 HOMR 许可证文本、对应源码提供方式以及 HOMR 自身依赖的许可信息。

## ONNX Runtime

用于 HOMR 的 CPU 推理后端。

## PySide6 / Qt

用于 ScorePlayer 桌面界面与音频播放。

本文件不是法律意见。发布前应根据最终实际打包的依赖清单重新生成完整第三方许可证清单。
