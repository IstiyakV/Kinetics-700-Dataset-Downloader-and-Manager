import os
import shutil

def get_free_space(path):
    """
    Returns the free space on the drive containing the given path in bytes.
    If the path does not exist, returns the free space of its parent directory recursively.
    """
    abs_path = os.path.abspath(path)
    while not os.path.exists(abs_path):
        parent = os.path.dirname(abs_path)
        if parent == abs_path:  # Reached root and still doesn't exist (should not happen normally)
            break
        abs_path = parent
    
    try:
        total, used, free = shutil.disk_usage(abs_path)
        return free
    except Exception:
        return 0

def format_size(size_in_bytes):
    """
    Converts a size in bytes to a human-readable string (e.g. GB, MB).
    """
    if size_in_bytes is None:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} PB"

def format_time(seconds):
    """
    Converts a duration in seconds into a human-readable duration string (HH:MM:SS).
    """
    if seconds is None or seconds < 0:
        return "--:--:--"
    
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes:02d}:{secs:02d}"

def check_disk_space(staging_path, destination_path, source_mode=False):
    """
    Validates if there is enough disk space.
    - Staging path needs at least ~90 GB (to download the ~42 GB part zip and extract it).
    - Destination path needs space for the output. If source mode is true, it needs ~45 GB per part.
      If downsampled, it needs ~1.5 GB per part.
    Returns: (bool, staging_free_bytes, dest_free_bytes, error_message)
    """
    staging_free = get_free_space(staging_path)
    dest_free = get_free_space(destination_path)
    
    # 90 GB in bytes
    required_staging = 90 * 1024 * 1024 * 1024
    # Destination requirement
    required_dest = (45 if source_mode else 2) * 1024 * 1024 * 1024
    
    # If staging and destination are on the same drive, they share space
    same_drive = False
    try:
        staging_drive = os.path.splitdrive(os.path.abspath(staging_path))[0]
        dest_drive = os.path.splitdrive(os.path.abspath(destination_path))[0]
        if staging_drive.lower() == dest_drive.lower() and staging_drive:
            same_drive = True
    except Exception:
        pass
        
    if same_drive:
        total_required = required_staging + required_dest
        if staging_free < total_required:
            msg = (f"Insufficient shared disk space on drive {staging_drive}.\n"
                   f"Required: {format_size(total_required)} (Staging + Final Output).\n"
                   f"Available: {format_size(staging_free)}.\n"
                   f"Please clear space to avoid download failure.")
            return False, staging_free, dest_free, msg
    else:
        if staging_free < required_staging:
            msg = (f"Insufficient disk space in staging buffer ({staging_path}).\n"
                   f"Required: ~90 GB (for Zip download & extraction buffer).\n"
                   f"Available: {format_size(staging_free)}.\n"
                   f"Please clear space or choose another drive.")
            return False, staging_free, dest_free, msg
            
        if dest_free < required_dest:
            msg = (f"Insufficient disk space in final destination folder ({destination_path}).\n"
                   f"Required: ~{format_size(required_dest)} (per part).\n"
                   f"Available: {format_size(dest_free)}.\n"
                   f"Please clear space or choose another drive.")
            return False, staging_free, dest_free, msg
            
    return True, staging_free, dest_free, ""

def is_video_file(filename):
    """
    Checks if the filename has a known video extension.
    """
    video_extensions = ('.mp4', '.avi', '.mkv', '.webm', '.mov', '.flv', '.wmv', '.mpeg', '.mpg')
    return filename.lower().endswith(video_extensions)
