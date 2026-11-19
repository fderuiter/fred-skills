import threading


def run_async(job_id: str, sink: list[str], done: threading.Event) -> None:
    def _work() -> None:
        sink.append(job_id)
        done.set()

    threading.Thread(target=_work, daemon=True).start()


def test_worker_records_job() -> None:
    seen_jobs: list[str] = []
    done = threading.Event()
    run_async("job-1", seen_jobs, done)
    assert done.wait(timeout=2.0)
    assert seen_jobs == ["job-1"]
