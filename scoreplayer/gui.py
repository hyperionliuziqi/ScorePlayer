from __future__ import annotations
import json
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QAction, QColor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QDoubleSpinBox, QFileDialog, QHBoxLayout,
    QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton, QProgressBar,
    QSlider, QSpinBox, QSplitter, QStatusBar, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QToolBar, QVBoxLayout, QWidget, QHeaderView,
    QGroupBox, QGridLayout
)

from scoreplayer.diagnostics import analyze_score
from scoreplayer.edit import ScoreEditSession
from scoreplayer.midi import export_midi
from scoreplayer.layout import FollowMap, build_follow_map
from scoreplayer.model import NoteEvent, Score
from scoreplayer.musicxml import parse_musicxml, event_times_seconds
from scoreplayer.omr import recognize_pages, homr_available
from scoreplayer.project import load_project, save_project
from scoreplayer.preview import ScoreImagePreview
from scoreplayer.synth import render_wav


class RecognitionWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(str, object)
    failed = Signal(str)

    def __init__(self, images: list[str], output_xml: str):
        super().__init__()
        self.images = images
        self.output_xml = output_xml

    def run(self):
        try:
            path, report = recognize_pages(
                self.images,
                self.output_xml,
                progress=lambda msg: self.progress.emit(msg),
            )
            self.finished_ok.emit(str(path), report)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ScorePlayer 乐谱自动演奏器 v0.8")
        self.resize(1500, 900)

        self.images: list[str] = []
        self.score: Score | None = None
        self.edit_session: ScoreEditSession | None = None
        self.musicxml_path: str | None = None
        self.preprocess_report: list[dict] = []
        self.current_project_path: str | None = None
        self.follow_map: FollowMap | None = None

        self.tempdir = tempfile.TemporaryDirectory(prefix="scoreplayer_ui_")
        self.temp = Path(self.tempdir.name)
        self.rendered_wav = self.temp / "preview.wav"
        self.single_note_wav = self.temp / "single_note.wav"

        self.audio = QAudioOutput()
        self.audio.setVolume(0.85)
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)

        self._build_ui()
        self._check_engine()

    def _build_ui(self):
        toolbar = QToolBar("Main")
        self.addToolBar(toolbar)

        add_action = QAction("添加曲谱图片", self)
        add_action.triggered.connect(self.add_images)
        toolbar.addAction(add_action)

        import_xml_action = QAction("导入 MusicXML", self)
        import_xml_action.triggered.connect(self.import_musicxml)
        toolbar.addAction(import_xml_action)

        toolbar.addSeparator()

        save_project_action = QAction("保存工程", self)
        save_project_action.triggered.connect(self.save_project_file)
        toolbar.addAction(save_project_action)

        load_project_action = QAction("打开工程", self)
        load_project_action.triggered.connect(self.open_project_file)
        toolbar.addAction(load_project_action)

        toolbar.addSeparator()

        export_xml_action = QAction("导出原始 MusicXML", self)
        export_xml_action.triggered.connect(self.export_musicxml)
        toolbar.addAction(export_xml_action)

        export_midi_action = QAction("导出修正后 MIDI", self)
        export_midi_action.triggered.connect(self.export_midi_file)
        toolbar.addAction(export_midi_action)

        toolbar.addSeparator()
        self.follow_action = QAction("播放跟随谱面", self)
        self.follow_action.setCheckable(True)
        self.follow_action.setChecked(True)
        self.follow_action.toggled.connect(self.on_follow_toggled)
        toolbar.addAction(self.follow_action)

        root = QWidget()
        outer = QVBoxLayout(root)
        self.setCentralWidget(root)

        split = QSplitter()
        outer.addWidget(split, 1)

        # left: page list
        left = QWidget()
        left_l = QVBoxLayout(left)
        title = QLabel("曲谱页面")
        title.setStyleSheet("font-weight:700;font-size:16px;")
        left_l.addWidget(title)
        self.page_list = QListWidget()
        self.page_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.page_list.currentRowChanged.connect(self.show_page)
        left_l.addWidget(self.page_list, 1)

        row = QHBoxLayout()
        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self.add_images)
        rm_btn = QPushButton("删除")
        rm_btn.clicked.connect(self.remove_page)
        row.addWidget(add_btn)
        row.addWidget(rm_btn)
        left_l.addLayout(row)

        self.recognize_btn = QPushButton("开始 AI 识别")
        self.recognize_btn.clicked.connect(self.recognize)
        self.recognize_btn.setMinimumHeight(42)
        left_l.addWidget(self.recognize_btn)

        self.engine_label = QLabel("")
        self.engine_label.setWordWrap(True)
        self.engine_label.setStyleSheet("color:#667085;font-size:12px;")
        left_l.addWidget(self.engine_label)
        split.addWidget(left)

        # center: score image
        center = QWidget()
        center_l = QVBoxLayout(center)
        self.preview = ScoreImagePreview()
        center_l.addWidget(self.preview, 1)
        self.page_hint = QLabel(
            "v0.8 会检测钢琴系统和小节线；播放时自动切页并高亮当前小节。"
        )
        self.page_hint.setStyleSheet("color:#667085;")
        center_l.addWidget(self.page_hint)
        split.addWidget(center)

        # right: events + diagnostics + report
        tabs = QTabWidget()

        notes_page = QWidget()
        notes_l = QVBoxLayout(notes_page)
        self.events_table = QTableWidget(0, 7)
        self.events_table.setHorizontalHeaderLabels(
            ["时间(s)", "音高", "时值(拍)", "小节", "谱表", "声部", "MIDI"]
        )
        self.events_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.events_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.events_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.events_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.events_table.itemSelectionChanged.connect(self.on_event_selected)
        self.events_table.cellDoubleClicked.connect(lambda _r, _c: self.preview_selected_note())
        notes_l.addWidget(self.events_table, 1)

        edit_group = QGroupBox("人工校正（修改后立即影响播放 / MIDI 导出）")
        edit_grid = QGridLayout(edit_group)

        self.pitch_down_oct = QPushButton("-8度")
        self.pitch_down = QPushButton("-半音")
        self.pitch_up = QPushButton("+半音")
        self.pitch_up_oct = QPushButton("+8度")
        self.pitch_down_oct.clicked.connect(lambda: self.transpose_selected(-12))
        self.pitch_down.clicked.connect(lambda: self.transpose_selected(-1))
        self.pitch_up.clicked.connect(lambda: self.transpose_selected(1))
        self.pitch_up_oct.clicked.connect(lambda: self.transpose_selected(12))
        edit_grid.addWidget(self.pitch_down_oct, 0, 0)
        edit_grid.addWidget(self.pitch_down, 0, 1)
        edit_grid.addWidget(self.pitch_up, 0, 2)
        edit_grid.addWidget(self.pitch_up_oct, 0, 3)

        edit_grid.addWidget(QLabel("精确 MIDI"), 1, 0)
        self.midi_spin = QSpinBox()
        self.midi_spin.setRange(0, 127)
        edit_grid.addWidget(self.midi_spin, 1, 1)
        midi_apply = QPushButton("应用音高")
        midi_apply.clicked.connect(self.apply_exact_midi)
        edit_grid.addWidget(midi_apply, 1, 2, 1, 2)

        self.duration_half = QPushButton("时值 ÷2")
        self.duration_double = QPushButton("时值 ×2")
        self.duration_half.clicked.connect(lambda: self.scale_selected_duration(.5))
        self.duration_double.clicked.connect(lambda: self.scale_selected_duration(2.0))
        edit_grid.addWidget(self.duration_half, 2, 0)
        edit_grid.addWidget(self.duration_double, 2, 1)

        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(1/128, 64.0)
        self.duration_spin.setDecimals(4)
        self.duration_spin.setSingleStep(.125)
        edit_grid.addWidget(self.duration_spin, 2, 2)
        duration_apply = QPushButton("应用时值")
        duration_apply.clicked.connect(self.apply_exact_duration)
        edit_grid.addWidget(duration_apply, 2, 3)

        preview_btn = QPushButton("试听此音")
        preview_btn.clicked.connect(self.preview_selected_note)
        delete_btn = QPushButton("删除误识别音")
        delete_btn.clicked.connect(self.delete_selected)
        edit_grid.addWidget(preview_btn, 3, 0, 1, 2)
        edit_grid.addWidget(delete_btn, 3, 2, 1, 2)

        undo_btn = QPushButton("撤销")
        redo_btn = QPushButton("重做")
        reset_btn = QPushButton("恢复全部原识别")
        undo_btn.clicked.connect(self.undo_edit)
        redo_btn.clicked.connect(self.redo_edit)
        reset_btn.clicked.connect(self.reset_all_edits)
        edit_grid.addWidget(undo_btn, 4, 0)
        edit_grid.addWidget(redo_btn, 4, 1)
        edit_grid.addWidget(reset_btn, 4, 2, 1, 2)

        notes_l.addWidget(edit_group)
        tabs.addTab(notes_page, "识别音符 / 校正")

        diag_page = QWidget()
        diag_l = QVBoxLayout(diag_page)
        diag_hint = QLabel("双击异常项可定位到对应音符。程序只提示可疑点，不会擅自改谱。")
        diag_hint.setStyleSheet("color:#667085;")
        diag_l.addWidget(diag_hint)
        self.diag_table = QTableWidget(0, 5)
        self.diag_table.setHorizontalHeaderLabels(["级别", "小节", "拍", "代码", "说明"])
        self.diag_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.diag_table.horizontalHeader().setStretchLastSection(True)
        self.diag_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.diag_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.diag_table.cellDoubleClicked.connect(self.locate_diagnostic)
        diag_l.addWidget(self.diag_table, 1)
        self.diag_summary = QLabel("暂无诊断结果")
        diag_l.addWidget(self.diag_summary)
        tabs.addTab(diag_page, "自动校错")

        report_page = QWidget()
        report_l = QVBoxLayout(report_page)
        self.report_text = QTextEdit()
        self.report_text.setReadOnly(True)
        report_l.addWidget(self.report_text)
        tabs.addTab(report_page, "预处理报告")
        split.addWidget(tabs)

        split.setSizes([230, 720, 520])

        # bottom transport
        transport = QHBoxLayout()
        self.play_btn = QPushButton("▶ 播放")
        self.pause_btn = QPushButton("⏸ 暂停")
        self.stop_btn = QPushButton("■ 停止")
        self.play_btn.clicked.connect(self.play)
        self.pause_btn.clicked.connect(self.pause)
        self.stop_btn.clicked.connect(self.stop)
        transport.addWidget(self.play_btn)
        transport.addWidget(self.pause_btn)
        transport.addWidget(self.stop_btn)

        transport.addWidget(QLabel("BPM"))
        self.bpm = QSpinBox()
        self.bpm.setRange(40, 240)
        self.bpm.setValue(96)
        self.bpm.valueChanged.connect(self.on_bpm_changed)
        transport.addWidget(self.bpm)

        self.position = QSlider(Qt.Horizontal)
        self.position.setRange(0, 0)
        self.position.sliderMoved.connect(self.player.setPosition)
        transport.addWidget(self.position, 1)

        self.time_label = QLabel("00:00 / 00:00")
        transport.addWidget(self.time_label)
        outer.addLayout(transport)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.hide()
        outer.addWidget(self.progress)

        self.setStatusBar(QStatusBar())

    def _check_engine(self):
        ok, msg = homr_available()
        self.engine_label.setText(msg)
        self.engine_label.setStyleSheet(
            "color:#067647;font-size:12px;" if ok else "color:#b42318;font-size:12px;"
        )
        self.statusBar().showMessage(msg)
        self.recognize_btn.setEnabled(ok)

    def add_images(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择曲谱图片", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if not paths:
            return
        self.images.extend(paths)
        self.current_project_path = None
        self.refresh_page_list()
        if self.page_list.currentRow() < 0:
            self.page_list.setCurrentRow(0)
        if self.score is not None:
            self.rebuild_follow_map()

    def refresh_page_list(self):
        self.page_list.clear()
        for idx, p in enumerate(self.images, 1):
            self.page_list.addItem(f"{idx:02d}  {Path(p).name}")

    def remove_page(self):
        row = self.page_list.currentRow()
        if 0 <= row < len(self.images):
            del self.images[row]
            self.refresh_page_list()
            if self.images:
                self.page_list.setCurrentRow(min(row, len(self.images)-1))
            else:
                self.preview.clear_source()
                self.preview.set_empty_text("添加曲谱图片后会显示在这里")
            if self.score is not None:
                self.rebuild_follow_map()

    def show_page(self, row: int):
        if not (0 <= row < len(self.images)):
            return
        self.preview.set_source(self.images[row])
        self._apply_selected_follow_highlight()

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def recognize(self):
        if not self.images:
            QMessageBox.information(self, "提示", "请先添加曲谱图片。")
            return
        self.progress.show()
        self.progress.setRange(0, 0)
        self.recognize_btn.setEnabled(False)
        out = self.temp / "recognized.musicxml"

        self.worker = RecognitionWorker(self.images, str(out))
        self.worker.progress.connect(self.statusBar().showMessage)
        self.worker.finished_ok.connect(self.on_recognition_finished)
        self.worker.failed.connect(self.on_recognition_failed)
        self.worker.start()

    def on_recognition_finished(self, path: str, report):
        self.progress.hide()
        self.recognize_btn.setEnabled(True)
        self.preprocess_report = report
        self.musicxml_path = path
        self.current_project_path = None
        self.load_musicxml(path)
        self.report_text.setPlainText(json.dumps(report, ensure_ascii=False, indent=2))
        self.statusBar().showMessage("识别完成。建议先查看‘自动校错’，再播放。", 8000)

    def on_recognition_failed(self, message: str):
        self.progress.hide()
        self.recognize_btn.setEnabled(True)
        QMessageBox.critical(self, "识别失败", message)
        self.statusBar().showMessage("识别失败。")

    def import_musicxml(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 MusicXML", "", "MusicXML (*.musicxml *.xml)"
        )
        if path:
            self.musicxml_path = path
            self.current_project_path = None
            self.load_musicxml(path)

    def load_musicxml(self, path: str):
        try:
            score = parse_musicxml(path)
        except Exception as exc:
            QMessageBox.critical(self, "MusicXML 解析失败", str(exc))
            return
        self.load_score(score)

    def load_score(self, score: Score):
        self.score = score
        self.edit_session = ScoreEditSession(score)
        self.rebuild_follow_map()
        self.refresh_events_and_diagnostics()
        self.invalidate_audio()

    def refresh_events_and_diagnostics(self, select_source_order: int | None = None):
        if self.score is None:
            self.events_table.setRowCount(0)
            self.diag_table.setRowCount(0)
            return

        timed = event_times_seconds(self.score, self.bpm.value())
        self.events_table.blockSignals(True)
        self.events_table.setRowCount(len(timed))
        selected_row = None
        for row, (event, start, _duration) in enumerate(timed):
            values = [
                f"{start:.3f}", event.pitch_name, f"{event.duration_beats:.4g}",
                str(event.measure), str(event.staff), event.voice, str(event.midi)
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, event.source_order)
                self.events_table.setItem(row, col, item)
            if select_source_order is not None and event.source_order == select_source_order:
                selected_row = row
        self.events_table.blockSignals(False)

        if selected_row is not None:
            self.events_table.selectRow(selected_row)
            self.events_table.scrollToItem(self.events_table.item(selected_row, 0))

        diagnostics = analyze_score(self.score)
        self.diag_table.setRowCount(len(diagnostics))
        warn_count = 0
        err_count = 0
        for row, d in enumerate(diagnostics):
            level = {"error": "错误", "warning": "警告", "info": "提示"}.get(d.severity, d.severity)
            values = [
                level,
                str(d.measure) if d.measure is not None else "—",
                f"{d.beat:.3f}" if d.beat is not None else "—",
                d.code,
                d.message,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, {"beat": d.beat, "midi": d.midi, "measure": d.measure})
                if d.severity == "error":
                    item.setBackground(QColor("#fee4e2"))
                elif d.severity == "warning":
                    item.setBackground(QColor("#fef0c7"))
                self.diag_table.setItem(row, col, item)
            if d.severity == "error":
                err_count += 1
            elif d.severity == "warning":
                warn_count += 1

        self.diag_summary.setText(
            f"音符 {len(self.score.events)} 个 · 错误 {err_count} · 警告 {warn_count}。"
            "这些只是异常提示，不会自动改动乐谱。"
        )

    def on_bpm_changed(self):
        self.invalidate_audio()
        # Time column changes when BPM override changes.
        current = self._selected_event_source_order()
        self.refresh_events_and_diagnostics(current)

    def _selected_event_index(self) -> int | None:
        row = self.events_table.currentRow()
        if self.score is None or row < 0 or row >= len(self.score.events):
            return None
        return row

    def _selected_event_source_order(self) -> int | None:
        row = self.events_table.currentRow()
        item = self.events_table.item(row, 0) if row >= 0 else None
        if item is None:
            return None
        value = item.data(Qt.UserRole)
        return int(value) if value is not None else None

    def on_event_selected(self):
        idx = self._selected_event_index()
        if idx is None or self.score is None:
            return
        e = self.score.events[idx]
        self.midi_spin.blockSignals(True)
        self.duration_spin.blockSignals(True)
        self.midi_spin.setValue(e.midi)
        self.duration_spin.setValue(e.duration_beats)
        self.midi_spin.blockSignals(False)
        self.duration_spin.blockSignals(False)
        self._follow_event(e)


    def rebuild_follow_map(self):
        self.follow_map = None
        self.preview.clear_highlight()

        if self.score is None or not self.images:
            self.page_hint.setText(
                "添加曲谱图片并识别后，播放时会自动跟随到对应小节。"
            )
            return

        target_measures = max(
            (event.measure for event in self.score.events),
            default=0,
        )
        if target_measures <= 0:
            return

        try:
            self.statusBar().showMessage("正在建立谱面跟随地图…")
            self.follow_map = build_follow_map(
                self.images,
                target_measure_count=target_measures,
            )
        except Exception as exc:
            self.follow_map = None
            self.page_hint.setText(f"谱面跟随地图建立失败：{exc}")
            return

        system_count = sum(
            len(page.systems)
            for page in self.follow_map.pages
        )
        raw = self.follow_map.detected_measure_count
        mapped = len(self.follow_map.regions)

        text = (
            f"谱面跟随：检测到 {system_count} 个钢琴系统、约 {raw} 个图像小节，"
            f"已映射 {mapped} 个 MusicXML 小节。"
        )
        if self.follow_map.warnings:
            text += " " + " ".join(self.follow_map.warnings)
        self.page_hint.setText(text)
        self.statusBar().showMessage("谱面跟随地图已建立。", 3500)
        self._apply_selected_follow_highlight()

    def on_follow_toggled(self, enabled: bool):
        if not enabled:
            self.preview.clear_highlight()
            return
        self._apply_selected_follow_highlight()

    def _apply_selected_follow_highlight(self):
        idx = self._selected_event_index()
        if (
            idx is None
            or self.score is None
            or idx >= len(self.score.events)
        ):
            self.preview.clear_highlight()
            return
        self._follow_event(self.score.events[idx], switch_page=False)

    def _follow_event(self, event: NoteEvent, switch_page: bool = True):
        if (
            not hasattr(self, "follow_action")
            or not self.follow_action.isChecked()
            or self.follow_map is None
        ):
            return

        region = self.follow_map.regions.get(event.measure)
        if region is None:
            self.preview.clear_highlight()
            return

        if switch_page and self.page_list.currentRow() != region.page_index:
            self.page_list.setCurrentRow(region.page_index)

        if self.page_list.currentRow() == region.page_index:
            mode_text = "检测" if region.mode == "detected" else "对齐"
            self.preview.set_highlight(
                (region.x0, region.y0, region.x1, region.y1),
                f"第 {event.measure} 小节 · {event.pitch_name} · {mode_text}",
            )

    def _after_edit(self, result, source_order: int | None = None):
        if not result.changed:
            self.statusBar().showMessage(result.message, 3000)
            return
        self.invalidate_audio()
        self.refresh_events_and_diagnostics(source_order)
        self.statusBar().showMessage(result.message + " 播放缓存已刷新。", 5000)

    def transpose_selected(self, semitones: int):
        idx = self._selected_event_index()
        if idx is None or self.edit_session is None or self.score is None:
            return
        source_order = self.score.events[idx].source_order
        self._after_edit(self.edit_session.transpose(idx, semitones), source_order)

    def apply_exact_midi(self):
        idx = self._selected_event_index()
        if idx is None or self.edit_session is None or self.score is None:
            return
        source_order = self.score.events[idx].source_order
        self._after_edit(self.edit_session.set_midi(idx, self.midi_spin.value()), source_order)

    def scale_selected_duration(self, factor: float):
        idx = self._selected_event_index()
        if idx is None or self.edit_session is None or self.score is None:
            return
        source_order = self.score.events[idx].source_order
        self._after_edit(self.edit_session.scale_duration(idx, factor), source_order)

    def apply_exact_duration(self):
        idx = self._selected_event_index()
        if idx is None or self.edit_session is None or self.score is None:
            return
        source_order = self.score.events[idx].source_order
        self._after_edit(self.edit_session.set_duration(idx, self.duration_spin.value()), source_order)

    def delete_selected(self):
        idx = self._selected_event_index()
        if idx is None or self.edit_session is None:
            return
        if QMessageBox.question(
            self, "删除音符", "确认删除当前识别音符？可用“撤销”恢复。"
        ) != QMessageBox.Yes:
            return
        self._after_edit(self.edit_session.delete(idx))

    def undo_edit(self):
        if self.edit_session:
            self._after_edit(self.edit_session.undo())

    def redo_edit(self):
        if self.edit_session:
            self._after_edit(self.edit_session.redo())

    def reset_all_edits(self):
        if not self.edit_session:
            return
        if QMessageBox.question(
            self, "恢复原识别", "确认丢弃本次所有人工修正，恢复到刚识别完成的状态？"
        ) != QMessageBox.Yes:
            return
        self._after_edit(self.edit_session.reset_all())

    def preview_selected_note(self):
        idx = self._selected_event_index()
        if idx is None or self.score is None:
            return
        e = self.score.events[idx]
        preview_score = Score(events=[
            NoteEvent(
                beat=0.0,
                duration_beats=max(.5, min(2.0, e.duration_beats)),
                midi=e.midi,
                velocity=e.velocity,
                part=e.part,
                staff=e.staff,
                voice=e.voice,
                measure=e.measure,
                source_order=e.source_order,
            )
        ])
        render_wav(preview_score, self.single_note_wav, bpm_override=96)
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(self.single_note_wav)))
        self.player.play()
        self.statusBar().showMessage(f"试听 {e.pitch_name}", 1800)

    def locate_diagnostic(self, row: int, _col: int):
        item = self.diag_table.item(row, 0)
        if item is None or self.score is None:
            return
        info = item.data(Qt.UserRole) or {}
        beat = info.get("beat")
        midi = info.get("midi")
        measure = info.get("measure")

        best = None
        for idx, e in enumerate(self.score.events):
            score = 0
            if measure is not None and e.measure == measure:
                score += 4
            if beat is not None and abs(e.beat - beat) < 1e-5:
                score += 8
            if midi is not None and e.midi == midi:
                score += 6
            if best is None or score > best[0]:
                best = (score, idx)
        if best and best[0] > 0:
            self.events_table.selectRow(best[1])
            self.events_table.scrollToItem(self.events_table.item(best[1], 0))

    def save_project_file(self):
        if self.score is None:
            QMessageBox.information(self, "提示", "先完成识别或导入 MusicXML。")
            return
        default = self.current_project_path or "ScorePlayer工程.scoreplayer"
        dest, _ = QFileDialog.getSaveFileName(
            self, "保存 ScorePlayer 工程", default,
            "ScorePlayer Project (*.scoreplayer)"
        )
        if not dest:
            return
        try:
            saved = save_project(
                dest,
                self.score,
                images=self.images,
                source_musicxml=self.musicxml_path,
                preprocess_report=self.preprocess_report,
            )
            self.current_project_path = str(saved)
            self.statusBar().showMessage(f"工程已保存：{saved.name}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def open_project_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 ScorePlayer 工程", "",
            "ScorePlayer Project (*.scoreplayer)"
        )
        if not path:
            return
        try:
            score, images, xml_path, report = load_project(path, self.temp / "projects")
        except Exception as exc:
            QMessageBox.critical(self, "工程读取失败", str(exc))
            return
        self.images = images
        self.musicxml_path = xml_path
        self.preprocess_report = report
        self.current_project_path = path
        self.refresh_page_list()
        if self.images:
            self.page_list.setCurrentRow(0)
        self.report_text.setPlainText(json.dumps(report, ensure_ascii=False, indent=2))
        self.load_score(score)
        self.statusBar().showMessage("工程已载入，人工修正也已恢复。", 5000)

    def invalidate_audio(self):
        self.player.stop()
        # Return source away from single-note preview or prior score WAV.
        self.player.setSource(QUrl())
        for p in (self.rendered_wav, self.single_note_wav):
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass

    def ensure_audio(self):
        if self.score is None:
            raise ValueError("还没有可播放的识别结果。")
        if not self.rendered_wav.exists():
            self.statusBar().showMessage("正在生成修正后的离线钢琴音频…")
            render_wav(self.score, self.rendered_wav, bpm_override=float(self.bpm.value()))
        self.player.setSource(QUrl.fromLocalFile(str(self.rendered_wav)))
        self.statusBar().showMessage("播放的是当前修正版本。", 3000)

    def play(self):
        try:
            self.ensure_audio()
            self.player.play()
        except Exception as exc:
            QMessageBox.warning(self, "无法播放", str(exc))

    def pause(self):
        self.player.pause()

    def stop(self):
        self.player.stop()

    def on_position(self, ms: int):
        self.position.blockSignals(True)
        self.position.setValue(ms)
        self.position.blockSignals(False)
        self._update_time(ms, self.player.duration())
        self.highlight_current_note(ms / 1000.0)

    def on_duration(self, ms: int):
        self.position.setRange(0, ms)
        self._update_time(self.player.position(), ms)

    def _update_time(self, current: int, total: int):
        def fmt(ms):
            s = max(0, ms // 1000)
            return f"{s//60:02d}:{s%60:02d}"
        self.time_label.setText(f"{fmt(current)} / {fmt(total)}")

    def highlight_current_note(self, seconds: float):
        if self.score is None or self.player.source().isEmpty():
            return
        timed = event_times_seconds(self.score, self.bpm.value())
        row_to_select = None
        for idx, (_event, start, dur) in enumerate(timed):
            if start <= seconds < start + max(dur, 0.05):
                row_to_select = idx
                break
            if start <= seconds:
                row_to_select = idx
        if row_to_select is not None:
            self.events_table.selectRow(row_to_select)
            self.events_table.scrollToItem(self.events_table.item(row_to_select, 0))

    def export_musicxml(self):
        if not self.musicxml_path:
            QMessageBox.information(self, "提示", "当前没有原始 MusicXML。")
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "导出原始 MusicXML", "score-original.musicxml", "MusicXML (*.musicxml)"
        )
        if dest:
            Path(dest).write_bytes(Path(self.musicxml_path).read_bytes())
            QMessageBox.information(
                self, "已导出",
                "已导出 OMR 原始 MusicXML。人工修正保存在 .scoreplayer 工程与导出的 MIDI/WAV 播放结果中。"
            )

    def export_midi_file(self):
        if self.score is None:
            QMessageBox.information(self, "提示", "当前没有识别结果。")
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "导出修正后的 MIDI", "score-corrected.mid", "MIDI (*.mid)"
        )
        if dest:
            export_midi(self.score, dest, bpm=float(self.bpm.value()))
            self.statusBar().showMessage("已导出当前修正版本 MIDI。", 5000)


def run():
    app = QApplication([])
    app.setApplicationName("ScorePlayer")
    app.setStyleSheet("""
        QMainWindow { background: #f7f8fa; }
        QPushButton { padding: 7px 10px; }
        QGroupBox { font-weight: 600; margin-top: 8px; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
    """)
    win = MainWindow()
    win.show()
    app.exec()
