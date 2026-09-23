import codecs

import pytest

from core.subtitle_generator import OCRToASSOptimizer


@pytest.mark.parametrize('failure', ['fsync', 'replace'])
def test_failed_atomic_write_keeps_existing_file(tmp_path, monkeypatch, failure):
    from utils import atomic_write

    dest = tmp_path / 'sub.ass'
    dest.write_bytes(b'OLD')

    def fail(*args):
        raise OSError('disk failure')

    monkeypatch.setattr(atomic_write.os, failure, fail)
    with pytest.raises(OSError, match='disk failure'):
        atomic_write.atomic_write_text(dest, 'NEW', encoding='utf-8-sig')
    assert dest.read_bytes() == b'OLD'
    assert list(tmp_path.iterdir()) == [dest]


@pytest.mark.parametrize('empty_path', ['no_data', 'no_events', 'events'])
def test_ass_output_paths_are_atomic(tmp_path, monkeypatch, empty_path):
    from utils import atomic_write

    dest = tmp_path / 'sub.ass'
    dest.write_bytes(b'OLD')
    converter = OCRToASSOptimizer(video_path='fake.mp4', output_path=str(dest),
                                   fps=25, width=320, height=240)
    event = {'tags': '', 'body': '字幕', 'start_time': '0:00:01.00',
             'end_time': '0:00:02.00', 'style': 'Default'}
    replacements = []
    original = atomic_write.os.replace

    def replace(src, dst):
        assert dest.read_bytes() == b'OLD'
        replacements.append(src)
        original(src, dst)

    monkeypatch.setattr(atomic_write.os, 'replace', replace)
    if empty_path == 'no_data':
        converter.convert_from_memory(iter([]))
    else:
        converter._write_final_file([event] if empty_path == 'events' else [])
    assert len(replacements) == 1
    assert dest.read_bytes().startswith(codecs.BOM_UTF8)
    assert '[Events]' in dest.read_text(encoding='utf-8-sig')
    assert ('字幕' in dest.read_text(encoding='utf-8-sig')) == (empty_path == 'events')
    assert list(tmp_path.iterdir()) == [dest]
