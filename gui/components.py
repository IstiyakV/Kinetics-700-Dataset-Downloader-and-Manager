import os
from PyQt6.QtWidgets import (QTableWidget, QTableWidgetItem, QHeaderView, 
                             QProgressBar, QTextEdit, QMenu, QWidget, QHBoxLayout)
from PyQt6.QtGui import QTextCursor, QColor, QFont
from PyQt6.QtCore import Qt, pyqtSlot
from core.utils import format_size, format_time

class SegmentProgressTable(QTableWidget):
    """
    IDM-style Grid displaying active download connections, individual progress bars,
    download speeds, byte progress, and ETAs.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(5)
        self.setHorizontalHeaderLabels([
            "Segment", "Progress", "Speed", "Downloaded / Total", "ETA"
        ])
        
        # Grid layout properties
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # stretch progress bar
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(True)
        
        # Set column widths
        self.setColumnWidth(0, 80)
        self.setColumnWidth(2, 90)
        self.setColumnWidth(3, 180)
        self.setColumnWidth(4, 80)
        
        self.progress_bars = {}

    def init_segments(self, num_segments):
        """Initialises the table rows with empty progress bars for the designated segment count."""
        self.setRowCount(0)
        self.progress_bars.clear()
        self.setRowCount(num_segments)
        
        for i in range(num_segments):
            # Segment ID
            item_id = QTableWidgetItem(f"Conn #{i+1}")
            item_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setItem(i, 0, item_id)
            
            # Progress Bar cell container
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(4, 2, 4, 2)
            
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(True)
            layout.addWidget(bar)
            
            self.setCellWidget(i, 1, container)
            self.progress_bars[i] = bar
            
            # Speed
            item_speed = QTableWidgetItem("0.00 MB/s")
            item_speed.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setItem(i, 2, item_speed)
            
            # Downloaded / Total
            item_size = QTableWidgetItem("0 B / 0 B")
            item_size.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setItem(i, 3, item_size)
            
            # ETA
            item_eta = QTableWidgetItem("--:--:--")
            item_eta.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setItem(i, 4, item_eta)

    def update_segment(self, idx, downloaded, total, speed, eta):
        """Updates segment data inside cells dynamically from downloader thread signals."""
        if idx not in self.progress_bars:
            return
            
        # Update progress bar
        bar = self.progress_bars[idx]
        if total > 0:
            percentage = int((downloaded / total) * 100)
            bar.setValue(percentage)
        else:
            bar.setValue(0)
            
        # Update speed
        self.item(idx, 2).setText(f"{speed:.2f} MB/s")
        
        # Update downloaded size
        self.item(idx, 3).setText(f"{format_size(downloaded)} / {format_size(total)}")
        
        # Update ETA
        if eta > 0:
            self.item(idx, 4).setText(format_time(eta))
        else:
            self.item(idx, 4).setText("--:--:--")

    def reset_grid(self):
        """Cleans and resets the entire grid status."""
        self.setRowCount(0)
        self.progress_bars.clear()


class LogConsole(QTextEdit):
    """
    Interactive command-console looking text logger. Supports text colors,
    context clearing, and automatically scrolls to the end of logs.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setUndoRedoEnabled(False)
        
        # Styling for console
        font = QFont("Consolas", 10)
        self.setFont(font)
        
        # Custom color styling
        self.setStyleSheet("""
            QTextEdit {
                background-color: #050811;
                border: 1px solid #1E293B;
                border-radius: 6px;
                color: #A7F3D0;
                padding: 8px;
            }
        """)

    @pyqtSlot(str)
    def append_log(self, text):
        """Appends text logs, colorizes warnings/errors, and auto-scrolls to the bottom."""
        self.moveCursor(QTextCursor.MoveOperation.End)
        
        # Highlight warnings/errors/info
        if "[ERROR]" in text or "failed" in text.lower():
            color = "#EF4444"  # Red
        elif "[WARNING]" in text:
            color = "#F59E0B"  # Yellow/Orange
        elif "---" in text:
            color = "#38BDF8"  # Steel Blue
        elif "[SUCCESS]" in text or "successfully" in text.lower():
            color = "#10B981"  # Emerald Green
        else:
            color = "#A7F3D0"  # Sage/Cyan Default
            
        formatted_text = f"<span style='color:{color};'>{text}</span>"
        self.insertHtml(formatted_text + "<br>")
        self.moveCursor(QTextCursor.MoveOperation.End)

    def contextMenuEvent(self, event):
        """Adds custom clear context action to console."""
        menu = self.createStandardContextMenu()
        menu.addSeparator()
        
        clear_action = menu.addAction("Clear Console")
        clear_action.triggered.connect(self.clear)
        
        menu.exec(event.globalPos())
