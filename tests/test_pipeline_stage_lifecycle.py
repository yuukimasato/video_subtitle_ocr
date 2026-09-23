import threading
from dataclasses import replace

import pytest

from core import pipeline_stages as stages


@pytest.fixture
def harness(monkeypatch, tmp_path):
    instances, groups, closed = [], [], []

    class Optimizer:
        ocr_calls = 1
        frames_filled = 0

        def __init__(self, **kwargs):
            self.cleaned = False
            instances.append(self)

        def process_roi_group(self, frames, **kwargs):
            groups.append([f[3] for f in frames])
            return [(f[0], {}, *f[2:]) for f in frames]

        def cleanup(self):
            self.cleaned = True

    def extract(*args, **kwargs):
        try:
            for frame in range(4):
                for roi in range(2):
                    yield {}, None, frame, f'roi_{roi}', float(frame)
        finally:
            closed.append(True)

    monkeypatch.setattr(stages, 'OcrOptimizer', Optimizer)
    monkeypatch.setattr(stages.roi_extractor, 'extract_roi_frames', extract)
    monkeypatch.setattr(stages.roi_extractor, 'calculate_total_roi_frames', lambda *a: 8)
    monkeypatch.setattr(stages.ocr_processor, 'get_device_mode', lambda: 'cpu')
    ctx = stages.PipelineContext('fake.mp4', [{}, {}], 4, 1, str(tmp_path), False,
                                 in_memory_ocr=True)
    return ctx, Optimizer, instances, groups, closed


@pytest.mark.parametrize('stream', [False, True])
def test_groups_are_isolated_and_resources_cleaned(harness, stream):
    ctx, _, instances, groups, closed = harness
    records, _ = stages.extract_and_ocr_stage(
        replace(ctx, time_slice_enabled=stream, time_slice_seconds=1),
        progress_cb=lambda *a: None, cancel_check=lambda: False)
    assert len(records) == 8
    assert all(len(set(group)) == 1 for group in groups)
    assert all(obj.cleaned for obj in instances)
    assert closed == [True]


@pytest.mark.parametrize('stream', [False, True])
def test_failure_cleans_every_optimizer(harness, monkeypatch, stream):
    ctx, optimizer, instances, _, closed = harness

    def fail(*a, **kw):
        raise RuntimeError('OCR failed')

    monkeypatch.setattr(optimizer, 'process_roi_group', fail)
    with pytest.raises(RuntimeError, match='OCR failed'):
        stages.extract_and_ocr_stage(
            replace(ctx, time_slice_enabled=stream, time_slice_seconds=1),
            progress_cb=lambda *a: None, cancel_check=lambda: False)
    assert instances and all(obj.cleaned for obj in instances)
    assert closed == [True]


def test_cancel_closes_generator_and_cleans_optimizer(harness):
    ctx, _, instances, _, closed = harness
    stop = threading.Event()

    def progress(pct, message):
        if pct == 10:
            stop.set()

    with pytest.raises(stages.PipelineCancelled):
        stages.extract_and_ocr_stage(ctx, progress_cb=progress, cancel_check=stop.is_set)
    assert all(obj.cleaned for obj in instances)
    assert closed == [True]


def test_streaming_progress_never_goes_backwards(harness):
    ctx, _, _, _, _ = harness
    seen = []
    stages.extract_and_ocr_stage(
        replace(ctx, time_slice_enabled=True, time_slice_seconds=1, visualize=True),
        progress_cb=lambda p, m: seen.append(p), cancel_check=lambda: False)
    assert seen == sorted(set(seen))


@pytest.mark.parametrize("stream", [False, True])
def test_failure_joins_running_task_before_return(harness, monkeypatch, stream):
    ctx, optimizer, instances, _, closed = harness
    started = threading.Event()
    finished = threading.Event()

    def process(self, frames, is_cancelled_func, **kw):
        if frames[0][3] == 'roi_0':
            assert started.wait(2)
            raise RuntimeError('bucket failed')
        started.set()
        try:
            # Emulate cooperative OCR running while another task fails.
            for _ in range(2000):
                if is_cancelled_func():
                    return []
                threading.Event().wait(0.001)
            pytest.fail('running task never received cancellation')
        finally:
            finished.set()

    monkeypatch.setattr(optimizer, 'process_roi_group', process)
    with pytest.raises(RuntimeError, match='bucket failed'):
        stages.extract_and_ocr_stage(
            replace(ctx, time_slice_enabled=stream, time_slice_seconds=1),
            progress_cb=lambda *a: None, cancel_check=lambda: False)
    assert finished.is_set()
    assert all(obj.cleaned for obj in instances)
    assert closed == [True]
