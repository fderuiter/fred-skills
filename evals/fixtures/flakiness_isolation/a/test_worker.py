import threading
import time

SEEN_JOBS: list[str] = []


def run_async(job_id: str) -> None:
    threading.Thread(target=lambda: SEEN_JOBS.append(job_id), daemon=True).start()


def test_worker_records_job() -> None:
    run_async("job-1")
    time.sleep(0.05)
    assert SEEN_JOBS == ["job-1"]
