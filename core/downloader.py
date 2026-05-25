import os
import time
import requests
import threading
from PyQt6.QtCore import QObject, pyqtSignal

class SegmentWorker(QObject):
    # Signals to communicate progress back to the manager
    progress_updated = pyqtSignal(int, int, float)  # segment_idx, bytes_downloaded_in_interval, speed_mb_s
    finished = pyqtSignal(int, bool, str)  # segment_idx, success, error_msg

    def __init__(self, url, headers, start_byte, end_byte, filepath, segment_idx):
        super().__init__()
        self.url = url
        self.headers = headers.copy() if headers else {}
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.filepath = filepath
        self.segment_idx = segment_idx
        self.is_paused = False
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._download, daemon=True)
        self.thread.start()

    def pause(self):
        self.is_paused = True

    def _download(self):
        try:
            # Check how much has already been downloaded (for resume support)
            downloaded_so_far = 0
            if os.path.exists(self.filepath):
                downloaded_so_far = os.path.getsize(self.filepath)

            current_start = self.start_byte + downloaded_so_far
            
            # If this segment is already completely downloaded, finish immediately
            if current_start >= self.end_byte:
                self.progress_updated.emit(self.segment_idx, 0, 0.0)
                self.finished.emit(self.segment_idx, True, "")
                return

            # Prepare range headers
            self.headers["Range"] = f"bytes={current_start}-{self.end_byte}"
            
            # Open output file in append mode if resuming, else write mode
            mode = "ab" if downloaded_so_far > 0 else "wb"
            
            response = requests.get(self.url, headers=self.headers, stream=True, timeout=15)
            # 206 means Partial Content, which is expected for Range queries.
            # 200 is acceptable if server doesn't support Range, but it means we download from start.
            if response.status_code not in (200, 206):
                self.finished.emit(self.segment_idx, False, f"Server returned status {response.status_code}")
                return

            # Keep track of speed and interval downloads
            last_time = time.time()
            interval_bytes = 0
            
            with open(self.filepath, mode) as f:
                for chunk in response.iter_content(chunk_size=128 * 1024):  # 128KB chunks
                    if self.is_paused:
                        response.close()
                        self.finished.emit(self.segment_idx, False, "Paused")
                        return

                    if chunk:
                        f.write(chunk)
                        f.flush()
                        
                        chunk_len = len(chunk)
                        interval_bytes += chunk_len
                        
                        now = time.time()
                        time_diff = now - last_time
                        
                        # Emit updates every 0.5 seconds
                        if time_diff >= 0.5:
                            speed = (interval_bytes / (1024 * 1024)) / time_diff  # MB/s
                            self.progress_updated.emit(self.segment_idx, interval_bytes, speed)
                            interval_bytes = 0
                            last_time = now
                
                # Emit any remaining bytes in the interval buffer
                if interval_bytes > 0:
                    time_diff = max(time.time() - last_time, 0.001)
                    speed = (interval_bytes / (1024 * 1024)) / time_diff
                    self.progress_updated.emit(self.segment_idx, interval_bytes, speed)

            self.finished.emit(self.segment_idx, True, "")
            
        except Exception as e:
            self.finished.emit(self.segment_idx, False, str(e))

