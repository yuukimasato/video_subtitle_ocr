from types import SimpleNamespace

import cli
from core import pipeline_stages as stages


def test_cli_uses_shared_stages_and_preserves_context(monkeypatch, tmp_path):
    video = tmp_path / 'video.mp4'
    video.touch()
    calls = []
    monkeypatch.setattr(cli, 'probe_video', lambda _: {
        'width': 320, 'height': 240, 'fps': 25, 'total_frames': 100})

    def extract(ctx, **kw):
        assert ctx.in_memory_ocr
        assert not ctx.enable_boundary_refine
        assert ctx.ocr_engine_id == 'rapid'
        assert ctx.engine_options['lang'] == 'japan'
        assert len(ctx.roi_data) == 2
        calls.append('extract')
        return [('raw',)], {'total_ocr_calls': 2}

    def restore(ctx, raw, **kw):
        assert raw == [('raw',)]
        calls.append('restore')
        return [({}, 0, 'roi_0', 0.0)]

    def convert(records):
        assert list(records) == [({}, 0, 'roi_0', 0.0)]
        calls.append('convert')
        video.with_suffix('.ass').write_text('output')

    monkeypatch.setattr(stages, 'extract_and_ocr_stage', extract)
    monkeypatch.setattr(stages, 'restore_stage', restore)
    monkeypatch.setattr('core.subtitle_generator.OCRToASSOptimizer',
                        lambda **kw: SimpleNamespace(convert_from_memory=convert))
    args = cli.parse_args([str(video), '--workers', '1', '--engine', 'rapid',
                           '--lang', 'japan', '--roi', '0,0,100,50',
                           '--roi', '0,100,100,50', '-q'])
    assert cli.run_pipeline(args) == 0
    assert calls == ['extract', 'restore', 'convert']
