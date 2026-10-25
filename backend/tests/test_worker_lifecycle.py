from threading import Event

from app import worker


def test_worker_flushes_telemetry_and_disposes_database_on_shutdown(monkeypatch):
    calls: list[str] = []

    class Telemetry:
        def shutdown(self):
            calls.append("telemetry")

    stop_event = Event()
    stop_event.set()
    monkeypatch.setattr(worker, "telemetry_providers", Telemetry())
    monkeypatch.setattr(worker.engine, "dispose", lambda: calls.append("database"))

    worker.run(stop_event)

    assert calls == ["telemetry", "database"]
