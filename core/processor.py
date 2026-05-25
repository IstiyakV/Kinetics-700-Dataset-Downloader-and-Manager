import os
import shutil
import zipfile
import tarfile
import random
import time
import cv2
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

def is_video_decodable(src_path, timeout=10):
    """Verifies if the video can be decoded by FFmpeg without hanging or critical errors by reading the first 0.5s."""
    import subprocess
    
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        
    cmd = [
        "ffmpeg", "-y",
        "-v", "error",
        "-i", src_path,
        "-t", "0.5",  # Scan only the first 0.5 seconds for extremely fast decodability verification
        "-f", "null",
        "-"
    ]
    try:
        # Run FFmpeg to verify decodability with a timeout
        res = subprocess.run(cmd, startupinfo=startupinfo, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            return False
        return True
    except subprocess.TimeoutExpired:
        # If it takes longer than timeout, it's hanging/corrupt
        return False
    except Exception:
        # Fall back to True if ffmpeg command is missing/fails to run (avoid breaking environment)
        return True

def extract_sampled_frames(cap, sampled_indices):
    """Extracts only the specified frames from VideoCapture using seeking, with sequential grab fallback."""
    unique_indices = sorted(list(set(sampled_indices)))
    frames_map = {}
    
    # Try seeking first (very fast on well-indexed MP4/Kinetics files)
    use_seeking = True
    for idx in unique_indices:
        ret = cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        if ret:
            ret_val, frame = cap.read()
            if ret_val and frame is not None:
                frames_map[idx] = frame.copy()
                continue
        # If seeking fails or read returns None/False, abort seeking and fall back to sequential grab
        use_seeking = False
        break
        
    if not use_seeking or len(frames_map) < len(unique_indices):
        # Fall back to sequential grab/retrieve (much faster than read because it skips pixel decoding)
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        frames_map.clear()
        current_idx = 0
        target_set = set(unique_indices)
        max_target = max(unique_indices) if unique_indices else 0
        
        while current_idx <= max_target:
            if current_idx in target_set:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break
                frames_map[current_idx] = frame.copy()
            else:
                ret = cap.grab()
                if not ret:
                    break
            current_idx += 1
            
    # Reconstruct final frame sequence, duplicating missing or out-of-bound frames if needed
    output_frames = []
    for idx in sampled_indices:
        if idx in frames_map:
            output_frames.append(frames_map[idx])
        else:
            if output_frames:
                output_frames.append(output_frames[-1].copy())
            else:
                # Return empty list if no frames were decoded successfully
                pass
                
    return output_frames

def _downsample_video_standalone(src_path, dest_path, is_train, resolution, frame_count, device_mode, use_gpu=False):
    """Standalone spatiotemporal downsampling function for video processing (fully picklable)."""
    import os
    import cv2
    cv2.setNumThreads(1)  # Limit OpenCV's internal thread count to prevent CPU thrashing in multi-worker scenarios
    import numpy as np
    import random
    
    if device_mode == "ffmpeg":
        import uuid
        import subprocess
        import shutil
        
        dest_dir = os.path.dirname(dest_path)
        temp_sub_dir = os.path.join(dest_dir, f"tmp_ff_{uuid.uuid4().hex}")
        os.makedirs(temp_sub_dir, exist_ok=True)
        
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            
        try:
            # 1. Extract all frames as JPEGs
            cmd_extract = [
                "ffmpeg", "-y",
                "-i", src_path,
                "-q:v", "2",
                os.path.join(temp_sub_dir, "frame_%05d.jpg")
            ]
            subprocess.run(cmd_extract, startupinfo=startupinfo, capture_output=True, text=True, check=False)
            
            # Read extracted frames list
            frame_files = sorted([
                os.path.join(temp_sub_dir, f)
                for f in os.listdir(temp_sub_dir)
                if f.startswith("frame_") and f.endswith(".jpg")
            ])
            
            total_frames = len(frame_files)
            if total_frames == 0:
                return False
                
            # 2. TSN Temporal Sampling
            sampled_indices = []
            T = frame_count
            
            if total_frames >= T:
                seg_len = total_frames / T
                for i in range(T):
                    start_idx = int(i * seg_len)
                    end_idx = int((i + 1) * seg_len) - 1
                    end_idx = max(start_idx, end_idx)
                    
                    if is_train:
                        idx = random.randint(start_idx, end_idx)
                    else:
                        idx = start_idx + (end_idx - start_idx) // 2
                    
                    idx = min(idx, total_frames - 1)
                    sampled_indices.append(idx)
            else:
                sampled_indices = list(range(total_frames))
                while len(sampled_indices) < T:
                    sampled_indices.append(total_frames - 1)
                    
            # 3. Copy sampled frame sequence
            temp_sampled_dir = os.path.join(temp_sub_dir, "sampled")
            os.makedirs(temp_sampled_dir, exist_ok=True)
            
            for seq_idx, frame_idx in enumerate(sampled_indices):
                src_frame = frame_files[frame_idx]
                dest_frame = os.path.join(temp_sampled_dir, f"seq_{seq_idx+1:05d}.jpg")
                shutil.copy2(src_frame, dest_frame)
                
            # 4. Compile into target H.264 video applying Gaussian blur and scaling
            w, h = resolution
            cmd_compile = [
                "ffmpeg", "-y",
                "-f", "image2",
                "-framerate", "30",
                "-i", os.path.join(temp_sampled_dir, "seq_%05d.jpg"),
                "-vf", f"gblur=sigma=0.8,scale={w}:{h}:flags=area",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                dest_path
            ]
            res = subprocess.run(cmd_compile, startupinfo=startupinfo, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                return False
                
            return True
        except Exception:
            return False
        finally:
            shutil.rmtree(temp_sub_dir, ignore_errors=True)
            
    else:
        if not is_video_decodable(src_path):
            return False

        cap = cv2.VideoCapture(src_path)
        if not cap.isOpened():
            return False

        try:
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total_frames <= 0:
                # Fallback to legacy full decode
                frames_full = []
                while True:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    frames_full.append(frame)
                total_frames = len(frames_full)
                if total_frames == 0:
                    return False
                
                # TSN Sampling
                sampled_indices = []
                T = frame_count
                if total_frames >= T:
                    seg_len = total_frames / T
                    for i in range(T):
                        start_idx = int(i * seg_len)
                        end_idx = int((i + 1) * seg_len) - 1
                        end_idx = max(start_idx, end_idx)
                        if is_train:
                            idx = random.randint(start_idx, end_idx)
                        else:
                            idx = start_idx + (end_idx - start_idx) // 2
                        idx = min(idx, total_frames - 1)
                        sampled_indices.append(idx)
                else:
                    sampled_indices = list(range(total_frames))
                    while len(sampled_indices) < T:
                        sampled_indices.append(total_frames - 1)
                
                frames = [frames_full[idx] for idx in sampled_indices]
            else:
                # TSN Sampling
                sampled_indices = []
                T = frame_count
                if total_frames >= T:
                    seg_len = total_frames / T
                    for i in range(T):
                        start_idx = int(i * seg_len)
                        end_idx = int((i + 1) * seg_len) - 1
                        end_idx = max(start_idx, end_idx)
                        if is_train:
                            idx = random.randint(start_idx, end_idx)
                        else:
                            idx = start_idx + (end_idx - start_idx) // 2
                        idx = min(idx, total_frames - 1)
                        sampled_indices.append(idx)
                else:
                    sampled_indices = list(range(total_frames))
                    while len(sampled_indices) < T:
                        sampled_indices.append(total_frames - 1)
                
                frames = extract_sampled_frames(cap, sampled_indices)
        finally:
            cap.release()

        T = frame_count
        if len(frames) == 0 or len(frames) != T:
            return False

        processed_frames = []
        w, h = resolution

        if use_gpu:
            try:
                import torch
                # Gaussian 3x3 kernel definition in PyTorch
                kernel = torch.tensor([[1, 2, 1],
                                       [2, 4, 2],
                                       [1, 2, 1]], dtype=torch.float32, device='cuda')
                kernel = kernel / kernel.sum()
                kernel = kernel.view(3, 1, 3, 3)

                # Stack frames into a single tensor on CPU: shape [T, H, W, 3]
                frames_np = np.stack(frames, axis=0)
                
                # Single host-to-device memory transfer
                frames_t = torch.from_numpy(frames_np).to('cuda').permute(0, 3, 1, 2).float() # [T, 3, H, W]

                # Apply batched blur in a single CUDA execution
                blurred_t = torch.nn.functional.conv2d(frames_t, kernel, padding=1, groups=3)

                # Batched resize
                resized_t = torch.nn.functional.interpolate(blurred_t, size=(h, w), mode='bilinear', align_corners=False)

                # Single device-to-host memory transfer back to CPU
                processed_np = resized_t.permute(0, 2, 3, 1).clamp(0, 255).byte().cpu().numpy()
                processed_frames = [processed_np[i] for i in range(T)]
            except Exception:
                processed_frames.clear()
                use_gpu = False

        if not use_gpu:
            # CPU mode fallback
            for frame in frames:
                blurred = cv2.GaussianBlur(frame, (3, 3), 0)
                resized = cv2.resize(blurred, (w, h), interpolation=cv2.INTER_AREA)
                processed_frames.append(resized)

        # 3. Write H.264 MP4 output
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(dest_path, fourcc, 30.0, (w, h))
        if not out.isOpened():
            return False

        try:
            for frame in processed_frames:
                out.write(frame)
        finally:
            out.release()

        return True

def _process_video_task(args):
    """Processes a single video inside a separate process (fully picklable)."""
    (src_path, source_dir, dest_dir, keep_source, resolution, frame_count,
     sampling_strategy, skip_existing, device_mode, use_gpu) = args
    
    import os
    import shutil
    
    rel_path = os.path.relpath(src_path, source_dir)
    dest_path = os.path.join(dest_dir, rel_path)
    
    # Force target extension to .mp4
    base, ext = os.path.splitext(dest_path)
    dest_path_mp4 = base + ".mp4"
    video_name = os.path.basename(src_path)

    # Check Auto-Skip option
    if skip_existing and os.path.exists(dest_path_mp4):
        return {"success": True, "video_name": video_name, "skipped": True, "error": None}

    os.makedirs(os.path.dirname(dest_path_mp4), exist_ok=True)

    # Sampling strategy split determination
    is_train = False
    if sampling_strategy == "train":
        is_train = True
    elif sampling_strategy == "val":
        is_train = False
    else:  # "auto"
        parts = rel_path.split(os.sep)
        for part in parts:
            if part.lower() == "train":
                is_train = True
                break

    try:
        if keep_source:
            shutil.copy2(src_path, dest_path_mp4)
            success = True
        else:
            success = _downsample_video_standalone(src_path, dest_path_mp4, is_train, resolution, frame_count, device_mode, use_gpu)
            
        if not success:
            if os.path.exists(dest_path_mp4):
                try:
                    os.remove(dest_path_mp4)
                except Exception:
                    pass
            return {"success": False, "video_name": video_name, "skipped": False, "error": "Downsampling failed"}
            
        return {"success": True, "video_name": video_name, "skipped": False, "error": None}
    except Exception as e:
        if os.path.exists(dest_path_mp4):
            try:
                os.remove(dest_path_mp4)
            except Exception:
                pass
        return {"success": False, "video_name": video_name, "skipped": False, "error": str(e)}

class ProcessingWorker(QObject):
    # Signals for GUI communication
    log_message = pyqtSignal(str)
    extraction_progress = pyqtSignal(int, int)  # current, total files
    video_progress = pyqtSignal(int, int, str)  # current, total, video name
    finished = pyqtSignal(bool, str)

    def __init__(self, archive_path, staging_dir, dest_dir, keep_source=False, resolution=(112, 112), frame_count=16, engine_mode="cpu"):
        super().__init__()
        self.archive_path = archive_path
        self.staging_dir = staging_dir
        self.dest_dir = dest_dir
        self.keep_source = keep_source
        self.resolution = resolution  # (width, height)
        self.frame_count = frame_count
        self.engine_mode = engine_mode
        self.is_stopped = False

    def stop(self):
        self.is_stopped = True

    def run(self):
        # Create staging extract directory name proactively
        archive_name = os.path.basename(self.archive_path).replace(".zip", "").replace(".tar", "").replace(".gz", "")
        expected_extract_to = os.path.join(self.staging_dir, f"extracted_{archive_name}")
        
        try:
            self.log_message.emit("--- Processing Pipeline Started ---")
            
            # 1. Extraction Phase
            extracted_dir = self._extract_archive()
            if not extracted_dir:
                # Clean up any partial extractions and the bad archive
                self._purge(expected_extract_to)
                
                if self.is_stopped:
                    self.finished.emit(False, "Stopped by user")
                else:
                    self.finished.emit(False, "Extraction failed (corrupt or invalid archive)")
                return
            
            if self.is_stopped:
                self._purge(extracted_dir)
                self.finished.emit(False, "Stopped by user")
                return

            # 2. Downsampling & Migration Phase
            self.log_message.emit("Replicating folders and processing videos...")
            success = self._process_extracted_data(extracted_dir)
            
            # 3. Purge Phase (Always clean up buffer directories)
            self._purge(extracted_dir)
            
            if success:
                self.log_message.emit("Successfully completed part processing and staging purged.")
                self.finished.emit(True, "")
            else:
                self.finished.emit(False, "Processing failed or was stopped.")
                
        except Exception as e:
            self.log_message.emit(f"Critical error in processing: {str(e)}")
            # Make sure to clean up even on unexpected critical crashes
            self._purge(expected_extract_to)
            self.finished.emit(False, str(e))

    def _extract_archive(self):
        """Extracts zip or tar archives with file-by-file visual progress updates."""
        self.log_message.emit(f"Extracting archive: {os.path.basename(self.archive_path)}")
        
        # Create staging extract directory
        archive_name = os.path.basename(self.archive_path).replace(".zip", "").replace(".tar", "").replace(".gz", "")
        extract_to = os.path.join(self.staging_dir, f"extracted_{archive_name}")
        os.makedirs(extract_to, exist_ok=True)
        
        if self.archive_path.endswith(".zip"):
            try:
                with zipfile.ZipFile(self.archive_path, 'r') as zip_ref:
                    infolist = zip_ref.infolist()
                    # Filter out directories if zipfile counts them separately
                    files_to_extract = [info for info in infolist if not info.is_dir()]
                    total_files = len(files_to_extract)
                    
                    self.log_message.emit(f"Total files to extract: {total_files}")
                    self.extraction_progress.emit(0, total_files)
                    
                    for idx, info in enumerate(files_to_extract):
                        if self.is_stopped:
                            self.log_message.emit("Extraction stopped by user.")
                            return None
                        
                        zip_ref.extract(info, extract_to)
                        self.extraction_progress.emit(idx + 1, total_files)
                        
                return extract_to
            except Exception as e:
                self.log_message.emit(f"Zip extraction error: {str(e)}")
                return None
                
        elif self.archive_path.endswith((".tar", ".tar.gz")):
            try:
                # Open tar file
                mode = "r:gz" if self.archive_path.endswith(".gz") else "r:"
                with tarfile.open(self.archive_path, mode) as tar_ref:
                    members = tar_ref.getmembers()
                    files_to_extract = [m for m in members if m.isfile()]
                    total_files = len(files_to_extract)
                    
                    self.log_message.emit(f"Total files to extract: {total_files}")
                    self.extraction_progress.emit(0, total_files)
                    
                    for idx, member in enumerate(files_to_extract):
                        if self.is_stopped:
                            self.log_message.emit("Extraction stopped by user.")
                            return None
                        
                        tar_ref.extract(member, extract_to)
                        self.extraction_progress.emit(idx + 1, total_files)
                        
                return extract_to
            except Exception as e:
                self.log_message.emit(f"Tar extraction error: {str(e)}")
                return None
        else:
            self.log_message.emit(f"Unsupported archive format: {self.archive_path}")
            return None

    def _process_extracted_data(self, root_extracted):
        """Walks extracted folder, downsamples videos, and copies non-video metadata files."""
        # Find all files recursively in the extracted folder
        all_filepaths = []
        for root, dirs, files in os.walk(root_extracted):
            for file in files:
                all_filepaths.append(os.path.join(root, file))
        
        # Filter files
        video_extensions = ('.mp4', '.avi', '.mkv', '.webm', '.mov', '.flv', '.wmv', '.mpeg', '.mpg')
        video_files = []
        non_video_files = []
        
        for path in all_filepaths:
            if path.lower().endswith(video_extensions):
                video_files.append(path)
            else:
                non_video_files.append(path)
                
        total_videos = len(video_files)
        total_non_videos = len(non_video_files)
        
        self.log_message.emit(f"Found {total_videos} videos and {total_non_videos} non-video metadata/raw files.")
        
        # 1. Copy non-video metadata/raw files first (keeps layout and JSONs intact)
        self.log_message.emit("Copying metadata, json, and non-video files...")
        for idx, src_path in enumerate(non_video_files):
            if self.is_stopped:
                return False
            
            # Determine relative path from extracted root
            rel_path = os.path.relpath(src_path, root_extracted)
            # If the extraction root contains an extra wrapper folder (e.g. part_001), skip it
            parts = rel_path.split(os.sep)
            if len(parts) > 1 and parts[0].startswith("Kinetics700"):
                rel_path = os.path.join(*parts[1:])
                
            dest_path = os.path.join(self.dest_dir, rel_path)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            try:
                shutil.copy2(src_path, dest_path)
            except Exception as e:
                self.log_message.emit(f"Failed to copy {os.path.basename(src_path)}: {str(e)}")

        # 2. Process video files
        self.video_progress.emit(0, total_videos, "Starting...")
        
        for idx, src_path in enumerate(video_files):
            if self.is_stopped:
                return False
                
            rel_path = os.path.relpath(src_path, root_extracted)
            parts = rel_path.split(os.sep)
            if len(parts) > 1 and parts[0].startswith("Kinetics700"):
                rel_path = os.path.join(*parts[1:])
                
            # Kinetic-700 dataset uses folders: train, validation, test
            # Determine split (train, validation, test) from relative path
            is_train = False
            for part in parts:
                if part.lower() == "train":
                    is_train = True
                    break

            dest_path = os.path.join(self.dest_dir, rel_path)
            # Force target extension to .mp4
            base, ext = os.path.splitext(dest_path)
            dest_path_mp4 = base + ".mp4"
            os.makedirs(os.path.dirname(dest_path_mp4), exist_ok=True)
            
            video_name = os.path.basename(src_path)
            self.video_progress.emit(idx, total_videos, video_name)
            
            if self.keep_source:
                # Bypass downsampling: just copy original video
                try:
                    shutil.copy2(src_path, dest_path_mp4)
                except Exception as e:
                    self.log_message.emit(f"Failed to copy original video {video_name}: {str(e)}")
            else:
                # Spatiotemporal downsampler
                if self.engine_mode == "ffmpeg":
                    success = self._downsample_video_ffmpeg(src_path, dest_path_mp4, is_train)
                else:
                    success = self._downsample_video(src_path, dest_path_mp4, is_train)
                
                if not success:
                    self.log_message.emit(f"Warning: Failed decoding {video_name}. Skipping this video (corrupted/invalid).")
                    if os.path.exists(dest_path_mp4):
                        try:
                            os.remove(dest_path_mp4)
                        except Exception as rm_err:
                            self.log_message.emit(f"Failed to clean up partial file for {video_name}: {str(rm_err)}")
                        
        self.video_progress.emit(total_videos, total_videos, "Finished Processing")
        return True

    def _downsample_video(self, src_path, dest_path, is_train):
        """Performs Gaussian low-pass spatial filtering, resolution resize, and TSN frame sampling with selective decoding."""
        return _downsample_video_standalone(src_path, dest_path, is_train, self.resolution, self.frame_count, self.engine_mode, False)

    def _downsample_video_ffmpeg(self, src_path, dest_path, is_train):
        """Performs Gaussian low-pass spatial filtering, resolution resize, and TSN frame sampling using FFmpeg."""
        import subprocess
        import uuid
        
        # Create a workspace-nested unique temp folder inside dest_dir to hold frames
        temp_sub_dir = os.path.join(self.dest_dir, f"tmp_ff_{uuid.uuid4().hex}")
        os.makedirs(temp_sub_dir, exist_ok=True)
        
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            
        try:
            # 1. Extract all frames as JPEGs
            cmd_extract = [
                "ffmpeg", "-y",
                "-i", src_path,
                "-q:v", "2",
                os.path.join(temp_sub_dir, "frame_%05d.jpg")
            ]
            subprocess.run(cmd_extract, startupinfo=startupinfo, capture_output=True, text=True, check=False)
            
            # Read extracted frames list
            frame_files = sorted([
                os.path.join(temp_sub_dir, f)
                for f in os.listdir(temp_sub_dir)
                if f.startswith("frame_") and f.endswith(".jpg")
            ])
            
            total_frames = len(frame_files)
            if total_frames == 0:
                return False
                
            # 2. TSN Temporal Sampling
            sampled_indices = []
            T = self.frame_count
            
            if total_frames >= T:
                seg_len = total_frames / T
                for i in range(T):
                    start_idx = int(i * seg_len)
                    end_idx = int((i + 1) * seg_len) - 1
                    end_idx = max(start_idx, end_idx)
                    
                    if is_train:
                        idx = random.randint(start_idx, end_idx)
                    else:
                        idx = start_idx + (end_idx - start_idx) // 2
                    
                    idx = min(idx, total_frames - 1)
                    sampled_indices.append(idx)
            else:
                sampled_indices = list(range(total_frames))
                while len(sampled_indices) < T:
                    sampled_indices.append(total_frames - 1)
                    
            # 3. Copy sampled frame sequence
            temp_sampled_dir = os.path.join(temp_sub_dir, "sampled")
            os.makedirs(temp_sampled_dir, exist_ok=True)
            
            for seq_idx, frame_idx in enumerate(sampled_indices):
                src_frame = frame_files[frame_idx]
                dest_frame = os.path.join(temp_sampled_dir, f"seq_{seq_idx+1:05d}.jpg")
                shutil.copy2(src_frame, dest_frame)
                
            # 4. Compile into target H.264 video applying Gaussian blur and scaling
            w, h = self.resolution
            cmd_compile = [
                "ffmpeg", "-y",
                "-f", "image2",
                "-framerate", "30",
                "-i", os.path.join(temp_sampled_dir, "seq_%05d.jpg"),
                "-vf", f"gblur=sigma=0.8,scale={w}:{h}:flags=area",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                dest_path
            ]
            res = subprocess.run(cmd_compile, startupinfo=startupinfo, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                return False
                
            return True
        except Exception as e:
            return False
        finally:
            shutil.rmtree(temp_sub_dir, ignore_errors=True)

    def _purge(self, folder_path):
        """Immediately deletes intermediate extracted folder and the downloaded raw zip."""
        self.log_message.emit("Purging staging buffer...")
        
        # Delete extracted folder
        if os.path.exists(folder_path):
            try:
                shutil.rmtree(folder_path)
                self.log_message.emit(f"Deleted extracted buffer: {os.path.basename(folder_path)}")
            except Exception as e:
                self.log_message.emit(f"Failed to delete buffer folder: {str(e)}")
                
        # Delete raw zip file
        if os.path.exists(self.archive_path):
            try:
                os.remove(self.archive_path)
                self.log_message.emit(f"Deleted raw archive zip: {os.path.basename(self.archive_path)}")
            except Exception as e:
                self.log_message.emit(f"Failed to delete raw archive: {str(e)}")


class FolderProcessingWorker(QObject):
    # Signals for GUI communication
    log_message = pyqtSignal(str)
    video_progress = pyqtSignal(int, int, str)  # current, total, video name
    video_frame_progress = pyqtSignal(int, int)  # current_frame, total_frames
    finished = pyqtSignal(bool, str)

    def __init__(self, source_dir, dest_dir, keep_source=False, resolution=(112, 112), frame_count=16, copy_metadata=True, sampling_strategy="auto", skip_existing=True, device_mode="cpu", num_workers=1):
        super().__init__()
        self.source_dir = source_dir
        self.dest_dir = dest_dir
        self.keep_source = keep_source
        self.resolution = resolution  # (width, height)
        self.frame_count = frame_count
        self.copy_metadata = copy_metadata
        self.sampling_strategy = sampling_strategy  # "auto", "train", "val"
        self.skip_existing = skip_existing
        self.device_mode = device_mode  # "cpu", "gpu"
        self.num_workers = num_workers
        self.is_stopped = False

    def stop(self):
        self.is_stopped = True

    def run(self):
        try:
            self._total_skipped = 0
            self._last_log_skipped = 0
            self._last_progress_time = 0.0

            self.log_message.emit("--- Direct Folder Downframing Pipeline Started ---")
            self.log_message.emit(f"Source Folder: {self.source_dir}")
            self.log_message.emit(f"Destination Folder: {self.dest_dir}")
            self.log_message.emit(f"Resolution: {self.resolution[0]}x{self.resolution[1]} | Frames: {self.frame_count}")
            self.log_message.emit(f"Hardware Device: {self.device_mode.upper()} | Auto-Skip: {'Enabled' if self.skip_existing else 'Disabled'}")
            self.log_message.emit(f"Parallel Workers: {self.num_workers}")
            
            # Check PyTorch / CUDA availability if GPU selected
            use_gpu = False
            if self.device_mode == "gpu":
                try:
                    import torch
                    if torch.cuda.is_available():
                        use_gpu = True
                        self.log_message.emit(f"[INFO] PyTorch CUDA is available. Accelerating operations on GPU: {torch.cuda.get_device_name(0)}")
                    else:
                        self.log_message.emit("[WARNING] CUDA device not found. PyTorch is installed but CUDA is unavailable. Falling back to CPU mode.")
                except ImportError:
                    self.log_message.emit("[WARNING] PyTorch library not found. Falling back to standard CPU mode.")

            # Walk source directory
            all_filepaths = []
            for root, dirs, files in os.walk(self.source_dir):
                for file in files:
                    all_filepaths.append(os.path.join(root, file))

            video_extensions = ('.mp4', '.avi', '.mkv', '.webm', '.mov', '.flv', '.wmv', '.mpeg', '.mpg')
            video_files = []
            non_video_files = []

            for path in all_filepaths:
                if path.lower().endswith(video_extensions):
                    video_files.append(path)
                else:
                    non_video_files.append(path)

            total_videos = len(video_files)
            total_non_videos = len(non_video_files)

            self.log_message.emit(f"Found {total_videos} videos and {total_non_videos} metadata/raw files.")

            # 1. Copy non-video files if requested
            if self.copy_metadata and total_non_videos > 0:
                self.log_message.emit("Copying metadata, json, and other non-video files...")
                for idx, src_path in enumerate(non_video_files):
                    if self.is_stopped:
                        self.finished.emit(False, "Stopped by user")
                        return

                    # Replicate directory structure
                    rel_path = os.path.relpath(src_path, self.source_dir)
                    dest_path = os.path.join(self.dest_dir, rel_path)
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

                    try:
                        shutil.copy2(src_path, dest_path)
                    except Exception as e:
                        self.log_message.emit(f"Failed to copy {os.path.basename(src_path)}: {str(e)}")

            # 2. Process videos
            self.video_progress.emit(0, total_videos, "Starting...")

            import threading
            from concurrent.futures import ThreadPoolExecutor

            progress_lock = threading.Lock()
            completed_counter = [0]

            if self.num_workers > 1:
                self.log_message.emit(f"Spawning thread pool with {self.num_workers} parallel workers...")
                with ThreadPoolExecutor(max_workers=self.num_workers) as executor:
                    futures = []
                    for src_path in video_files:
                        if self.is_stopped:
                            break
                        futures.append(executor.submit(
                            self._process_single_video,
                            src_path,
                            total_videos,
                            use_gpu,
                            progress_lock,
                            completed_counter
                        ))
                    
                    # Wait for all futures to resolve
                    for fut in futures:
                        if self.is_stopped:
                            executor.shutdown(wait=False, cancel_futures=True)
                            break
                        try:
                            fut.result()
                        except Exception as fut_err:
                            self.log_message.emit(f"Error in execution thread: {str(fut_err)}")
            else:
                self.log_message.emit("Running in single-worker mode.")
                for src_path in video_files:
                    if self.is_stopped:
                        break
                    self._process_single_video(src_path, total_videos, use_gpu, progress_lock, completed_counter)

            if self.is_stopped:
                self.finished.emit(False, "Stopped by user")
                return

            if self._total_skipped > 0:
                self.log_message.emit(f"[INFO] Auto-skip summary: Total of {self._total_skipped} / {total_videos} videos were already downcompiled and successfully skipped.")

            self.video_progress.emit(total_videos, total_videos, "Finished Processing")
            self.log_message.emit("--- Direct Folder Downframing Completed Successfully ---")
            self.finished.emit(True, "")

        except Exception as e:
            self.log_message.emit(f"Critical error in folder downframing: {str(e)}")
            self.finished.emit(False, str(e))

    def _process_single_video(self, src_path, total_videos, use_gpu, progress_lock, completed_counter):
        if self.is_stopped:
            return

        rel_path = os.path.relpath(src_path, self.source_dir)
        dest_path = os.path.join(self.dest_dir, rel_path)
        
        # Force target extension to .mp4
        base, ext = os.path.splitext(dest_path)
        dest_path_mp4 = base + ".mp4"
        
        video_name = os.path.basename(src_path)

        # Check Auto-Skip option
        if self.skip_existing and os.path.exists(dest_path_mp4):
            with progress_lock:
                completed_counter[0] += 1
                current_completed = completed_counter[0]
                self._total_skipped += 1
                
                # Check if we should log a summary (every 100 skips) to prevent GUI thread congestion
                now = time.time()
                should_log = False
                if (self._total_skipped - self._last_log_skipped) >= 100:
                    should_log = True
                    log_msg = f"[INFO] Auto-skipped {self._total_skipped} already downcompiled videos..."
                    self._last_log_skipped = self._total_skipped
                
                # Throttle progress updates to 10Hz (every 100ms) or when finished
                should_progress = False
                if now - self._last_progress_time >= 0.1 or current_completed == total_videos:
                    should_progress = True
                    self._last_progress_time = now

            if should_log:
                self.log_message.emit(log_msg)
            if should_progress:
                self.video_progress.emit(current_completed, total_videos, f"Skipping {video_name}")
            return

        os.makedirs(os.path.dirname(dest_path_mp4), exist_ok=True)
        
        with progress_lock:
            current_completed = completed_counter[0]
        self.video_progress.emit(current_completed, total_videos, video_name)

        # Sampling strategy split determination
        is_train = False
        if self.sampling_strategy == "train":
            is_train = True
        elif self.sampling_strategy == "val":
            is_train = False
        else:  # "auto"
            parts = rel_path.split(os.sep)
            for part in parts:
                if part.lower() == "train":
                    is_train = True
                    break

        if self.keep_source:
            # Copy original raw
            try:
                shutil.copy2(src_path, dest_path_mp4)
            except Exception as e:
                self.log_message.emit(f"Failed to copy original video {video_name}: {str(e)}")
        else:
            # Run downsampling (GPU or CPU or FFmpeg)
            if self.device_mode == "ffmpeg":
                success = self._downsample_video_ffmpeg(src_path, dest_path_mp4, is_train)
            else:
                success = self._downsample_video(src_path, dest_path_mp4, is_train, use_gpu)
                
            if not success:
                if not self.is_stopped:
                    self.log_message.emit(f"Warning: Failed decoding {video_name}. Skipping this video (corrupted/invalid).")
                    if os.path.exists(dest_path_mp4):
                        try:
                            os.remove(dest_path_mp4)
                        except Exception as rm_err:
                            self.log_message.emit(f"Failed to clean up partial file for {video_name}: {str(rm_err)}")

        with progress_lock:
            completed_counter[0] += 1
            current_completed = completed_counter[0]
        self.video_progress.emit(current_completed, total_videos, video_name)

    def _downsample_video(self, src_path, dest_path, is_train, use_gpu):
        """Performs spatiotemporal downsampling with selective decoding and optional GPU acceleration."""
        return _downsample_video_standalone(src_path, dest_path, is_train, self.resolution, self.frame_count, self.device_mode, use_gpu)

    def _downsample_video_ffmpeg(self, src_path, dest_path, is_train):
        """Performs spatiotemporal downsampling with high-compatibility FFmpeg CLI."""
        import subprocess
        import uuid
        
        # Create a workspace-nested unique temp folder inside dest_dir
        temp_sub_dir = os.path.join(self.dest_dir, f"tmp_ff_{uuid.uuid4().hex}")
        os.makedirs(temp_sub_dir, exist_ok=True)
        
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            
        try:
            # 1. Extract all frames as JPEGs
            cmd_extract = [
                "ffmpeg", "-y",
                "-i", src_path,
                "-q:v", "2",
                os.path.join(temp_sub_dir, "frame_%05d.jpg")
            ]
            subprocess.run(cmd_extract, startupinfo=startupinfo, capture_output=True, text=True, check=False)
            
            # Read extracted frames list
            frame_files = sorted([
                os.path.join(temp_sub_dir, f)
                for f in os.listdir(temp_sub_dir)
                if f.startswith("frame_") and f.endswith(".jpg")
            ])
            
            total_frames = len(frame_files)
            if total_frames == 0:
                return False
                
            # 2. TSN Temporal Sampling
            sampled_indices = []
            T = self.frame_count
            
            if total_frames >= T:
                seg_len = total_frames / T
                for i in range(T):
                    start_idx = int(i * seg_len)
                    end_idx = int((i + 1) * seg_len) - 1
                    end_idx = max(start_idx, end_idx)
                    
                    if is_train:
                        idx = random.randint(start_idx, end_idx)
                    else:
                        idx = start_idx + (end_idx - start_idx) // 2
                    
                    idx = min(idx, total_frames - 1)
                    sampled_indices.append(idx)
            else:
                sampled_indices = list(range(total_frames))
                while len(sampled_indices) < T:
                    sampled_indices.append(total_frames - 1)
                    
            # 3. Copy sampled frame sequence & emit frame progress
            temp_sampled_dir = os.path.join(temp_sub_dir, "sampled")
            os.makedirs(temp_sampled_dir, exist_ok=True)
            
            # Emit 0% frame progress
            if self.num_workers == 1:
                self.video_frame_progress.emit(0, T)
                
            for seq_idx, frame_idx in enumerate(sampled_indices):
                if self.is_stopped:
                    return False
                src_frame = frame_files[frame_idx]
                dest_frame = os.path.join(temp_sampled_dir, f"seq_{seq_idx+1:05d}.jpg")
                shutil.copy2(src_frame, dest_frame)
                
                # Emit frame copy progress
                if self.num_workers == 1:
                    self.video_frame_progress.emit(seq_idx + 1, T)
                
            # 4. Compile into target H.264 video applying Gaussian blur and scaling
            w, h = self.resolution
            cmd_compile = [
                "ffmpeg", "-y",
                "-f", "image2",
                "-framerate", "30",
                "-i", os.path.join(temp_sampled_dir, "seq_%05d.jpg"),
                "-vf", f"gblur=sigma=0.8,scale={w}:{h}:flags=area",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                dest_path
            ]
            res = subprocess.run(cmd_compile, startupinfo=startupinfo, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                return False
                
            return True
        except Exception as e:
            return False
        finally:
            shutil.rmtree(temp_sub_dir, ignore_errors=True)
