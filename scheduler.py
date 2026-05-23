"""Dynamic scheduler logic for IA Batch Downloader GUI."""

import os
import threading


class SchedulerMixin:
    def run_dynamic_scheduler(self, identifier, item_total, completed_for_item, pending_jobs):
        pending = list(pending_jobs)
        active = {}
        finished_count = 0

        while pending or active:
            self.scheduler_event.clear()
            current_limit = self.get_concurrent_count()

            # Start new jobs only if stop has not been requested and app is not paused.
            while pending and len(active) < current_limit and not self.stop_requested and not self.pause_requested:
                # Check free space before starting each next file.
                dest_for_space_check = pending[0].get("dest") or os.path.dirname(pending[0]["local_path"])
                if self.stop_if_no_space_var.get() and not self.check_space_before_starting_next_file(dest_for_space_check):
                    self.stop_requested = True
                    break

                job = pending.pop(0)
                done_event = threading.Event()
                result_holder = {"ok": False}

                t = threading.Thread(target=self.download_job_wrapper, args=(job, done_event, result_holder), daemon=True)
                active[t] = (job, done_event, result_holder)
                t.start()

                self.ui_status(
                    f"{identifier}: {completed_for_item + finished_count}/{item_total} complete, "
                    f"{item_total - completed_for_item - finished_count} left; "
                    f"{len(active)} active; limit {current_limit}"
                )

            if self.pause_requested and pending:
                self.ui_status(
                    f"Paused: {completed_for_item + finished_count}/{item_total} complete; "
                    f"{len(active)} active; {len(pending)} waiting"
                )

            # If stop was requested, preserve not-yet-started jobs for same-session resume.
            if self.stop_requested and pending:
                for job in pending:
                    self.ui_add_or_update_file(
                        job["row_id"], identifier, job["file_name"], "Queued - stopped",
                        "0%", job["size_text"], job["local_path"], job["source_url"]
                    )

                for job in pending:
                    self.queue_stopped_job_for_resume(job, was_active=False)
                pending.clear()

            finished_threads = []
            for t, (job, done_event, result_holder) in list(active.items()):
                if done_event.is_set():
                    finished_threads.append(t)
                    if result_holder.get("ok"):
                        finished_count += 1

            for t in finished_threads:
                active.pop(t, None)

            self.ui_status(
                f"{identifier}: {completed_for_item + finished_count}/{item_total} complete, "
                f"{item_total - completed_for_item - finished_count} left; "
                f"{len(active)} active; limit {current_limit}"
            )

            if not pending and not active:
                break

            self.scheduler_event.wait(0.5)

        return finished_count


    def run_streaming_scheduler(self, identifier, pending_jobs, producer_done_event):
        """Run downloads while another part of the program is still building the queue.

        pending_jobs is a shared list. The producer appends jobs in strict URL/file
        order and calls scheduler_event.set(). This scheduler always pops from the
        front, so download start order follows the order files were added.
        """
        pending = pending_jobs
        active = {}
        finished_count = 0

        while True:
            self.scheduler_event.clear()
            current_limit = self.get_concurrent_count()

            while pending and len(active) < current_limit and not self.stop_requested and not self.pause_requested:
                job = pending[0]
                dest_for_space_check = job.get("dest") or os.path.dirname(job["local_path"])
                if self.stop_if_no_space_var.get() and not self.check_space_before_starting_next_file(dest_for_space_check):
                    self.stop_requested = True
                    break

                job = pending.pop(0)
                done_event = threading.Event()
                result_holder = {"ok": False}

                t = threading.Thread(target=self.download_job_wrapper, args=(job, done_event, result_holder), daemon=True)
                active[t] = (job, done_event, result_holder)
                t.start()

            if self.stop_requested and pending:
                for job in list(pending):
                    self.ui_add_or_update_file(
                        job["row_id"], job.get("identifier", identifier), job["file_name"], "Queued - stopped",
                        "0%", job["size_text"], job["local_path"], job["source_url"]
                    )
                    self.queue_stopped_job_for_resume(job, was_active=False)
                pending.clear()

            finished_threads = []
            for t, (job, done_event, result_holder) in list(active.items()):
                if done_event.is_set():
                    finished_threads.append(t)
                    if result_holder.get("ok"):
                        finished_count += 1

            for t in finished_threads:
                active.pop(t, None)

            with self.progress_lock:
                total = self.queue_total_files
                complete = self.queue_completed_files

            left_known = max(0, total - complete) if total else len(pending) + len(active)
            self.ui_status(
                f"{identifier}: {complete}/{total or '?'} complete, "
                f"{left_known} left; {len(active)} active; "
                f"{len(pending)} ready; limit {current_limit}"
            )

            if producer_done_event.is_set() and not pending and not active:
                break

            self.scheduler_event.wait(0.25)

        return finished_count

    def download_job_wrapper(self, job, done_event, result_holder):
        try:
            result_holder["ok"] = self.download_one_file(job)
        finally:
            done_event.set()
            self.scheduler_event.set()

