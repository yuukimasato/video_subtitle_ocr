"""Write complete text files without exposing a partially written result."""

import os
import tempfile
from pathlib import Path


def atomic_write_text(path, content: str, *, encoding: str = 'utf-8') -> None:
    destination = Path(path).absolute()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding=encoding, dir=destination.parent,
            prefix=f'.{destination.name}.', suffix='.tmp', delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
