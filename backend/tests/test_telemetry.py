from app.core.telemetry import _enabled_signals


def test_no_telemetry_signal_starts_without_an_endpoint():
    assert _enabled_signals({}) == (False, False)


def test_signal_specific_endpoints_only_enable_the_requested_signal():
    assert _enabled_signals({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://collector/traces"}) == (
        True,
        False,
    )
    metrics_only = _enabled_signals(
        {"OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "https://collector/metrics"}
    )
    assert metrics_only == (False, True)


def test_generic_otel_endpoint_enables_both_signals():
    assert _enabled_signals({"OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector"}) == (True, True)