class FileDownloader(QObject):
    # Signals emitted to the GUI
    segment_progress = pyqtSignal(int, object, object, float, float)  # idx, downloaded, total, speed, eta
    total_progress = pyqtSignal(object, object, float, float)  # downloaded, total, speed_mb_s, eta
    finished = pyqtSignal(bool, str)  # success, error_message
    paused_confirmed = pyqtSignal()  # confirmation that pause succeeded
    merge_progress = pyqtSignal(object, object)  # merged_bytes, total_bytes

    def __init__(self, url, final_filepath, num_segments=8, token=None, single_connection=False):
        super().__init__()
        self.url = url
        self.final_filepath = final_filepath
        self.single_connection = single_connection
        self.num_segments = 1 if single_connection else num_segments
        self.token = token
        self.workers = {}
        self.segment_sizes = {}
        self.downloaded_bytes = {}  # segment_idx: bytes downloaded
        self.segment_speeds = {}    # segment_idx: speed
        self.total_size = 0
        self.is_paused = False
        self.is_finished = False
        self.lock = threading.RLock()
        
        # Robust tracking variables for race-free pause/resume execution
        self.finished_workers = set()
        self.paused_confirmed_emitted = False
        self.finished_emitted = False
        self.failed_error_msg = None
        
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"

    def start(self):
        """Starts the multi-segmented download in a background thread."""
        threading.Thread(target=self._run_download, daemon=True).start()

    def pause(self):
        """Signals all active workers to pause."""
        self.is_paused = True
        for worker in self.workers.values():
            if worker:
                worker.pause()

    def _run_download(self):
        try:
            if self.is_paused:
                self.paused_confirmed.emit()
                return

            # 1. Fetch file content length using standard requests
            # First attempt a HEAD request
            res = requests.head(self.url, headers=self.headers, allow_redirects=True, timeout=10)
            
            if self.is_paused:
                if hasattr(res, 'close'):
                    res.close()
                self.paused_confirmed.emit()
                return

            if res.status_code != 200:
                # Fallback to GET request with limited range or streaming to just read headers
                res = requests.get(self.url, headers=self.headers, stream=True, allow_redirects=True, timeout=10)
                
            if self.is_paused:
                if hasattr(res, 'close'):
                    res.close()
                self.paused_confirmed.emit()
                return

            if res.status_code not in (200, 206):
                self.finished.emit(False, f"Failed to access file. Status Code: {res.status_code}")
                return

            self.total_size = int(res.headers.get("content-length", 0))
            if self.total_size == 0:
                self.finished.emit(False, "Could not determine file size (Content-Length is 0 or missing).")
                return

            # Close response if it was streamed
            if hasattr(res, 'close'):
                res.close()

            # If the combined final file already exists and matches the size, bypass download
            if os.path.exists(self.final_filepath) and os.path.getsize(self.final_filepath) == self.total_size:
                self.finished.emit(True, "")
                return

            if self.is_paused:
                self.paused_confirmed.emit()
                return

            # Temporary files base name
            temp_dir = os.path.dirname(self.final_filepath)
            os.makedirs(temp_dir, exist_ok=True)
            filename = os.path.basename(self.final_filepath)

            if self.single_connection:
                # Merge existing segments if any, showing progress
                self._merge_existing_segments_for_single_conn(temp_dir, filename)
                
                # If combined ZIP file is already complete on disk, bypass download
                if os.path.exists(self.final_filepath) and os.path.getsize(self.final_filepath) == self.total_size:
                    self.finished.emit(True, "")
                    return
                
                if self.is_paused:
                    self.paused_confirmed.emit()
                    return
                
                # Single connection downloader setup
                self.segment_sizes = {0: self.total_size}
                curr_size = os.path.getsize(self.final_filepath) if os.path.exists(self.final_filepath) else 0
                self.downloaded_bytes = {0: curr_size}
                self.segment_speeds = {0: 0.0}
                
                worker = SegmentWorker(self.url, self.headers, 0, self.total_size - 1, self.final_filepath, 0)
                worker.progress_updated.connect(self._on_segment_progress)
                worker.finished.connect(self._on_segment_finished)
                self.workers = {0: worker}
                
                # Emit initial segment progress
                self.segment_progress.emit(0, curr_size, self.total_size, 0.0, -1.0)
                self.total_progress.emit(curr_size, self.total_size, 0.0, -1.0)
                
                worker.start()
                return

            # 2. Divide file into segment bounds
            seg_size = self.total_size // self.num_segments
            self.workers = {}
            self.segment_sizes = {}
            
            # Start workers
            completed_workers = 0
            
            for i in range(self.num_segments):
                start = i * seg_size
                # Last segment takes any remainder bytes
                end = (self.total_size - 1) if (i == self.num_segments - 1) else ((i + 1) * seg_size - 1)
                
                self.segment_sizes[i] = end - start + 1
                seg_filepath = os.path.join(temp_dir, f"{filename}.seg{i}.part")
                
                # Check current downloaded size for this segment
                curr_size = os.path.getsize(seg_filepath) if os.path.exists(seg_filepath) else 0
                self.downloaded_bytes[i] = curr_size
                self.segment_speeds[i] = 0.0
                
                worker = SegmentWorker(self.url, self.headers, start, end, seg_filepath, i)
                worker.progress_updated.connect(self._on_segment_progress)
                worker.finished.connect(self._on_segment_finished)
                self.workers[i] = worker

            if self.is_paused:
                self.paused_confirmed.emit()
                return

            # Emit initial segment progress to set correct visual sizes on restart/resume immediately
            for i in range(self.num_segments):
                seg_downloaded = self.downloaded_bytes[i]
                seg_total = self.segment_sizes[i]
                self.segment_progress.emit(i, seg_downloaded, seg_total, 0.0, -1.0)

            # Emit initial overall download progress
            total_downloaded = sum(self.downloaded_bytes.values())
            self.total_progress.emit(total_downloaded, self.total_size, 0.0, -1.0)
            
            # Start downloading all segments
            for worker in self.workers.values():
                worker.start()
                
        except Exception as e:
            self.finished.emit(False, f"Error initializing download: {str(e)}")

    def _on_segment_progress(self, segment_idx, new_bytes, speed):
        with self.lock:
            # Update downloaded bytes and speed
            self.downloaded_bytes[segment_idx] += new_bytes
            self.segment_speeds[segment_idx] = speed
            
            # Calculate segment progress percentages, speed, ETA
            seg_downloaded = self.downloaded_bytes[segment_idx]
            seg_total = self.segment_sizes[segment_idx]
            
            seg_eta = (seg_total - seg_downloaded) / (speed * 1024 * 1024) if speed > 0 else -1
            
            # Emit segment update
            self.segment_progress.emit(segment_idx, seg_downloaded, seg_total, speed, seg_eta)
            
            # Calculate total statistics
            total_downloaded = sum(self.downloaded_bytes.values())
            total_speed = sum(self.segment_speeds.values())
            total_eta = (self.total_size - total_downloaded) / (total_speed * 1024 * 1024) if total_speed > 0 else -1
            
            # Emit total progress
            self.total_progress.emit(total_downloaded, self.total_size, total_speed, total_eta)

    def _on_segment_finished(self, segment_idx, success, error_msg):
        with self.lock:
            self.finished_workers.add(segment_idx)
            
            # If a segment failed and we are not already pausing/paused
            if not success and error_msg != "Paused" and not self.is_paused:
                self.is_paused = True
                self.failed_error_msg = f"Segment {segment_idx + 1} failed: {error_msg}"
                for worker in self.workers.values():
                    if worker:
                        worker.pause()
            
            # Check if all workers have finished (successfully, paused, or with error)
            if len(self.finished_workers) == self.num_segments:
                # If a segment failed, report it only after all threads have fully exited
                if self.failed_error_msg:
                    if not self.finished_emitted:
                        self.finished_emitted = True
                        self.finished.emit(False, self.failed_error_msg)
                    return
                
                if self.is_paused:
                    if not self.paused_confirmed_emitted and not self.finished_emitted:
                        self.paused_confirmed_emitted = True
                        self.paused_confirmed.emit()
                    return
                
                if self.single_connection:
                    if not self.is_finished:
                        self.is_finished = True
                        self.finished.emit(True, "")
                    return

                # Check if all segments are done successfully
                all_done = True
                for i in range(self.num_segments):
                    seg_filepath = os.path.join(
                        os.path.dirname(self.final_filepath), 
                        f"{os.path.basename(self.final_filepath)}.seg{i}.part"
                    )
                    if not os.path.exists(seg_filepath) or os.path.getsize(seg_filepath) < self.segment_sizes[i]:
                        all_done = False
                        break
                
                if all_done:
                    if not self.is_finished:
                        self.is_finished = True
                        # Combine files in a separate thread to prevent blocking
                        threading.Thread(target=self._combine_segments, daemon=True).start()
                else:
                    if not self.finished_emitted:
                        self.finished_emitted = True
                        self.finished.emit(False, "Some download segments failed or were incomplete.")

    def _combine_segments(self):
        try:
            temp_dir = os.path.dirname(self.final_filepath)
            filename = os.path.basename(self.final_filepath)
            
            # Emit dummy total speed during combining
            self.total_progress.emit(self.total_size, self.total_size, 0.0, 0.0)
            
            # Combine parts
            with open(self.final_filepath, "wb") as dest_f:
                for i in range(self.num_segments):
                    seg_filepath = os.path.join(temp_dir, f"{filename}.seg{i}.part")
                    with open(seg_filepath, "rb") as src_f:
                        # Stream in 1MB chunks to save memory
                        while True:
                            data = src_f.read(1024 * 1024)
                            if not data:
                                break
                            dest_f.write(data)
                    
                    # Delete segment file immediately to free space
                    os.remove(seg_filepath)
            
            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, f"Error combining segments: {str(e)}")

    def _merge_existing_segments_for_single_conn(self, temp_dir, filename):
        """Finds previously downloaded segments, merges them into final_filepath sequentially up to any gap,
           and emits merge progress back to the GUI."""
        import re
        seg_pattern = re.compile(rf"^{re.escape(filename)}\.seg(\d+)\.part$")
        existing_segs = {}
        try:
            for f in os.listdir(temp_dir):
                m = seg_pattern.match(f)
                if m:
                    idx = int(m.group(1))
                    existing_segs[idx] = os.path.join(temp_dir, f)
        except Exception:
            return 0

        if not existing_segs:
            return 0

        # Determine segment sizes based on combined size and count
        num_segs = max(existing_segs.keys()) + 1
        seg_size = self.total_size // num_segs
        
        expected_sizes = {}
        for i in range(num_segs):
            start = i * seg_size
            end = (self.total_size - 1) if (i == num_segs - 1) else ((i + 1) * seg_size - 1)
            expected_sizes[i] = end - start + 1

        # We can sequentially merge segments starting from 0 until we hit an incomplete segment or gap
        total_merge_bytes = 0
        segs_to_merge = []
        
        for i in range(num_segs):
            if i not in existing_segs:
                break
            seg_path = existing_segs[i]
            actual_size = os.path.getsize(seg_path) if os.path.exists(seg_path) else 0
            
            if actual_size == 0:
                break
                
            expected_size = expected_sizes[i]
            
            if actual_size == expected_size:
                segs_to_merge.append((seg_path, actual_size))
                total_merge_bytes += actual_size
            else:
                # Partially complete segment, we can append its downloaded bytes and then must stop
                segs_to_merge.append((seg_path, actual_size))
                total_merge_bytes += actual_size
                break

        if not segs_to_merge:
            return 0

        # Perform the actual binary file concatenation/merging with progress tracking
        merged_so_far = 0
        try:
            self.merge_progress.emit(0, total_merge_bytes)
            
            with open(self.final_filepath, "wb") as dest_f:
                for seg_path, actual_size in segs_to_merge:
                    with open(seg_path, "rb") as src_f:
                        while True:
                            if self.is_paused:
                                break
                            data = src_f.read(1024 * 1024)  # 1MB buffer
                            if not data:
                                break
                            dest_f.write(data)
                            merged_so_far += len(data)
                            self.merge_progress.emit(merged_so_far, total_merge_bytes)
                    
                    if self.is_paused:
                        break

            # Clean up all segment files once merged to free staging HDD buffer space
            for seg_path, _ in segs_to_merge:
                try:
                    os.remove(seg_path)
                except Exception:
                    pass
                    
            for i in range(num_segs):
                seg_path = os.path.join(temp_dir, f"{filename}.seg{i}.part")
                if os.path.exists(seg_path):
                    try:
                        os.remove(seg_path)
                    except Exception:
                        pass

            return merged_so_far
        except Exception:
            return merged_so_far
