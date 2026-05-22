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

    def download_job_wrapper(self, job, done_event, result_holder):
        try:
            result_holder["ok"] = self.download_one_file(job)
        finally:
            done_event.set()
            self.scheduler_event.set()

