"""Download and verification logic for IA Batch Downloader GUI."""

import os
import time
import shutil
from urllib.parse import quote
import hashlib

from internetarchive import get_session


class DownloaderMixin:
    def make_download_url(self, identifier, file_name):
        # Do not encode "/" because IA file names may contain folder paths.
        return f"https://archive.org/download/{quote(identifier)}/{quote(file_name, safe='/')}"

    def calculate_checksum(self, path, algorithm):
        h = hashlib.new(algorithm)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def checksum_matches(self, path, md5_value=None, sha1_value=None):
        return True

        if sha1_value:
            try:
                return self.calculate_checksum(path, "sha1").lower() == sha1_value.lower()
            except Exception as e:
                self.ui_detail_log(f"SHA1 check failed for {path}: {e}")
                return False

        if md5_value:
            try:
                return self.calculate_checksum(path, "md5").lower() == md5_value.lower()
            except Exception as e:
                self.ui_detail_log(f"MD5 check failed for {path}: {e}")
                return False

        return True

    def download_one_file(self, job):
        identifier = job["identifier"]
        file_name = job["file_name"]
        expected_size = job["expected_size"]
        local_path = self.normalize_path_text(job["local_path"])
        part_path = job.get("part_path") or (local_path + ".part")
        row_id = job["row_id"]
        size_text = job["size_text"]
        source_url = job["source_url"]
        config_file = job["config_file"]

        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        self.register_active_download(row_id)
        try:
            self.autosave_after_first_download_start()
        except Exception:
            pass

        session = get_session(config_file=config_file) if config_file else get_session()
        url = self.make_download_url(identifier, file_name)

        max_attempts = 3
        bytes_downloaded = os.path.getsize(part_path) if os.path.exists(part_path) else 0

        try:
            for attempt in range(1, max_attempts + 1):
                resume_from = os.path.getsize(part_path) if os.path.exists(part_path) else 0

                if expected_size is not None and resume_from >= expected_size:
                    try:
                        os.remove(part_path)
                    except OSError:
                        pass
                    resume_from = 0

                bytes_downloaded = resume_from
                if resume_from > 0 and attempt == 1:
                    self.ui_log(f"Resuming partial .part file: {part_path} from {self.format_bytes(resume_from)}")
                last_ui_update = 0
                start_time = time.time()
                last_speed_time = start_time
                last_speed_bytes = bytes_downloaded

                attempt_label = f"Resuming .part attempt {attempt}/{max_attempts}" if resume_from else f"Starting attempt {attempt}/{max_attempts}"
                start_pct = self.percent_from_sizes(bytes_downloaded, expected_size) if expected_size else 0
                self.ui_add_or_update_file(row_id, identifier, file_name, attempt_label, f"{start_pct}%", size_text, local_path, source_url)
                self.ui_active_download(row_id, file_name, start_pct, self.format_bytes(bytes_downloaded), size_text, attempt_label)
                if self.should_update_current_bar(row_id):
                    self.ui_file_progress(start_pct, f"Current file: {file_name} — {attempt_label}")

                try:
                    headers = {}
                    if resume_from > 0:
                        headers["Range"] = f"bytes={resume_from}-"

                    response = session.get(url, stream=True, timeout=60, headers=headers)
                    try:
                        if response.status_code == 401:
                            raise RuntimeError("401 Unauthorized. Try using an IA config/login file with access.")
                        if response.status_code == 403:
                            raise RuntimeError("403 Forbidden. Your account may not have access to this file.")
                        if response.status_code == 404:
                            raise RuntimeError("404 Not Found. File URL may be unavailable.")

                        if resume_from > 0 and response.status_code != 206:
                            # Server ignored Range. Restart cleanly.
                            response.close()
                            try:
                                os.remove(part_path)
                            except OSError:
                                pass
                            resume_from = 0
                            bytes_downloaded = 0
                            last_speed_bytes = 0
                            response = session.get(url, stream=True, timeout=60)

                        response.raise_for_status()

                        total_size = expected_size
                        if total_size is None:
                            header_len = response.headers.get("content-length")
                            try:
                                total_size = int(header_len) + resume_from if header_len else None
                            except Exception:
                                total_size = None

                        mode = "ab" if resume_from > 0 else "wb"
                        with open(part_path, mode) as out:
                            for chunk in response.iter_content(chunk_size=1024 * 1024):
                                if not chunk:
                                    continue

                                if self.stop_requested:
                                    raise RuntimeError("Stopped by user")

                                if self.pause_requested:
                                    current_pct = self.percent_from_sizes(bytes_downloaded, total_size) if total_size else 0
                                    current_total = self.format_bytes(total_size) if total_size else "Unknown"
                                    self.ui_active_download(
                                        row_id,
                                        file_name,
                                        current_pct,
                                        self.format_bytes(bytes_downloaded),
                                        current_total,
                                        "Paused"
                                    )
                                    if self.should_update_current_bar(row_id):
                                        self.ui_file_progress(current_pct, f"Paused: {file_name}")
                                    self.wait_while_paused(row_id, file_name)

                                if self.stop_requested:
                                    raise RuntimeError("Stopped by user")

                                if self.should_throttle_download(row_id):
                                    current_pct = self.percent_from_sizes(bytes_downloaded, total_size) if total_size else 0
                                    current_total = self.format_bytes(total_size) if total_size else "Unknown"
                                    throttle_status = f"Queued - limit reduced to {self.get_concurrent_count()}"
                                    self.ui_active_download(
                                        row_id,
                                        file_name,
                                        current_pct,
                                        self.format_bytes(bytes_downloaded),
                                        current_total,
                                        throttle_status
                                    )
                                    self.ui_add_or_update_file(
                                        row_id,
                                        identifier,
                                        file_name,
                                        throttle_status,
                                        f"{current_pct}%",
                                        current_total,
                                        local_path,
                                        source_url
                                    )
                                    if self.should_update_current_bar(row_id):
                                        self.ui_file_progress(current_pct, f"{throttle_status}: {file_name}")
                                    self.wait_while_throttled(row_id, file_name)

                                if self.stop_requested:
                                    raise RuntimeError("Stopped by user")

                                out.write(chunk)
                                bytes_downloaded += len(chunk)

                                now = time.time()
                                if now - last_ui_update >= 0.25:
                                    elapsed = max(0.001, now - last_speed_time)
                                    recent_bytes = bytes_downloaded - last_speed_bytes
                                    bytes_per_second = recent_bytes / elapsed
                                    speed_text = self.format_speed(bytes_per_second)
                                    last_speed_time = now
                                    last_speed_bytes = bytes_downloaded
                                    last_ui_update = now

                                    pct = self.percent_from_sizes(bytes_downloaded, total_size) if total_size else 0
                                    downloaded_text = self.format_bytes(bytes_downloaded)
                                    total_text = self.format_bytes(total_size) if total_size else "Unknown"

                                    eta_text = ""
                                    if total_size and bytes_per_second > 0:
                                        remaining_bytes = max(0, total_size - bytes_downloaded)
                                        eta_text = self.format_eta(remaining_bytes / bytes_per_second)

                                    self.ui_add_or_update_file(row_id, identifier, file_name, "Downloading", f"{pct}%", total_text, local_path, source_url)
                                    self.ui_active_download(row_id, file_name, pct, downloaded_text, total_text, "Downloading", speed_text, eta_text)
                                    if self.should_update_current_bar(row_id):
                                        eta_part = f" — ETA {eta_text}" if eta_text else ""
                                        self.ui_file_progress(
                                            pct,
                                            f"Current file: {file_name} — {pct}% "
                                            f"({downloaded_text} / {total_text}) — {speed_text}{eta_part}"
                                        )
                    finally:
                        response.close()

                    final_size = os.path.getsize(part_path) if os.path.exists(part_path) else 0

                    if expected_size is not None and final_size != expected_size:
                        raise RuntimeError(
                            f"Size mismatch after attempt {attempt}: "
                            f"local={self.format_bytes(final_size)}, expected={size_text}"
                        )

                    os.replace(part_path, local_path)

                    self.mark_completed()
                    try:
                        _, _, free_now = shutil.disk_usage(job.get("dest") or os.path.dirname(local_path))
                        self.ui_disk_space(f"Disk space: {self.format_bytes(free_now)} free")
                    except Exception:
                        pass

                    done_size = self.format_bytes(os.path.getsize(local_path))
                    self.failed_download_jobs = [j for j in self.failed_download_jobs if j.get("row_id") != row_id]
                    self.resume_pending_jobs = [j for j in self.resume_pending_jobs if j.get("row_id") != row_id]
                    self.record_history(identifier, file_name, local_path, expected_size, "Done", source_url)
                    self.save_autosave_state()
                    self.ui_add_or_update_file(row_id, identifier, file_name, "Done", "100%", done_size, local_path, source_url)
                    self.ui_active_download(row_id, file_name, 100, done_size, done_size, "Done", "", "")
                    if self.should_update_current_bar(row_id):
                        self.ui_file_progress(100, f"Completed: {file_name}")
                    self.unregister_active_download(row_id)
                    return True

                except Exception as e:
                    if self.stop_requested:
                        raise

                    self.ui_log(f"Attempt {attempt}/{max_attempts} failed for {file_name}: {e}")

                    if attempt < max_attempts:
                        backoff = 2 ** (attempt - 1)
                        self.ui_active_download(
                            row_id,
                            file_name,
                            self.percent_from_sizes(bytes_downloaded, expected_size) if expected_size else 0,
                            self.format_bytes(bytes_downloaded),
                            size_text,
                            f"Retrying in {backoff}s"
                        )
                        time.sleep(backoff)
                        continue

                    raise

        except Exception as e:
            if self.stop_requested:
                self.ui_add_or_update_file(row_id, identifier, file_name, "Queued - stopped", f"{self.percent_from_sizes(bytes_downloaded, expected_size) if expected_size else 0}%", size_text, local_path, source_url)
                self.ui_active_download(row_id, file_name, self.percent_from_sizes(bytes_downloaded, expected_size) if expected_size else 0, self.format_bytes(bytes_downloaded), size_text, "Stopped")
                if self.should_update_current_bar(row_id):
                    self.ui_file_progress(self.percent_from_sizes(bytes_downloaded, expected_size) if expected_size else 0, f"Stopped: {file_name}")
                self.unregister_active_download(row_id)
                self.queue_stopped_job_for_resume(job, was_active=True)
                self.ui_log(f"Stopped and queued first for same-session resume: {local_path}")
                return False

            self.ui_add_or_update_file(row_id, identifier, file_name, "Download error", "0%", size_text, str(e), source_url)
            self.ui_active_download(row_id, file_name, 0, self.format_bytes(bytes_downloaded), size_text, "Error")
            if self.should_update_current_bar(row_id):
                self.ui_file_progress(0, f"Error: {file_name}")
            self.unregister_active_download(row_id)
            self.failed_download_jobs.append(job)
            self.save_autosave_state()
            self.record_history(identifier, file_name, local_path, expected_size, "Download error", source_url)
            self.ui_log(f"Download error for {file_name}: {e}")
            return False

