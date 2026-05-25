import os
import shutil
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QGroupBox, QLabel, QLineEdit, QSpinBox, QComboBox, 
                             QCheckBox, QPushButton, QProgressBar, QFileDialog, 
                             QMessageBox, QSplitter, QTabWidget, QTableWidget, 
                             QTableWidgetItem, QHeaderView)
from PyQt6.QtCore import Qt, pyqtSlot, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from gui.components import SegmentProgressTable, LogConsole
from core.downloader import FileDownloader
from core.processor import ProcessingWorker, FolderProcessingWorker
from core.utils import check_disk_space, format_size, format_time

class MainWindow(QMainWindow):
    # Signals to execute the sequence of downloading and processing parts in a loop
    start_next_part = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kinetics-700 High-Speed Downsampler & Manager GUI")
        self.resize(1150, 800)
        
        # Application state
        self.hf_token = ""
        self.start_part = 1
        self.end_part = 22
        self.staging_path = ""
        self.destination_path = ""
        self.keep_source = False
        self.resolution = (112, 112)
        self.frame_count = 16
        self.num_connections = 8
        
        # Pipeline execution variables
        self.current_running_part = -1
        self.pipeline_active = False
        self.pipeline_paused = False
        self.is_resuming = False
        self.active_downloader = None
        self.active_processor = None
        self.download_retry_count = 0
        self.max_retries = 10
        
        # Stats tracking
        self.total_downloaded_raw = 0
        self.total_processed_size = 0
        
        # Folder processing state
        self.folder_worker = None
        self.folder_pipeline_active = False
        
        self._init_ui()
        self._load_qss()
        
        # Connect pipeline control signal
        self.start_next_part.connect(self._execute_pipeline_step)
        
        # Load last used configuration if it exists
        self._load_config()

    def _init_ui(self):
        # Main central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # 1. Header App Title Section
        header_layout = QHBoxLayout()
        title_container = QVBoxLayout()
        
        title_label = QLabel("KINETICS-700 DATASET MANAGER")
        title_label.setObjectName("title_label")
        subtitle_label = QLabel("Multi-Segment High-Speed Downsampler • Storage Buffer & Purge Pipeline")
        subtitle_label.setObjectName("subtitle_label")
        
        title_container.addWidget(title_label)
        title_container.addWidget(subtitle_label)
        header_layout.addLayout(title_container)
        header_layout.addStretch()
        
        # Saved space visual badge
        self.space_saved_label = QLabel("Saved Space: 0 B")
        self.space_saved_label.setStyleSheet("""
            color: #10B981; 
            font-weight: bold; 
            font-size: 14px; 
            background-color: #064E3B; 
            padding: 8px 16px; 
            border-radius: 6px;
            border: 1px solid #059669;
        """)
        header_layout.addWidget(self.space_saved_label)
        main_layout.addLayout(header_layout)
        
        # Add spatial breathing room between title header and tabs
        main_layout.addSpacing(15)
        
        # Create Tab Widget
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)
        
        # --- TAB 1: DOWNLOADER PIPELINE ---
        downloader_tab = QWidget()
        downloader_layout = QVBoxLayout(downloader_tab)
        downloader_layout.setContentsMargins(0, 0, 0, 0)
        
        # Vertical Splitter to divide top configuration/grid panel from bottom console
        main_splitter = QSplitter(Qt.Orientation.Vertical)
        downloader_layout.addWidget(main_splitter)
        
        # Left Panel (Controls + Multi-layered Progress Bars)
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 5, 0)
        
        # A. Configuration Group Box
        config_group = QGroupBox("1. Configuration Settings")
        config_layout = QVBoxLayout(config_group)
        config_layout.setSpacing(10)
        
        # Hugging Face Token Row
        token_layout = QHBoxLayout()
        token_label = QLabel("Hugging Face Token:")
        token_label.setFixedWidth(130)
        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("Enter HF Read/Write token (leave blank if public)")
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        token_layout.addWidget(token_label)
        token_layout.addWidget(self.token_input)
        config_layout.addLayout(token_layout)
        
        # Range, connections, and keep original checkbox row
        row2_layout = QHBoxLayout()
        
        range_label = QLabel("Part Range:")
        self.start_part_spin = QSpinBox()
        self.start_part_spin.setRange(1, 22)
        self.start_part_spin.setValue(1)
        
        to_label = QLabel("to")
        self.end_part_spin = QSpinBox()
        self.end_part_spin.setRange(1, 22)
        self.end_part_spin.setValue(22)
        
        conn_label = QLabel("DL Connections:")
        self.conn_spin = QSpinBox()
        self.conn_spin.setRange(1, 16)
        self.conn_spin.setValue(8)
        self.conn_spin.setToolTip("Number of parallel download connections. Cannot be changed mid-download when paused.")
        
        self.single_conn_cb = QCheckBox("Single Connection Mode")
        self.single_conn_cb.setToolTip("Download as a single ZIP with one connection, automatically merging previous parts.")
        self.single_conn_cb.toggled.connect(self._on_single_conn_toggled)
        
        row2_layout.addWidget(range_label)
        row2_layout.addWidget(self.start_part_spin)
        row2_layout.addWidget(to_label)
        row2_layout.addWidget(self.end_part_spin)
        row2_layout.addSpacing(15)
        row2_layout.addWidget(conn_label)
        row2_layout.addWidget(self.conn_spin)
        row2_layout.addSpacing(15)
        row2_layout.addWidget(self.single_conn_cb)
        row2_layout.addStretch()
        config_layout.addLayout(row2_layout)
        
        # Parameters toggle (Source Mode vs Downsampling Mode)
        source_mode_layout = QHBoxLayout()
        self.source_mode_cb = QCheckBox("Keep Original Spatiotemporal Parameters (Source Mode)")
        self.source_mode_cb.setToolTip("If active, bypasses Gaussian filtering & TSN temporal resize. Video is copied as raw.")
        self.source_mode_cb.toggled.connect(self._on_source_mode_toggled)
        source_mode_layout.addWidget(self.source_mode_cb)
        config_layout.addLayout(source_mode_layout)
        
        # Downsampling options row
        downsample_layout = QHBoxLayout()
        
        res_label = QLabel("Resolution:")
        self.res_combo = QComboBox()
        self.res_combo.addItems(["112 x 112", "224 x 224"])
        self.res_combo.setCurrentIndex(0)
        
        frames_label = QLabel("Frame Count:")
        self.frames_combo = QComboBox()
        self.frames_combo.addItems(["8", "16", "32"])
        self.frames_combo.setCurrentIndex(1)
        
        engine_label = QLabel("Hardware/Engine:")
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(["CPU (Standard)", "FFmpeg CLI (High Compatibility)"])
        self.engine_combo.setCurrentIndex(0)
        
        downsample_layout.addWidget(res_label)
        downsample_layout.addWidget(self.res_combo)
        downsample_layout.addSpacing(20)
        downsample_layout.addWidget(frames_label)
        downsample_layout.addWidget(self.frames_combo)
        downsample_layout.addSpacing(20)
        downsample_layout.addWidget(engine_label)
        downsample_layout.addWidget(self.engine_combo)
        downsample_layout.addStretch()
        config_layout.addLayout(downsample_layout)
        
        # Paths Setup Row (Staging HDD + Destination SSD)
        paths_layout = QVBoxLayout()
        
        # Staging
        staging_row = QHBoxLayout()
        staging_lbl = QLabel("Staging HDD Buffer:")
        staging_lbl.setFixedWidth(130)
        self.staging_input = QLineEdit()
        self.staging_input.setPlaceholderText("Select large HDD directory for zip downloads (~90 GB free needed)")
        staging_browse = QPushButton("Browse")
        staging_browse.setObjectName("browse_btn")
        staging_browse.clicked.connect(self._browse_staging)
        staging_row.addWidget(staging_lbl)
        staging_row.addWidget(self.staging_input)
        staging_row.addWidget(staging_browse)
        paths_layout.addLayout(staging_row)
        
        # Destination
        dest_row = QHBoxLayout()
        dest_lbl = QLabel("Final Output SSD:")
        dest_lbl.setFixedWidth(130)
        self.dest_input = QLineEdit()
        self.dest_input.setPlaceholderText("Select SSD output directory to store downsampled dataset")
        dest_browse = QPushButton("Browse")
        dest_browse.setObjectName("browse_btn")
        dest_browse.clicked.connect(self._browse_destination)
        dest_row.addWidget(dest_lbl)
        dest_row.addWidget(self.dest_input)
        dest_row.addWidget(dest_browse)
        paths_layout.addLayout(dest_row)
        
        config_layout.addLayout(paths_layout)
        left_layout.addWidget(config_group)
        
        # B. Control Actions Panel
        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(10, 10, 10, 10)
        
        self.start_btn = QPushButton("Start Pipeline")
        self.start_btn.setIconSize(self.start_btn.sizeHint())
        self.start_btn.clicked.connect(self._start_pipeline)
        
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setObjectName("pause_btn")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._on_pause_btn_clicked)
        
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("stop_btn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_pipeline)
        
        action_layout.addWidget(self.start_btn)
        action_layout.addWidget(self.pause_btn)
        action_layout.addWidget(self.stop_btn)
        left_layout.addLayout(action_layout)
        
        # C. Multi-Layered Progress Indicators Group (Crucial request)
        progress_group = QGroupBox("2. Real-Time Processing Metrics")
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.setSpacing(12)
        
        # 1. Part Download Progress
        download_lbl_layout = QHBoxLayout()
        self.download_title_lbl = QLabel("Total Part Download Progress:")
        self.download_stats_lbl = QLabel("0 B / 0 B (0.00 MB/s)")
        download_lbl_layout.addWidget(self.download_title_lbl)
        download_lbl_layout.addStretch()
        download_lbl_layout.addWidget(self.download_stats_lbl)
        
        self.download_progress = QProgressBar()
        self.download_progress.setFormat("%p%")
        
        progress_layout.addLayout(download_lbl_layout)
        progress_layout.addWidget(self.download_progress)
        
        # 2. Part Extraction Progress
        extract_lbl_layout = QHBoxLayout()
        self.extract_title_lbl = QLabel("Zip Archive Extraction Progress:")
        self.extract_stats_lbl = QLabel("0 / 0 files extracted")
        extract_lbl_layout.addWidget(self.extract_title_lbl)
        extract_lbl_layout.addStretch()
        extract_lbl_layout.addWidget(self.extract_stats_lbl)
        
        self.extract_progress = QProgressBar()
        self.extract_progress.setFormat("%p%")
        
        progress_layout.addLayout(extract_lbl_layout)
        progress_layout.addWidget(self.extract_progress)
        
        # 3. Video Processing / Spatiotemporal Downsampling Progress
        video_lbl_layout = QHBoxLayout()
        self.video_title_lbl = QLabel("Spatiotemporal Processing Progress:")
        self.video_stats_lbl = QLabel("0 / 0 videos processed")
        video_lbl_layout.addWidget(self.video_title_lbl)
        video_lbl_layout.addStretch()
        video_lbl_layout.addWidget(self.video_stats_lbl)
        
        self.video_progress_bar = QProgressBar()
        self.video_progress_bar.setFormat("%p%")
        
        # Currently processing item subtitle
        self.curr_processing_item_lbl = QLabel("Staging Idle...")
        self.curr_processing_item_lbl.setStyleSheet("color: #60A5FA; font-style: italic; font-size: 11px;")
        
        progress_layout.addLayout(video_lbl_layout)
        progress_layout.addWidget(self.video_progress_bar)
        progress_layout.addWidget(self.curr_processing_item_lbl)
        
        # 4. Master Job Progress
        master_lbl_layout = QHBoxLayout()
        self.master_title_lbl = QLabel("Overall Job Progress:")
        self.master_stats_lbl = QLabel("0 / 0 parts fully completed")
        master_lbl_layout.addWidget(self.master_title_lbl)
        master_lbl_layout.addStretch()
        master_lbl_layout.addWidget(self.master_stats_lbl)
        
        self.master_progress = QProgressBar()
        self.master_progress.setObjectName("master_progress")
        self.master_progress.setFormat("%p%")
        
        progress_layout.addLayout(master_lbl_layout)
        progress_layout.addWidget(self.master_progress)
        
        left_layout.addWidget(progress_group)
        left_layout.addStretch()
        
        # Right Panel (IDM grid only)
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(5, 0, 0, 8)
        
        connections_group = QGroupBox("Active Parallel Segment Downloader Grid")
        connections_layout = QVBoxLayout(connections_group)
        connections_layout.setContentsMargins(10, 15, 10, 10)
        
        self.connections_table = SegmentProgressTable()
        connections_layout.addWidget(self.connections_table)
        right_layout.addWidget(connections_group)
        
        # Horizontal top splitter to divide config/progress on left from connections grid on right
        top_splitter = QSplitter(Qt.Orientation.Horizontal)
        top_splitter.addWidget(left_container)
        top_splitter.addWidget(right_container)
        top_splitter.setSizes([500, 650])
        
        # Bottom Panel (Interactive operations console log)
        bottom_container = QWidget()
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 8, 0, 0)
        
        console_group = QGroupBox("Interactive Operations Console Log")
        console_layout = QVBoxLayout(console_group)
        
        self.console = LogConsole()
        console_layout.addWidget(self.console)
        bottom_layout.addWidget(console_group)
        
        # Add panels to vertical main splitter
        main_splitter.addWidget(top_splitter)
        main_splitter.addWidget(bottom_container)
        main_splitter.setSizes([520, 200])
        
        # --- TAB 2: DIRECT FOLDER DOWNFRAMER ---
        folder_tab = QWidget()
        folder_tab_layout = QVBoxLayout(folder_tab)
        folder_tab_layout.setContentsMargins(0, 10, 0, 0)
        
        folder_splitter = QSplitter(Qt.Orientation.Vertical)
        folder_tab_layout.addWidget(folder_splitter)
        
        # Folder Top Panel (Configuration & Metrics)
        folder_left_container = QWidget()
        folder_left_layout = QVBoxLayout(folder_left_container)
        folder_left_layout.setContentsMargins(0, 0, 0, 8)
        
        # Folder Group Box 1: Configuration Settings
        folder_config_group = QGroupBox("1. Folder Configuration")
        folder_config_layout = QVBoxLayout(folder_config_group)
        folder_config_layout.setSpacing(10)
        
        # Source Directory Row
        src_row = QHBoxLayout()
        src_lbl = QLabel("Source Directory:")
        src_lbl.setFixedWidth(130)
        self.folder_source_input = QLineEdit()
        self.folder_source_input.setPlaceholderText("Select raw videos source folder...")
        folder_src_browse = QPushButton("Browse")
        folder_src_browse.setObjectName("browse_btn")
        folder_src_browse.clicked.connect(self._browse_folder_source)
        src_row.addWidget(src_lbl)
        src_row.addWidget(self.folder_source_input)
        src_row.addWidget(folder_src_browse)
        folder_config_layout.addLayout(src_row)
        
        # Destination Directory Row
        dest_row = QHBoxLayout()
        dest_lbl = QLabel("Destination SSD:")
        dest_lbl.setFixedWidth(130)
        self.folder_dest_input = QLineEdit()
        self.folder_dest_input.setPlaceholderText("Select SSD output folder...")
        folder_dest_browse = QPushButton("Browse")
        folder_dest_browse.setObjectName("browse_btn")
        folder_dest_browse.clicked.connect(self._browse_folder_dest)
        dest_row.addWidget(dest_lbl)
        dest_row.addWidget(self.folder_dest_input)
        dest_row.addWidget(folder_dest_browse)
        folder_config_layout.addLayout(dest_row)
        
        # Checkboxes row
        cb_row = QHBoxLayout()
        self.folder_copy_metadata_cb = QCheckBox("Copy Non-Video Files & Metadata")
        self.folder_copy_metadata_cb.setChecked(True)
        self.folder_skip_cb = QCheckBox("Skip Already Downcompiled Videos (Auto-Skip)")
        self.folder_skip_cb.setChecked(True)
        cb_row.addWidget(self.folder_copy_metadata_cb)
        cb_row.addWidget(self.folder_skip_cb)
        cb_row.addStretch()
        folder_config_layout.addLayout(cb_row)
        
        # Parameters row
        params_row = QHBoxLayout()
        
        res_lbl = QLabel("Resolution:")
        self.folder_res_combo = QComboBox()
        self.folder_res_combo.addItems(["112 x 112", "224 x 224"])
        self.folder_res_combo.setCurrentIndex(0)
        
        frames_lbl = QLabel("Frame Count:")
        self.folder_frames_combo = QComboBox()
        self.folder_frames_combo.addItems(["8", "16", "32"])
        self.folder_frames_combo.setCurrentIndex(1)
        
        strat_lbl = QLabel("Split Strategy:")
        self.folder_strategy_combo = QComboBox()
        self.folder_strategy_combo.addItems(["Auto-detect from path", "Force Train (Stochastic)", "Force Val/Test (Deterministic)"])
        self.folder_strategy_combo.setCurrentIndex(0)
        
        params_row.addWidget(res_lbl)
        params_row.addWidget(self.folder_res_combo)
        params_row.addSpacing(15)
        params_row.addWidget(frames_lbl)
        params_row.addWidget(self.folder_frames_combo)
        params_row.addSpacing(15)
        params_row.addWidget(strat_lbl)
        params_row.addWidget(self.folder_strategy_combo)
        params_row.addStretch()
        folder_config_layout.addLayout(params_row)
        
        # GPU/CPU device row
        device_row = QHBoxLayout()
        dev_lbl = QLabel("Hardware Device:")
        self.folder_device_combo = QComboBox()
        self.folder_device_combo.addItems(["CPU (Standard)", "GPU (PyTorch/CUDA Acceleration)", "FFmpeg CLI (High Compatibility)"])
        self.folder_device_combo.setCurrentIndex(0)
        
        workers_lbl = QLabel("Parallel Workers:")
        self.folder_workers_combo = QComboBox()
        self.folder_workers_combo.addItems(["1 (Single Thread)", "2", "4", "8", "16"])
        self.folder_workers_combo.setCurrentIndex(0)  # Default to 1 (Single Thread)
        
        self.folder_source_mode_cb = QCheckBox("Keep Original Spatiotemporal Parameters (Source Mode)")
        self.folder_source_mode_cb.toggled.connect(self._on_folder_source_mode_toggled)
        
        device_row.addWidget(dev_lbl)
        device_row.addWidget(self.folder_device_combo)
        device_row.addSpacing(15)
        device_row.addWidget(workers_lbl)
        device_row.addWidget(self.folder_workers_combo)
        device_row.addSpacing(20)
        device_row.addWidget(self.folder_source_mode_cb)
        device_row.addStretch()
        folder_config_layout.addLayout(device_row)
        
        folder_left_layout.addWidget(folder_config_group)
        
        # Folder Group Box 2: Action Controls
        folder_action_layout = QHBoxLayout()
        folder_action_layout.setContentsMargins(10, 10, 10, 10)
        
        self.folder_start_btn = QPushButton("Start Downframing")
        self.folder_start_btn.clicked.connect(self._start_folder_downframe)
        
        self.folder_stop_btn = QPushButton("Stop")
        self.folder_stop_btn.setObjectName("stop_btn")
        self.folder_stop_btn.setEnabled(False)
        self.folder_stop_btn.clicked.connect(self._stop_folder_downframe)
        
        folder_action_layout.addWidget(self.folder_start_btn)
        folder_action_layout.addWidget(self.folder_stop_btn)
        folder_left_layout.addLayout(folder_action_layout)
        
        # Folder Group Box 3: Downframing Progress Metrics
        folder_progress_group = QGroupBox("2. Real-Time Folder Metrics")
        folder_progress_layout = QVBoxLayout(folder_progress_group)
        folder_progress_layout.setSpacing(12)
        
        # 1. Overall Queue Progress
        queue_lbl_layout = QHBoxLayout()
        queue_title_lbl = QLabel("Overall Queue Progress:")
        self.folder_queue_stats_lbl = QLabel("0 / 0 videos processed")
        queue_lbl_layout.addWidget(queue_title_lbl)
        queue_lbl_layout.addStretch()
        queue_lbl_layout.addWidget(self.folder_queue_stats_lbl)
        
        self.folder_queue_progress = QProgressBar()
        self.folder_queue_progress.setFormat("%p%")
        
        folder_progress_layout.addLayout(queue_lbl_layout)
        folder_progress_layout.addWidget(self.folder_queue_progress)
        
        # 2. Active Video Frame Progress
        frame_lbl_layout = QHBoxLayout()
        frame_title_lbl = QLabel("Active Video Frame Progress:")
        self.folder_frame_stats_lbl = QLabel("Frame 0 / 0")
        frame_lbl_layout.addWidget(frame_title_lbl)
        frame_lbl_layout.addStretch()
        frame_lbl_layout.addWidget(self.folder_frame_stats_lbl)
        
        self.folder_frame_progress = QProgressBar()
        self.folder_frame_progress.setFormat("%p%")
        
        # Active file status subtitle
        self.folder_status_lbl = QLabel("Folder Processor Idle...")
        self.folder_status_lbl.setStyleSheet("color: #60A5FA; font-style: italic; font-size: 11px;")
        
        folder_progress_layout.addLayout(frame_lbl_layout)
        folder_progress_layout.addWidget(self.folder_frame_progress)
        folder_progress_layout.addWidget(self.folder_status_lbl)
        
        folder_left_layout.addWidget(folder_progress_group)
        folder_left_layout.addStretch()
        
        # Folder Bottom Panel (Downframer Log Console)
        folder_right_container = QWidget()
        folder_right_layout = QVBoxLayout(folder_right_container)
        folder_right_layout.setContentsMargins(0, 8, 0, 0)
        
        folder_console_group = QGroupBox("Downframer Log Console")
        folder_console_layout = QVBoxLayout(folder_console_group)
        self.folder_console = LogConsole()
        folder_console_layout.addWidget(self.folder_console)
        folder_right_layout.addWidget(folder_console_group)
        
        # Add to splitter
        folder_splitter.addWidget(folder_left_container)
        folder_splitter.addWidget(folder_right_container)
        folder_splitter.setSizes([450, 250])
        
        # Add tabs
        self.tabs.addTab(downloader_tab, "Kinetics-700 Download & Process Pipeline")
        self.tabs.addTab(folder_tab, "Direct Folder Downframer")
        
        # Add basic visual diagnostics status bar at the bottom
        self.info_status_bar = QLabel("Status: System Idle | Buffer empty")
        self.info_status_bar.setStyleSheet("""
            background-color: #0B0F19; 
            border: 1px solid #334155; 
            border-radius: 4px;
            padding: 6px 12px;
            color: #94A3B8;
            font-size: 12px;
        """)
        main_layout.addWidget(self.info_status_bar)



    def _load_qss(self):
        """Loads and applies the QSS styling stylesheet file."""
        gui_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gui")
        qss_path = os.path.join(gui_dir, "styles.qss")
        if os.path.exists(qss_path):
            with open(qss_path, "r", encoding="utf-8") as f:
                qss_content = f.read()
            # Dynamically resolve active_check.png to its absolute path for robust rendering in Qt
            active_check_path = os.path.join(gui_dir, "active_check.png").replace("\\", "/")
            qss_content = qss_content.replace('url("active_check.png")', f'url("{active_check_path}")')
            self.setStyleSheet(qss_content)

    # --- GUI Configuration Slots ---
    
    @pyqtSlot(bool)
    def _on_single_conn_toggled(self, checked):
        """Handles toggling Single Connection Mode."""
        self.conn_spin.setEnabled(not checked)
        if checked:
            self.console.append_log("[INFO] Single Connection Mode active: downloading as a single contiguous ZIP.")
        else:
            self.console.append_log("[INFO] Single Connection Mode disabled: multi-segment parallel downloads enabled.")

    @pyqtSlot(object, object)
    def _on_merge_progress(self, merged, total):
        """Updates UI progress bar and status during the binary merging phase."""
        percentage = int((merged / total) * 100) if total > 0 else 0
        self.download_progress.setValue(percentage)
        self.download_stats_lbl.setText(
            f"Merging existing segments: {format_size(merged)} / {format_size(total)} ({percentage}%)"
        )

    @pyqtSlot(bool)
    def _on_source_mode_toggled(self, checked):
        """Handles disabling parameters when in Keep Source Mode."""
        self.res_combo.setEnabled(not checked)
        self.frames_combo.setEnabled(not checked)
        self.engine_combo.setEnabled(not checked)
        if checked:
            self.console.append_log("[INFO] Keep Source Mode active: spatiotemporal downsampling disabled.")
        else:
            self.console.append_log("[INFO] Keep Source Mode disabled: dynamic downsampling enabled.")

    def _browse_staging(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Staging HDD Directory")
        if dir_path:
            self.staging_input.setText(dir_path)
            self._update_diagnostics()

    def _browse_destination(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select SSD Output Directory")
        if dir_path:
            self.dest_input.setText(dir_path)
            self._update_diagnostics()

    def _update_diagnostics(self):
        """Helper to print staging and output stats."""
        staging = self.staging_input.text()
        dest = self.dest_input.text()
        if not staging or not dest:
            return
            
        # Check disk space
        valid, s_free, d_free, _ = check_disk_space(staging, dest, self.source_mode_cb.isChecked())
        self.info_status_bar.setText(
            f"Staging Free: {format_size(s_free)} | Final Output Free: {format_size(d_free)}"
        )

    # --- Pipeline Coordination / Control Logic ---
    
    def _start_pipeline(self):
        if self.pipeline_active:
            # If paused, we resume
            if self.pipeline_paused:
                self._resume_pipeline()
                return
            return

        # Fetch inputs
        self.hf_token = self.token_input.text().strip()
        self.start_part = self.start_part_spin.value()
        self.end_part = self.end_part_spin.value()
        self.staging_path = self.staging_input.text().strip()
        self.destination_path = self.dest_input.text().strip()
        self.keep_source = self.source_mode_cb.isChecked()
        self.num_connections = self.conn_spin.value()
        
        # Parse downsampling options
        res_str = self.res_combo.currentText().split(" x ")
        self.resolution = (int(res_str[0]), int(res_str[1]))
        self.frame_count = int(self.frames_combo.currentText())
        self.engine_mode = "ffmpeg" if "FFmpeg" in self.engine_combo.currentText() else "cpu"

        # Validate inputs
        if self.start_part > self.end_part:
            QMessageBox.critical(self, "Validation Error", "Start Part must be less than or equal to End Part.")
            return
            
        if not self.staging_path or not os.path.exists(self.staging_path):
            QMessageBox.critical(self, "Validation Error", "Please select a valid Staging HDD Buffer directory.")
            return
            
        if not self.destination_path or not os.path.exists(self.destination_path):
            QMessageBox.critical(self, "Validation Error", "Please select a valid Final Output SSD directory.")
            return

        # Double check disk spaces
        valid, _, _, error_msg = check_disk_space(self.staging_path, self.destination_path, self.keep_source)
        if not valid:
            self.console.append_log(f"[WARNING] {error_msg}")
            reply = QMessageBox.warning(
                self, "Warning: Storage Bounds Alert", 
                f"{error_msg}\n\nAre you sure you want to continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                return

        # Setup state variables
        self.pipeline_active = True
        self.pipeline_paused = False
        self.current_running_part = self.start_part
        
        # Save last used configuration
        self._save_config()
        
        # Update buttons
        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setText("Pause")
        self.stop_btn.setEnabled(True)
        
        # Lock configuration panels
        self.token_input.setEnabled(False)
        self.start_part_spin.setEnabled(False)
        self.end_part_spin.setEnabled(False)
        self.conn_spin.setEnabled(False)
        self.single_conn_cb.setEnabled(False)
        self.source_mode_cb.setEnabled(False)
        self.res_combo.setEnabled(False)
        self.frames_combo.setEnabled(False)
        self.engine_combo.setEnabled(False)
        self.staging_input.setEnabled(False)
        self.dest_input.setEnabled(False)
        
        # Reset progress bars
        self.download_progress.setValue(0)
        self.extract_progress.setValue(0)
        self.video_progress_bar.setValue(0)
        
        total_parts = self.end_part - self.start_part + 1
        self.master_progress.setRange(0, total_parts)
        self.master_progress.setValue(0)
        self.master_stats_lbl.setText(f"0 / {total_parts} parts fully completed")
        
        self.console.clear()
        self.console.append_log(f"--- Starting Kinetics-700 Download & Process Pipeline ---")
        self.console.append_log(f"Range parts: {self.start_part} to {self.end_part}")
        self.console.append_log(f"Staging path: {self.staging_path}")
        self.console.append_log(f"Destination: {self.destination_path}")
        
        # Initialise connections table
        self.connections_table.init_segments(1 if self.single_conn_cb.isChecked() else self.num_connections)
        
        # Trigger execution of first part in list
        self.start_next_part.emit()

    @pyqtSlot()
    def _execute_pipeline_step(self):
        """Sequentially triggers the next part execution in the pipeline."""
        if not self.pipeline_active or self.pipeline_paused:
            return

        total_parts_range = self.end_part - self.start_part + 1
        current_completed = self.current_running_part - self.start_part
        self.master_progress.setValue(current_completed)
        self.master_stats_lbl.setText(f"{current_completed} / {total_parts_range} parts completed")

        if self.current_running_part > self.end_part:
            self._finalize_pipeline_success()
            return

        # Save config dynamically so that if the app is closed suddenly or crashes, it remembers the current part
        self._save_config()

        # Prepare specific URL
        # e.g., Kinetics700_part_001.zip
        part_filename = f"Kinetics700_part_{self.current_running_part:03d}.zip"
        hf_resolve_url = f"https://huggingface.co/datasets/atalaydenknalbant/Kinetics-700/resolve/main/{part_filename}"
        
        local_archive_filepath = os.path.join(self.staging_path, part_filename)
        
        self.console.append_log(f"\n--- [Pipeline Part {self.current_running_part} of {self.end_part}] ---")
        if self.is_resuming:
            self.console.append_log(f"[INFO] Resuming download for {part_filename}...")
        else:
            self.console.append_log(f"[INFO] Fetching download link: {part_filename}")
        
        # 1. Connection Count Auto-Matcher to prevent range boundary corruption
        if not self.single_conn_cb.isChecked():
            import re
            existing_segs = []
            try:
                for f in os.listdir(self.staging_path):
                    if f.startswith(part_filename) and f.endswith(".part") and ".seg" in f:
                        existing_segs.append(f)
            except Exception:
                pass

            if existing_segs:
                indices = []
                for f in existing_segs:
                    match = re.search(r'\.seg(\d+)\.part', f)
                    if match:
                        indices.append(int(match.group(1)))
                if indices:
                    detected_connections = max(indices) + 1
                    if detected_connections != self.num_connections:
                        self.console.append_log(
                            f"[WARNING] Detected existing segment files with {detected_connections} connections. "
                            f"Aligning downloader connections to {detected_connections} for safe resumption."
                        )
                        self.num_connections = detected_connections
                        self.conn_spin.setValue(detected_connections)

        # 2. Reset relative indicators only if not resuming
        if not self.is_resuming:
            self.download_progress.setValue(0)
            self.extract_progress.setValue(0)
            self.video_progress_bar.setValue(0)
            self.extract_stats_lbl.setText("Waiting on extraction...")
            self.video_stats_lbl.setText("Waiting on processing...")
            self.curr_processing_item_lbl.setText("Waiting for downsampler...")
        
        # Check storage space again before launching next massive part download
        valid, s_free, _, error_msg = check_disk_space(self.staging_path, self.destination_path, self.keep_source)
        if not valid:
            self.console.append_log(f"[ERROR] Insufficient space to proceed safely: {error_msg}")
            self._pause_pipeline()
            QMessageBox.critical(self, "Pipeline Halted", f"Pipeline paused due to low storage: {error_msg}")
            return
            
        # Start segmented downloader
        self.active_downloader = FileDownloader(
            url=hf_resolve_url, 
            final_filepath=local_archive_filepath, 
            num_segments=self.num_connections,
            token=self.hf_token,
            single_connection=self.single_conn_cb.isChecked()
        )
        self.active_downloader.segment_progress.connect(self._on_segment_progress)
        self.active_downloader.total_progress.connect(self._on_total_dl_progress)
        self.active_downloader.merge_progress.connect(self._on_merge_progress)
        self.active_downloader.finished.connect(self._on_downloader_finished)
        self.active_downloader.paused_confirmed.connect(self._on_pause_confirmed)
        
        # 3. Setup segments table visually without flickering to 0%
        if not self.is_resuming:
            self.connections_table.init_segments(1 if self.single_conn_cb.isChecked() else self.num_connections)
        else:
            # Table already initialized; set connection speeds to "Resuming..."
            for i in range(self.connections_table.rowCount()):
                try:
                    self.connections_table.item(i, 2).setText("Resuming...")
                except Exception:
                    pass
            # Reset resuming flag for subsequent automatic parts
            self.is_resuming = False
            
        self.active_downloader.start()

    # --- Downloader Signal Handlers ---
    
    def _on_segment_progress(self, idx, downloaded, total, speed, eta):
        self.connections_table.update_segment(idx, downloaded, total, speed, eta)

    def _on_total_dl_progress(self, downloaded, total, speed, eta):
        percentage = int((downloaded / total) * 100) if total > 0 else 0
        self.download_progress.setValue(percentage)
        self.download_stats_lbl.setText(
            f"{format_size(downloaded)} / {format_size(total)} ({speed:.2f} MB/s) | ETA: {format_time(eta)}"
        )

    def _on_downloader_finished(self, success, error_msg):
        if not success:
            if error_msg == "Paused" or self.pipeline_paused:
                # Do nothing, pause confirmed signal will handle UI transition
                return
            self.console.append_log(f"[ERROR] Download failed: {error_msg}")
            
            # Determine if error is transient (e.g. IncompleteRead, connection drops, timeout)
            is_transient = True
            err_lower = error_msg.lower()
            hard_failures = [
                "status 400", "status 401", "status 403", "status 404", "status 416",
                "no space left on device", "disk full", "permission denied", "access denied"
            ]
            if any(hf in err_lower for hf in hard_failures):
                is_transient = False

            # If transient, we retry indefinitely. Otherwise, respect self.max_retries limit.
            should_retry = False
            if is_transient:
                self.download_retry_count += 1
                should_retry = True
                retry_limit_str = "infinite"
            else:
                if self.download_retry_count < self.max_retries:
                    self.download_retry_count += 1
                    should_retry = True
                    retry_limit_str = f"{self.max_retries}"
                else:
                    should_retry = False

            if should_retry:
                # Exponential backoff: capped at 10 seconds (5s, 10s)
                delay_sec = min(5 * (2 ** (self.download_retry_count - 1)), 10)
                
                self.console.append_log(
                    f"[WARNING] Automatically retrying and resuming download in {delay_sec} seconds... "
                    f"(Attempt {self.download_retry_count} of {retry_limit_str})"
                )
                
                # Clean up active downloader reference
                if self.active_downloader:
                    self.active_downloader = None
                
                # Visually update connections grid to reflect retry state
                for i in range(self.connections_table.rowCount()):
                    try:
                        self.connections_table.item(i, 2).setText("Retrying...")
                        self.connections_table.item(i, 4).setText("--:--:--")
                    except Exception:
                        pass
                
                from PyQt6.QtCore import QTimer
                self.is_resuming = True
                QTimer.singleShot(delay_sec * 1000, self._retry_download)
                return

            self._pause_pipeline()
            QMessageBox.critical(self, "Download Failure", f"Part {self.current_running_part} download failed after {self.max_retries} attempts:\n{error_msg}")
            return
            
        self.download_retry_count = 0  # Reset retry counter on successful part download
        self.console.append_log(f"[SUCCESS] Download of Part {self.current_running_part} completed!")
        
        # Calculate raw sizing for space saved diagnostic
        part_filename = f"Kinetics700_part_{self.current_running_part:03d}.zip"
        downloaded_zip_path = os.path.join(self.staging_path, part_filename)
        zip_size = os.path.getsize(downloaded_zip_path) if os.path.exists(downloaded_zip_path) else 0
        self.total_downloaded_raw += zip_size
        
        # Clear segment table, ready for next part when time comes
        self.connections_table.reset_grid()
        
        # Trigger Extractor and Processor Phase
        self._start_processing_phase(downloaded_zip_path)

    def _retry_download(self):
        """Callback to execute auto-retry/resume step of current part."""
        if self.pipeline_active and not self.pipeline_paused:
            self.start_next_part.emit()

    # --- Processor Signal Handlers ---
    
    def _start_processing_phase(self, zip_path):
        self.console.append_log(f"[INFO] Initialising spatiotemporal processing loop...")
        
        # Setup background processing QThread
        self.process_thread = QThread()
        
        self.active_processor = ProcessingWorker(
            archive_path=zip_path,
            staging_dir=self.staging_path,
            dest_dir=self.destination_path,
            keep_source=self.keep_source,
            resolution=self.resolution,
            frame_count=self.frame_count,
            engine_mode=self.engine_mode
        )
        
        self.active_processor.moveToThread(self.process_thread)
        
        # Wire signals
        self.process_thread.started.connect(self.active_processor.run)
        self.active_processor.log_message.connect(self.console.append_log)
        self.active_processor.extraction_progress.connect(self._on_extraction_progress)
        self.active_processor.video_progress.connect(self._on_video_progress)
        self.active_processor.finished.connect(self._on_processor_finished)
        
        self.process_thread.start()

    def _on_extraction_progress(self, current, total):
        percentage = int((current / total) * 100) if total > 0 else 0
        self.extract_progress.setValue(percentage)
        self.extract_stats_lbl.setText(f"{current} / {total} files extracted")

    def _on_video_progress(self, current, total, name):
        percentage = int((current / total) * 100) if total > 0 else 0
        self.video_progress_bar.setValue(percentage)
        self.video_stats_lbl.setText(f"{current} / {total} videos processed")
        self.curr_processing_item_lbl.setText(f"Active: {name}")

    def _on_processor_finished(self, success, error_msg):
        # Shut down QThread cleanly
        self.process_thread.quit()
        self.process_thread.wait()
        
        self.active_processor = None
        
        if not success:
            if self.pipeline_paused:
                return
            self.console.append_log(f"[ERROR] Processing pipeline failed: {error_msg}")
            self._pause_pipeline()
            QMessageBox.critical(self, "Processing Failure", f"Spatiotemporal processing failed:\n{error_msg}")
            return
            
        self.console.append_log(f"[SUCCESS] Part {self.current_running_part} successfully compressed and integrated!")
        
        # Increment to next part
        self.current_running_part += 1
        
        # Save config
        self._save_config()
        
        # Dynamic Diagnostic Calculation of saved space
        self._calculate_saved_space()
        
        # Proceed with next iteration
        self.start_next_part.emit()

    def _calculate_saved_space(self):
        """Calculates space saved. Since raw archives are deleted immediately (Buffer & Purge),
           we count cumulative raw downloads against actual final ssd folder size."""
        # Estimate: each raw part zip is ~42 GB.
        # Check actual destination size dynamically
        actual_output_bytes = 0
        try:
            for root, dirs, files in os.walk(self.destination_path):
                for f in files:
                    fp = os.path.join(root, f)
                    actual_output_bytes += os.path.getsize(fp)
        except Exception:
            pass
            
        # Calculate saved size
        # Raw zip size * total completed parts
        completed_count = self.current_running_part - self.start_part
        estimated_raw_size = completed_count * 42 * 1024 * 1024 * 1024  # 42 GB per zip
        
        saved_bytes = max(0, estimated_raw_size - actual_output_bytes)
        self.space_saved_label.setText(f"Saved Space: {format_size(saved_bytes)}")
        self.console.append_log(f"[INFO] Storage statistics -> Raw space used: {format_size(estimated_raw_size)} | Extracted & Downsampled: {format_size(actual_output_bytes)} | Space Saved: {format_size(saved_bytes)}")

    # --- Pause, Resume, Stop controls ---
    
    def _on_pause_btn_clicked(self):
        if self.pipeline_paused:
            self._resume_pipeline()
        else:
            self._pause_pipeline()
            
    def _pause_pipeline(self):
        if not self.pipeline_active or self.pipeline_paused:
            return

        self.pipeline_paused = True
        self.pause_btn.setEnabled(False)
        self.console.append_log("[WARNING] Pausing pipeline... waiting for current connections to flush.")
        
        if self.active_downloader:
            self.active_downloader.pause()
        
        # If processor is active, it cannot be safely paused midway through a single video file write.
        # But we flag pause so it halts immediately before starting the next zip or video iteration.
        if self.active_processor:
            self.active_processor.stop()
            # Wait for thread to clean up
            self.process_thread.quit()
            self.process_thread.wait()
            self.active_processor = None
            self.console.append_log("[WARNING] Processing pipeline successfully paused.")
            self._on_pause_confirmed()

    def _on_pause_confirmed(self):
        self.console.append_log("[WARNING] Pipeline safely paused.")
        self.pause_btn.setText("Resume")
        self.pause_btn.setEnabled(True)
        self.start_btn.setEnabled(False)
        
        # Keep connection spinbox locked during pause to prevent segment boundary corruption
        self.conn_spin.setEnabled(False)
        
        # Visually pause the connections table rather than resetting it blank
        for i in range(self.connections_table.rowCount()):
            try:
                self.connections_table.item(i, 2).setText("Paused")
                self.connections_table.item(i, 4).setText("--:--:--")
            except Exception:
                pass

        # Visually update the main progress bar labels to reflect paused state
        if self.active_downloader:
            try:
                total_dl = sum(self.active_downloader.downloaded_bytes.values())
                total_sz = self.active_downloader.total_size
                pct = int((total_dl / total_sz) * 100) if total_sz > 0 else 0
                self.download_progress.setValue(pct)
                self.download_stats_lbl.setText(
                    f"{format_size(total_dl)} / {format_size(total_sz)} (Paused)"
                )
            except Exception:
                pass

    def _resume_pipeline(self):
        if not self.pipeline_active or not self.pipeline_paused:
            return
            
        self.console.append_log("[INFO] Resuming dataset pipeline step...")
        self.pipeline_paused = False
        self.is_resuming = True
        
        # Keep the active connections count to ensure range boundary integrity
        self.pause_btn.setText("Pause")
        self.conn_spin.setEnabled(False)
        
        # Trigger next step execution (resume downloads)
        self.start_next_part.emit()

    def _stop_pipeline(self, clean_parts=True, prompt=True):
        # PyQt6 clicked signal passes a bool (checked=False), so we override it to default True when prompting
        if isinstance(clean_parts, bool) and not clean_parts and prompt:
            clean_parts = True

        if prompt:
            reply = QMessageBox.question(
                self, "Confirm Stopping Pipeline", 
                "Are you sure you want to stop the download pipeline? This will abort all active tasks.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                return
            
        self.console.append_log("[CAUTION] Stopping execution pipeline immediately...")
        
        self.pipeline_active = False
        self.pipeline_paused = False
        
        if self.active_downloader:
            self.active_downloader.pause()
            self.active_downloader = None
            
        if self.active_processor:
            self.active_processor.stop()
            self.process_thread.quit()
            self.process_thread.wait()
            self.active_processor = None

        # Clean segment files for the currently running part to save space only if clean_parts is True
        if clean_parts:
            try:
                part_filename = f"Kinetics700_part_{self.current_running_part:03d}.zip"
                local_archive_filepath = os.path.join(self.staging_path, part_filename)
                # Remove main zip
                if os.path.exists(local_archive_filepath):
                    os.remove(local_archive_filepath)
                # Remove segments
                for i in range(16):
                    seg_filepath = os.path.join(self.staging_path, f"{part_filename}.seg{i}.part")
                    if os.path.exists(seg_filepath):
                        os.remove(seg_filepath)
            except Exception:
                pass

        self._reset_ui_controls()
        self.console.append_log("[CAUTION] Pipeline stopped and reset.")

    def _reset_ui_controls(self):
        # Reset buttons
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("Pause")
        self.stop_btn.setEnabled(False)
        
        # Unlock configuration panels
        self.token_input.setEnabled(True)
        self.start_part_spin.setEnabled(True)
        self.end_part_spin.setEnabled(True)
        self.single_conn_cb.setEnabled(True)
        self.conn_spin.setEnabled(not self.single_conn_cb.isChecked())
        self.source_mode_cb.setEnabled(True)
        self.res_combo.setEnabled(True)
        self.frames_combo.setEnabled(True)
        self.engine_combo.setEnabled(True)
        self.staging_input.setEnabled(True)
        self.dest_input.setEnabled(True)
        
        self.connections_table.reset_grid()

    def _finalize_pipeline_success(self):
        self.pipeline_active = False
        self._reset_ui_controls()
        self.console.append_log("\n[SUCCESS] =========================================")
        self.console.append_log("[SUCCESS] KINETICS-700 COMPRESSION PIPELINE FINISHED!")
        self.console.append_log("[SUCCESS] All files fully downsampled and structured.")
        self.console.append_log("[SUCCESS] Staging buffer purged and clean.")
        self.console.append_log("[SUCCESS] =========================================")
        QMessageBox.information(self, "Pipeline Completed", "Kinetics-700 pipeline completed successfully!")

    def closeEvent(self, event):
        """Clean shutdown handling when closing the GUI window."""
        self._save_config()
        if self.pipeline_active:
            reply = QMessageBox.question(
                self, "Exit Manager",
                "The pipeline is currently running. Exit anyway and abort active tasks?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                # Do NOT delete part files on close, do NOT prompt twice!
                self._stop_pipeline(clean_parts=False, prompt=False)
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    def _browse_folder_source(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Raw Videos Source Directory")
        if dir_path:
            self.folder_source_input.setText(dir_path)

    def _browse_folder_dest(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Output SSD Directory")
        if dir_path:
            self.folder_dest_input.setText(dir_path)

    @pyqtSlot(bool)
    def _on_folder_source_mode_toggled(self, checked):
        self.folder_res_combo.setEnabled(not checked)
        self.folder_frames_combo.setEnabled(not checked)
        if checked:
            self.folder_console.append_log("[INFO] Keep Source Mode active: spatiotemporal downsampling disabled.")
        else:
            self.folder_console.append_log("[INFO] Keep Source Mode disabled: dynamic downsampling enabled.")

    def _start_folder_downframe(self):
        source_dir = self.folder_source_input.text().strip()
        dest_dir = self.folder_dest_input.text().strip()
        
        if not source_dir or not os.path.exists(source_dir):
            QMessageBox.critical(self, "Validation Error", "Please select a valid Source Directory.")
            return
            
        if not dest_dir or not os.path.exists(dest_dir):
            QMessageBox.critical(self, "Validation Error", "Please select a valid Destination Directory.")
            return

        # Read configs
        keep_source = self.folder_source_mode_cb.isChecked()
        res_str = self.folder_res_combo.currentText().split(" x ")
        resolution = (int(res_str[0]), int(res_str[1]))
        frame_count = int(self.folder_frames_combo.currentText())
        copy_metadata = self.folder_copy_metadata_cb.isChecked()
        skip_existing = self.folder_skip_cb.isChecked()
        
        # Sampling strategy
        strat_text = self.folder_strategy_combo.currentText()
        if "Force Train" in strat_text:
            strategy = "train"
        elif "Force Val" in strat_text:
            strategy = "val"
        else:
            strategy = "auto"
            
        # Device mode
        device_text = self.folder_device_combo.currentText()
        if "FFmpeg" in device_text:
            device_mode = "ffmpeg"
        elif "GPU" in device_text:
            device_mode = "gpu"
        else:
            device_mode = "cpu"

        # Parallel workers
        workers_text = self.folder_workers_combo.currentText().split(" ")[0]
        num_workers = int(workers_text)

        # Initialize State
        self.folder_pipeline_active = True
        
        # Disable inputs
        self.folder_source_input.setEnabled(False)
        self.folder_dest_input.setEnabled(False)
        self.folder_res_combo.setEnabled(False)
        self.folder_frames_combo.setEnabled(False)
        self.folder_strategy_combo.setEnabled(False)
        self.folder_device_combo.setEnabled(False)
        self.folder_workers_combo.setEnabled(False)
        self.folder_skip_cb.setEnabled(False)
        self.folder_copy_metadata_cb.setEnabled(False)
        self.folder_source_mode_cb.setEnabled(False)
        
        self.folder_start_btn.setEnabled(False)
        self.folder_stop_btn.setEnabled(True)
        
        # Reset progress bars
        self.folder_queue_progress.setValue(0)
        self.folder_frame_progress.setValue(0)
        self.folder_queue_stats_lbl.setText("Scanning directory...")
        
        if num_workers > 1:
            self.folder_frame_stats_lbl.setText("Frame progress tracking disabled (multi-worker active)")
        else:
            self.folder_frame_stats_lbl.setText("Frame 0 / 0")
        
        self.folder_console.clear()
        
        # Setup background processing Thread
        self.folder_thread = QThread()
        self.folder_worker = FolderProcessingWorker(
            source_dir=source_dir,
            dest_dir=dest_dir,
            keep_source=keep_source,
            resolution=resolution,
            frame_count=frame_count,
            copy_metadata=copy_metadata,
            sampling_strategy=strategy,
            skip_existing=skip_existing,
            device_mode=device_mode,
            num_workers=num_workers
        )
        self.folder_worker.moveToThread(self.folder_thread)
        
        # Wire Signals
        self.folder_thread.started.connect(self.folder_worker.run)
        self.folder_worker.log_message.connect(self.folder_console.append_log)
        self.folder_worker.video_progress.connect(self._on_folder_video_progress)
        self.folder_worker.video_frame_progress.connect(self._on_folder_frame_progress)
        self.folder_worker.finished.connect(self._on_folder_finished)
        
        # Start execution
        self.folder_thread.start()

    def _stop_folder_downframe(self):
        reply = QMessageBox.question(
            self, "Confirm Stopping Downframer",
            "Are you sure you want to stop downframing folder files?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.No:
            return
            
        self.folder_console.append_log("[CAUTION] Stopping execution loop...")
        if self.folder_worker:
            self.folder_worker.stop()

    @pyqtSlot(int, int, str)
    def _on_folder_video_progress(self, current, total, name):
        percentage = int((current / total) * 100) if total > 0 else 0
        self.folder_queue_progress.setValue(percentage)
        self.folder_queue_stats_lbl.setText(f"{current} / {total} videos processed")
        self.folder_status_lbl.setText(f"Active File: {name}")

    @pyqtSlot(int, int)
    def _on_folder_frame_progress(self, current, total):
        percentage = int((current / total) * 100) if total > 0 else 0
        self.folder_frame_progress.setValue(percentage)
        self.folder_frame_stats_lbl.setText(f"Frame {current} / {total}")

    @pyqtSlot(bool, str)
    def _on_folder_finished(self, success, error_msg):
        # Shut down QThread cleanly
        self.folder_thread.quit()
        self.folder_thread.wait()
        
        self.folder_worker = None
        self.folder_pipeline_active = False
        
        # Re-enable inputs
        self.folder_source_input.setEnabled(True)
        self.folder_dest_input.setEnabled(True)
        self.folder_res_combo.setEnabled(not self.folder_source_mode_cb.isChecked())
        self.folder_frames_combo.setEnabled(not self.folder_source_mode_cb.isChecked())
        self.folder_strategy_combo.setEnabled(True)
        self.folder_device_combo.setEnabled(True)
        self.folder_workers_combo.setEnabled(True)
        self.folder_skip_cb.setEnabled(True)
        self.folder_copy_metadata_cb.setEnabled(True)
        self.folder_source_mode_cb.setEnabled(True)
        
        self.folder_start_btn.setEnabled(True)
        self.folder_stop_btn.setEnabled(False)
        
        if success:
            self.folder_console.append_log("[SUCCESS] Downframing completed successfully!")
            QMessageBox.information(self, "Processing Finished", "Direct Folder Downframing completed successfully!")
        else:
            self.folder_console.append_log(f"[ERROR] Process stopped: {error_msg}")
            QMessageBox.warning(self, "Processing Terminated", f"Downframing stopped/failed:\n{error_msg}")

    def _get_config_path(self):
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

    def _load_config(self):
        config_path = self._get_config_path()
        if os.path.exists(config_path):
            try:
                import json
                with open(config_path, "r") as f:
                    data = json.load(f)
                
                # Apply parameters to UI widgets
                self.token_input.setText(data.get("hf_token", ""))
                self.start_part_spin.setValue(data.get("start_part", 1))
                self.end_part_spin.setValue(data.get("end_part", 22))
                self.conn_spin.setValue(data.get("num_connections", 8))
                
                # Load single connection checkbox state
                single_conn = data.get("single_connection", False)
                self.single_conn_cb.setChecked(single_conn)
                self.conn_spin.setEnabled(not single_conn)
                
                keep_src = data.get("keep_source", False)
                self.source_mode_cb.setChecked(keep_src)
                self._on_source_mode_toggled(keep_src)
                
                self.res_combo.setCurrentIndex(data.get("resolution_index", 0))
                self.frames_combo.setCurrentIndex(data.get("frame_count_index", 1))
                self.engine_combo.setCurrentIndex(data.get("engine_mode_index", 0))
                
                self.staging_input.setText(data.get("staging_path", ""))
                self.dest_input.setText(data.get("destination_path", ""))
                
                # Restore folder downframer settings
                self.folder_device_combo.setCurrentIndex(data.get("folder_device_index", 0))
                self.folder_source_input.setText(data.get("folder_source_path", ""))
                self.folder_dest_input.setText(data.get("folder_dest_path", ""))
                
                self._update_diagnostics()
                
                # Log recovery message
                recovered_part = data.get("start_part", 1)
                self.console.append_log(f"[INFO] Recovered last active configuration. Ready to resume/start from Part {recovered_part}.")
            except Exception as e:
                self.console.append_log(f"[WARNING] Failed to load config.json: {str(e)}")

    def _save_config(self):
        config_path = self._get_config_path()
        try:
            import json
            # If the pipeline is currently running, save current_running_part as start_part
            # so that on sudden shutdown/restart, it loads the exact part that was active!
            start_val = self.current_running_part if (self.pipeline_active and self.current_running_part != -1) else self.start_part_spin.value()
            
            # Bound validation to avoid invalid UI spinbox values
            if start_val < 1:
                start_val = 1
            elif start_val > 22:
                start_val = 22

            data = {
                "hf_token": self.token_input.text().strip(),
                "start_part": start_val,
                "end_part": self.end_part_spin.value(),
                "num_connections": self.conn_spin.value(),
                "single_connection": self.single_conn_cb.isChecked(),
                "keep_source": self.source_mode_cb.isChecked(),
                "resolution_index": self.res_combo.currentIndex(),
                "frame_count_index": self.frames_combo.currentIndex(),
                "engine_mode_index": self.engine_combo.currentIndex(),
                "staging_path": self.staging_input.text().strip(),
                "destination_path": self.dest_input.text().strip(),
                "folder_device_index": self.folder_device_combo.currentIndex(),
                "folder_source_path": self.folder_source_input.text().strip(),
                "folder_dest_path": self.folder_dest_input.text().strip()
            }
            with open(config_path, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving config.json: {str(e)}")
